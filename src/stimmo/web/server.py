from __future__ import annotations

import os
import sys

import uvicorn


def _parse_port(raw: str) -> int:
    try:
        return int(raw)
    except ValueError:
        sys.stderr.write(f"STIMMO_PORT must be an integer, got {raw!r}\n")
        raise SystemExit(2) from None


def main() -> None:
    host = os.environ.get("STIMMO_HOST", "127.0.0.1")
    port = _parse_port(os.environ.get("STIMMO_PORT", "8000"))
    uvicorn.run("stimmo.web.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
