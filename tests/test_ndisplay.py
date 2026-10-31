"""nDisplay config extraction / overrides and the launch arguments that depend on them."""
import json
import struct

import pytest

from test_listener import NDISPLAY_JSON

from operator_board.core.config import DeviceConfig, MultiUserConfig, ProjectConfig
from operator_board.devices import ndisplay
from operator_board.devices.launcher import multiuser_server_spec, unreal_launch_spec


def _uasset(tmp_path, text, utf16=False):
    """A stand-in .uasset: binary noise around an FString ConfigExport tag, as Unreal stores it."""
    if utf16:
        body = text.encode("utf-16-le") + b"\x00\x00"
        length = -(len(text) + 1)
    else:
        body = text.encode("utf-8") + b"\x00"
        length = len(text) + 1
    data = b"\xc1\x83\x2a\x9e" + bytes(range(200)) + b"ConfigExport\x00" + struct.pack("<i", length) + body
    data += b'"nDisplay" also appears in other tags {"x"' + bytes(64)
    path = tmp_path / "Wall.uasset"
    path.write_bytes(data)
    return str(path)


@pytest.mark.parametrize("utf16", [False, True])
def test_extract_config_from_uasset(tmp_path, utf16):
    text = ndisplay.extract_config_json(_uasset(tmp_path, NDISPLAY_JSON, utf16))
    assert json.loads(text)["nDisplay"]["assetPath"] == "/Game/Wall.Wall"


def test_extract_rejects_other_assets(tmp_path):
    path = tmp_path / "Other.uasset"
    path.write_bytes(bytes(500))
    with pytest.raises(ValueError):
        ndisplay.extract_config_json(str(path))


def test_local_overrides_keep_our_nodes_and_addresses():
    out = json.loads(ndisplay.apply_local_overrides(NDISPLAY_JSON, {"N2": "192.168.1.50"}))
    cluster = out["nDisplay"]["cluster"]
    assert list(cluster["nodes"]) == ["N2"]
    assert cluster["nodes"]["N2"]["host"] == "192.168.1.50"
    assert cluster["primaryNode"]["id"] == "N2"  # the config's primary (N1) is not used, so N2 is
    window = ndisplay.node_window(NDISPLAY_JSON, "N2")
    assert window.fullscreen and (window.w, window.h) == (1920, 1080)


def test_ndisplay_command_line_matches_switchboard():
    dev = DeviceConfig(name="N1", role="ndisplay", ndisplay_node="N1")
    project = ProjectConfig(uproject_path="D:/P/P.uproject", engine_dir="C:/UE_5.8", default_map="/Game/Stage",
                            ndisplay_config="D:/P/Content/Wall.uasset")
    spec = unreal_launch_spec(dev, project, MultiUserConfig(), ndisplay_cfg="C:/Temp/ndisplay/1.ndisplay",
                              window=ndisplay.node_window(NDISPLAY_JSON, "N1"), multiprocess=True)
    args = spec.args
    assert args.index("-game") < args.index("/Game/Stage")
    assert '-dc_cfg="C:/Temp/ndisplay/1.ndisplay"' in args and ".uasset" not in args
    for needed in ("DisplayClusterGameEngine", "DisplayClusterViewportClient", "DisplayClusterPlayerInput",
                   "-dc_dev_mono", "-dx12", "Log=OperatorBoard_N1.log", "-multiprocess", "-CONCERTISHEADLESS",
                   "-windowed -forceres WinX=0 WinY=0 ResX=1920 ResY=1080"):
        assert needed in args, needed


def test_multiuser_server_does_not_create_the_session():
    project = ProjectConfig(engine_dir="C:/UE_5.8")
    args = multiuser_server_spec(project, MultiUserConfig(session_name="Shoot")).args
    assert "-CONCERTSESSION" not in args  # a server-made session has no project and refuses clients
    assert "-UDPMESSAGING_SHARE_KNOWN_NODES=1" in args
