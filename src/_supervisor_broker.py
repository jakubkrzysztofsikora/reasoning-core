"""Broker /health HTTP listener for sidecar_supervisor (P5 round-2).

Aggregates Mamba + Qwen child statuses on RC_BROKER_PORT (default 8764).
Read-only; no mutation endpoints.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List


def broker_health_snapshot(children: List[Any]) -> Dict[str, Any]:
    rows = []
    now = time.time()
    for c in children:
        with c.lock:
            rows.append({
                "name": c.name,
                "alive": (c.proc is not None and c.proc.poll() is None),
                "last_ok_age_s": (now - c.last_ok) if c.last_ok else None,
                "failures": c.failures,
                "circuit_open_for_s": max(0.0, c.open_until - now),
            })
    return {"supervisor": "ok", "children": rows}


class _BrokerHealthHandler(BaseHTTPRequestHandler):
    children: List[Any] = []

    def do_GET(self) -> None:  # noqa: N802
        if urllib.parse.urlparse(self.path).path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(broker_health_snapshot(self.children)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args, **_kwargs) -> None:
        return


def start_broker_http(children: List[Any], port: int) -> ThreadingHTTPServer:
    _BrokerHealthHandler.children = children
    server = ThreadingHTTPServer(("127.0.0.1", port), _BrokerHealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    sys.stderr.write(f"[supervisor] broker /health on http://127.0.0.1:{port}/health\n")
    return server
