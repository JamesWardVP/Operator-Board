"""Loads LED processor protocol profiles (built-in JSON plus user profiles)."""
from __future__ import annotations

import glob
import json
import logging
import os
from typing import Any, Dict, List, Optional

from operator_board.core.config import app_home

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


class ProfileRegistry:
    def __init__(self, extra_dirs: Optional[List[str]] = None) -> None:
        self.profiles: Dict[str, Dict[str, Any]] = {}
        for directory in [BUILTIN_DIR, user_profile_dir()] + list(extra_dirs or []):
            self.load_dir(directory)

    def load_dir(self, directory: str) -> None:
        for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    profile = json.load(fh)
                self.profiles[profile["id"]] = profile
            except (OSError, ValueError, KeyError) as exc:
                log.warning("Skipping LED profile %s: %s", path, exc)

    def get(self, profile_id: str) -> Optional[Dict[str, Any]]:
        return self.profiles.get(profile_id)

    def all(self) -> List[Dict[str, Any]]:
        return sorted(self.profiles.values(), key=lambda p: (p.get("manufacturer") == "Generic", p.get("name", "")))
