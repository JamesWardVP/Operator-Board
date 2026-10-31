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


_EMPTY_TAKE = RemoteControlError("TakeRecorderPanel: There are no currently enabled or valid sources to record "
                                 "from. Please add some above before recording.")


def test_empty_take_adds_the_stage_camera_and_records():
    editor = _Editor([_EMPTY_TAKE, {"added": "CineCameraActor1", "why": "the cine camera in the level"},
                      {"ok": True}])
    takes = _takes(editor)
    assert takes.start() == []
    assert takes.recording
    assert "add_source_for_actor" in editor.scripts[1]
    assert takes.notice == "added CineCameraActor1 to Take Recorder (the cine camera in the level)"


def test_take_camera_setting_is_used():
    editor = _Editor([_EMPTY_TAKE, {"added": "Hero_Cam", "why": "the take camera set in Operator Board"},
                      {"ok": True}])
    takes = _takes(editor)
    takes.settings.take_camera = "Hero_Cam"
    takes.start()
    assert "Hero_Cam" in editor.scripts[1]


def test_empty_take_without_auto_camera_explains():
    from operator_board.remote.takes import NO_SOURCES_HELP
    takes = _takes(_Editor([_EMPTY_TAKE]))
    takes.settings.auto_add_camera = False
    assert takes.start() == [f"Take Recorder: {NO_SOURCES_HELP}"] and not takes.recording


def test_no_camera_found_does_not_record():
    takes = _takes(_Editor([_EMPTY_TAKE, RemoteControlError("no nDisplay setup or cine camera found")]))
    errors = takes.start()
    assert "no nDisplay setup or cine camera" in errors[0] and not takes.recording


def test_record_action_reports_the_added_camera():
    from operator_board.context import AppContext
    ctx = AppContext(AppConfig())
    ctx.takes.targets = _Targets(_Editor([_EMPTY_TAKE, {"added": "CineCameraActor1", "why": "x"}, {"ok": True}]))
    result = ctx.actions.invoke("takes.toggle_record")
    assert result.ok and result.message == "Recording: added CineCameraActor1 to Take Recorder (x)"
    assert ctx.actions.invoke("takes.toggle_record").message == "Stopped"
    ctx.shutdown()


def test_long_path_warning_is_reported_but_still_records():
    from operator_board.context import AppContext
    ctx = AppContext(AppConfig())
    ctx.takes.targets = _Targets(_Editor([{"ok": True, "warning": "paths of about 320 characters"}]))
    result = ctx.actions.invoke("takes.toggle_record")
    assert not result.ok and ctx.takes.recording
    assert result.message == "Recording. Warning: paths of about 320 characters"
    ctx.shutdown()


def test_take_recorder_scripts_compile():
    from operator_board.remote import scripts
    for script in (scripts.take_recorder_start("Scene's 1", 3, "a \"b\""), scripts.take_recorder_add_camera("Cam"),
                   scripts.editor_timing()):
        compile(script, "<editor>", "exec")


class _ScriptedEditor:
    """Answers by what the script does, so background watchers can poll in any order."""

    def __init__(self, countdown=0.0, phases=(), sequence="/Game/Takes/Scene_01_01.Scene_01_01", save=None):
        self.countdown = countdown
        self.phases = list(phases)
        self.sequence = sequence
        self.save_reply = save or {}
        self.scripts = []

    def run_python(self, script):
        self.scripts.append(script)
        if "start_recording" in script:
            return {"ok": True, "countdown": self.countdown}
        if "get_active_recorder" in script:
            phase = self.phases.pop(0) if len(self.phases) > 1 else (self.phases[0] if self.phases else "recording")
            return {"phase": phase, "countdown": 1.0, "recording": phase == "recording"}
        if "cancel_recording" in script:
            return {"ok": True}
        if "persist_specified_packages" in script:
            return dict({"sequence": self.sequence, "packages": [], "unsaved": [], "multi_user": False,
                         "persisted": []}, **self.save_reply)
        if "get_last_recorded_level_sequence" in script:
            return {"sequence": self.sequence, "recording": False}
        return {"ok": True}


def test_countdown_is_followed_until_unreal_records():
    from operator_board.listener.client import wait_until
    editor = _ScriptedEditor(countdown=3.0, phases=["counting_down", "counting_down", "recording"])
    takes = _takes(editor)
    assert takes.start() == []
    assert takes.counting_down and takes.recording and 0 < takes.countdown_remaining() <= 3.0
    assert wait_until(lambda: takes.phase == "recording", 5)
    assert takes.countdown_remaining() == 0.0


def test_stop_during_countdown_cancels_without_logging():
    editor = _ScriptedEditor(countdown=3.0, phases=["counting_down"])
    takes = _takes(editor)
    takes.start()
    assert takes.stop() == []
    assert not takes.recording and takes.log.takes == []
    assert any("cancel_recording" in sc for sc in editor.scripts)


def test_countdown_cancelled_in_the_editor_drops_the_take():
    from operator_board.listener.client import wait_until
    takes = _takes(_ScriptedEditor(countdown=3.0, phases=["cancelled"]))
    takes.start()
    assert wait_until(lambda: not takes.recording, 5) and takes.log.takes == []


def test_stopped_take_remembers_its_sequence_and_saves():
    from operator_board.listener.client import wait_until
    editor = _ScriptedEditor(save={"multi_user": True, "persisted": ["/Game/Takes/Scene_01_01"]})
    takes = _takes(editor)
    takes.start()
    takes.stop()
    record = takes.log.takes[-1]
    assert wait_until(lambda: record.sequence == editor.sequence, 5)
    message = takes.save_take()
    assert record.saved == "persisted" and "multi-user" in message
    assert editor.sequence in editor.scripts[-1]


def test_save_reports_files_unreal_could_not_save():
    import pytest
    editor = _ScriptedEditor(save={"unsaved": ["/Game/Takes/Scene_01_01.Scene_01_01"]})
    takes = _takes(editor)
    takes.start()
    takes.stop()
    with pytest.raises(RuntimeError, match="could not save"):
        takes.save_take()
    assert takes.log.takes[-1].saved == ""


def test_new_take_recorder_scripts_compile():
    from operator_board.remote import scripts
    for script in (scripts.take_recorder_status(), scripts.take_recorder_cancel(), scripts.take_recorder_save(),
                   scripts.take_recorder_save("/Game/T/A.A"), scripts.take_recorder_last_sequence()):
        compile(script, "<editor>", "exec")
