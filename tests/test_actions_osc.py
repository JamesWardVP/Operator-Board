"""OSC messages and multi-step buttons, Remote Control ports per PC, and camera recall without an editor."""
import json
import os
import socket
import time

import pytest
from fakes import RecordingHTTPServer

from operator_board.context import AppContext
from operator_board.core.config import (AppConfig, CameraPreset, DeviceConfig, LEDProcessorConfig, ProjectConfig,
                                        RemoteControlConfig)
from operator_board.devices.launcher import unreal_launch_spec
from operator_board.devices.manager import unique_rc_ports
from operator_board.remote import scripts
from operator_board.remote.cameras import CameraService
from operator_board.remote.rc_client import RESULT_MARKER
from operator_board.remote.targets import TargetResolver
from operator_board.surfaces.osc import parse_message
from operator_board.surfaces.osc_send import osc_args, send_osc


def test_osc_argument_types():
    assert osc_args("none", "x") == []
    assert osc_args("int", " 42 ") == [42] and osc_args("float", "0.5") == [0.5]
    assert osc_args("string", "hello there") == ["hello there"] and osc_args("bool", "false") == [False]
    assert osc_args("multi", '1 2.5 "two words" true go') == [1, 2.5, "two words", True, "go"]
    with pytest.raises(ValueError):
        osc_args("int", "abc")
    with pytest.raises(ValueError):
        osc_args("multi", '"unclosed')


def test_osc_message_is_sent_over_udp():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(3)
    try:
        message = send_osc("127.0.0.1", sock.getsockname()[1], "/cue/7/go", "multi", '3 "a b"')
        assert "/cue/7/go" in message
        assert parse_message(sock.recvfrom(1024)[0]) == ("/cue/7/go", [3, "a b"])
        with pytest.raises(ValueError, match="start with /"):
            send_osc("127.0.0.1", 9, "cue", "none")
    finally:
        sock.close()


@pytest.fixture
def ctx():
    cfg = AppConfig(led_processors=[LEDProcessorConfig(name="Sim", profile_id="simulator")])
    cfg.surfaces.streamdeck_enabled = False
    c = AppContext(cfg)
    yield c
    c.shutdown()


def test_several_actions_run_in_order_with_delays(ctx):
    started = time.time()
    result = ctx.actions.invoke("macro.run", steps=[
        {"action": "led.freeze", "params": {}}, {"delay": 200}, {"action": "no.such_action"},
        {"action": "takes.next_take"}, {"action": "macro.run", "params": {"steps": []}}])
    assert time.time() - started >= 0.2
    assert not result.ok and "2 of 4 steps done" in result.message
    assert "step 3" in result.message and "step 5" in result.message
    assert ctx.config.takes.take == 2


def test_each_unreal_instance_on_a_pc_gets_its_own_remote_control_port():
    devices = [DeviceConfig(name="Editor_1", address="127.0.0.1"), DeviceConfig(name="Node_0", address="localhost"),
               DeviceConfig(name="Node_1", address="10.0.0.5"), DeviceConfig(name="Node_2", address="10.0.0.5"),
               DeviceConfig(name="Off", address="127.0.0.1", rc_enabled=False)]
    messages = unique_rc_ports(devices)
    assert [d.rc_port for d in devices[:4]] == [30010, 30011, 30010, 30011]
    assert len(messages) == 2 and "Node_0" in messages[0]
    assert unique_rc_ports(devices) == []  # nothing more to change


def test_launch_sets_the_remote_control_port_as_a_setting():
    project = ProjectConfig(uproject_path="D:/P/P.uproject", engine_dir="C:/UE_5.8/Engine")
    local = unreal_launch_spec(DeviceConfig(name="N", rc_port=30011), project).args
    assert "RemoteControlHttpServerPort=30011" in local and "RemoteControlWebSocketServerPort=30021" in local
    assert "-RCWebControlPort" not in local and "DefaultBindAddress" not in local
    remote = unreal_launch_spec(DeviceConfig(name="N", address="10.0.0.5"), project).args
    assert "[HTTPServer.Listeners]:DefaultBindAddress=0.0.0.0" in remote  # reachable from this PC


