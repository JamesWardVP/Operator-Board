"""Multi-user auto start, blackout confirmation and the multi-user refusal message."""
import json
import threading
import time

from fakes import FakeListener
from test_listener import _config

from operator_board.context import AppContext
from operator_board.core.config import AppConfig
from operator_board.listener.client import wait_until


def _states(ctx):
    return [(d.name, d.state, d.last_error) for d in ctx.devices.devices()]


def _ctx_with_fake_mu(listener, auto_start=True):
    cfg = _config(listener.port)
    cfg.multiuser.auto_start = auto_start
    ctx = AppContext(cfg)
    calls = []

    def fake_start(*_a, **_k):
        time.sleep(0.2)  # long enough for parallel starts to overlap
        calls.append(threading.current_thread().name)
        ctx.multiuser.running = True

    ctx.multiuser.start = fake_start
    return ctx, calls


def test_auto_start_multiuser_once_before_unreal():
    listener = FakeListener()
    ctx, calls = _ctx_with_fake_mu(listener)
    try:
        ctx.devices.start_all()
        assert wait_until(lambda: all(d.state == "running" for d in ctx.devices.devices()), 5), _states(ctx)
        assert len(calls) == 1  # one server, even though two devices started in parallel
    finally:
        ctx.shutdown()
        listener.close()


def test_no_auto_start_when_off_or_not_joining():
    listener = FakeListener()
    ctx, calls = _ctx_with_fake_mu(listener, auto_start=False)
    try:
        ctx.devices.start("Ed")
        assert wait_until(lambda: ctx.devices.get("Ed").state == "running", 5), _states(ctx)
        ctx.config.multiuser.auto_start = True
        ctx.config.device("N1").join_multiuser = False
        ctx.devices.start("N1")
        assert wait_until(lambda: ctx.devices.get("N1").state == "running", 5), _states(ctx)
        assert calls == []
    finally:
        ctx.shutdown()
        listener.close()


def test_blackout_confirmation_default_and_migration():
    assert AppConfig().ui.confirm_blackout is True
    old = json.dumps({"schema_version": 1, "ui": {"confirm_blackout": False}})
    assert AppConfig.from_json(old).ui.confirm_blackout is True  # old default switched on once
    chosen = json.dumps({"schema_version": 2, "ui": {"confirm_blackout": False}})
    assert AppConfig.from_json(chosen).ui.confirm_blackout is False  # a later choice is kept


def test_blackout_confirm_follows_setting_and_state():
    from operator_board.core.config import LEDProcessorConfig
    ctx = AppContext(AppConfig(led_processors=[LEDProcessorConfig(name="Sim", profile_id="simulator")]))
    action = ctx.actions.get("led.blackout")
    assert action.needs_confirm()
    ctx.config.ui.confirm_blackout = False
    assert not action.needs_confirm()  # read live, not only at start-up
    ctx.config.ui.confirm_blackout = True
    ctx.actions.invoke("led.blackout_on")
    assert ctx.led.blackout
    assert not action.needs_confirm()  # restoring the picture needs no confirmation
    assert ctx.actions.get("led.blackout_on").needs_confirm() is False
    ctx.shutdown()


def test_multiuser_project_refusal_is_explained():
    ctx = AppContext(AppConfig())
    ctx.multiuser.feed_output("[2026.10.31-00.40.10:642][116]LogConcert: Display: Refusing user james to join "
                              "session MU_1 (Id: 6A36, Owner: james, Reason: Invalid project name (expected '', "
                              "got 'UpdatedMyProjectV2'))")
    assert "UpdatedMyProjectV2" in ctx.multiuser.last_error and "Clean history" in ctx.multiuser.last_error
    ctx.shutdown()


def test_set_scene_and_take_actions_for_surfaces_and_companion():
    ctx = AppContext(AppConfig())
    ctx.config.takes.record_with_take_recorder = False
    assert ctx.actions.invoke("takes.set_slate", slate="Scene_40").ok
    assert ctx.actions.invoke("takes.set_take", take="123").ok  # Companion sends query values as text
    assert (ctx.config.takes.slate, ctx.config.takes.take) == ("Scene_40", 123)
    ctx.shutdown()
