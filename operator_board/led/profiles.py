"""Loads LED processor protocol profiles (built-in JSON plus user profiles).

Built-in profiles are never changed. Editing a built-in profile's commands saves a custom copy
("<name> (custom)", id "<id>_custom") in the user profile folder; editing a custom profile again
saves it in place.
"""
from __future__ import annotations

import copy
import glob
import json
import logging
import os
from typing import Any, Dict, List, Optional

from operator_board.core.config import app_home, atomic_write

log = logging.getLogger(__name__)

BUILTIN_DIR = os.path.join(os.path.dirname(__file__), "profiles")

# How sure we are that a command matches the vendor protocol.
CONFIDENCE_LABELS = {
    "source": "Matches published module source",
    "partial": "Endpoint known, value format assumed - verify",
    "assumed": "Assumed - verify against vendor docs",
    "user": "User defined",
}


def user_profile_dir() -> str:
    return os.path.join(app_home(), "led_profiles")


def _strip(value: Any) -> Any:
    """A command without its confidence rating, to see whether it was edited."""
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k != "confidence"}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def _mark_edited(commands: Dict[str, Any], before: Dict[str, Any]) -> None:
    """Commands that differ from ``before`` are the operator's own: rated "user"."""
    for name, cmd in commands.items():
        if isinstance(cmd, dict) and cmd and "ref" not in cmd and _strip(cmd) != _strip(before.get(name)):
            cmd["confidence"] = "user"


class ProfileRegistry:
    def __init__(self, extra_dirs: Optional[List[str]] = None, user_dir: Optional[str] = None) -> None:
        self.profiles: Dict[str, Dict[str, Any]] = {}
        self.builtin_ids = set()
        self.paths: Dict[str, str] = {}
        self.user_dir = user_dir or user_profile_dir()
        self.load_dir(BUILTIN_DIR, builtin=True)
        for directory in [self.user_dir] + list(extra_dirs or []):
            self.load_dir(directory)

    def load_dir(self, directory: str, builtin: bool = False) -> None:
        for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    profile = json.load(fh)
                self.profiles[profile["id"]] = profile
                self.paths[profile["id"]] = path
                if builtin:
                    self.builtin_ids.add(profile["id"])
                else:
                    self.builtin_ids.discard(profile["id"])  # a user file of the same id replaces it
            except (OSError, ValueError, KeyError) as exc:
                log.warning("Skipping LED profile %s: %s", path, exc)

    def is_builtin(self, profile_id: str) -> bool:
        return profile_id in self.builtin_ids

    def save_custom(self, profile: Dict[str, Any], base_id: str) -> Dict[str, Any]:
        """Save edited commands. For a built-in profile this makes (or updates) its custom copy, so
        the original is never changed; a custom profile is saved in place. Returns the saved profile."""
        profile = copy.deepcopy(profile)
        before = self.profiles.get(base_id, {})
        _mark_edited(profile.get("commands", {}), before.get("commands", {}))
        old_patterns = {p.get("id"): p for p in before.get("test_patterns", [])}
        for pattern in profile.get("test_patterns", []):
            old = old_patterns.get(pattern.get("id"), {}).get("commands", [])
            if _strip(pattern.get("commands")) != _strip(old):
                for cmd in pattern.get("commands", []):
                    if isinstance(cmd, dict):
                        cmd["confidence"] = "user"
        if self.is_builtin(base_id):
            base = self.profiles[base_id]
            profile["id"] = self._custom_id(base_id, profile.get("id"))
            profile["based_on"] = base_id
            if not profile.get("name") or profile.get("name") == base.get("name"):
                profile["name"] = f"{base.get('name', base_id)} (custom)"
        else:
            profile["id"] = base_id
        for cmd in profile.get("commands", {}).values():
            if isinstance(cmd, dict) and cmd and "ref" not in cmd:
                cmd.setdefault("confidence", "user")
        path = os.path.join(self.user_dir, f"{profile['id']}.json")
        atomic_write(path, json.dumps(profile, indent=2))
        self.profiles[profile["id"]] = profile
        self.paths[profile["id"]] = path
        return profile

    def _custom_id(self, base_id: str, wanted: Optional[str]) -> str:
        if wanted and wanted != base_id and not self.is_builtin(wanted):
            return wanted
        candidate, n = f"{base_id}_custom", 2
        while candidate in self.profiles:
            candidate, n = f"{base_id}_custom_{n}", n + 1
        return candidate

    def delete_custom(self, profile_id: str) -> Optional[str]:
        """Delete a custom profile file; returns the profile it was based on (or None)."""
        if self.is_builtin(profile_id) or profile_id not in self.profiles:
            return None
        based_on = self.profiles[profile_id].get("based_on")
        path = self.paths.pop(profile_id, "")
        if path and os.path.dirname(os.path.abspath(path)) == os.path.abspath(self.user_dir) and os.path.exists(path):
            os.remove(path)
        del self.profiles[profile_id]
        return based_on

    def get(self, profile_id: str) -> Optional[Dict[str, Any]]:
        return self.profiles.get(profile_id)

    def all(self) -> List[Dict[str, Any]]:
        return sorted(self.profiles.values(), key=lambda p: (p.get("manufacturer") == "Generic", p.get("name", "")))
