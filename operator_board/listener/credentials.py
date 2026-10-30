"""Saved SwitchboardListener credentials, shared with Switchboard (UE 5.4+).

Switchboard keeps one credential per listener host:

* username = SHA-256 fingerprint (hex) of the listener's self-signed certificate,
  remembered on first use and checked on every later connection;
* secret   = the listener password (Windows, where the store is encrypted at rest)
  or the JWT the listener hands back after a password login (other platforms).

On Windows the entries live in Windows Credential Manager under
``Switchboard_<host>``, exactly where Switchboard's ``CredentialStoreWindows``
puts them, so a password entered in either program works in both.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import dataclass
from typing import Dict, Optional

TARGET_PREFIX = "Switchboard_"


@dataclass
class Credential:
    fingerprint: str  # hex SHA-256 of the listener certificate ("" = not seen yet)
    secret: str       # password (encrypted stores) or JWT


class CredentialStore:
    """In-memory store; base class for the persistent ones."""

    # True when the secret is safe to keep as the password itself.
    encrypted_at_rest = False

    def __init__(self) -> None:
        self._mem: Dict[str, Credential] = {}
        self._lock = threading.Lock()

    def get(self, host: str) -> Optional[Credential]:
        with self._lock:
            cred = self._mem.get(host)
            return Credential(cred.fingerprint, cred.secret) if cred else None

    def set(self, host: str, cred: Optional[Credential]) -> None:
        with self._lock:
            if cred is None:
                self._mem.pop(host, None)
            else:
                self._mem[host] = Credential(cred.fingerprint, cred.secret)


class FileCredentialStore(CredentialStore):
    """JWTs in a private JSON file (~/.operator_board/listener_credentials.json)."""

    def __init__(self, path: str) -> None:
        super().__init__()
        self.path = path
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self._mem = {h: Credential(str(v.get("fingerprint", "")), str(v.get("secret", "")))
                         for h, v in data.items() if isinstance(v, dict)}
        except (OSError, ValueError, AttributeError):
            self._mem = {}

    def set(self, host: str, cred: Optional[Credential]) -> None:
        super().set(host, cred)
        with self._lock:
            data = {h: {"fingerprint": c.fingerprint, "secret": c.secret} for h, c in self._mem.items()}
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, self.path)


if sys.platform.startswith("win"):
    import ctypes
    from ctypes import wintypes

    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2
    ERROR_NOT_FOUND = 1168

    class _FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class _CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", _FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    _advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    _CredReadW = _advapi.CredReadW
    _CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           ctypes.POINTER(ctypes.POINTER(_CREDENTIALW))]
    _CredReadW.restype = wintypes.BOOL
    _CredWriteW = _advapi.CredWriteW
    _CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
    _CredWriteW.restype = wintypes.BOOL
    _CredDeleteW = _advapi.CredDeleteW
    _CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    _CredDeleteW.restype = wintypes.BOOL
    _CredFree = _advapi.CredFree
    _CredFree.argtypes = [ctypes.c_void_p]
    _CredFree.restype = None

    class WindowsCredentialStore(CredentialStore):
        """Windows Credential Manager, same entries as Switchboard's CredentialStoreWindows."""

        encrypted_at_rest = True

        def get(self, host: str) -> Optional[Credential]:
            ptr = ctypes.POINTER(_CREDENTIALW)()
            if not _CredReadW(TARGET_PREFIX + host, CRED_TYPE_GENERIC, 0, ctypes.byref(ptr)):
                return None
            try:
                cred = ptr.contents
                blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
                return Credential(cred.UserName or "", blob.decode("utf-16-le", errors="replace"))
            finally:
                _CredFree(ptr)

        def set(self, host: str, cred: Optional[Credential]) -> None:
            target = TARGET_PREFIX + host
            if cred is None:
                _CredDeleteW(target, CRED_TYPE_GENERIC, 0)
                return
            blob = cred.secret.encode("utf-16-le")
            buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
            c = _CREDENTIALW()
            c.Type = CRED_TYPE_GENERIC
            c.Persist = CRED_PERSIST_LOCAL_MACHINE
            c.TargetName = target
            c.UserName = cred.fingerprint
            c.CredentialBlobSize = len(blob)
            c.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
            if not _CredWriteW(ctypes.byref(c), 0):
                raise OSError(ctypes.get_last_error(), f"Could not save the listener password for {host}")


_default: Optional[CredentialStore] = None


def default_store() -> CredentialStore:
    global _default
    if _default is None:
        if sys.platform.startswith("win"):
            _default = WindowsCredentialStore()
        else:
            from operator_board.core.config import app_home
            _default = FileCredentialStore(os.path.join(app_home(), "listener_credentials.json"))
    return _default


def set_default_store(store: Optional[CredentialStore]) -> None:
    """Tests use an in-memory store so nothing touches the real credential store."""
    global _default
    _default = store
