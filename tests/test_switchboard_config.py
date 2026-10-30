"""Switchboard config read / merge / create and the .opboard sidecar store."""
import copy
import json
import os

import pytest

from operator_board.core.config import AppConfig, GroupConfig, LEDProcessorConfig, load_config
from operator_board.core.stage import ConfigStore, sidecar_path
from operator_board.core.switchboard_config import (DEFAULT_MAP_TEXT, config_dir_for_engine, config_path_for_name,
                                                    list_configs, merge_into_switchboard, parse_switchboard_config)

# Trimmed from a real UE 5.8 Switchboard config.
SB_58 = {
    "project_name": "nDisplayTest",
    "uproject": "C:\\Projects\\Stage\\Stage.uproject",
    "engine_dir": "C:/Program Files/Epic Games/UE_5.8/Engine/",
    "engine_sync_method": "Use Existing (do not sync/build)",
    "maps_filter": "*.umap",
    "osc_server_port": 6000,
    "muserver_server_name": "nDisplayTest_MU_Server",
    "muserver_session_name": "MU_nDisplayTest_1",
    "muserver_slate_mode": True,
    "muserver_clean_history": False,
    "muserver_command_line_arguments": "",
    "muserver_endpoint": ":9030",
    "current_level": "/Game/nDisplayTest",
    "devices": {
        "LiveLinkFace": {"settings": {"osc_port": 8000}},
        "Unreal": {
            "settings": {"port": 2980, "buffer_size": 1024, "command_line_arguments": ""},
            "Editor_1": {"address": "127.0.0.1", "roles": [], "autojoin_mu_server": True,
                         "last_launch_command": "UnrealEditor.exe ..."},
        },
        "nDisplay": {
            "settings": {"ndisplay_cfg_file": "C:/Projects/Stage/Content/nDisplayConfig.uasset", "port": 2980,
                         "render_api": "dx12", "primary_device_name": "Node_0"},
            "Node_0": {"address": "10.0.0.10", "window_position": [0, 0], "window_resolution": [2560, 1600],
                       "fullscreen": False, "headless": False, "autojoin_mu_server": True},
            "Node_1": {"address": "10.0.0.11", "port": 2981, "fullscreen": True},
        },
    },
}


def _write(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=4)


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def test_parse_real_layout():
    project, devices = parse_switchboard_config(SB_58)
    assert project.name == "nDisplayTest"
    assert project.default_map == "/Game/nDisplayTest"
    assert project.ndisplay_config.endswith("nDisplayConfig.uasset")
    assert [(d.name, d.role, d.address, d.listener_port) for d in devices] == [
        ("Editor_1", "editor", "127.0.0.1", 2980),
        ("Node_0", "ndisplay", "10.0.0.10", 2980),
        ("Node_1", "ndisplay", "10.0.0.11", 2981),
    ]
    assert devices[1].ndisplay_node == "Node_0" and devices[1].fullscreen is False


def test_merge_keeps_switchboard_only_keys():
    cfg = AppConfig()
    from operator_board.core.switchboard_config import apply_switchboard
    apply_switchboard(copy.deepcopy(SB_58), cfg)
    cfg.devices[1].address = "10.0.0.20"
    cfg.devices = [d for d in cfg.devices if d.name != "Node_1"]
    cfg.multiuser.session_name = "Shoot_Day_2"
    out = merge_into_switchboard(copy.deepcopy(SB_58), cfg)
    assert out["osc_server_port"] == 6000 and out["muserver_endpoint"] == ":9030"
    assert out["muserver_session_name"] == "Shoot_Day_2"
    assert out["devices"]["LiveLinkFace"] == {"settings": {"osc_port": 8000}}
    nd = out["devices"]["nDisplay"]
    assert list(nd) == ["settings", "Node_0"]
    assert nd["settings"]["render_api"] == "dx12"
    assert nd["Node_0"]["address"] == "10.0.0.20"
    assert nd["Node_0"]["window_resolution"] == [2560, 1600]
    assert out["devices"]["Unreal"]["Editor_1"]["last_launch_command"] == "UnrealEditor.exe ..."
    # the merged file parses back to the same stage
    _, devices = parse_switchboard_config(out)
    assert [d.name for d in devices] == ["Editor_1", "Node_0"]


