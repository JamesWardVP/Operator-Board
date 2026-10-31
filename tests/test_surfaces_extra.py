"""Web panel, OSC and MIDI control surfaces; camera CSV; take descriptions; editor-ready events."""
import json
import socket
import struct
import time
import urllib.request

import pytest

from operator_board.core.actions import ActionRegistry, ActionResult
from operator_board.core.config import AppConfig, CameraPreset
from operator_board.core.events import EventBus
from operator_board.surfaces.api_server import ApiServer
from operator_board.surfaces.controller import SurfaceController
from operator_board.surfaces.layout import ButtonBinding, SurfaceLayout, SurfacePage, midi_key_for_note
from operator_board.surfaces.osc import OscServer, parse_message


def _surface():
    bus = EventBus()
    actions = ActionRegistry(bus)
    pressed = []
    actions.add("test.go", "Go", "Test", lambda: (pressed.append(1), ActionResult(True, "went"))[1],
                state=lambda: bool(pressed))
    layout = SurfaceLayout(model="custom", custom_columns=3, custom_rows=2,
                           pages=[SurfacePage(name="Main", buttons={"4": ButtonBinding(action="test.go", label="GO")}),
                                  SurfacePage(name="Second")])
    return SurfaceController(layout, actions, bus), pressed


def _osc(address, *args):
    def pad(b):
        return b + b"\0" * (4 - len(b) % 4)
    tags, payload = ",", b""
    for a in args:
        if isinstance(a, int):
            tags, payload = tags + "i", payload + struct.pack(">i", a)
        elif isinstance(a, float):
            tags, payload = tags + "f", payload + struct.pack(">f", a)
        else:
            tags, payload = tags + "s", payload + pad(a.encode())
    return pad(address.encode()) + pad(tags.encode()) + payload


def test_web_panel_shows_and_presses_keys():
    surface, pressed = _surface()
    api = ApiServer(surface.actions, lambda: {}, "127.0.0.1", 0, token="t0k", surface=surface)
    api.start()
    base = f"http://127.0.0.1:{api.port}"
    try:
        html = urllib.request.urlopen(base + "/panel").read().decode()
        assert "OPERATOR BOARD" in html and "/api/surface/press/" in html
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/api/surface")  # no token
        state = json.loads(urllib.request.urlopen(base + "/api/surface?token=t0k").read())
        assert (state["columns"], state["rows"], len(state["keys"])) == (3, 2, 6)
        assert state["keys"][4]["label"] == "GO" and not state["keys"][0]["assigned"]
        req = urllib.request.Request(base + "/api/surface/press/4?token=t0k", method="POST")
        assert json.loads(urllib.request.urlopen(req).read())["message"] == "went" and pressed
        assert json.loads(urllib.request.urlopen(base + "/api/surface?token=t0k").read())["keys"][4]["active"]
        req = urllib.request.Request(base + "/api/surface/page/next?token=t0k", method="POST")
        assert json.loads(urllib.request.urlopen(req).read())["message"] == "Page: Second"
    finally:
        api.stop()


def test_osc_messages_are_decoded():
    assert parse_message(_osc("/operator/key", 4)) == ("/operator/key", [4])
    address, args = parse_message(_osc("/operator/action/test.go", "a=1", 0.5))
    assert address == "/operator/action/test.go" and args[0] == "a=1" and abs(args[1] - 0.5) < 1e-6
    assert parse_message(b"#bundle\0") is None and parse_message(b"garbage") is None


def test_osc_server_presses_keys_over_udp():
    surface, pressed = _surface()
    server = OscServer(surface, port=0, bind="127.0.0.1")
    server.start()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(_osc("/operator/key/4", 0.0), ("127.0.0.1", server.port))  # a release: ignored
        sock.sendto(_osc("/operator/key", 4), ("127.0.0.1", server.port))
        deadline = time.time() + 3
        while not pressed and time.time() < deadline:
            time.sleep(0.05)
        assert pressed == [1]
        server.handle("/operator/page", ["next"])
        assert surface.current_page.name == "Second"
    finally:
        server.stop()


def test_midi_pads_map_to_keys():
    assert midi_key_for_note("midi_apc_mini", 8, 8, 56) == 0 and midi_key_for_note("midi_apc_mini", 8, 8, 7) == 63
    assert midi_key_for_note("midi_launchpad", 8, 8, 81) == 0 and midi_key_for_note("midi_launchpad", 8, 8, 9) is None
    assert midi_key_for_note("custom", 3, 2, 36, base_note=36) == 3  # bottom-left key of a 3 x 2 grid


def test_midi_note_presses_the_key():
    from operator_board.surfaces import midi
    surface, pressed = _surface()
    backend = midi.MidiBackend(surface, surface.bus)

    class Msg:
        type, note, velocity = "note_on", 37, 100   # custom 3 x 2 from note 36: bottom row 36-38
    backend._on_message(Msg())
    deadline = time.time() + 3
    while not pressed and time.time() < deadline:
        time.sleep(0.05)
    assert pressed == [1]


def test_camera_positions_round_trip_through_csv(tmp_path):
    from operator_board.remote.cameras import CameraService
    cfg = AppConfig(cameras=[CameraPreset(name="Wide", target="viewport", location=[1, 2, 3], rotation=[4, 5, 6])])
    cams = CameraService(cfg, None)
    path = str(tmp_path / "cams.csv")
    assert cams.export_csv(path) == 1
    added = cams.import_csv(path)
    assert [c.name for c in cfg.cameras] == ["Wide", "Wide_2"]
    assert added[0].location == [1.0, 2.0, 3.0] and added[0].rotation == [4.0, 5.0, 6.0]
    (tmp_path / "bad.csv").write_text("name,x\nCam,abc\n")
    with pytest.raises(ValueError, match="line 2"):
        cams.import_csv(str(tmp_path / "bad.csv"))


def test_take_descriptions_can_be_changed():
    from operator_board.remote.takes import TakeManager
    takes = TakeManager(AppConfig(), EventBus())
    takes.set_description("Wide, slow push")
    assert takes.settings.description == "Wide, slow push"
    takes.start()
    takes.set_description("Wide, fast push")
    takes.stop()
    assert takes.log.takes[-1].description == "Wide, fast push"
    takes.describe_take(0, "Keeper")
    assert takes.log.takes[0].description == "Keeper"


def test_editor_ready_events():
    from operator_board.remote.editor_watch import EditorWatcher
    bus = EventBus()
    seen = []
    for topic in ("editor.ready", "editor.level_changed", "editor.lost"):
        bus.subscribe(topic, lambda t, p: seen.append(t))
    watch = EditorWatcher(None, bus)
    watch.update({"ready": False, "level": ""})  # still loading
    watch.update({"ready": True, "level": "/Game/A"})
    watch.update({"ready": True, "level": "/Game/A"})
    watch.update({"ready": True, "level": "/Game/B"})
    watch.update(None)
    assert seen == ["editor.ready", "editor.level_changed", "editor.lost"]