def test_python_literals():
    assert scripts._lit(True) == "True" and scripts._lit(None) == "None"
    assert scripts._lit({"a": [False, 1.5, "x"]}) == '{"a": [False, 1.5, "x"]}'
    code = scripts.set_actor_quick_control("/Game/M.M:PersistentLevel.L", "visible", False)
    assert "control, value = \"visible\", False" in code
    compile("import unreal\n" + code, "<script>", "exec")


def test_camera_recall_falls_back_to_the_nodes_without_an_editor():
    def respond(method, path, body):
        return {"ReturnValue": True, "LogOutput": [{"Output": RESULT_MARKER + json.dumps({"ok": True})}]}
    node = RecordingHTTPServer(respond)
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    free_port = closed.getsockname()[1]
    closed.close()  # nothing listens there: the editor is not running
    try:
        cfg = AppConfig(devices=[DeviceConfig(name="Ed", address="127.0.0.1", rc_port=free_port),
                                 DeviceConfig(name="N1", role="ndisplay", address="127.0.0.1", rc_port=node.port)],
                        remote_control=RemoteControlConfig(primary_target="Ed", timeout_s=1.0),
                        cameras=[CameraPreset(name="Wide", target="/Game/M.M:PersistentLevel.nDisplayConfig_C_1",
                                              location=[1, 2, 3], rotation=[0, 90, 0])])
        cams = CameraService(cfg, TargetResolver(cfg))
        assert cams.recall("Wide") == []
        assert "set_actor_location_and_rotation" in node.requests[0][2]["parameters"]["PythonCommand"]
        cfg.cameras.append(CameraPreset(name="View", target="viewport"))
        with pytest.raises(RuntimeError, match="needs the editor"):
            cams.recall("View")
    finally:
        node.close()


def test_osc_and_steps_editors():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt = pytest.importorskip("operator_board.qt", exc_type=ImportError)
    from operator_board.ui.action_editors import OscEditor, StepsEditor
    app = qt.QtWidgets.QApplication.instance() or qt.QtWidgets.QApplication([])
    osc = OscEditor()
    osc.set_params({"host": "10.0.0.9", "port": 53000, "address": "/go", "type": "float", "value": "0.75"})
    assert osc.value_stack.currentWidget() is osc.float_value
    assert osc.params() == {"host": "10.0.0.9", "port": 53000, "address": "/go", "type": "float", "value": "0.75"}
    osc.kind.setCurrentIndex(osc.kind.findData("bool"))
    assert osc.value_stack.currentWidget() is osc.bool_value and osc.params()["value"] is True
    osc.address.setText("go")
    with pytest.raises(ValueError):
        osc.params()

    ctx = AppContext(AppConfig())
    steps = StepsEditor(ctx.actions)
    steps.add_combo.setCurrentIndex(steps.add_combo.findData("led.freeze"))
    steps.add_combo.setCurrentIndex(steps.add_combo.findData("__delay__"))
    steps.add_combo.setCurrentIndex(steps.add_combo.findData("takes.next_take"))
    assert steps.add_combo.currentIndex() == 0
    assert [s.get("action", "delay") for s in steps.steps()] == ["led.freeze", "delay", "takes.next_take"]
    rows = steps._row_widgets()
    steps.move(rows[2], -1)  # next take before the delay
    steps.duplicate(steps._row_widgets()[0])
    steps.remove(steps._row_widgets()[2])
    assert [s.get("action", "delay") for s in steps.steps()] == ["led.freeze", "takes.next_take", "led.freeze"]
    assert steps.add_combo.findData("macro.run") < 0  # no steps inside steps
    ctx.shutdown()
    app.processEvents()


def test_unreachable_listener_on_this_pc_is_reported(ctx):
    from operator_board.core.config import ProjectConfig
    events = []
    ctx.bus.subscribe("device.listener_unreachable", lambda t, p: events.append(p))
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()
    ctx.config.projects = [ProjectConfig(ue_version="5.3")]  # TCP: refused at once
    ctx.config.devices = [DeviceConfig(name="Local", address="127.0.0.1", listener_port=port)]
    ctx.devices.reload()
    ctx.devices.connect("Local")
    assert events and events[0]["name"] == "Local" and events[0]["local"] is True


