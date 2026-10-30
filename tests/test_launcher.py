import pytest

from operator_board.core.config import DeviceConfig, MultiUserConfig, ProjectConfig
from operator_board.devices.launcher import engine_binary, multiuser_server_spec, unreal_launch_spec

PROJECT = ProjectConfig(uproject_path="D:/Projects/Stage/Stage.uproject", engine_dir="C:/Program Files/Epic Games/UE_5.6",
                        ue_version="5.6", default_map="/Game/Maps/Main", ndisplay_config="D:/Projects/Stage/wall.ndisplay")


def test_engine_binary_windows_and_linux():
    assert engine_binary("C:/UE_5.6", "UnrealEditor", "Win64") == r"C:\UE_5.6\Engine\Binaries\Win64\UnrealEditor.exe"
    assert engine_binary("/opt/UE/Engine", "UnrealEditor", "Linux") == "/opt/UE/Engine/Binaries/Linux/UnrealEditor"


def test_editor_command_line():
    spec = unreal_launch_spec(DeviceConfig(name="Editor"), PROJECT, MultiUserConfig(server_name="MU", session_name="S"))
    assert spec.exe.endswith(r"Win64\UnrealEditor.exe")
    assert spec.args.startswith('"D:/Projects/Stage/Stage.uproject" /Game/Maps/Main')
    for flag in ("-messaging", "-RCWebControlEnable", "-CONCERTSERVER=MU", "-CONCERTSESSION=S", "-CONCERTAUTOCONNECT"):
        assert flag in spec.args
    assert "-game" not in spec.args


def test_ndisplay_command_line_and_map_override():
    dev = DeviceConfig(name="N1", role="ndisplay", ndisplay_node="Wall_L", join_multiuser=False)
    spec = unreal_launch_spec(dev, PROJECT, MultiUserConfig(), map_override="/Game/Maps/Night")
    assert "/Game/Maps/Night" in spec.args and "/Game/Maps/Main" not in spec.args
    for flag in ("-game", "-dc_cluster", '-dc_cfg="D:/Projects/Stage/wall.ndisplay"', "-dc_node=Wall_L", "-fullscreen"):
        assert flag in spec.args
    assert "CONCERT" not in spec.args


def test_ndisplay_requires_config():
    project = ProjectConfig(uproject_path="a.uproject", engine_dir="C:/UE")
    with pytest.raises(ValueError):
        unreal_launch_spec(DeviceConfig(role="ndisplay"), project)


def test_multiuser_server_spec():
    spec = multiuser_server_spec(PROJECT, MultiUserConfig(server_name="My Server", use_slate_server=True,
                                                          clean_history=True))
    assert spec.exe.endswith("UnrealMultiUserSlateServer.exe")
    assert '-CONCERTSERVER="My Server"' in spec.args and "-CONCERTCLEAN" in spec.args
