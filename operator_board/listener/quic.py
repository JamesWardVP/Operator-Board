"""QUIC connection to a UE 5.4+ SwitchboardListener.

Mirrors Switchboard's listener_client.py (5.4 - 5.8):

* QUIC over UDP (default port 2980), ALPN ``ue-switchboard``.
* The listener uses a self-signed certificate. It is trusted on first use and its
  SHA-256 fingerprint is remembered with the saved credential; a different
  fingerprint later is reported (FingerprintChanged) instead of connecting.
* One bidirectional stream carries the usual null-terminated JSON messages.
  The first one is ``authenticate`` with the listener password (or a saved JWT);
  the reply carries ``bAuthenticated`` and, after a password login, a ``jwt``.
* Keepalives every second; the connection closes after 5 s of silence.

The asyncio loop runs on its own thread so callers keep the same blocking
connect() / thread-safe send() shape as the TCP client.
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import threading
from typing import Any, Callable, Dict, List, Optional

from operator_board.listener import protocol
from operator_board.listener.credentials import Credential, CredentialStore

log = logging.getLogger(__name__)

ALPN = "ue-switchboard"
IDLE_TIMEOUT_S = 5.0
KEEPALIVE_S = 1.0
AUTH_TIMEOUT_S = 5.0
MAX_MESSAGE_BYTES = 10 * 2 ** 20

try:
    from aioquic.asyncio.client import connect as _quic_connect
    from aioquic.asyncio.protocol import QuicConnectionProtocol
    from aioquic.quic.configuration import QuicConfiguration
    from aioquic.quic.events import HandshakeCompleted
    from cryptography.hazmat.primitives import hashes
    from cryptography.x509.oid import NameOID
    AVAILABLE = True
except ImportError:  # pragma: no cover - aioquic is a declared dependency
    AVAILABLE = False
    QuicConnectionProtocol = object  # type: ignore


# Characters that come along when a password is copied and pasted (line breaks from the
# listener window, zero-width characters) but can never be part of a listener password.
_PASTE_JUNK = dict.fromkeys(map(ord, "\r\n\t\u200b\u200c\u200d\u2060\ufeff"), None)


def password_candidates(password: str) -> List[str]:
    """Passwords to try, most likely first: without paste junk, then also without end spaces."""
    cleaned = password.translate(_PASTE_JUNK)
    out: List[str] = []
    for candidate in (cleaned, cleaned.strip()):
        if candidate and candidate not in out:
            out.append(candidate)
    return out or [password]


class ListenerAuthError(ConnectionError):
    """The listener needs (other) credentials. Carries what a UI needs to ask."""

    def __init__(self, message: str, host: str, fingerprint: str = "", subject: str = "") -> None:
        super().__init__(message)
        self.host = host
        self.fingerprint = fingerprint
        self.subject = subject

    @property
    def fingerprint_display(self) -> str:
        """First 128 bits, colon separated, as Switchboard shows it."""
        raw = bytes.fromhex(self.fingerprint) if self.fingerprint else b""
        return ":".join(f"{b:02X}" for b in raw[:16])


class PasswordRequired(ListenerAuthError):
    pass


class PasswordRejected(ListenerAuthError):
    pass


class FingerprintChanged(ListenerAuthError):
    pass


class _Protocol(QuicConnectionProtocol):  # type: ignore[misc]
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fingerprint = ""
        self.subject = ""

    def quic_event_received(self, event: Any) -> None:
        super().quic_event_received(event)
        if isinstance(event, HandshakeCompleted):
            cert = getattr(self._quic.tls, "_peer_certificate", None)
            if cert is not None:
                self.fingerprint = cert.fingerprint(hashes.SHA256()).hex()
                self.subject = "+".join(str(a.value) for a in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME))


class QuicListenerConnection:
    def __init__(self, address: str, port: int, store: CredentialStore,
                 on_message: Callable[[Dict[str, Any]], None],
                 on_closed: Callable[[str], None],
                 password: Optional[str] = None, trust_new_fingerprint: bool = False,
                 idle_timeout: float = IDLE_TIMEOUT_S) -> None:
        if not AVAILABLE:
            raise ConnectionError("Connecting to UE 5.4+ listeners needs the 'aioquic' package")
        self.address = address
        self.port = port
        self.store = store
        self.on_message = on_message
        self.on_closed = on_closed
        self.password = password
        self.trust_new_fingerprint = trust_new_fingerprint
        self.idle_timeout = idle_timeout
        self.fingerprint = ""
        self.subject = ""
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._closing = False
        self._ready = threading.Event()
        self._error: Optional[BaseException] = None
        self._thread: Optional[threading.Thread] = None
        self._auth_id = ""
        self._auth_reply: Optional[asyncio.Future] = None

    # ---- public (any thread) -----------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._ready.is_set() and self._error is None and not self._closing and self._writer is not None

    def open(self) -> None:
        """Connect and authenticate; raises ConnectionError / ListenerAuthError on failure."""
        self._thread = threading.Thread(target=self._run, name=f"listener-quic-{self.address}", daemon=True)
        self._thread.start()
        if not self._ready.wait(self.idle_timeout + AUTH_TIMEOUT_S + 2.0):
            self.close()
            raise ConnectionError(f"Timed out connecting to {self.address}:{self.port} (QUIC)")
        if self._error is not None:
            self._thread.join(timeout=2.0)
            raise self._error

    def send(self, data: bytes) -> None:
        loop, writer = self._loop, self._writer
        if not (loop and writer) or self._closing or loop.is_closed():
            raise ConnectionError(f"Not connected to listener at {self.address}:{self.port}")
        loop.call_soon_threadsafe(writer.write, data)

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        loop, writer = self._loop, self._writer
        if loop and writer and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(writer.write, protocol.disconnect()[1])
            except RuntimeError:
                pass
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=3.0)

    # ---- loop thread -------------------------------------------------------------------
    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except BaseException as exc:  # noqa: BLE001 - reported to the caller of open()
            if not self._ready.is_set():
                self._fail(exc if isinstance(exc, ConnectionError) else ConnectionError(
                    f"QUIC connection to {self.address}:{self.port} failed: {exc or type(exc).__name__}"))
            log.debug("QUIC loop for %s ended: %r", self.address, exc)
        self._loop = None
        self._writer = None

    def _fail(self, exc: BaseException) -> None:
        self._error = exc
        self._ready.set()

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        config = QuicConfiguration(alpn_protocols=[ALPN], is_client=True)
        config.idle_timeout = self.idle_timeout
        # Listeners use self-signed certificates: trust on first use + fingerprint check.
        config.verify_mode = ssl.CERT_NONE
        async with _quic_connect(self.address, self.port, configuration=config,
                                 create_protocol=_Protocol) as proto:
            self.fingerprint, self.subject = proto.fingerprint, proto.subject
            reader, writer = await proto.create_stream()
            reader._limit = MAX_MESSAGE_BYTES  # type: ignore[attr-defined]
            self._writer = writer
            recv = asyncio.ensure_future(self._recv_loop(reader))
            keepalive = asyncio.ensure_future(self._keepalive_loop(writer))
            try:
                await self._authenticate(writer)
            except ConnectionError as exc:
                self._fail(exc)
            else:
                self._ready.set()
                done, _ = await asyncio.wait([recv, keepalive], return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in (recv, keepalive):
                    task.cancel()
            if self._closing:
                await asyncio.sleep(0.05)  # let the disconnect message go out
            proto.close()
        if self._ready.is_set() and self._error is None and not self._closing:
            self._closing = True
            self.on_closed("listener closed the connection")

    async def _recv_loop(self, reader: asyncio.StreamReader) -> None:
        while not self._closing:
            try:
                raw = await reader.readuntil(protocol.DELIMITER)
            except (asyncio.IncompleteReadError, ConnectionError):
                return
            try:
                msg = json.loads(raw[:-1].decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if self._is_auth_reply(msg):
                self._auth_reply.set_result(msg)
                continue
            if msg.get("command") == "command accepted" and protocol.same_id(msg.get("id"), self._auth_id):
                continue  # the listener acknowledges "authenticate" before its real reply
            try:
                self.on_message(msg)
            except Exception:  # noqa: BLE001
                log.exception("Listener message handler failed")

    def _is_auth_reply(self, msg: Dict[str, Any]) -> bool:
        """The login result: carries bAuthenticated, or refuses the command outright.

        The listener first sends {"command accepted": true} with the same id; that is only an
        acknowledgement, not the result (Switchboard routes acknowledgements separately).
        """
        if self._auth_reply is None or self._auth_reply.done() or not protocol.same_id(msg.get("id"), self._auth_id):
            return False
        if "bAuthenticated" in msg:
            return True
        return msg.get("command") == "command accepted" and msg.get("command accepted") is False

    async def _keepalive_loop(self, writer: asyncio.StreamWriter) -> None:
        while not self._closing:
            await asyncio.sleep(KEEPALIVE_S)
            writer.write(protocol.keepalive()[1])

    async def _authenticate(self, writer: asyncio.StreamWriter) -> None:
        host, fp = self.address, self.fingerprint
        details = dict(host=host, fingerprint=fp, subject=self.subject)
        saved = self.store.get(host)
        unsaved = self.password is not None or saved is None
        if self.password is not None:
            cred = Credential(saved.fingerprint if saved else "", self.password)
        elif saved is not None:
            cred = saved
        else:
            raise PasswordRequired(f"The listener at {host} needs its password", **details)

        if not cred.fingerprint:
            cred.fingerprint = fp
        elif fp and cred.fingerprint != fp:
            if not self.trust_new_fingerprint:
                raise FingerprintChanged(
                    f"The listener at {host} presented a different security key than last time", **details)
            cred.fingerprint = fp
            unsaved = True

        use_jwt = not unsaved and not self.store.encrypted_at_rest
        self._auth_id, payload = (protocol.authenticate(jwt=cred.secret) if use_jwt
                                  else protocol.authenticate(password=cred.secret))
        self._auth_reply = asyncio.get_running_loop().create_future()
        writer.write(payload)
        try:
            reply = await asyncio.wait_for(self._auth_reply, AUTH_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise ConnectionError(f"The listener at {host} did not answer the login") from None

        if "bAuthenticated" not in reply:  # the listener refused the command itself
            raise ConnectionError(f"The listener at {host} refused the login: {reply.get('error', 'no reason given')}")
        ok = str(reply.get("bAuthenticated", "")).lower() == "true"
        if ok:
            if self.store.encrypted_at_rest:
                if unsaved:
                    self.store.set(host, cred)
            elif reply.get("jwt"):
                self.store.set(host, Credential(cred.fingerprint, str(reply["jwt"])))
            return
        if not unsaved:
            self.store.set(host, None)  # forget the saved credential that no longer works
        raise PasswordRejected(f"The listener at {host} rejected the password", **details)
