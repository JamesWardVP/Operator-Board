import json
import time

from fakes import FakeListener

from operator_board.core.config import AppConfig, DeviceConfig, GroupConfig, ProjectConfig
from operator_board.core.events import EventBus
from operator_board.devices.manager import DeviceManager
from operator_board.listener import protocol
from operator_board.listener.client import wait_until


def test_message_buffer_handles_fragments():
    buf = protocol.MessageBuffer()
    payload = protocol.start_process("a.exe", "-x", "unreal", "OB")[1]
    assert buf.feed(payload[:10]) == []
    msgs = buf.feed(payload[10:] + b'{"command": "keepalive"}\x00garbage\x00')
    assert [m["command"] for m in msgs] == ["start", "keepalive"]


def test_start_message_matches_switchboard_fields():
    _id, payload = protocol.start_process("a.exe", "-x", "unreal", "OB", update_clients_with_stdout=True)
    msg = json.loads(payload[:-1])
    for key in ("exe", "args", "name", "caller", "working_dir", "bUpdateClientsWithStdout", "id"):
        assert key in msg
    assert payload.endswith(b"\x00")


def _config(port):
    cfg = AppConfig()
    cfg.projects = [ProjectConfig(name="P", uproject_path="D:/P/P.uproject", engine_dir="C:/UE", ndisplay_config="D:/w.ndisplay")]
    cfg.active_project = "P"
    cfg.devices = [DeviceConfig(name="Ed", address="127.0.0.1", listener_port=port),
                   DeviceConfig(name="N1", address="127.0.0.1", listener_port=port, role="ndisplay", ndisplay_node="N1")]
    cfg.groups = [GroupConfig(name="Nodes", devices=["N1"])]
    return cfg


def test_device_lifecycle_against_fake_listener():
    listener = FakeListener()
    bus = EventBus()
    states = []
    bus.subscribe("device.state", lambda t, p: states.append((p["name"], p["state"])))
    mgr = DeviceManager(_config(listener.port), bus)
    try:
        mgr.start_group("Nodes")
        n1 = mgr.get("N1")
        assert wait_until(lambda: n1.state == "running", 3)
        assert mgr.get("Ed").state == "disconnected"
        start = next(m for m in listener.received if m["command"] == "start")
        assert "-dc_node=N1" in start["args"] and start["name"] == "unreal"

        metrics = []
        bus.subscribe("device.metrics", lambda t, p: metrics.append(p))
        mgr.request_metrics()
        assert wait_until(lambda: metrics, 3)
        assert metrics[0]["metrics"]["cpuUtilization"] == 42.0

        mgr.stop("N1")
        assert wait_until(lambda: n1.state == "connected", 3)
        assert ("N1", "running") in states
        time.sleep(1.3)  # at least one keepalive
        assert listener.unknown == []  # every command is one the real listener understands
    finally:
        mgr.disconnect_all()
        listener.close()


def test_unreachable_listener_reports_error():
    mgr = DeviceManager(_config(1), EventBus())
    mgr.connect("Ed")
    rt = mgr.get("Ed")
    assert rt.state == "error" and "Cannot reach listener" in rt.last_error


def test_connect_and_disconnect_one_device_by_action():
    from operator_board.context import AppContext
    listener = FakeListener()
    ctx = AppContext(_config(listener.port))
    try:
        ok = ctx.actions.invoke("devices.connect", device="N1")
        assert ok.ok and ctx.devices.get("N1").state == "connected"
        assert ctx.devices.get("Ed").state == "disconnected"  # only the chosen device
        assert ctx.actions.invoke("devices.disconnect", device="N1").ok
        rt = ctx.devices.get("N1")
        assert rt.state == "disconnected" and rt.client is None
    finally:
        ctx.shutdown()
        listener.close()
