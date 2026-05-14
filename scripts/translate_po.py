#!/usr/bin/env python3
"""Translate a stimmo .po catalog using an OpenRouter LLM.

Usage:
    uv run python scripts/translate_po.py --locale it_IT
    uv run python scripts/translate_po.py --locale it_IT --force      # re-translate all
    uv run python scripts/translate_po.py --locale it_IT --dry-run    # print without writing

The OPENROUTER_API_KEY env var is used unless --key is passed.
Defaults to deepseek/deepseek-v4-flash via OpenRouter; override with --model.

Strings are sent in batches to reduce API round-trips. Python-format
placeholders (%(name)s) and HTML tags are preserved by the system prompt.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx
from babel.messages import pofile
from babel.messages.catalog import Message

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LOCALE_DIR = _REPO_ROOT / "src" / "stimmo" / "locale"

_SYSTEM_PROMPT = """\
You are a professional technical translator working on stimmo, a Milan \
(Italy) real-estate fair-price estimator. Your job is to translate \
UI strings from English into the target language.

Rules:
1. Preserve Python %-format placeholders exactly: %(name)s, %(n)d, etc.
2. Preserve HTML tags exactly: <em>, <b>, <span class="...">, etc.
3. Italian domain jargon stays in Italian even in English translations: \
rogito, perizia, perito, OMI (Osservatorio del Mercato Immobiliare), \
Compr_min, Compr_max, spese condominiali, spese straordinarie, piano nobile, \
cortile, Agenzia delle Entrate, comune, cintura metropolitana, semestre.
4. Keep the same register (professional but accessible) and roughly the \
same length as the source.
5. Do NOT translate proper nouns: Milano, OpenStreetMap, Overpass, Nominatim, \
immobiliare.it, IODL, ODbL, GitHub.
6. Return ONLY a valid JSON object mapping each source string exactly to its \
translation. No comments, no explanation, no markdown fences.\
"""


def _call_openrouter(
    messages: list[dict],
    *,
    model: str,
    api_key: str,
    timeout: int = 60,
) -> str:
    url = "https://openrouter.ai/api/v1/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
    }
    resp = httpx.post(
        url,
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://stimmo.it",
            "X-Title": "stimmo translate_po",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _translate_batch(
    batch: dict[str, str],
    *,
    target_lang: str,
    model: str,
    api_key: str,
) -> dict[str, str]:
    """Send a {msgid: ""} dict and return {msgid: translation}."""
    user_content = f"Translate the following strings into {target_lang}.\n\n" + json.dumps(
        batch, ensure_ascii=False, indent=2
    )
    raw = _call_openrouter(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        model=model,
        api_key=api_key,
    )
    # Strip accidental markdown fences
    raw = re.sub(r"^```[a-z]*\n?", "", raw.strip())
    raw = re.sub(r"\n?```$", "", raw.strip())
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"  [warn] JSON parse failed: {exc}\n  Raw: {raw[:300]}", file=sys.stderr)
        return {}
    return result


def _needs_translation(msg: Message, force: bool) -> bool:
    if not msg.id or msg.id == "":
        return False
    if msg.fuzzy:
        return False
    if isinstance(msg.id, tuple):
        # Plural form — skip for now (stimmo has none currently)
        return False
    return force or not msg.string


def main() -> None:
    parser = argparse.ArgumentParser(description="Translate stimmo .po catalog via OpenRouter")
    parser.add_argument("--locale", required=True, help="Target locale, e.g. it_IT")
    parser.add_argument("--key", default=os.getenv("OPENROUTER_API_KEY"), help="OpenRouter API key")
    parser.add_argument(
        "--model",
        default="deepseek/deepseek-v4-flash",
        help="OpenRouter model ID (default: deepseek/deepseek-v4-flash)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=30, help="Strings per API call (default: 30)"
    )
    parser.add_argument(
        "--force", action="store_true", help="Re-translate already-translated entries"
    )
    parser.add_argument("--dry-run", action="store_true", help="Print translations without writing")
    parser.add_argument(
        "--delay", type=float, default=0.5, help="Seconds between API calls (default: 0.5)"
    )
    args = parser.parse_args()

    if not args.key:
        sys.exit("Error: provide --key or set OPENROUTER_API_KEY")

    po_path = _LOCALE_DIR / args.locale / "LC_MESSAGES" / "messages.po"
    if not po_path.exists():
        sys.exit(f"Error: {po_path} not found. Run pybabel init first.")

    # Derive a human-readable language name for the prompt
    lang_names = {
        "it_IT": "Italian",
        "en_US": "English",
        "de_DE": "German",
        "fr_FR": "French",
        "es_ES": "Spanish",
    }
    target_lang = lang_names.get(args.locale, args.locale)

    with po_path.open("rb") as fh:
        catalog = pofile.read_po(fh, locale=args.locale)

    # Collect messages that need translation
    pending: list[Message] = [m for m in catalog if _needs_translation(m, args.force)]

    if not pending:
        print(f"Nothing to translate in {po_path} (use --force to re-translate).")
        return

    print(f"Translating {len(pending)} strings into {target_lang} using {args.model}…")

    # Process in batches
    translated_count = 0
    for i in range(0, len(pending), args.batch_size):
        batch_msgs = pending[i : i + args.batch_size]
        batch = {str(m.id): "" for m in batch_msgs}
        batch_num = i // args.batch_size + 1
        total_batches = (len(pending) + args.batch_size - 1) // args.batch_size
        print(
            f"  batch {batch_num}/{total_batches} ({len(batch_msgs)} strings)…", end=" ", flush=True
        )

        try:
            translations = _translate_batch(
                batch, target_lang=target_lang, model=args.model, api_key=args.key
            )
        except httpx.HTTPStatusError as exc:
            print(f"HTTP {exc.response.status_code}: {exc.response.text[:200]}", file=sys.stderr)
            sys.exit(1)
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)

        # Apply translations back to catalog messages
        for msg in batch_msgs:
            key = str(msg.id)
            if translations.get(key):
                msg.string = translations[key]
                translated_count += 1
            else:
                print(f"\n  [warn] no translation returned for: {key[:60]!r}", file=sys.stderr)

        print(f"ok ({len(translations)} returned)")

        if i + args.batch_size < len(pending):
            time.sleep(args.delay)

    print(f"\n{translated_count}/{len(pending)} strings translated.")

    if args.dry_run:
        print("\n--- dry run: printing translations ---")
        for msg in pending:
            if msg.string:
                print(f"  {str(msg.id)[:60]!r}\n  → {msg.string[:60]!r}\n")
        return

    # Write the updated catalog
    with po_path.open("wb") as fh:
        pofile.write_po(fh, catalog, include_previous=False)
    print(f"Written: {po_path}")

    # Compile to .mo
    mo_path = po_path.with_suffix(".mo")
    from babel.messages.mofile import write_mo

    with mo_path.open("wb") as fh:
        write_mo(fh, catalog)
    print(f"Compiled: {mo_path}")


if __name__ == "__main__":
    main()
