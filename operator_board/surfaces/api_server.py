"""Local HTTP API so Bitfocus Companion (or any HTTP-capable surface) can trigger actions.

  GET  /api/actions                  list actions
  GET  /api/state                    wall / takes / multi-user / device state
  GET|POST /api/action/<id>?k=v      invoke an action (POST may carry a JSON body of params)
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional

from operator_board.core.actions import ActionRegistry

log = logging.getLogger(__name__)


def _coerce(value: str) -> Any:
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    return value


class ApiServer:
    def __init__(self, actions: ActionRegistry, state_provider: Callable[[], Dict[str, Any]],
                 bind: str = "127.0.0.1", port: int = 8765, token: str = "") -> None:
        self.actions = actions
        self.state_provider = state_provider
        self.bind = bind
        self.port = port
        self.token = token
        self._server: Optional[ThreadingHTTPServer] = None

    def start(self) -> None:
        server_ref = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # route to logging, not stderr
                log.debug("api: " + fmt, *args)

            def _send(self, code: int, payload: Any) -> None:
                body = json.dumps(payload, default=str).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorised(self, query: Dict[str, Any]) -> bool:
                if not server_ref.token:
                    return True
                return self.headers.get("X-Operator-Token") == server_ref.token or query.get("token") == server_ref.token

            def _handle(self, body: Optional[Dict[str, Any]] = None) -> None:
                parsed = urllib.parse.urlparse(self.path)
                query = {k: v[-1] for k, v in urllib.parse.parse_qs(parsed.query).items()}
                if not self._authorised(query):
                    return self._send(401, {"ok": False, "message": "invalid token"})
                query.pop("token", None)
                parts = [p for p in parsed.path.split("/") if p]
                if parts == ["api", "actions"]:
                    return self._send(200, [{"id": a.id, "label": a.label, "category": a.category,
                                             "params": [p.name for p in a.params]} for a in server_ref.actions.all()])
                if parts == ["api", "state"]:
                    return self._send(200, server_ref.state_provider())
                if len(parts) == 3 and parts[:2] == ["api", "action"]:
                    params = {k: _coerce(v) for k, v in query.items()}
                    params.update(body or {})
                    result = server_ref.actions.invoke(parts[2], **params)
                    return self._send(200 if result.ok else 400, {"ok": result.ok, "message": result.message})
                return self._send(404, {"ok": False, "message": "not found"})

            def do_GET(self):
                self._handle()

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw) if raw else {}
                except ValueError:
                    return self._send(400, {"ok": False, "message": "invalid JSON body"})
                self._handle(body if isinstance(body, dict) else {})

        self._server = ThreadingHTTPServer((self.bind, self.port), Handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True, name="api-server").start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
