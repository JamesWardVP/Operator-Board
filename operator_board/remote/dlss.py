"""NVIDIA DLSS controls, defined data-first in dlss.json so plugin API changes need no code changes."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from operator_board.core.config import app_home
from operator_board.remote.rc_client import RemoteControlError
from operator_board.remote.targets import TargetResolver

BUILTIN_PROFILE = os.path.join(os.path.dirname(__file__), "dlss.json")


def load_dlss_profile() -> Dict[str, Any]:
    for path in (os.path.join(app_home(), "dlss.json"), BUILTIN_PROFILE):
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
    return {"controls": [], "detect": []}


def cvar_value(control: Dict[str, Any], value: Any) -> str:
    if control.get("kind") == "bool":
        return "1" if value else "0"
    return str(value)


class DLSSService:
    def __init__(self, targets: TargetResolver) -> None:
        self.targets = targets
        self.profile = load_dlss_profile()
        self.values: Dict[str, Any] = {c["id"]: c.get("default") for c in self.controls}

    @property
    def controls(self) -> List[Dict[str, Any]]:
        return list(self.profile.get("controls", []))

    def detect(self) -> Dict[str, bool]:
        """Report which DLSS plugin libraries the primary instance exposes."""
        client = self.targets.primary()
        found = {}
        for path in self.profile.get("detect", []):
            try:
                client.describe(path)
                found[path] = True
            except RemoteControlError:
                found[path] = False
        return found

    def apply(self, control_id: str, value: Any, target: str = "all") -> List[str]:
        control = next((c for c in self.controls if c["id"] == control_id), None)
        if control is None:
            raise KeyError(f"Unknown DLSS control '{control_id}'")
        spec = control["apply"]
        errors = []
        clients = self.targets.resolve(target)
        if not clients:
            return ["No Unreal instances with Remote Control enabled"]
        for client in clients:
            try:
                if spec["type"] == "function":
                    client.call(spec["object"], spec["function"], {spec["param"]: value})
                else:
                    client.console_command(f"{spec['name']} {cvar_value(control, value)}")
            except RemoteControlError as exc:
                errors.append(f"{client.host}: {exc}")
        self.values[control_id] = value
        return errors
