"""Import devices from an existing Switchboard config file.

Switchboard stores its configs as JSON (Engine/Plugins/VirtualProduction/Switchboard/
Source/Switchboard/configs/*.json, or the user settings folder in newer engines).
The layout has stayed broadly the same from 5.2 onwards: top-level project
settings plus a "devices" mapping of device type -> {device name: {address, kwargs}}.
This importer is deliberately tolerant of missing keys.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from operator_board.core.config import ROLE_EDITOR, ROLE_NDISPLAY, DeviceConfig, ProjectConfig

_ROLE_BY_TYPE = {"unreal": ROLE_EDITOR, "ndisplay": ROLE_NDISPLAY}


def _first(d: Dict[str, Any], *keys: str, default: Any = "") -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def parse_switchboard_config(data: Dict[str, Any]) -> Tuple[ProjectConfig, List[DeviceConfig]]:
    project = ProjectConfig(
        name=str(_first(data, "project_name", default="Imported Project")),
        uproject_path=str(_first(data, "uproject")),
        engine_dir=str(_first(data, "engine_dir")),
    )
    devices: List[DeviceConfig] = []
    for dev_type, entries in (data.get("devices") or {}).items():
        role = _ROLE_BY_TYPE.get(str(dev_type).lower())
        if role is None or not isinstance(entries, dict):
            continue
        for name, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            kwargs = entry.get("kwargs") or {}
            dev = DeviceConfig(name=str(name), address=str(entry.get("address", "127.0.0.1")), role=role)
            if role == ROLE_NDISPLAY:
                dev.ndisplay_node = str(_first(kwargs, "node_name", "ndisplay_node", default=name))
                cfg = _first(kwargs, "ndisplay_cfg_file", "config_file", default="")
                if cfg and not project.ndisplay_config:
                    project.ndisplay_config = str(cfg)
            port = kwargs.get("port") or kwargs.get("listener_port")
            if isinstance(port, int):
                dev.listener_port = port
            devices.append(dev)
    return project, devices


def import_switchboard_config(path: str) -> Tuple[ProjectConfig, List[DeviceConfig]]:
    with open(path, "r", encoding="utf-8") as fh:
        return parse_switchboard_config(json.load(fh))
