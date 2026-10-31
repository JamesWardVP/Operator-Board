"""Runs sync jobs locally or via listeners and keeps a result history."""
from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from operator_board.core.config import AppConfig, SyncJobConfig
from operator_board.core.events import EventBus
from operator_board.listener import protocol
from operator_board.listener.client import client_for_device, decode_stdout
from operator_board.sync.providers import PROVIDERS


@dataclass
class SyncResult:
    job: str
    target: str
    ok: bool
    message: str
    started: float
    finished: float
    output: str = ""


class SyncManager:
    def __init__(self, config: AppConfig, bus: EventBus,
                 runner: Optional[Callable[[str, List[str]], "subprocess.CompletedProcess"]] = None) -> None:
        self.config = config
        self.bus = bus
        self.history: List[SyncResult] = []
        self.running: Dict[str, bool] = {}
        self._runner = runner or self._run_local

    def job(self, name: str) -> SyncJobConfig:
        job = next((j for j in self.config.sync_jobs if j.name == name), None)
        if job is None:
            raise KeyError(f"No sync job named '{name}'")
        return job

    @staticmethod
    def _run_local(exe: str, args: List[str]) -> "subprocess.CompletedProcess":
        return subprocess.run([exe] + args, capture_output=True, text=True, errors="replace", timeout=6 * 3600)

    def run(self, name: str) -> List[SyncResult]:
        job = self.job(name)
        provider = PROVIDERS[job.provider]
        exe, args = provider.build(job.params)
        self.running[name] = True
        self.bus.publish("sync.state", job=name, running=True)
        results: List[SyncResult] = []
        threads = []
        for target in job.targets or ["local"]:
            t = threading.Thread(target=lambda tg=target: results.append(self._run_target(job, tg, exe, args)), daemon=True)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        self.history.extend(results)
        del self.history[:-200]
        self.running[name] = False
        self.bus.publish("sync.state", job=name, running=False, results=[(r.target, r.ok, r.message) for r in results])
        return results

    def run_async(self, name: str) -> None:
        threading.Thread(target=self.run, args=(name,), daemon=True).start()

    def _run_target(self, job: SyncJobConfig, target: str, exe: str, args: List[str]) -> SyncResult:
        provider = PROVIDERS[job.provider]
        started = time.time()
        try:
            if target == "local":
                proc = self._runner(exe, args)
                code, output = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
            else:
                code, output = self._run_remote(target, exe, args)
            ok, message = provider.interpret(code)
        except FileNotFoundError:
            ok, message, output = False, f"Executable not found: {exe}", ""
        except Exception as exc:
            ok, message, output = False, str(exc), ""
        return SyncResult(job.name, target, ok, message, started, time.time(), output[-5000:])

    def _run_remote(self, device_name: str, exe: str, args: List[str]):
        dev = self.config.device(device_name)
        if dev is None:
            raise KeyError(f"Unknown device '{device_name}'")
        done = threading.Event()
        state = {"code": -1, "out": []}

        caller = f"OperatorBoard_Sync_{device_name}"

        def on_msg(msg):
            if protocol.program_info(msg).get("caller") not in (None, caller):
                return  # another program on the same listener
            if msg.get("command") == "programstdout":
                state["out"].append(decode_stdout(msg))
            elif msg.get("command") == "program ended":
                state["code"] = int(msg.get("returncode", -1))
                state["out"].append(decode_stdout(msg))
                done.set()
            elif msg.get("command") == "start" and msg.get("bAck") is False:
                state["out"].append(str(msg.get("error", "listener refused")))
                done.set()

        client = client_for_device(dev, self.config.project().ue_version, on_message=on_msg)
        client.connect()
        try:
            quoted = " ".join(f'"{a}"' if " " in a else a for a in args)
            client.start_process(exe=exe, args=quoted, name="operatorboard_sync", caller=caller,
                                 update_clients_with_stdout=True)
            if not done.wait(6 * 3600):
                raise TimeoutError("Sync job timed out")
        finally:
            client.disconnect("user disconnect")
        return state["code"], "".join(state["out"])
