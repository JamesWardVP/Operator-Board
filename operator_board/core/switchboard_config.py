"""Read, create and update Unreal Engine Switchboard config files.

Switchboard keeps one JSON file per config in
``<Engine>/Plugins/VirtualProduction/Switchboard/Source/Switchboard/configs`` (every
version from 5.2 to 5.8). From 5.6 the folder can be moved with the
``SWITCHBOARD_CONFIG_DIR`` environment variable. Switchboard lists configs with
``rglob('*.json')``, so nothing else Operator Board writes there may end in ``.json``.

Layout (checked against the 5.2, 5.6 and 5.8 sources)::

    {project_name, uproject, engine_dir, muserver_*, current_level, ...,
     "devices": {<type>: {"settings": {<type-wide settings>},
                          <device name>: {"address": ..., <per-device settings / overrides>}}}}

Operator Board only rewrites the keys it maps (see ``apply_switchboard`` and
``merge_into_switchboard``). Every other key, device type and per-device setting is
kept exactly as Switchboard wrote it, so a config stays fully usable in Switchboard.
"""
from __future__ import annotations

import glob
import json
import os
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from operator_board.core.config import (ROLE_EDITOR, ROLE_NDISPLAY, AppConfig, DeviceConfig, ProjectConfig,
                                        atomic_write)
from operator_board.core.ue_versions import detect_engine_version, is_supported

CONFIG_SUFFIX = ".json"
CONFIG_ENV_VAR = "SWITCHBOARD_CONFIG_DIR"
CONFIGS_SUBDIR = ("Plugins", "VirtualProduction", "Switchboard", "Source", "Switchboard", "configs")
NOT_CONFIGS = ("user_settings.json", "corrupted_user_settings_backup.json")
DEFAULT_MAP_TEXT = "-- Default Map --"
DEFAULT_LISTENER_PORT = 2980

# Switchboard device type <-> Operator Board role
TYPE_BY_ROLE = {ROLE_EDITOR: "Unreal", ROLE_NDISPLAY: "nDisplay"}
ROLE_BY_TYPE = {v: k for k, v in TYPE_BY_ROLE.items()}

# Top-level multi-user keys: Switchboard key -> MultiUserConfig attribute
MU_KEYS = (
    ("muserver_server_name", "server_name"),
    ("muserver_session_name", "session_name"),
    ("muserver_slate_mode", "use_slate_server"),
    ("muserver_clean_history", "clean_history"),
    ("muserver_command_line_arguments", "extra_args"),
)


# ---- locating engines and config folders ----------------------------------------------
def engine_folder(engine_dir: str) -> str:
    """Return the ``.../Engine`` folder for an install root or an Engine folder."""
    root = os.path.normpath(engine_dir.rstrip("/\\"))
    if os.path.basename(root).lower() != "engine":
        root = os.path.join(root, "Engine")
    return root


def config_dir_for_engine(engine_dir: str) -> str:
    return os.path.join(engine_folder(engine_dir), *CONFIGS_SUBDIR)


def _launcher_installs() -> List[str]:
    """Engine install roots recorded by the Epic Games Launcher (Windows)."""
    program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    path = os.path.join(program_data, "Epic", "UnrealEngineLauncher", "LauncherInstalled.dat")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    return [str(i.get("InstallLocation", "")) for i in data.get("InstallationList", [])
            if str(i.get("AppName", "")).startswith("UE_")]


def find_engine_installs(extra: Optional[List[str]] = None) -> List[Tuple[str, str]]:
    """Return ``[(version, install_root)]`` for supported engines, newest first."""
    roots: List[str] = list(extra or [])
    roots += _launcher_installs()
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramW6432", "")):
        if base:
            roots += glob.glob(os.path.join(base, "Epic Games", "UE_*"))
    found: Dict[str, Tuple[str, str]] = {}
    for root in roots:
        if not root:
            continue
        root = os.path.dirname(engine_folder(root))
        key = os.path.normcase(root)
        if key in found:
            continue
        version = detect_engine_version(root)
        if version and is_supported(version):
            found[key] = (version, root)
    return sorted(found.values(), key=lambda vr: tuple(int(x) for x in vr[0].split(".")), reverse=True)


def candidate_config_dirs(engine_dirs: Optional[List[str]] = None) -> List[Tuple[str, str]]:
    """``[(label, folder)]`` for every Switchboard config folder that can be offered to the user."""
    out: List[Tuple[str, str]] = []
    seen = set()

    def add(label: str, path: str) -> None:
        key = os.path.normcase(os.path.normpath(path))
        if key not in seen:
            seen.add(key)
            out.append((label, path))

    env = os.environ.get(CONFIG_ENV_VAR)
    if env:
        add(f"{CONFIG_ENV_VAR} ({env})", env)
    for version, root in find_engine_installs(engine_dirs):
        sb_dir = config_dir_for_engine(root)
        if os.path.isdir(os.path.dirname(sb_dir)):  # Switchboard plugin is installed
            add(f"Switchboard UE {version}", sb_dir)
    return out


