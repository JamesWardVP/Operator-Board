"""Remote Control security switches (UE 5.2 - 5.8) and Take Recorder start/stop."""
import io
import urllib.error

import pytest

from operator_board.core.config import AppConfig, DeviceConfig, ProjectConfig
from operator_board.core.events import EventBus
from operator_board.devices.launcher import REMOTE_CONTROL_UNLOCK, unreal_launch_spec
from operator_board.remote import rc_client
from operator_board.remote.rc_client import RemoteControlClient, RemoteControlError
from operator_board.remote.takes import TakeManager

PROJECT = ProjectConfig(uproject_path="D:/P/P.uproject", engine_dir="C:/UE_5.8")


def test_launch_allows_remote_python_console_and_functions():
    args = unreal_launch_spec(DeviceConfig(name="Ed"), PROJECT).args
    assert REMOTE_CONTROL_UNLOCK in args
    for switch in ("bEnableRemotePythonExecution=True", "bAllowConsoleCommandRemoteExecution=True",
                   "bAllowAnyRemoteFunctionCall=True"):
        assert switch in args
    assert "-ini:RemoteControl:[/Script/RemoteControlCommon.RemoteControlSettings]" in args
    assert " " not in REMOTE_CONTROL_UNLOCK  # one command-line argument, no quoting needed


def test_launch_without_unlock_when_turned_off_or_no_remote_control():
    assert REMOTE_CONTROL_UNLOCK not in unreal_launch_spec(DeviceConfig(name="Ed"), PROJECT,
                                                           allow_remote_features=False).args
    assert REMOTE_CONTROL_UNLOCK not in unreal_launch_spec(DeviceConfig(name="Ed", rc_enabled=False), PROJECT).args


@pytest.mark.parametrize("reply", [
    '{ "errorMessage": "Object Default__PythonScriptLibrary cannot be accessed remotely, check remote control '
    'project settings." }',
    '{ "errorMessage": "Executing function \'KismetSystemLibrary ExecuteConsoleCommand\' is not allowed by remote '
    'control settings." }',
])
def test_blocked_call_explains_the_fix(monkeypatch, reply):
    def refuse(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(reply.encode()))
    monkeypatch.setattr(rc_client.urllib.request, "urlopen", refuse)
    with pytest.raises(RemoteControlError) as info:
        RemoteControlClient("127.0.0.1").run_python("print(1)")
    assert "Project Settings > Plugins > Remote Control > Security" in str(info.value)
    assert "started from Operator Board" in str(info.value)


class _Editor:
    def __init__(self, replies):
        self.replies = list(replies)
        self.scripts = []

    def run_python(self, script):
        self.scripts.append(script)
        reply = self.replies.pop(0) if self.replies else {"ok": True}
        if isinstance(reply, Exception):
            raise reply
        return reply


class _Targets:
    def __init__(self, editor):
        self.editor = editor

    def primary(self):
        return self.editor


def _takes(editor):
    cfg = AppConfig()
    cfg.takes.record_with_take_recorder = True
    return TakeManager(cfg, EventBus(), _Targets(editor))


def test_take_recorder_waits_for_a_freshly_opened_panel(monkeypatch):
    monkeypatch.setattr("operator_board.remote.takes.time.sleep", lambda s: None)
    editor = _Editor([{"ok": False, "retry": True}, {"ok": True}])
    takes = _takes(editor)
    assert takes.start() == []
    assert takes.recording and len(editor.scripts) == 2


def test_failed_take_recorder_start_does_not_record():
    takes = _takes(_Editor([RemoteControlError("blocked")]))
    errors = takes.start()
    assert errors and "blocked" in errors[0]
    assert not takes.recording and takes.log.takes == []


def test_python_error_inside_unreal_is_a_failure(monkeypatch):
    reply = {"ReturnValue": True, "LogOutput": [{"Output": 'OPB_RESULT:{"error": "TakeRecorderPanel: boom"}'}]}
    monkeypatch.setattr(RemoteControlClient, "call", lambda self, *a, **k: reply)
    with pytest.raises(RemoteControlError, match="boom"):
        RemoteControlClient("127.0.0.1").run_python("x")


def test_no_sources_is_explained_and_nothing_records():
    from operator_board.remote.takes import NO_SOURCES_HELP
    no_sources = RemoteControlError("TakeRecorderPanel: There are no currently enabled or valid sources to record "
                                    "from. Please add some above before recording.")
    takes = _takes(_Editor([no_sources]))
    errors = takes.start()
    assert errors == [f"Take Recorder: {NO_SOURCES_HELP}"]
    assert not takes.recording


def test_take_preset_is_loaded_before_recording():
    editor = _Editor([{"ok": True}, {"ok": True}])
    takes = _takes(editor)
    takes.settings.take_preset = "/Game/Takes/StagePreset"
    assert takes.start() == []
    assert "setup_for_recording_take_preset" in editor.scripts[0] and "/Game/Takes/StagePreset" in editor.scripts[0]
    assert "start_recording" in editor.scripts[1]


def test_take_changes_are_shown_in_the_editor_but_not_while_recording():
    from operator_board.listener.client import wait_until
    editor = _Editor([])
    takes = _takes(editor)
    takes.set_take(7)
    assert wait_until(lambda: any("set_take_number(7)" in sc for sc in editor.scripts), 2)
    takes.start()
    count = len(editor.scripts)
    takes.increment(1)
    import time
    time.sleep(0.2)
    assert len(editor.scripts) == count  # no metadata changes during a recording


def test_quick_take_changes_end_on_the_latest_value():
    """Several quick Take + presses must leave the editor on the last number, not an older one."""
    import threading
    import time
    from operator_board.listener.client import wait_until

    class SlowEditor(_Editor):
        def run_python(self, script):
            time.sleep(0.05)  # a real editor takes a moment, so presses overlap
            return super().run_python(script)

    editor = SlowEditor([])
    takes = _takes(editor)
    threads = [threading.Thread(target=takes.increment, args=(1,)) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    final = takes.settings.take
    assert wait_until(lambda: not takes._sync_running, 3)
    assert f"set_take_number({final})" in editor.scripts[-1]
