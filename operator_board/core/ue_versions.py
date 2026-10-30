"""Knowledge about the Unreal Engine versions Operator Board supports (5.2 - 5.8)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

SUPPORTED_VERSIONS = ("5.2", "5.3", "5.4", "5.5", "5.6", "5.7", "5.8")
DEFAULT_VERSION = SUPPORTED_VERSIONS[-1]


@dataclass(frozen=True)
class EngineVersionInfo:
    version: str
    editor_exe: str = "UnrealEditor"
    multiuser_server_exe: str = "UnrealMultiUserServer"
    multiuser_slate_server_exe: str = "UnrealMultiUserSlateServer"
    ndisplay_config_ext: str = ".ndisplay"
    remote_control_http_port: int = 30010
    remote_control_ws_port: int = 30020
    listener_port: int = 2980

    @property
    def tuple(self) -> Tuple[int, int]:
        major, minor = self.version.split(".")[:2]
        return int(major), int(minor)


# All supported versions share executable names and default ports. The table is
# kept per-version so differences can be recorded here as they appear.
VERSIONS: Dict[str, EngineVersionInfo] = {v: EngineVersionInfo(version=v) for v in SUPPORTED_VERSIONS}


def get_version_info(version: Optional[str]) -> EngineVersionInfo:
    if version in VERSIONS:
        return VERSIONS[version]
    return VERSIONS[DEFAULT_VERSION]


def is_supported(version: str) -> bool:
    return version in VERSIONS


def detect_engine_version(engine_dir: str) -> Optional[str]:
    """Read Engine/Build/Build.version from an engine install and return "major.minor"."""
    if not engine_dir:
        return None
    candidates = [
        os.path.join(engine_dir, "Engine", "Build", "Build.version"),
        os.path.join(engine_dir, "Build", "Build.version"),
    ]
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return f"{data['MajorVersion']}.{data['MinorVersion']}"
        except (OSError, ValueError, KeyError):
            continue
    return None


def platform_binaries_dir(platform: str) -> str:
    return {"Win64": "Win64", "Linux": "Linux", "Mac": "Mac"}.get(platform, "Win64")


def exe_suffix(platform: str) -> str:
    return ".exe" if platform == "Win64" else ""
