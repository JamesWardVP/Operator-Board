import subprocess
import time

from fakes import FakeListener

from operator_board.analytics.metrics import MetricStore, normalise_metrics
from operator_board.core.actions import ActionRegistry
from operator_board.core.config import AppConfig, DeviceConfig, MultiUserConfig, SyncJobConfig
from operator_board.core.events import EventBus
from operator_board.multiuser.server import ClientTracker
from operator_board.remote.takes import TakeManager
from operator_board.sync.manager import SyncManager
from operator_board.sync.providers import PROVIDERS


def test_client_tracker_counts_joins_and_leaves():
    mu = MultiUserConfig()
    t = ClientTracker(mu.client_join_pattern, mu.client_leave_pattern)
    lines = ["LogConcert: Display: User 'Editor_1' (Endpoint: 1A2B) joined the session 'VP_Session'",
             "LogConcert: Display: Client 'Node_Wall_L' has connected",
             "LogConcert: Display: Server started",
             "LogConcert: Display: User 'Editor_1' (Endpoint: 1A2B) left the session 'VP_Session'"]
    changes = [t.feed_line(l) for l in lines]
    assert changes == [True, True, False, True]
    assert t.clients == {"Node_Wall_L"}


def test_sync_provider_commands():
    exe, args = PROVIDERS["freefilesync"].build({"exe": "ffs.exe", "batch_file": "job.ffs_batch"})
    assert (exe, args) == ("ffs.exe", ["job.ffs_batch"])
    assert PROVIDERS["perforce"].build({"depot_path": "//d/...", "client": "ws"})[1] == ["-c", "ws", "sync", "//d/..."]
    assert PROVIDERS["robocopy"].interpret(3)[0] and not PROVIDERS["robocopy"].interpret(8)[0]
    assert PROVIDERS["freefilesync"].interpret(1) == (True, "Synchronization completed with warnings")


def test_sync_local_and_remote():
    listener = FakeListener()
    try:
        cfg = AppConfig(devices=[DeviceConfig(name="N1", address="127.0.0.1", listener_port=listener.port)],
                        sync_jobs=[SyncJobConfig(name="J", provider="git", params={"repo_dir": "D:/p"},
                                                 targets=["local", "N1"])])
        calls = []

        def runner(exe, args):
            calls.append((exe, args))
            return subprocess.CompletedProcess(args, 0, "Already up to date.", "")
        mgr = SyncManager(cfg, EventBus(), runner)
        results = {r.target: r for r in mgr.run("J")}
        assert calls == [("git", ["-C", "D:/p", "pull"])]
        assert results["local"].ok and results["N1"].ok
        start = next(m for m in listener.received if m["command"] == "start")
        assert start["exe"] == "git" and start["name"] == "operatorboard_sync"
    finally:
        listener.close()


def test_take_manager_log_and_increment(tmp_path):
    cfg = AppConfig()
    cfg.takes.record_with_take_recorder = False
    tm = TakeManager(cfg, EventBus())
    tm.set_slate("Scene_02", take=1)
    tm.start()
    time.sleep(0.01)
    tm.stop()
    assert cfg.takes.take == 2 and tm.log.takes[0].slate == "Scene_02"
    tm.rate_last("good")
    reloaded = TakeManager(cfg, EventBus())
    assert reloaded.log.takes[0].rating == "good"
    tm.set_slate("Scene_02")
    assert cfg.takes.take == 2  # next take after the logged take 1
    tm.export_csv(str(tmp_path / "t.csv"))
    assert "Scene_02" in (tmp_path / "t.csv").read_text()


def test_metrics_normalise_and_store(tmp_path):
    m = normalise_metrics({"cpuUtilization": 30, "gpuUtilization": [80, 60], "gpuCoreClocksKhz": [1500000],
                           "flipModeHistory": ["x"], "bIsSynced": True})
    assert m["CPU %"] == 30 and m["GPU %"] == 70 and m["GPU clock (MHz)"] == 1500
    assert "bIsSynced" not in m
    store = MetricStore()
    store.add_raw("N1", {"cpuUtilization": 10})
    assert store.latest("N1", "CPU %") == 10
    assert store.export_csv(str(tmp_path / "m.csv")) == 1


def test_action_registry():
    reg = ActionRegistry()
    reg.add("x.ok", "OK", "Test", lambda value=1: value * 2)
    reg.add("x.bad", "Bad", "Test", lambda: 1 / 0)
    assert reg.invoke("x.ok", value=4).data == 8
    assert not reg.invoke("x.bad").ok
    assert not reg.invoke("missing").ok
