import pytest

from fakes import RecordingHTTPServer

from operator_board.core.config import AppConfig, LEDProcessorConfig
from operator_board.core.events import EventBus
from operator_board.led.manager import LEDManager
from operator_board.led.processor import LEDProcessor, SimulatorTransport, novastar_checksum, scale_value
from operator_board.led.profiles import ProfileRegistry
from operator_board.led.transports import TransportError, encode_osc

REG = ProfileRegistry()


def frame(proc, name, value=None):
    return bytes(proc.prepare(proc._resolve(name), value)["bytes"])


def test_all_builtin_profiles_load():
    ids = set(REG.profiles)
    assert {"brompton_tessera", "megapixel_helios", "novastar_mctrl", "novastar_vx", "simulator",
            "generic_http", "generic_tcp", "generic_udp", "generic_osc"} <= ids


@pytest.mark.parametrize("hex_frame", [
    # Frames published in the Bitfocus Companion novastar-controller module (checksum = last 2 bytes)
    "55 AA 00 80 FE 00 01 FF FF FF 01 00 02 01 00 02 01 00 FF D7 5A",
    "55 AA 00 80 FE 00 01 FF FF FF 01 00 00 01 00 02 01 00 FF D5 5A",
    "55 AA 00 00 FE 00 00 00 00 00 01 00 50 00 20 02 01 00 01 C8 56",
    "55 AA 00 00 FE FF 01 FF FF FF 01 00 01 00 00 02 01 00 FF 54 5B",
])
def test_novastar_checksum_matches_companion(hex_frame):
    data = bytes(int(b, 16) for b in hex_frame.split())
    assert novastar_checksum(data[:-2]) == data[-2:]


def test_novastar_profile_frames():
    p = LEDProcessor(LEDProcessorConfig(profile_id="novastar_mctrl"), REG.get("novastar_mctrl"), SimulatorTransport())
    assert frame(p, "freeze_on")[-2:].hex() == "d75a"
    assert frame(p, "blackout_on")[-2:].hex() == "d55a"
    assert frame(p, "freeze_off") == frame(p, "normal")
    assert frame(p, "brightness", "FF")[-3:].hex() == "ff545b"
    red = bytes(p.prepare(p.test_patterns[0]["commands"][0])["bytes"])
    assert red[-2:].hex() == "5959"


def test_scale_value():
    assert scale_value(100, {"min": 0, "max": 255, "format": "hex_byte"}) == "FF"
    assert scale_value(50, {"min": 0, "max": 10000}) == 5000
    assert scale_value(150, {"min": 0, "max": 100}) == 100


def test_novastar_freeze_and_black_are_exclusive():
    p = LEDProcessor(LEDProcessorConfig(profile_id="novastar_vx"), REG.get("novastar_vx"), SimulatorTransport())
    p.set_freeze(True)
    p.set_blackout(True)
    assert p.state.blackout and not p.state.frozen


def test_brompton_http_requests():
    server = RecordingHTTPServer()
    try:
        cfg = LEDProcessorConfig(name="B", profile_id="brompton_tessera", host="127.0.0.1", port=server.port)
        p = LEDProcessor(cfg, REG.get("brompton_tessera"))
        p.set_blackout(True)
        p.set_test_pattern("red")
        p.set_brightness(50)
        paths = [r[1] for r in server.requests]
        assert paths == ["/api/override/blackout/enabled?set=true",
                         "/api/override/test-pattern/type?set=red",
                         "/api/override/test-pattern/enabled?set=true",
                         "/api/output/global-colour/brightness?set=5000"]
    finally:
        server.close()


def test_helios_patch_bodies():
    server = RecordingHTTPServer()
    try:
        cfg = LEDProcessorConfig(name="H", profile_id="megapixel_helios", host="127.0.0.1", port=server.port)
        p = LEDProcessor(cfg, REG.get("megapixel_helios"))
        p.set_blackout(True)
        p.set_brightness(40)
        assert server.requests[0] == ("PATCH", "/api/v1/public", {"dev": {"display": {"blackout": True}}})
        assert server.requests[1][2] == {"dev": {"display": {"brightness": 40}}}
    finally:
        server.close()


def test_command_override_wins():
    cfg = LEDProcessorConfig(profile_id="generic_http", command_overrides={
        "freeze_on": {"method": "POST", "path": "/freeze"}, "freeze_off": {"method": "POST", "path": "/unfreeze"}})
    p = LEDProcessor(cfg, REG.get("generic_http"), SimulatorTransport())
    assert p.capabilities() == ["freeze"]
    assert p.confidence("freeze_on") == "user"
    p.set_freeze(True)
    assert p.transport.sent[-1] == {"method": "POST", "path": "/freeze"}


def test_unsupported_command_raises():
    p = LEDProcessor(LEDProcessorConfig(profile_id="generic_tcp"), REG.get("generic_tcp"), SimulatorTransport())
    with pytest.raises(TransportError):
        p.set_freeze(True)


def test_unreachable_processor_reports_offline():
    cfg = LEDProcessorConfig(profile_id="megapixel_helios", host="127.0.0.1", port=1)
    p = LEDProcessor(cfg, REG.get("megapixel_helios"))
    with pytest.raises(TransportError):
        p.set_blackout(True)
    assert p.state.online is False and not p.state.blackout


def test_manager_fan_out_and_restore():
    cfg = AppConfig(led_processors=[LEDProcessorConfig(name="A", profile_id="simulator"),
                                    LEDProcessorConfig(name="B", profile_id="simulator"),
                                    LEDProcessorConfig(name="Off", profile_id="simulator", enabled=False)])
    events = []
    bus = EventBus()
    bus.subscribe("led.state", lambda t, p: events.append(p))
    mgr = LEDManager(cfg, bus, REG)
    assert mgr.set_freeze() == [] and mgr.frozen
    assert mgr.set_blackout(True) == [] and mgr.blackout
    assert mgr.set_test_pattern("red") == [] and mgr.test_pattern == "red"
    assert not mgr.get("Off").state.frozen
    assert mgr.restore_all() == []
    assert not (mgr.frozen or mgr.blackout or mgr.test_pattern)
    assert events[-1] == {"frozen": False, "blackout": False, "test_pattern": None}


def test_manager_with_no_processors_reports_error():
    mgr = LEDManager(AppConfig(), EventBus(), REG)
    assert mgr.set_freeze(True)


def test_osc_encoding():
    assert encode_osc("/a", [1]) == b"/a\x00\x00,i\x00\x00\x00\x00\x00\x01"
