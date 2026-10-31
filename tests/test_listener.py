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


NDISPLAY_JSON = json.dumps({"nDisplay": {
    "version": "5.00", "assetPath": "/Game/Wall.Wall",
    "cluster": {
        "primaryNode": {"id": "N1", "ports": {"ClusterEventsJson": 41003, "ClusterEventsBinary": 41004}},
        "sync": {"renderSyncPolicy": {"type": "Ethernet", "parameters": {}}},
        "nodes": {
            "N1": {"host": "10.0.0.1", "window": {"x": 0, "y": 0, "w": 1920, "h": 1080}, "fullScreen": False},
            "N2": {"host": "10.0.0.2", "window": {"x": 0, "y": 0, "w": 1920, "h": 1080}, "fullScreen": True},
        }}}})


def _ndisplay_file():
    import os
    import tempfile
    path = os.path.join(tempfile.mkdtemp(prefix="opb_nd_"), "wall.ndisplay")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(NDISPLAY_JSON)
    return path


def _config(port):
    cfg = AppConfig()
    cfg.projects = [ProjectConfig(name="P", uproject_path="D:/P/P.uproject", engine_dir="C:/UE",
                                  ndisplay_config=_ndisplay_file())]
    cfg.active_project = "P"
    # FakeListener speaks TCP (the UE 5.2 - 5.3 protocol); "auto" would try QUIC first and wait for it to time out.
    cfg.devices = [DeviceConfig(name="Ed", address="127.0.0.1", listener_port=port, listener_protocol="tcp"),
                   DeviceConfig(name="N1", address="127.0.0.1", listener_port=port, role="ndisplay", ndisplay_node="N1",
                                listener_protocol="tcp")]
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
        # the config was written to the node first, with only our node, pointing at its address
        written = json.loads(listener.files["C:/Temp/ndisplay/4242.ndisplay"])
        assert list(written["nDisplay"]["cluster"]["nodes"]) == ["N1"]
        assert written["nDisplay"]["cluster"]["nodes"]["N1"]["host"] == "127.0.0.1"
        assert '-dc_cfg="C:/Temp/ndisplay/4242.ndisplay"' in start["args"]
        assert "GameEngine=/Script/DisplayCluster.DisplayClusterGameEngine" in start["args"]
        assert "-windowed -forceres WinX=0 WinY=0 ResX=1920 ResY=1080" in start["args"]

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


def test_editor_and_node_on_one_listener_stop_independently():
    """Both devices share a listener, which announces every program to every connection."""
    listener = FakeListener()
    mgr = DeviceManager(_config(listener.port), EventBus())
    try:
        ed, n1 = mgr.get("Ed"), mgr.get("N1")
        mgr.connect("N1")
        mgr.start("Ed")
        assert wait_until(lambda: ed.state == "running", 3), (ed.state, ed.last_error)
        time.sleep(0.3)
        assert n1.state == "connected"  # the editor's announcement is not taken as the node's
        mgr.start("N1")
        assert wait_until(lambda: n1.state == "running", 3), (n1.state, n1.last_error)
        start_ed = next(m for m in listener.received if m["command"] == "start" and m["caller"] == "Ed")

        mgr.stop("Ed")
        assert wait_until(lambda: ed.state == "connected", 3)
        kill = listener.kills[-1]
        assert protocol.same_id(kill["uuid"], start_ed["id"]) and kill["soft_kill_timeout"] == 3.0
        time.sleep(0.3)
        assert n1.state == "running"  # stopping the editor leaves the node alone
    finally:
        mgr.disconnect_all()
        listener.close()
