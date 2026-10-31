from operator_board.remote.timing import (EditorTiming, TimingMonitor, format_timecode, genlock_from_metrics,
                                          genlock_summary, parse_editor_timing)


def _sample(**kw):
    base = {"timecode": [10, 59, 59, 23], "rate": [24, 1], "drop_frame": False, "time_step": "", "time_step_fps": 0}
    base.update(kw)
    return base


def _topo(synced=(True,), house=True, source=1):
    return {"syncTopos": [{"syncGpus": [{"bIsSynced": s} for s in synced],
                           "syncStatusParams": {"bHouseSync": house, "houseSyncIncoming": 240000,
                                                "refreshRate": 600000},
                           "syncControlParams": {"source": source}}]}


def test_zero_or_missing_timecode_is_no_data():
    assert parse_editor_timing(_sample(timecode=[0, 0, 0, 0])) is None
    assert parse_editor_timing(_sample(rate=[0, 1])) is None
    assert parse_editor_timing({"oops": 1}) is None
    assert parse_editor_timing(None) is None


def test_timecode_ticks_on_between_polls():
    t = parse_editor_timing(_sample(), now=100.0)
    assert format_timecode(t, 100.0) == "10:59:59:23"
    assert format_timecode(t, 100.0 + 1 / 24 + 1e-6) == "11:00:00:00"
    assert format_timecode(t, 102.0) == "11:00:01:23"


def test_stale_sample_is_no_data():
    class _Targets:
        def primary(self):
            raise LookupError("no editor")

    class _Devices:
        def running(self):
            return []

    mon = TimingMonitor(_Targets(), _Devices())
    assert mon.poll() is None and mon.timecode() is None and mon.genlock() == (None, None)
    mon.editor = EditorTiming(1, 2, 3, 4, 24, 1, sampled=0.0)
    assert mon.timecode(now=1.0) == "01:02:04:04" and mon.timecode(now=10.0) is None


def test_genlock_from_listener_sync_data():
    assert genlock_from_metrics({}) is None
    assert genlock_from_metrics({"syncTopos": []}) is None
    g = genlock_from_metrics(_topo())
    assert g["locked"] and g["source"] == "House" and abs(g["house_fps"] - 24.0) < 1e-9
    assert genlock_summary([("Node1", _topo()), ("Node2", _topo())]) == ("Locked (all machines, house 24.000)", True)
    assert genlock_summary([("Node1", _topo()), ("Node2", _topo(synced=(True, False)))]) == ("NOT LOCKED: Node2", False)
    assert genlock_summary([("Editor", {"CpuUtilization": 3})]) == (None, None)


def test_editor_time_step_is_shown_when_no_machine_reports_sync():
    t = parse_editor_timing(_sample(time_step="Blackmagic", time_step_fps=24.0))
    assert genlock_summary([], t) == ("Editor time step: Blackmagic 24 fps", None)
    assert genlock_summary([], parse_editor_timing(_sample())) == (None, None)
