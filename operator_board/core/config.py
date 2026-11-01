"""Persistent application configuration.

Everything an operator sets up (projects, devices, groups, LED processors, camera
presets, sync jobs...) lives in one JSON file. The location defaults to
~/.operator_board/config.json and can be overridden with OPERATOR_BOARD_HOME.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from operator_board.core.serde import from_dict, to_dict
from operator_board.core.ue_versions import DEFAULT_VERSION

CONFIG_SCHEMA_VERSION = 2


def app_home() -> str:
    return os.environ.get("OPERATOR_BOARD_HOME") or os.path.join(os.path.expanduser("~"), ".operator_board")


def default_config_path() -> str:
    return os.path.join(app_home(), "config.json")


@dataclass
class ProjectConfig:
    name: str = "Untitled Project"
    uproject_path: str = ""
    engine_dir: str = ""
    ue_version: str = DEFAULT_VERSION
    default_map: str = ""
    ndisplay_config: str = ""


ROLE_EDITOR = "editor"
ROLE_NDISPLAY = "ndisplay"
ROLES = (ROLE_EDITOR, ROLE_NDISPLAY)


@dataclass
class DeviceConfig:
    name: str = "Device"
    address: str = "127.0.0.1"
    role: str = ROLE_EDITOR
    listener_port: int = 2980
    # auto (QUIC for UE 5.4+, TCP before) | quic | tcp
    listener_protocol: str = "auto"
    platform: str = "Win64"
    enabled: bool = True
    # nDisplay
    ndisplay_node: str = ""
    fullscreen: bool = True
    # Remote Control API (HTTP) for this instance
    rc_enabled: bool = True
    rc_port: int = 30010
    # Multi-user
    join_multiuser: bool = True
    extra_args: str = ""
    # Optional per-device overrides of project paths (if drive letters differ per machine)
    uproject_path_override: str = ""
    engine_dir_override: str = ""


@dataclass
class GroupConfig:
    name: str = "Group"
    devices: List[str] = field(default_factory=list)
    color: str = "#3d8bfd"


@dataclass
class MultiUserConfig:
    server_name: str = "OperatorBoard_MU"
    session_name: str = "VP_Session"
    host: str = "local"  # "local" or the name of a device whose listener should run the server
    use_slate_server: bool = False
    clean_history: bool = False
    # Start the server automatically when an editor or node that joins multi-user is started.
    auto_start: bool = True
    # Move to the next session name (VP_Session -> VP_Session_2) whenever the server is stopped,
    # and when it starts with a session of that name still on it.
    auto_increment_session: bool = True
    # Make the session live as soon as the server starts (after any name clash is settled),
    # instead of when the first editor or node joins.
    auto_start_session: bool = False
    extra_args: str = ""
    udp_unicast_endpoint: str = ""
    udp_static_endpoints: str = ""
    # Log patterns used to count connected clients. Configurable because Concert
    # log wording differs slightly between engine versions.
    client_join_pattern: str = r"(?i)\b(?:client|user)\s+'?(?P<name>[^'(]*?)'?\s*(?:\([^)]*\)\s*)?(?:has\s+)?(?:joined|connected)\b"
    client_leave_pattern: str = r"(?i)\b(?:client|user)\s+'?(?P<name>[^'(]*?)'?\s*(?:\([^)]*\)\s*)?(?:has\s+)?(?:left|disconnected)\b"


@dataclass
class LEDProcessorConfig:
    name: str = "LED Processor"
    profile_id: str = "simulator"
    host: str = "192.168.0.100"
    port: int = 0  # 0 = use the profile default
    enabled: bool = True
    options: Dict[str, Any] = field(default_factory=dict)
    # command name -> replacement command definition (same schema as profile commands)
    command_overrides: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RemoteControlConfig:
    timeout_s: float = 3.0
    # Device that receives editor-only operations (level loads, take recorder...)
    primary_target: str = ""
    # How "Load level" behaves: editor | nodes (console 'open') | relaunch (restart nDisplay nodes on the map) | all
    level_load_mode: str = "editor"
    # Remote Control blocks remote Python, console commands and (5.8) most function calls by default.
    # Operator Board needs them, so it allows them on the editors and nodes it launches.
    allow_remote_features: bool = True


@dataclass
class CameraPreset:
    name: str = "Camera"
    target: str = "viewport"  # "viewport" or an actor object path
    location: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])  # pitch, yaw, roll


@dataclass
class SyncJobConfig:
    name: str = "Sync Job"
    provider: str = "freefilesync"
    params: Dict[str, Any] = field(default_factory=dict)
    targets: List[str] = field(default_factory=lambda: ["local"])


@dataclass
class TakesConfig:
    slate: str = "Scene_01"
    take: int = 1
    description: str = ""
    auto_increment: bool = True
    record_with_take_recorder: bool = True
    # Optional Take Preset asset (e.g. /Game/Takes/StagePreset) loaded into Take Recorder before each
    # recording, so it always has its sources. Empty = use whatever the Take Recorder panel has.
    take_preset: str = ""
    # When Take Recorder has no sources, add the stage camera (the nDisplay ICVFX camera, else a cine
    # camera in the level) and record. take_camera names a specific actor (its label) instead.
    auto_add_camera: bool = True
    take_camera: str = ""
    take_log_path: str = ""


@dataclass
class SurfacesConfig:
    # Every control surface (surfaces.layout.SurfaceLayout as a dict; see surfaces/manager.py).
    devices: List[Dict[str, Any]] = field(default_factory=list)
    devices_migrated: bool = False  # set once layout_path / midi_* below became the first surface
    layout_path: str = ""  # older versions: one layout file (empty = built-in default)
    streamdeck_enabled: bool = True
    streamdeck_brightness: int = 60
    api_server_enabled: bool = True
    api_bind: str = "127.0.0.1"
    api_port: int = 8765
    api_token: str = ""  # if set, API callers must send ?token=... or an X-Operator-Token header
    osc_enabled: bool = False  # TouchOSC / Open Stage Control etc. send /operator/... messages here
    osc_port: int = 8000
    midi_enabled: bool = False  # MIDI pad controllers (needs the mido + python-rtmidi packages)
    midi_port: str = ""  # MIDI input name (part of it is enough); empty = the first one found


@dataclass
class UIConfig:
    confirm_blackout: bool = True  # ask before blacking out the wall, so it cannot happen by accident
    confirm_stop_all: bool = True
    metrics_poll_s: float = 2.0


@dataclass
class SwitchboardLinkConfig:
    # Folder holding Switchboard configs. Empty = the folder Switchboard itself uses.
    config_dir: str = ""
    # Switchboard config (.json) that is the current stage. Empty = standalone stage.
    active_config: str = ""


@dataclass
class AppConfig:
    schema_version: int = CONFIG_SCHEMA_VERSION
    projects: List[ProjectConfig] = field(default_factory=lambda: [ProjectConfig()])
    active_project: str = "Untitled Project"
    devices: List[DeviceConfig] = field(default_factory=list)
    groups: List[GroupConfig] = field(default_factory=list)
    multiuser: MultiUserConfig = field(default_factory=MultiUserConfig)
    led_processors: List[LEDProcessorConfig] = field(default_factory=list)
    remote_control: RemoteControlConfig = field(default_factory=RemoteControlConfig)
    cameras: List[CameraPreset] = field(default_factory=list)
    sync_jobs: List[SyncJobConfig] = field(default_factory=list)
    takes: TakesConfig = field(default_factory=TakesConfig)
    surfaces: SurfacesConfig = field(default_factory=SurfacesConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    switchboard: SwitchboardLinkConfig = field(default_factory=SwitchboardLinkConfig)
    favourite_properties: List[Dict[str, str]] = field(default_factory=list)

    # ---- helpers -------------------------------------------------------
    def project(self, name: Optional[str] = None) -> ProjectConfig:
        name = name or self.active_project
        for p in self.projects:
            if p.name == name:
                return p
        if not self.projects:
            self.projects.append(ProjectConfig())
        return self.projects[0]

    def device(self, name: str) -> Optional[DeviceConfig]:
        return next((d for d in self.devices if d.name == name), None)

    def group(self, name: str) -> Optional[GroupConfig]:
        return next((g for g in self.groups if g.name == name), None)

    def to_json(self) -> str:
        return json.dumps(to_dict(self), indent=2)

    @classmethod
    def from_json(cls, text: str) -> "AppConfig":
        return migrate(from_dict(cls, json.loads(text)))


def migrate(cfg: "AppConfig") -> "AppConfig":
    """Bring settings saved by older versions up to date."""
    if cfg.schema_version < 2:
        # Blackout confirmation became the default in schema 2. Older files saved the old
        # default (off) without anyone choosing it, so switch it on once.
        cfg.ui.confirm_blackout = True
    cfg.schema_version = CONFIG_SCHEMA_VERSION
    return cfg


def load_config(path: Optional[str] = None) -> AppConfig:
    path = path or default_config_path()
    if not os.path.exists(path):
        return AppConfig()
    with open(path, "r", encoding="utf-8") as fh:
        return AppConfig.from_json(fh.read())


def atomic_write(path: str, text: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def save_config(config: AppConfig, path: Optional[str] = None) -> str:
    path = path or default_config_path()
    atomic_write(path, config.to_json())
    return path
