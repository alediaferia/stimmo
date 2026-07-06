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
    metrics_port = os.environ.get("STIMMO_METRICS_PORT")
    if metrics_port:
        from stimmo.web.metrics import start_metrics_server

        start_metrics_server(int(metrics_port))
    # proxy_headers + forwarded_allow_ips="*" let uvicorn trust the X-Forwarded-Proto/-For
    # headers set by the cloudflared sidecar, so request.url/base_url render https:// instead
    # of http://. Safe here: the VPS exposes no public ports, so only cloudflared can connect
    # to uvicorn (see deployment topology in docs/architecture.md).
    uvicorn.run(
        "stimmo.web.app:application",
        host=host,
        port=port,
        reload=False,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