def default_config_dir(config: AppConfig) -> str:
    """The folder Switchboard itself would use for this stage.

    Order: the user's choice, then ``SWITCHBOARD_CONFIG_DIR``, then the folder of the
    engine the active project uses, then the newest installed engine.
    """
    if config.switchboard.config_dir:
        return config.switchboard.config_dir
    env = os.environ.get(CONFIG_ENV_VAR)
    if env:
        return env
    if config.switchboard.active_config:
        return os.path.dirname(config.switchboard.active_config)
    engine = config.project().engine_dir if config.projects else ""
    if engine and os.path.isdir(os.path.dirname(config_dir_for_engine(engine))):
        return config_dir_for_engine(engine)
    dirs = candidate_config_dirs()
    return dirs[0][1] if dirs else ""


def list_configs(folder: str) -> List[str]:
    """Config files in a folder, found the same way Switchboard finds them."""
    if not folder or not os.path.isdir(folder):
        return []
    paths = glob.glob(os.path.join(folder, "**", "*" + CONFIG_SUFFIX), recursive=True)
    return sorted(p for p in paths if os.path.basename(p).lower() not in NOT_CONFIGS)


def config_summary(path: str) -> Dict[str, Any]:
    """Project name, uproject and device counts for listing a config without loading it."""
    try:
        data = read_switchboard_config(path)
    except (OSError, ValueError):
        return {"valid": False}
    devices = data.get("devices") or {}
    counts = {}
    for sb_type in TYPE_BY_ROLE.values():
        entries = devices.get(sb_type) or {}
        counts[sb_type] = sum(1 for k in entries if k != "settings")
    return {"valid": True, "project": data.get("project_name", ""), "uproject": data.get("uproject", ""),
            "engine": data.get("engine_dir", ""), "devices": counts}


def config_path_for_name(folder: str, name: str) -> str:
    name = name.strip()
    if not name:
        raise ValueError("Config name cannot be empty")
    if any(c in name for c in '<>:"/\\|?*'):
        raise ValueError('Config name cannot contain any of < > : " / \\ | ? *')
    if not name.lower().endswith(CONFIG_SUFFIX):
        name += CONFIG_SUFFIX
    if name.lower() in NOT_CONFIGS:
        raise ValueError(f"{name} is reserved by Switchboard")
    return os.path.join(folder, name)


# ---- file io ---------------------------------------------------------------------------
def read_switchboard_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh, object_pairs_hook=OrderedDict)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a Switchboard config")
    return data


def write_switchboard_config(path: str, data: Dict[str, Any]) -> None:
    # Switchboard writes with indent=4; keep the same so diffs stay small.
    atomic_write(path, json.dumps(data, indent=4))


def new_switchboard_config(project: ProjectConfig) -> Dict[str, Any]:
    """A minimal config. Switchboard fills in every other setting with its defaults."""
    return OrderedDict([
        ("project_name", project.name),
        ("uproject", project.uproject_path),
        ("engine_dir", project.engine_dir),
        ("maps_path", ""),
        ("maps_filter", "*.umap"),
        ("listener_exe", "SwitchboardListener"),
        ("current_level", DEFAULT_MAP_TEXT),
        ("devices", OrderedDict()),
    ])


# ---- Switchboard -> Operator Board --------------------------------------------------------
def _type_port(entries: Dict[str, Any]) -> int:
    port = (entries.get("settings") or {}).get("port")
    return port if isinstance(port, int) else DEFAULT_LISTENER_PORT


