import json

from operator_board.core.config import AppConfig, DeviceConfig, LEDProcessorConfig, load_config, save_config
from operator_board.core.switchboard_config import parse_switchboard_config
from operator_board.core.ue_versions import SUPPORTED_VERSIONS, detect_engine_version, get_version_info


def test_round_trip(tmp_path):
    cfg = AppConfig()
    cfg.devices.append(DeviceConfig(name="Node1", role="ndisplay", ndisplay_node="N1"))
    cfg.led_processors.append(LEDProcessorConfig(name="P", options={"username": "x"}))
    path = save_config(cfg, str(tmp_path / "c.json"))
    loaded = load_config(path)
    assert loaded.devices[0].ndisplay_node == "N1"
    assert loaded.led_processors[0].options == {"username": "x"}
    assert loaded.to_json() == cfg.to_json()


def test_unknown_and_missing_keys_tolerated():
    data = {"devices": [{"name": "A", "future_field": 1}], "something_new": True}
    cfg = AppConfig.from_json(json.dumps(data))
    assert cfg.devices[0].name == "A"
    assert cfg.devices[0].listener_port == 2980


def test_versions_cover_52_to_58():
    assert SUPPORTED_VERSIONS[0] == "5.2" and SUPPORTED_VERSIONS[-1] == "5.8"
    assert get_version_info("4.27").version == "5.8"


def test_detect_engine_version(tmp_path):
    build = tmp_path / "Engine" / "Build"
    build.mkdir(parents=True)
    (build / "Build.version").write_text(json.dumps({"MajorVersion": 5, "MinorVersion": 4}))
    assert detect_engine_version(str(tmp_path)) == "5.4"
    assert detect_engine_version(str(tmp_path / "nope")) is None


def test_switchboard_import():
    data = {"project_name": "Stage", "uproject": "D:/P/P.uproject", "engine_dir": "C:/UE_5.5",
            "devices": {"Unreal": {"Editor": {"address": "10.0.0.1"}},
                        "nDisplay": {"settings": {"ndisplay_cfg_file": "D:/P/wall.ndisplay"},
                                     "Wall": {"address": "10.0.0.2"}},
                        "LiveLinkFace": {"Phone": {"address": "10.0.0.9"}}}}
    project, devices = parse_switchboard_config(data)
    assert project.uproject_path == "D:/P/P.uproject"
    assert project.ndisplay_config == "D:/P/wall.ndisplay"
    assert [(d.name, d.role) for d in devices] == [("Editor", "editor"), ("Wall", "ndisplay")]
