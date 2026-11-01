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


# Runtime class overrides that turn a -game instance into an nDisplay cluster node
# (same -ini injections as Switchboard 5.2 - 5.8).
NDISPLAY_INI = [
    "-ini:Engine:[/Script/Engine.Engine]:GameEngine=/Script/DisplayCluster.DisplayClusterGameEngine,"
    "[/Script/Engine.Engine]:GameViewportClientClassName=/Script/DisplayCluster.DisplayClusterViewportClient,"
    "[/Script/Engine.UserInterfaceSettings]:bAllowHighDPIInGameMode=True,"
    "[/Script/Engine.UserInterfaceSettings]:bAllowHighDpiWhenUnattended=True",
    "-ini:Game:[/Script/EngineSettings.GeneralProjectSettings]:bUseBorderlessWindow=True",
    "-ini:Input:[/Script/Engine.InputSettings]:DefaultPlayerInputClass=/Script/DisplayCluster.DisplayClusterPlayerInput",
]


# Remote Control's security switches (Project Settings > Plugins > Remote Control > Security), all off by
# default. Operator Board runs editor Python (Take Recorder, levels, sub-levels, cameras), console commands
# and functions outside Epic's allowlist (5.8), so it turns them on for the instances it launches.
# Engines that lack a switch (e.g. 5.2 has only the Python one) ignore it.
_RC_SECTION = "[/Script/RemoteControlCommon.RemoteControlSettings]"
REMOTE_CONTROL_UNLOCK = ("-ini:RemoteControl:"
                         f"{_RC_SECTION}:bEnableRemotePythonExecution=True,"
                         f"{_RC_SECTION}:bAllowConsoleCommandRemoteExecution=True,"
                         f"{_RC_SECTION}:bAllowAnyRemoteFunctionCall=True")


def unreal_launch_spec(device: DeviceConfig, project: ProjectConfig, multiuser: Optional[MultiUserConfig] = None,
                       map_override: str = "", ndisplay_cfg: str = "", window=None,
                       multiprocess: bool = False, allow_remote_features: bool = True) -> LaunchSpec:
    """Command line for an editor or an nDisplay node.

    For nDisplay, ``ndisplay_cfg`` is the .ndisplay path *on the node* (written there
    through the listener, see devices/ndisplay.py) and ``window`` the node's window from
    that config. ``multiprocess`` is set for every node but the first on one machine.
    """
    info = get_version_info(project.ue_version)
    engine_dir = device.engine_dir_override or project.engine_dir
    uproject = device.uproject_path_override or project.uproject_path
    if not engine_dir:
        raise ValueError("Project has no engine directory configured")
    if not uproject:
        raise ValueError("Project has no .uproject path configured")
    exe = engine_binary(engine_dir, info.editor_exe, device.platform)

    level = map_override or project.default_map
    args: List[str] = [f'"{uproject}"']

    if device.role == ROLE_NDISPLAY:
        cfg = ndisplay_cfg or project.ndisplay_config
        if not cfg:
            raise ValueError("nDisplay device requires an nDisplay config on the project")
        node = device.ndisplay_node or device.name
        args.append("-game")  # before the map, as Switchboard does
        if level:
            args.append(level)
        args += [
            "-messaging", "-dc_cluster", "-nosplash", "-fixedseed", "-NoVerifyGC", "-noxrstereo",
            "-xrtrackingonly", "-RemoteControlIsHeadless",
            f"-StageFriendlyName={_quote(device.name.replace(' ', '_'))}",
            f'-dc_cfg="{cfg}"', "-dx12", "-dc_dev_mono",
            "-nosound", "-nomcp", "-NoLoadingScreen",
            f"-dc_node={node}", f"Log=OperatorBoard_{node}.log",
        ] + NDISPLAY_INI + [
            "-unattended", "-NoScreenMessages", "-handleensurepercent=0", '-ExecCmds="DisableAllScreenMessages"',
            # Switchboard's default device-profile cvars for render nodes
            '-DPCVars="Slate.bAllowNotifications=0,p.Chaos.Solver.Deterministic=1,'
            'LevelInstance.ForceEditorWorldMode=1,gc.AllowIncrementalReachability=1"',
        ]
        if multiprocess:
            args.append("-multiprocess")
        fullscreen = window.fullscreen if window is not None else device.fullscreen
        if fullscreen:
            args.append("-fullscreen")
        else:
            args += ["-windowed"]
            if window is not None and window.w > 0 and window.h > 0:
                args += ["-forceres", f"WinX={window.x}", f"WinY={window.y}", f"ResX={window.w}", f"ResY={window.h}"]
        if window is not None and window.headless:
            args.append("-RenderOffscreen")
    else:
        if level:
            args.append(level)
        args.append("-messaging")
    args.append(f"-SessionName={_quote('OperatorBoard_' + device.name)}")
    if device.rc_enabled:
        args += ["-RCWebControlEnable", "-RCWebInterfaceEnable", f"-RCWebControlPort={device.rc_port}"]
        if allow_remote_features:
            args.append(REMOTE_CONTROL_UNLOCK)
    if multiuser and device.join_multiuser:
        args += multiuser_client_args(multiuser, device.name)
        if device.role == ROLE_NDISPLAY:
            args.append("-CONCERTISHEADLESS")
    if device.extra_args:
        args.append(device.extra_args)
    return LaunchSpec(exe=exe, args=" ".join(args))


def multiuser_server_spec(project: ProjectConfig, mu: MultiUserConfig, platform: str = "Win64",
                          engine_dir_override: str = "", session: str = "", clean: Optional[bool] = None) -> LaunchSpec:
    """The multi-user server's command line.

    ``session`` makes the server create (or reuse) that session for the project at start-up
    ("Auto-start session"); ``clean`` overrides ``mu.clean_history``.
    """
    info = get_version_info(project.ue_version)
    engine_dir = engine_dir_override or project.engine_dir
    if not engine_dir:
        raise ValueError("Project has no engine directory configured")
    exe_name = info.multiuser_slate_server_exe if mu.use_slate_server else info.multiuser_server_exe
    exe = engine_binary(engine_dir, exe_name, platform)
    # Without a session, the first client to join creates it with its project (as in Switchboard).
    # With one, -CONCERTPROJECT is required: a session created without it has no project and
    # refuses every client with "Invalid project name (expected '', got '<project>')". A server
    # given a session name that already exists reuses it (both checked on a real 5.8 server).
    args = [f"-CONCERTSERVER={_quote(mu.server_name)}", "-UDPMESSAGING_SHARE_KNOWN_NODES=1"]
    if session:
        uproject = project.uproject_path
        if not uproject:
            raise ValueError("Project has no .uproject configured, so the server cannot create its session")
        name = posixpath.splitext(posixpath.basename(uproject.replace("\\", "/")))[0]
        args += [f"-CONCERTSESSION={_quote(session)}", f"-CONCERTPROJECT={_quote(name)}"]
    if mu.clean_history if clean is None else clean:
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
