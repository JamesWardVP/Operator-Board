from operator_board.core.config import AppConfig, ProjectConfig
from operator_board.core.events import EventBus
from operator_board.multiuser import server as mu_server
from operator_board.multiuser.server import MultiUserServer, next_session_name

# The start-up log of a real 5.8 server restarted after a forced stop.
RESTART_LOG = """LogSyncServer: Display: Launch : -CONCERTSERVER=OPB_Probe -UDPMESSAGING_SHARE_KNOWN_NODES=1
LogConcert: Display: Default session repository E79DDC2B445BBF2842F10F84FB6AB1DD set successfully.
LogConcert: Display: Initialized Concert session 'VP_Session' (Id: 5FE174D54AF2E960E43509968BC50D21, Owner: james).
LogConcert: Display: Live session 'VP_Session' (5FE174D54AF2E960E43509968BC50D21) was recovered.
LogSyncServer: Display: Multi-User Editing Server Initialized (Name: OPB_Probe, Version: 5.8, Role: MultiUser)
"""
FRESH_LOG = """LogConcert: Display: Default session repository E79DDC2B445BBF2842F10F84FB6AB1DD set successfully.
LogSyncServer: Display: Multi-User Editing Server Initialized (Name: OPB_Probe, Version: 5.8, Role: MultiUser)
"""


def test_next_session_name():
    assert next_session_name("VP_Session") == "VP_Session_2"
    assert next_session_name("VP_Session_2") == "VP_Session_3"
    assert next_session_name("Day1_07") == "Day1_08"
    assert next_session_name("Shoot9") == "Shoot10"
    assert next_session_name("2026") == "2026_2"
    assert next_session_name("VP_Session", {"VP_Session_2", "VP_Session_3"}) == "VP_Session_4"


def _server(monkeypatch, startup_log):
    cfg = AppConfig()
    cfg.projects = [ProjectConfig(engine_dir="C:/UE/Engine", uproject_path="C:/P/P.uproject")]
    bus = EventBus()
    events = []
    bus.subscribe("multiuser.session_renamed", lambda t, p: events.append(p))
    srv = MultiUserServer(cfg, bus)
    monkeypatch.setattr(srv, "_start_local", lambda exe, args: srv.feed_output(startup_log))
    monkeypatch.setattr(mu_server, "READY_TIMEOUT_S", 0.5)
    return cfg, srv, events


def test_existing_session_starts_a_new_one_with_a_warning(monkeypatch):
    cfg, srv, events = _server(monkeypatch, RESTART_LOG)
    srv.start(session_name="VP_Session")
    assert cfg.multiuser.session_name == "VP_Session_2"
    assert "NEW session" in srv.session_warning and "NOT the previous session" in srv.session_warning
    assert events[0]["reason"] == "exists" and events[0]["new"] == "VP_Session_2"


def test_free_session_name_is_kept(monkeypatch):
    cfg, srv, events = _server(monkeypatch, FRESH_LOG)
    srv.start(session_name="VP_Session")
    assert cfg.multiuser.session_name == "VP_Session" and srv.session_warning == "" and events == []


def test_numbering_off_joins_the_existing_session_and_says_so(monkeypatch):
    cfg, srv, events = _server(monkeypatch, RESTART_LOG)
    cfg.multiuser.auto_increment_session = False
    srv.start(session_name="VP_Session")
    assert cfg.multiuser.session_name == "VP_Session" and "existing session" in srv.session_warning
    srv.stop()
    assert cfg.multiuser.session_name == "VP_Session"


def test_stop_moves_to_the_next_session(monkeypatch):
    cfg, srv, events = _server(monkeypatch, FRESH_LOG)
    srv.start(session_name="VP_Session")
    srv.stop()
    assert cfg.multiuser.session_name == "VP_Session_2" and events[-1]["reason"] == "stopped"
    srv.stop()  # not running: no further change
    assert cfg.multiuser.session_name == "VP_Session_2"


def test_start_action_reports_the_new_session_as_a_warning(monkeypatch):
    from operator_board.context import AppContext
    ctx = AppContext(AppConfig(projects=[ProjectConfig(engine_dir="C:/UE/Engine", uproject_path="C:/P/P.uproject")]))
    monkeypatch.setattr(ctx.multiuser, "_start_local", lambda exe, args: ctx.multiuser.feed_output(RESTART_LOG))
    result = ctx.actions.invoke("multiuser.start", session_name="VP_Session")
    assert not result.ok and "VP_Session_2" in result.message
    result = ctx.actions.invoke("multiuser.stop")
    assert result.ok and "VP_Session_3" in result.message
    ctx.shutdown()
