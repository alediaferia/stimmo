# Contributing

## Setup

```sh
uv sync
uv run pytest        # run tests
uv run stimmo-web    # start the app on http://127.0.0.1:8000
```

Install pre-commit hooks (one-time):

```sh
uv tool install pre-commit
pre-commit install
pre-commit install --hook-type commit-msg
```

Two hooks are active: `gitleaks` (secret scanning, runs on `pre-commit`) and `commitizen` (message format, runs on `commit-msg`). CI runs the same checks on every push and PR.

## Key invariants

- **All coefficients live in `src/stimmo/valuation/adjustments.py`.** Do not scatter multipliers into the engine, models, or renderers.
- **The OMI band is the spine.** There is no comparable-listings logic — the absence is by design.
- **User-facing docs track the engine.** Any change under `src/stimmo/valuation/` that adds, removes, or renames a coefficient — or otherwise alters something quoted in the methodology copy — must be paired with the corresponding update to `src/stimmo/web/templates/about.html` (notably the pipeline step 05 description and the "no machine learning" principle block, which both enumerate the coefficient set). Pure magnitude tweaks that don't change the enumeration do not need a docs change.

## Commit messages

Commits must follow [Conventional Commits](https://www.conventionalcommits.org/):

```text
feat: add construction era to breakdown table
fix: correct floor multiplier for ground-floor units
docs: update OMI semester reference
```

Common types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`. The `commit-msg` hook rejects non-conforming messages.

## Cutting a release

```sh
uv run cz bump          # bumps version, updates CHANGELOG.md, creates git tag
git push origin main    # push the bump commit
git push origin vX.Y.Z  # push the tag — triggers the release workflow (build → deploy → GitHub Release)
```

`cz bump` infers the semver increment from commits since the last tag (`feat` → minor, `fix` → patch, `BREAKING CHANGE` → major).

**Push the tag by name — `git push --follow-tags` will not do it.** `cz bump` creates a
*lightweight* tag (`annotated_tag` is not enabled in `[tool.commitizen]`), and `--follow-tags`
pushes annotated tags only. It silently pushes the commit and skips the tag, so the release
workflow — which triggers on `v[0-9]+.[0-9]+.[0-9]+` tag pushes — never fires, and the failure
looks like nothing happening at all. This bit v0.19.0.

Verify the tag actually landed before assuming a release is running:

```sh
git ls-remote --tags origin | grep vX.Y.Z
```

## Before pushing

```sh
uv run ruff check --fix
uv run ruff format
uv run pytest
```

## Reporting issues

Open a GitHub issue for data-quality bugs (wrong zone match, stale OMI data, coefficient disagreements, etc.). Please include the address, inputs, and the output you received.