def parse_switchboard_config(data: Dict[str, Any], previous: Optional[List[DeviceConfig]] = None
                             ) -> Tuple[ProjectConfig, List[DeviceConfig]]:
    """Project and Unreal/nDisplay devices from a Switchboard config.

    ``previous`` devices with the same name keep their Operator Board-only fields
    (Remote Control port, extra arguments, overrides...).
    """
    prev = {d.name: d for d in (previous or [])}
    project = ProjectConfig(
        name=str(data.get("project_name") or "Untitled Project"),
        uproject_path=str(data.get("uproject") or ""),
        engine_dir=str(data.get("engine_dir") or ""),
    )
    level = data.get("current_level")
    if isinstance(level, str) and level not in (DEFAULT_MAP_TEXT, "None"):
        project.default_map = level
    version = detect_engine_version(project.engine_dir)
    if version and is_supported(version):
        project.ue_version = version

    devices: List[DeviceConfig] = []
    for sb_type, entries in (data.get("devices") or {}).items():
        role = ROLE_BY_TYPE.get(sb_type)
        if role is None or not isinstance(entries, dict):
            continue
        if role == ROLE_NDISPLAY:
            cfg_file = (entries.get("settings") or {}).get("ndisplay_cfg_file")
            if cfg_file:
                project.ndisplay_config = str(cfg_file)
        type_port = _type_port(entries)
        for name, entry in entries.items():
            if name == "settings" or not isinstance(entry, dict):
                continue
            base = prev.get(name)
            dev = DeviceConfig(**vars(base)) if base else DeviceConfig(name=str(name))
            dev.name = str(name)
            dev.role = role
            dev.address = str(entry.get("address") or entry.get("ip_address") or "127.0.0.1")
            port = entry.get("port")
            if isinstance(port, int):
                dev.listener_port = port
            elif not (base and base.listener_port != DEFAULT_LISTENER_PORT):
                # Switchboard keeps the listener port per device type and drops per-device
                # values when it saves, so a non-default port set in Operator Board is kept.
                dev.listener_port = type_port
            if "autojoin_mu_server" in entry:
                dev.join_multiuser = bool(entry["autojoin_mu_server"])
            if role == ROLE_NDISPLAY:
                dev.ndisplay_node = str(name)
                if "fullscreen" in entry:
                    dev.fullscreen = bool(entry["fullscreen"])
            devices.append(dev)
    return project, devices


def apply_switchboard(data: Dict[str, Any], config: AppConfig) -> None:
    """Overlay a Switchboard config onto an Operator Board stage (Switchboard wins)."""
    project, devices = parse_switchboard_config(data, config.devices)
    existing = next((p for p in config.projects if p.name == project.name), None)
    if existing is None and project.uproject_path:
        existing = next((p for p in config.projects if p.uproject_path == project.uproject_path), None)
    if existing is None:
        config.projects.insert(0, project)
    else:
        existing.name = project.name
        existing.uproject_path = project.uproject_path
        existing.engine_dir = project.engine_dir
        existing.ue_version = project.ue_version
        existing.default_map = project.default_map
        existing.ndisplay_config = project.ndisplay_config or existing.ndisplay_config
    config.active_project = project.name
    config.devices = devices
    names = {d.name for d in devices}
    for g in config.groups:
        g.devices = [n for n in g.devices if n in names]
    for sb_key, attr in MU_KEYS:
        if sb_key in data and data[sb_key] is not None:
            setattr(config.multiuser, attr, type(getattr(config.multiuser, attr))(data[sb_key]))


# ---- Operator Board -> Switchboard --------------------------------------------------------
def merge_into_switchboard(data: Dict[str, Any], config: AppConfig) -> Dict[str, Any]:
    """Write the stage's project, devices and multi-user settings into a Switchboard config.

    ``data`` is the config as currently on disk; unmapped keys are left untouched.
    """
    out = OrderedDict(data)
    project = config.project()
    out["project_name"] = project.name
    out["uproject"] = project.uproject_path
    out["engine_dir"] = project.engine_dir
    out["current_level"] = project.default_map or DEFAULT_MAP_TEXT
    mu = config.multiuser
    for sb_key, attr in MU_KEYS:
        out[sb_key] = getattr(mu, attr)

    devices = OrderedDict(out.get("devices") or {})
    for role, sb_type in TYPE_BY_ROLE.items():
        old = devices.get(sb_type) or {}
        mine = [d for d in config.devices if d.role == role]
        settings = OrderedDict(old.get("settings") or {})
        if role == ROLE_NDISPLAY and project.ndisplay_config:
            settings["ndisplay_cfg_file"] = project.ndisplay_config
        if not mine and not settings:
            devices.pop(sb_type, None)
            continue
        type_port = settings.get("port") if isinstance(settings.get("port"), int) else DEFAULT_LISTENER_PORT
        entries = OrderedDict()
        if settings:
            entries["settings"] = settings
        for dev in mine:
            entry = OrderedDict(old.get(dev.name) or {})
            entry.pop("ip_address", None)
            entry["address"] = dev.address
            entry.move_to_end("address", last=False)
            entry["autojoin_mu_server"] = dev.join_multiuser
            if dev.listener_port != type_port:
                entry["port"] = dev.listener_port
            else:
                entry.pop("port", None)
            if role == ROLE_NDISPLAY:
                entry["fullscreen"] = dev.fullscreen
            entries[dev.name] = entry
        devices[sb_type] = entries
    out["devices"] = devices
    out.move_to_end("devices")  # Switchboard writes devices last
    return out


# ---- simple import (Devices page) ----------------------------------------------------------
def import_switchboard_config(path: str) -> Tuple[ProjectConfig, List[DeviceConfig]]:
    return parse_switchboard_config(read_switchboard_config(path))
