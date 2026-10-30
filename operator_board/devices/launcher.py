"""Command-line builders for Unreal Engine processes launched through the listener."""
from __future__ import annotations

import ntpath
import posixpath
import shlex
from dataclasses import dataclass
from typing import List, Optional

from operator_board.core.config import (ROLE_NDISPLAY, DeviceConfig, MultiUserConfig, ProjectConfig)
from operator_board.core.ue_versions import exe_suffix, get_version_info, platform_binaries_dir


@dataclass
class LaunchSpec:
    exe: str
    args: str
    working_dir: str = ""

    def as_command_line(self) -> str:
        return f'"{self.exe}" {self.args}'.strip()


def _pathmod(platform: str):
    return ntpath if platform == "Win64" else posixpath


def _quote(value: str) -> str:
    return f'"{value}"' if any(c in value for c in ' \t') else value


def engine_binary(engine_dir: str, exe_name: str, platform: str) -> str:
    pm = _pathmod(platform)
    root = engine_dir.rstrip("/\\")
    if pm.basename(root).lower() != "engine":
        root = pm.join(root, "Engine")
    path = pm.join(root, "Binaries", platform_binaries_dir(platform), exe_name + exe_suffix(platform))
    return pm.normpath(path) if platform == "Win64" else path


def multiuser_client_args(mu: MultiUserConfig, display_name: str) -> List[str]:
    args = [
        f"-CONCERTSERVER={_quote(mu.server_name)}",
        f"-CONCERTSESSION={_quote(mu.session_name)}",
        f"-CONCERTDISPLAYNAME={_quote(display_name)}",
        "-CONCERTAUTOCONNECT",
        "-CONCERTRETRYAUTOCONNECTONERROR",
    ]
    return args


def unreal_launch_spec(device: DeviceConfig, project: ProjectConfig, multiuser: Optional[MultiUserConfig] = None,
                       map_override: str = "") -> LaunchSpec:
    info = get_version_info(project.ue_version)
    engine_dir = device.engine_dir_override or project.engine_dir
    uproject = device.uproject_path_override or project.uproject_path
    if not engine_dir:
        raise ValueError("Project has no engine directory configured")
    if not uproject:
        raise ValueError("Project has no .uproject path configured")
    exe = engine_binary(engine_dir, info.editor_exe, device.platform)

    args: List[str] = [f'"{uproject}"']
    level = map_override or project.default_map
    if level:
        args.append(level)
    args.append("-messaging")
    args.append(f"-SessionName={_quote('OperatorBoard_' + device.name)}")

    if device.role == ROLE_NDISPLAY:
        if not project.ndisplay_config:
            raise ValueError("nDisplay device requires an nDisplay config (.ndisplay) on the project")
        node = device.ndisplay_node or device.name
        args += [
            "-game", "-dc_cluster", "-nosplash", "-fixedseed", "-NoVerifyGC", "-noxrstereo", "-xrtrackingonly",
            "-RemoteControlIsHeadless", "-unattended", "-NoScreenMessages",
            f'-dc_cfg="{project.ndisplay_config}"', f"-dc_node={node}",
            "-fullscreen" if device.fullscreen else "-windowed",
            f"-log=OperatorBoard_{node}.log",
        ]
    if device.rc_enabled:
        args += ["-RCWebControlEnable", "-RCWebInterfaceEnable", f"-RCWebControlPort={device.rc_port}"]
    if multiuser and device.join_multiuser:
        args += multiuser_client_args(multiuser, device.name)
    if device.extra_args:
        args.append(device.extra_args)
    return LaunchSpec(exe=exe, args=" ".join(args))


def multiuser_server_spec(project: ProjectConfig, mu: MultiUserConfig, platform: str = "Win64",
                          engine_dir_override: str = "") -> LaunchSpec:
    info = get_version_info(project.ue_version)
    engine_dir = engine_dir_override or project.engine_dir
    if not engine_dir:
        raise ValueError("Project has no engine directory configured")
    exe_name = info.multiuser_slate_server_exe if mu.use_slate_server else info.multiuser_server_exe
    exe = engine_binary(engine_dir, exe_name, platform)
    args = [f"-CONCERTSERVER={_quote(mu.server_name)}", "-messaging"]
    if mu.session_name:
        args.append(f"-CONCERTSESSION={_quote(mu.session_name)}")
    if mu.clean_history:
        args.append("-CONCERTCLEAN")
    if mu.udp_unicast_endpoint:
        args.append(f"-UDPMESSAGING_TRANSPORT_UNICAST={mu.udp_unicast_endpoint}")
    if mu.udp_static_endpoints:
        args.append(f"-UDPMESSAGING_TRANSPORT_STATIC={mu.udp_static_endpoints}")
    if mu.extra_args:
        args.append(mu.extra_args)
    return LaunchSpec(exe=exe, args=" ".join(args))


def split_args(args: str) -> List[str]:
    """Split an argument string for local subprocess launches."""
    return shlex.split(args, posix=False)
