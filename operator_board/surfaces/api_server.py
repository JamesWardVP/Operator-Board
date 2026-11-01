"""Local HTTP API so Bitfocus Companion (or any HTTP-capable surface) can trigger actions.

  GET  /api/actions                  list actions
  GET  /api/state                    wall / takes / multi-user / device state
  GET|POST /api/action/<id>?k=v      invoke an action (POST may carry a JSON body of params)
  GET  /panel                        the control surface as a web page (phone / tablet)
  GET  /api/surface                  the surface's current page (labels, colours, active keys)
  GET|POST /api/surface/press/<n>    press key n;  /api/surface/page/next|prev|<n>  change page
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional

from operator_board.core.actions import ActionRegistry
from operator_board.surfaces.web_panel import PANEL_HTML, surface_state

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
                 bind: str = "127.0.0.1", port: int = 8765, token: str = "", surface=None) -> None:
        self.actions = actions
        self.surface = surface  # SurfaceManager (or one SurfaceController) for the web panel (optional)
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

            def _send_html(self, html: str) -> None:
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
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
                if parsed.path.rstrip("/") in ("/panel", ""):  # the page itself holds no secrets
                    return self._send_html(PANEL_HTML)
                if not self._authorised(query):
                    return self._send(401, {"ok": False, "message": "invalid token"})
                query.pop("token", None)
                parts = [p for p in parsed.path.split("/") if p]
                if parts == ["api", "actions"]:
                    return self._send(200, [{"id": a.id, "label": a.label, "category": a.category,
                                             "params": [p.name for p in a.params]} for a in server_ref.actions.all()])
                if parts == ["api", "state"]:
                    return self._send(200, server_ref.state_provider())
                surface = server_ref.surface_for(query.pop("surface", None))
                if parts == ["api", "surfaces"]:
                    manager = server_ref.surface
                    return self._send(200, manager.state() if hasattr(manager, "state") else [])
                if parts[:2] == ["api", "surface"] and surface is None:
                    return self._send(404, {"ok": False, "message": "no control surface"})
                if parts == ["api", "surface"]:
                    return self._send(200, surface_state(surface))
                if len(parts) == 4 and parts[:3] == ["api", "surface", "press"] and parts[3].isdigit():
                    result = surface.press(int(parts[3]))
                    return self._send(200 if result.ok else 400, {"ok": result.ok, "message": result.message})
                if len(parts) == 4 and parts[:3] == ["api", "surface", "page"]:
                    n = max(1, len(surface.layout.pages))
                    target = parts[3]
                    if target == "next":
                        surface.page = (surface.page + 1) % n
                    elif target == "prev":
                        surface.page = (surface.page - 1) % n
                    elif target.isdigit():
                        surface.page = int(target) % n
                    surface.bus.publish("surface.changed")
                    page = surface.current_page
                    return self._send(200, {"ok": True, "message": f"Page: {page.name if page else ''}"})
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

    def surface_for(self, ref=None):
        """The surface a web panel request is for: ``?surface=<id|name|number>``, else the first
        web panel surface (else the first Custom surface, else the first surface)."""
        if self.surface is None or not hasattr(self.surface, "controllers"):
            return self.surface
        if ref:
            return self.surface.find(ref)
        return self.surface.first_of(("web", "custom"))

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
