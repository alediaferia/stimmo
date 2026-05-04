FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ ./src/
RUN uv sync --frozen --no-dev

FROM python:3.13-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgeos-c1v5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY src/ /app/src/

RUN useradd -r -s /bin/false stimmo
USER stimmo

ENV PATH="/app/.venv/bin:$PATH"
ENV STIMMO_HOST=0.0.0.0

CMD ["stimmo-web"]
