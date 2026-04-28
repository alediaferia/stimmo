# Contributing

## Setup

```sh
uv sync
uv run pytest        # run tests
uv run stimmo-web    # start the app on http://127.0.0.1:8000
```

## Key invariants

- **All coefficients live in `src/stimmo/valuation/adjustments.py`.** Do not scatter multipliers into the engine, models, or renderers.
- **The OMI band is the spine.** There is no comparable-listings logic — the absence is by design.

## Before pushing

```sh
uv run ruff check --fix
uv run ruff format
uv run pytest
```

## Reporting issues

Open a GitHub issue for data-quality bugs (wrong zone match, stale OMI data, coefficient disagreements, etc.). Please include the address, inputs, and the output you received.
