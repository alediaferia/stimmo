FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ ./src/
RUN uv sync --frozen --no-dev \
    && uv run --no-dev pybabel compile -d src/stimmo/locale

FROM python:3.13-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgeos-c1v5 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

# web/app.py's _release_date() reads this to date the static pages' sitemap <lastmod>,
# resolving it as /app/CHANGELOG.md. It is deliberately copied here rather than added to
# the builder's dependency-manifest COPY above: this file changes on every release, and
# putting it there would invalidate the cached `uv sync` layer on each one. _release_date
# returns None when the file is absent and the sitemap then omits <lastmod> for those
# URLs rather than inventing a date — which is exactly how its absence was noticed, in
# v0.19.0, where 8 of 112 URLs shipped without one.
COPY CHANGELOG.md ./

RUN useradd -r -s /bin/false stimmo

# Editorial neighborhood blurbs (src/stimmo/data/neighborhoods.py) are baked into
# the image here, at build time, rather than pushed to the host at deploy time —
# an image tag is then a complete, reproducible deployment unit that rolls back
# atomically with the code. neighborhoods.json itself is never committed (it's
# licensed content — see .gitignore); the release workflow drops a real,
# jq-validated copy into var/content/ before invoking this build. The bracket
# glob is BuildKit's optional-source idiom: a COPY with an unmatched glob source
# only no-ops if at least one *other* source in the same instruction is
# guaranteed to exist, hence pairing it with the always-tracked .gitkeep — a
# lone optional glob source errors on a plain `docker build .` where var/content/
# has nothing in it yet (verified). Runs before the USER switch below (root
# still owns /app here) with an explicit --chown so the non-root `stimmo` user
# can read the result without a separate chmod step.
COPY --chown=stimmo:stimmo var/content/.gitkeep var/content/neighborhoods.jso[n] /app/var/content/

USER stimmo

ENV PATH="/app/.venv/bin:$PATH"
ENV STIMMO_HOST=0.0.0.0

CMD ["stimmo-web"]
