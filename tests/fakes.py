"""Fake network peers for tests: a SwitchboardListener and an HTTP recorder."""
from __future__ import annotations

import base64
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeListener:
    """Minimal SwitchboardListener: acks 'start', reports program started/ended, answers sync status."""

    def __init__(self, stdout_lines=None):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.received = []
        self.stdout_lines = stdout_lines or []
        self.exit_code = 0
        self._conns = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _send(self, conn, msg):
        conn.sendall(json.dumps(msg).encode() + b"\x00")

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self._conns.append(conn)
            self._send(conn, {"command": "state", "version": "5.6.0", "runningProcesses": []})
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        buf = b""
        while True:
            try:
                data = conn.recv(65536)
            except OSError:
                return
            if not data:
                return
            buf += data
            while b"\x00" in buf:
                raw, buf = buf.split(b"\x00", 1)
                msg = json.loads(raw)
                if msg["command"] != "keepalive":
                    self.received.append(msg)
                self._handle(conn, msg)

    def _handle(self, conn, msg):
        cmd = msg["command"]
        if cmd == "start":
            self._send(conn, {"command": "start", "bAck": True, "id": msg["id"]})
            pid = "proc-" + msg["id"][:8]
            self._send(conn, {"command": "program started", "process_id": pid, "pid": 1234, "name": msg["name"],
                              "caller": msg["caller"]})
            for line in self.stdout_lines:
                self._send(conn, {"command": "programstdout", "process_id": pid,
                                  "partialStdoutB64": base64.b64encode((line + "\n").encode()).decode()})
            if msg["name"] == "operatorboard_sync":
                self._send(conn, {"command": "program ended", "process_id": pid, "returncode": self.exit_code,
                                  "output": base64.b64encode(b"done").decode()})
        elif cmd == "kill":
            self._send(conn, {"command": "program ended", "process_id": msg["uuid"], "returncode": 0})
        elif cmd == "get sync status":
            self._send(conn, {"command": "get sync status", "bAck": True,
                              "syncStatus": {"cpuUtilization": 42.0, "gpuUtilization": [80, 60],
                                             "gpuTemperature": [70], "gpuCoreClocksKhz": [1800000]}})

    def close(self):
        for c in self._conns:
            try:
                c.close()
            except OSError:
                pass
        self.sock.close()


class RecordingHTTPServer:
    """Records every request and replies with a canned JSON response (callable or dict)."""

    def __init__(self, responder=None):
        self.requests = []
        self.responder = responder or (lambda method, path, body: {})
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _do(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else None
                outer.requests.append((self.command, self.path, body))
                reply = outer.responder(self.command, self.path, body)
                data = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_PUT = do_PATCH = do_POST = _do

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
