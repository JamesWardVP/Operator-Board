import json

from fakes import RecordingHTTPServer

from operator_board.core.config import AppConfig, CameraPreset, DeviceConfig, RemoteControlConfig
from operator_board.remote.cameras import CameraService
from operator_board.remote.dlss import DLSSService
from operator_board.remote.objects import extract_value, flatten_preset
from operator_board.remote.rc_client import RESULT_MARKER, RemoteControlClient, python_result_wrapper
from operator_board.remote.targets import TargetResolver


def _cfg(port):
    return AppConfig(devices=[DeviceConfig(name="Ed", address="127.0.0.1", rc_port=port),
                              DeviceConfig(name="N1", address="127.0.0.1", rc_port=port, role="ndisplay")],
                     remote_control=RemoteControlConfig(primary_target="Ed"))


def test_run_python_returns_marked_json():
    def respond(method, path, body):
        return {"ReturnValue": True, "LogOutput": [{"Type": "Info", "Output": "noise"},
                                                   {"Type": "Info", "Output": RESULT_MARKER + json.dumps({"a": 1})}]}
    server = RecordingHTTPServer(respond)
    try:
        client = RemoteControlClient("127.0.0.1", server.port)
        assert client.run_python(python_result_wrapper("result = {'a': 1}")) == {"a": 1}
        method, path, body = server.requests[0]
        assert (method, path) == ("PUT", "/remote/object/call")
        assert body["functionName"] == "ExecutePythonCommandEx"
        compile(body["parameters"]["PythonCommand"], "<rc>", "exec")  # wrapper produces valid Python
    finally:
        server.close()


def test_console_command_and_targets():
    server = RecordingHTTPServer()
    try:
        resolver = TargetResolver(_cfg(server.port))
        assert len(resolver.resolve("all")) == 2 and len(resolver.resolve("ndisplay")) == 1
        resolver.primary().console_command("stat fps")
        assert server.requests[0][2]["parameters"] == {"Command": "stat fps"}
    finally:
        server.close()


def test_camera_recall_on_actor():
    server = RecordingHTTPServer()
    try:
        cfg = _cfg(server.port)
        cfg.cameras = [CameraPreset(name="C1", target="/Game/Map.Map:PersistentLevel.Cam", location=[1, 2, 3],
                                    rotation=[10, 20, 30])]
        assert CameraService(cfg, TargetResolver(cfg)).recall("C1", "all") == []
        body = server.requests[0][2]  # Python, which 5.8 allows where K2_SetActorLocationAndRotation is blocked
        assert body["functionName"] == "ExecutePythonCommandEx"
        script = body["parameters"]["PythonCommand"]
        assert "unreal.Vector(1.0, 2.0, 3.0)" in script and "pitch=10.0, yaw=20.0" in script
    finally:
        server.close()


def test_dlss_function_and_cvar():
    server = RecordingHTTPServer()
    try:
        dlss = DLSSService(TargetResolver(_cfg(server.port)))
        assert dlss.apply("dlss_mode", "Performance", target="primary") == []
        assert dlss.apply("reflex", True, target="primary") == []
        assert server.requests[0][2]["parameters"] == {"DLSSMode": "Performance"}
        assert server.requests[1][2]["parameters"] == {"Command": "r.Streamline.Reflex.Enable 1"}
    finally:
        server.close()


def test_flatten_preset_and_extract_value():
    preset = {"Groups": [{"Name": "Lights", "ExposedProperties": [
        {"DisplayName": "Intensity", "ID": "1", "UnderlyingProperty": {"Type": "float"},
         "OwnerObjects": [{"Path": "/Game/M.M:PersistentLevel.Light"}]}],
        "ExposedFunctions": [{"DisplayName": "Reset", "ID": "2"}]}]}
    rows = flatten_preset(preset)
    assert [(r["kind"], r["label"]) for r in rows] == [("property", "Intensity"), ("function", "Reset")]
    assert extract_value({"PropertyValues": [{"PropertyValue": 5}]}) == 5
    assert extract_value({"Intensity": 2}) == 2


def test_camera_names_are_never_overwritten():
    from operator_board.remote.cameras import unique_name
    assert unique_name("Wide", []) == "Wide"
    assert unique_name("Wide", ["Wide"]) == "Wide_2"
    assert unique_name("Wide", ["Wide", "Wide_2"]) == "Wide_3"
    assert unique_name("Wide_2", ["Wide", "Wide_2"]) == "Wide_3"


def test_camera_scripts_compile():
    from operator_board.remote import scripts
    for script in (scripts.list_camera_targets(), scripts.get_actor_transform("Cine'Cam"),
                   scripts.set_actor_transform("/Game/M.M:PersistentLevel.Cam", [1, 2, 3], [4, 5, 6]),
                   scripts.take_recorder_save("", "Scene's", 3)):
        compile(script, "<editor>", "exec")
