"""Resolves which Unreal instances a remote operation should go to."""
from __future__ import annotations

from typing import List, Optional

from operator_board.core.config import ROLE_EDITOR, ROLE_NDISPLAY, AppConfig
from operator_board.remote.rc_client import RemoteControlClient

TARGET_PRIMARY = "primary"      # the main editor
TARGET_EDITORS = "editors"
TARGET_NDISPLAY = "ndisplay"    # all render nodes
TARGET_ALL = "all"
TARGET_CHOICES = (TARGET_PRIMARY, TARGET_EDITORS, TARGET_NDISPLAY, TARGET_ALL)


class TargetResolver:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._clients = {}

    def client_for(self, device_name: str) -> Optional[RemoteControlClient]:
        dev = self.config.device(device_name)
        if dev is None or not dev.rc_enabled:
            return None
        key = (dev.address, dev.rc_port)
        client = self._clients.get(key)
        if client is None:
            client = RemoteControlClient(dev.address, dev.rc_port, self.config.remote_control.timeout_s)
            self._clients[key] = client
        client.timeout = self.config.remote_control.timeout_s
        return client

    def primary_name(self) -> Optional[str]:
        name = self.config.remote_control.primary_target
        if name and self.config.device(name):
            return name
        editor = next((d for d in self.config.devices if d.role == ROLE_EDITOR and d.enabled), None)
        return editor.name if editor else None

    def primary(self) -> RemoteControlClient:
        name = self.primary_name()
        client = self.client_for(name) if name else None
        if client is None:
            raise LookupError("No editor with Remote Control enabled is configured")
        return client

    def resolve(self, target: str) -> List[RemoteControlClient]:
        if target == TARGET_PRIMARY:
            return [self.primary()]
        if self.config.device(target):
            c = self.client_for(target)
            return [c] if c else []
        roles = {TARGET_EDITORS: (ROLE_EDITOR,), TARGET_NDISPLAY: (ROLE_NDISPLAY,), TARGET_ALL: (ROLE_EDITOR, ROLE_NDISPLAY)}
        wanted = roles.get(target, ())
        out = []
        for d in self.config.devices:
            if d.enabled and d.role in wanted:
                c = self.client_for(d.name)
                if c:
                    out.append(c)
        return out

    def names(self) -> List[str]:
        return list(TARGET_CHOICES) + [d.name for d in self.config.devices if d.rc_enabled]