def test_store_round_trip_with_sidecar(tmp_path):
    sb = tmp_path / "configs" / "Stage.json"
    sb.parent.mkdir()
    _write(sb, SB_58)
    store = ConfigStore(str(tmp_path / "home" / "config.json"))
    stage = store.open_switchboard(AppConfig(), str(sb))
    assert stage.active_project == "nDisplayTest"
    stage.led_processors.append(LEDProcessorConfig(name="Wall", profile_id="simulator"))
    stage.groups.append(GroupConfig(name="Wall", devices=["Node_0", "Node_1"]))
    stage.devices[1].rc_port = 30011  # Operator Board-only field
    stage.surfaces.api_port = 9999  # machine setting
    store.save(stage)

    side = _read(sidecar_path(str(sb)))
    assert side["format"] == "operator-board-stage" and "surfaces" not in side
    assert not sidecar_path(str(sb)).endswith(".json")
    assert list_configs(str(sb.parent)) == [str(sb)]  # Switchboard will not see the sidecar

    # Switchboard edits the file meanwhile: add a node, remove Node_1
    data = _read(sb)
    data["devices"]["nDisplay"]["Node_2"] = {"address": "10.0.0.12"}
    del data["devices"]["nDisplay"]["Node_1"]
    _write(sb, data)

    reopened = ConfigStore(str(tmp_path / "home" / "config.json")).load()
    assert reopened.switchboard.active_config == str(sb)
    assert [d.name for d in reopened.devices] == ["Editor_1", "Node_0", "Node_2"]
    assert reopened.devices[1].rc_port == 30011
    assert reopened.groups[0].devices == ["Node_0"]
    assert reopened.led_processors[0].name == "Wall"
    assert reopened.surfaces.api_port == 9999


def test_per_device_port_survives_switchboard_save(tmp_path):
    """Switchboard drops per-device "port" on save; the sidecar keeps it."""
    sb = tmp_path / "Stage.json"
    _write(sb, SB_58)
    store = ConfigStore(str(tmp_path / "home.json"))
    stage = store.open_switchboard(AppConfig(), str(sb))
    assert stage.devices[2].listener_port == 2981
    data = _read(sb)
    data["devices"]["nDisplay"]["Node_1"].pop("port")  # what Switchboard's save does
    assert list(data)[-1] == "devices"
    _write(sb, data)
    assert ConfigStore(str(tmp_path / "home.json")).load().devices[2].listener_port == 2981


def test_create_new_config_and_close(tmp_path):
    home = tmp_path / "home" / "config.json"
    store = ConfigStore(str(home))
    standalone = AppConfig()
    standalone.led_processors.append(LEDProcessorConfig(name="Sim"))
    from operator_board.core.config import DeviceConfig, save_config
    standalone.devices.append(DeviceConfig(name="Node_A", role="ndisplay", address="10.1.1.1"))
    save_config(standalone, str(home))

    path = config_path_for_name(str(tmp_path / "configs"), "New Stage")
    stage = store.create_switchboard(standalone, path, "New Stage", "D:/P/P.uproject", "C:/UE_5.6")
    data = _read(path)
    assert data["project_name"] == "New Stage" and data["current_level"] == DEFAULT_MAP_TEXT
    assert data["devices"]["nDisplay"]["Node_A"]["address"] == "10.1.1.1"
    assert "Unreal" not in data["devices"]
    assert stage.led_processors[0].name == "Sim"
    with pytest.raises(FileExistsError):
        store.create_switchboard(standalone, path, "X", "", "")

    back = store.close_switchboard(stage)
    assert back.switchboard.active_config == ""
    assert load_config(str(home)).devices[0].name == "Node_A"  # standalone stage untouched


def test_missing_switchboard_config_falls_back(tmp_path):
    home = tmp_path / "config.json"
    cfg = AppConfig()
    cfg.switchboard.active_config = str(tmp_path / "gone.json")
    from operator_board.core.config import save_config
    save_config(cfg, str(home))
    store = ConfigStore(str(home))
    loaded = store.load()
    assert loaded.switchboard.active_config == "" and "gone.json" in store.load_error


def test_config_names_and_dirs():
    assert config_dir_for_engine("C:/UE_5.8/Engine/").replace("\\", "/").endswith(
        "UE_5.8/Engine/Plugins/VirtualProduction/Switchboard/Source/Switchboard/configs")
    assert config_dir_for_engine("C:/UE_5.8") == config_dir_for_engine("C:/UE_5.8/Engine")
    assert os.path.basename(config_path_for_name("x", "Stage A")) == "Stage A.json"
    for bad in ("", "a/b", "user_settings"):
        with pytest.raises(ValueError):
            config_path_for_name("x", bad)
