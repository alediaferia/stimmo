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
git push --follow-tags  # triggers the release workflow (build → deploy → GitHub Release)
```

`cz bump` infers the semver increment from commits since the last tag (`feat` → minor, `fix` → patch, `BREAKING CHANGE` → major).

## Before pushing

```sh
uv run ruff check --fix
uv run ruff format
uv run pytest
```

## Reporting issues

Open a GitHub issue for data-quality bugs (wrong zone match, stale OMI data, coefficient disagreements, etc.). Please include the address, inputs, and the output you received.
