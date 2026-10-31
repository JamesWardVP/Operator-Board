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
        reply = self.replies.pop(0)
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