def test_listener_prompt_starts_it_once(ctx, monkeypatch, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt = pytest.importorskip("operator_board.qt", exc_type=ImportError)
    from operator_board.ui import main_window
    app = qt.QtWidgets.QApplication.instance() or qt.QtWidgets.QApplication([])
    window = main_window.MainWindow(ctx)
    asked = []
    monkeypatch.setattr(qt.QtWidgets.QMessageBox, "question",
                        lambda *a, **k: (asked.append(a[2]), qt.QtWidgets.QMessageBox.No)[1])
    payload = {"name": "Node_0", "address": "127.0.0.1", "port": 2980, "local": True}
    window._on_event("device.listener_unreachable", payload)
    window._on_event("device.listener_unreachable", dict(payload, name="Editor_1"))  # declined: not again soon
    window._on_event("device.listener_unreachable", dict(payload, name="Far", local=False))
    assert len(asked) == 1 and "Node_0" in asked[0]
    window.skip_save_on_close = True
    window.close()
    app.processEvents()


def test_actor_browser_uses_the_nodes_without_an_editor():
    from operator_board.remote.objects import ObjectService
    from operator_board.remote.rc_client import RemoteControlError

    def respond(method, path, body):
        return {"ReturnValue": True, "LogOutput": [{"Output": RESULT_MARKER + json.dumps(
            {"actors": [{"label": "RectLight", "path": "/Game/M.M:PersistentLevel.RectLight", "class": "RectLight"}],
             "ok": True, "world": "game"})}]}
    nodes = [RecordingHTTPServer(respond), RecordingHTTPServer(respond)]
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    free_port = closed.getsockname()[1]
    closed.close()
    try:
        cfg = AppConfig(devices=[DeviceConfig(name="Ed", address="127.0.0.1", rc_port=free_port)] +
                        [DeviceConfig(name=f"N{i}", role="ndisplay", address="127.0.0.1", rc_port=n.port)
                         for i, n in enumerate(nodes)],
                        remote_control=RemoteControlConfig(primary_target="Ed", timeout_s=1.0))
        objs = ObjectService(cfg, TargetResolver(cfg))
        assert objs.actors("Light")[0]["label"] == "RectLight"
        assert len(nodes[0].requests) == 1 and not nodes[1].requests  # a read: the first node only
        objs.set_quick_control("/Game/M.M:PersistentLevel.RectLight", "visible", False)
        assert len(nodes[0].requests) == 2 and len(nodes[1].requests) == 1  # a change: every node
        cfg.devices = cfg.devices[:1]  # no nodes either
        with pytest.raises(RemoteControlError, match="No editor is running, and no nDisplay node"):
            objs.actors()
    finally:
        for n in nodes:
            n.close()


def _engine(tmp_path, name, major, minor):
    root = tmp_path / name
    (root / "Engine" / "Build").mkdir(parents=True)
    (root / "Engine" / "Build" / "Build.version").write_text(json.dumps({"MajorVersion": major,
                                                                          "MinorVersion": minor}))
    (root / "Engine" / "Binaries" / "Win64").mkdir(parents=True)
    return root


def test_listener_from_another_engine_is_spotted(ctx, tmp_path, monkeypatch):
    from operator_board.listener import local
    old = _engine(tmp_path, "UE_5.6", 5, 6)
    new = _engine(tmp_path, "UE_5.8", 5, 8)
    running = local.RunningListener(4242, str(old / "Engine" / "Binaries" / "Win64" / "SwitchboardListener.exe"))
    assert running.engine_dir == str(old) and running.version == "5.6"
    monkeypatch.setattr(local, "running_listeners", lambda: [running])
    ctx.config.projects[0].engine_dir = str(new / "Engine")
    assert ctx.check_local_listener()["status"] == "no_devices"  # nothing on this PC uses a listener
    ctx.config.devices = [DeviceConfig(name="Ed", address="127.0.0.1")]
    result = ctx.check_local_listener()
    assert result["status"] == "mismatch" and result["wanted_version"] == "5.8"
    assert result["running"][0]["version"] == "5.6"
    running.path = str(new / "Engine" / "Binaries" / "Win64" / "SwitchboardListener.exe")
    assert ctx.check_local_listener()["status"] == "ok"
    monkeypatch.setattr(local, "running_listeners", lambda: [])
    assert ctx.check_local_listener()["status"] == "none"


def test_process_path_lookup_on_windows():
    from operator_board.listener import local
    if os.name != "nt":
        pytest.skip("Windows only")
    assert local._image_path(os.getpid()).lower().endswith("python.exe")
    assert local.running_listeners() == [] or all(l.pid for l in local.running_listeners())
