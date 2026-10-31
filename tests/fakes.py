"""Fake network peers for tests: a SwitchboardListener and an HTTP recorder."""
from __future__ import annotations

import base64
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Every command Switchboard 5.8's message_protocol.py sends. The real listener logs
# "Could not parse message" for anything else, so the fakes record such commands in .unknown.
KNOWN_COMMANDS = {
    "authenticate", "disconnect", "fixExeFlags", "free binary", "get sync status", "keep alive", "kill",
    "minimize windows", "receive file", "redeploy listener", "refresh mosaics", "send file",
    "set inactive timeout", "start live log stream", "start", "stop live log stream", "vcs init",
    "vcs report revision", "vcs sync",
}


class FakeListener:
    """Minimal SwitchboardListener: acks 'start', reports program started/ended, answers sync status."""

    def __init__(self, stdout_lines=None):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.received = []
        self.unknown = []
        self.files = {}
        self.processes = {}
        self.kills = []
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
                if msg["command"] not in KNOWN_COMMANDS:
                    self.unknown.append(msg["command"])
                if msg["command"] != "keep alive":
                    self.received.append(msg)
                self._handle(conn, msg)

    def _handle(self, conn, msg):
        cmd = msg["command"]
        if cmd == "send file":
            path = msg["destination"].replace("%TEMP%", "C:/Temp").replace("%RANDOM%", "4242")
            self.files[path] = base64.b64decode(msg["content"])
            self._send(conn, {"command": "send file", "send file complete": True, "destination": path})
        elif cmd == "start":
            # As the real listener: acknowledge, then announce the program to EVERY connection,
            # with its details nested under "process" and the start message id as program id.
            puuid = msg["id"].replace("-", "").upper()
            self._send(conn, {"command accepted": True, "command": "command accepted", "bAck": True, "id": puuid})
            proc = {"uuid": puuid, "pid": 1234, "name": msg["name"], "caller": msg["caller"]}
            self.processes[puuid] = proc
            self._broadcast({"command": "program started", "bAck": True, "process": proc})
            for line in self.stdout_lines:
                self._broadcast({"command": "programstdout", "process": proc,
                                 "partialStdoutB64": base64.b64encode((line + "\n").encode()).decode()})
            if msg["name"] == "operatorboard_sync":
                self._broadcast({"command": "program ended", "process": proc, "returncode": self.exit_code,
                                 "stdoutB64": base64.b64encode(b"done").decode(), "stderrB64": ""})
        elif cmd == "kill":
            self.kills.append(msg)
            proc = self.processes.pop(msg["uuid"].replace("-", "").upper(), None)
            if proc is None:
                self._send(conn, {"command": "program killed", "bAck": False, "puuid": msg["uuid"],
                                  "error": "No such program"})
            else:
                self._broadcast({"command": "program ended", "process": proc, "returncode": 0,
                                 "stdoutB64": "", "stderrB64": ""})
        elif cmd == "get sync status":
            self._send(conn, {"command": "get sync status", "bAck": True,
                              "syncStatus": {"cpuUtilization": 42.0, "gpuUtilization": [80, 60],
                                             "gpuTemperature": [70], "gpuCoreClocksKhz": [1800000]}})

    def _broadcast(self, msg):
        for conn in list(self._conns):
            try:
                self._send(conn, msg)
            except OSError:
                pass

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


class FakeQuicListener(FakeListener):
    """UE 5.4+ style listener: QUIC (ALPN ue-switchboard), self-signed certificate, password login.

    Answers 'authenticate' like the real listener ("bAuthenticated": "true"/"false" plus a
    JWT after a password login), then behaves like FakeListener.
    """

    def __init__(self, password="secret", stdout_lines=None):
        import asyncio
        import datetime
        import os
        import tempfile

        from aioquic.asyncio.server import serve
        from aioquic.quic.configuration import QuicConfiguration
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        self.password = password
        self.jwt = "jwt-token-123"
        self.received = []
        self.auth_attempts = []
        self.unknown = []
        self.files = {}
        self.processes = {}
        self.kills = []
        self._conns = []  # logged-in streams, for _broadcast
        self.stdout_lines = stdout_lines or []
        self.exit_code = 0

        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "FAKE-NODE")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=30)).sign(key, hashes.SHA256()))
        self.fingerprint = cert.fingerprint(hashes.SHA256()).hex()
        tmp = tempfile.mkdtemp(prefix="fakequic_")
        cert_path, key_path = os.path.join(tmp, "cert.pem"), os.path.join(tmp, "key.pem")
        with open(cert_path, "wb") as fh:
            fh.write(cert.public_bytes(serialization.Encoding.PEM))
        with open(key_path, "wb") as fh:
            fh.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))

        config = QuicConfiguration(alpn_protocols=["ue-switchboard"], is_client=False)
        config.idle_timeout = 5.0
        config.load_cert_chain(cert_path, key_path)

        self.loop = asyncio.new_event_loop()
        ready = threading.Event()
        holder = {}

        async def start():
            holder["server"] = await serve("127.0.0.1", 0, configuration=config, stream_handler=self._stream)
            ready.set()

        def run():
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(start())
            self.loop.run_forever()

        threading.Thread(target=run, daemon=True).start()
        ready.wait(5)
        self.server = holder["server"]
        self.port = self.server._transport.get_extra_info("sockname")[1]

    def _stream(self, reader, writer):
        import asyncio
        asyncio.ensure_future(self._serve_stream(reader, writer))

    async def _serve_stream(self, reader, writer):
        import asyncio
        authed = False
        while True:
            try:
                raw = await reader.readuntil(b"\x00")
            except (asyncio.IncompleteReadError, ConnectionError):
                return
            msg = json.loads(raw[:-1])
            if msg["command"] == "authenticate":
                self.auth_attempts.append(msg)
                authed = msg.get("password") == self.password or msg.get("jwt") == self.jwt
                # The real listener echoes ids as Unreal FGuid strings (uppercase hex, no dashes).
                fguid = msg["id"].replace("-", "").upper()
                # Like the real listener, acknowledge the command first, with the same id.
                self._send(writer, {"command accepted": True, "command": "command accepted", "bAck": True,
                                    "id": fguid})
                reply = {"command": "authenticate", "id": fguid, "bAuthenticated": "true" if authed else "false"}
                if authed and "password" in msg:
                    reply["jwt"] = self.jwt
                self._send(writer, reply)
                if authed:
                    self._conns.append(writer)
                    self._send(writer, {"command": "state", "version": "5.8.0", "runningProcesses": []})
                continue
            if not authed:
                continue
            if msg["command"] not in KNOWN_COMMANDS:
                self.unknown.append(msg["command"])
            if msg["command"] not in ("keep alive", "disconnect"):
                self.received.append(msg)
            self._handle(writer, msg)

    def _send(self, writer, msg):
        writer.write(json.dumps(msg).encode() + b"\x00")

    def close(self):
        self.loop.call_soon_threadsafe(self.server.close)
        self.loop.call_soon_threadsafe(self.loop.stop)
