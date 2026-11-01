"""Builds every service and registers the operator actions shared by UI, surfaces and API."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from operator_board.analytics.metrics import MetricStore
from operator_board.core.actions import ActionParam, ActionRegistry, ActionResult
from operator_board.core.changelog import COLUMNS, ChangeLog, ChangeRecord, export_workbook, show
from operator_board.core.config import AppConfig
from operator_board.core.events import EventBus
from operator_board.core.stage import ConfigStore, copy_into
from operator_board.devices.manager import DeviceManager
from operator_board.led.manager import LEDManager
from operator_board.multiuser.server import MultiUserServer
from operator_board.remote.cameras import CameraService
from operator_board.remote.dlss import DLSSService
from operator_board.remote.levels import LevelInfo, LevelService
from operator_board.remote.objects import ObjectService
from operator_board.remote.takes import TakeManager
from operator_board.remote.timing import TimingMonitor
from operator_board.remote.editor_watch import EditorWatcher
from operator_board.remote.targets import TargetResolver
from operator_board.surfaces.api_server import ApiServer
from operator_board.surfaces.manager import SurfaceManager
from operator_board.sync.manager import SyncManager

log = logging.getLogger(__name__)

MACRO_ACTION = "macro.run"


def _errors_result(label: str, errors: List[str]) -> ActionResult:
    return ActionResult(not errors, label if not errors else f"{label}: " + "; ".join(errors))


class AppContext:
    def __init__(self, config: Optional[AppConfig] = None, config_path: Optional[str] = None) -> None:
        self.config_path = config_path
        self.store = ConfigStore(config_path)
        self.config = config or self.store.load()
        self.bus = EventBus()
        self.actions = ActionRegistry(self.bus)
        self.targets = TargetResolver(self.config)
        self.devices = DeviceManager(self.config, self.bus)
        self.devices.before_start = self._auto_start_multiuser
        self._mu_lock = threading.Lock()
        self.multiuser = MultiUserServer(self.config, self.bus)
        self.led = LEDManager(self.config, self.bus)
        self.levels = LevelService(self.config, self.targets)
        self.cameras = CameraService(self.config, self.targets)
        self.objects = ObjectService(self.config, self.targets)
        self.dlss = DLSSService(self.targets)
        self.takes = TakeManager(self.config, self.bus, self.targets)
        self.timing = TimingMonitor(self.targets, self.devices)
        self.editor = EditorWatcher(self.targets, self.bus)
        self.sync = SyncManager(self.config, self.bus)
        self.metrics = MetricStore()
        self.level_cache: List[LevelInfo] = []
        self.selected_level = 0
        self.surfaces = SurfaceManager(self.config, self.actions, self.bus)
        self.api: Optional[ApiServer] = None
        self.streamdeck = None
        self.osc = None
        self.midi: List[Any] = []  # one MidiBackend per MIDI surface with a controller found
        self._hardware = True
        self._scan_lock = threading.Lock()
        self._midi_inputs: List[str] = []
        self._stop = threading.Event()
        self.changes = ChangeLog(self._change_context)
        self._session_start = len(self.changes.records)  # changes before this index are from earlier runs
        self.changes.listeners.append(lambda rec: self.bus.publish("changes.recorded", category=rec.category))
        self.bus.subscribe("action.invoked", lambda _t, p: self._log_action(p))
        self.bus.subscribe("multiuser.session_renamed", lambda _t, _p: self._save_quietly())
        self.bus.subscribe("device.metrics", lambda _t, p: self.metrics.add_raw(p["name"], p.get("metrics") or {}))
        self._register_actions()

    # ---- lifecycle ------------------------------------------------------------
    @property
    def surface(self):
        """The first control surface (older code and tests talk to one surface)."""
        return self.surfaces.primary

    def start_services(self, enable_hardware: bool = True) -> None:
        s = self.config.surfaces
        self._hardware = enable_hardware
        if s.api_server_enabled:
            try:
                self.api = ApiServer(self.actions, self.state_snapshot, s.api_bind, s.api_port, s.api_token,
                                     surface=self.surfaces)
                self.api.start()
            except OSError as exc:
                log.warning("API server could not start on %s:%s: %s", s.api_bind, s.api_port, exc)
                self.api = None
        if enable_hardware and s.streamdeck_enabled:
            from operator_board.surfaces.streamdeck import StreamDeckBackend
            if StreamDeckBackend.available():
                self.streamdeck = StreamDeckBackend(self.surfaces, self.bus, s.streamdeck_brightness)
                self.streamdeck.start()
        self.start_surface_backends(enable_hardware)
        self.bus.subscribe("surface.list_changed", lambda _t, _p: self._restart_midi())
        threading.Thread(target=self._metrics_loop, daemon=True, name="metrics-poll").start()
        threading.Thread(target=self._led_monitor, daemon=True, name="led-monitor").start()
        self.refresh_levels_in_background()
        self.timing.start()
        self.editor.start()

    def start_surface_backends(self, enable_hardware: Optional[bool] = None) -> None:
        """The OSC listener (when switched on) and a MIDI input for every MIDI surface (restartable)."""
        if enable_hardware is not None:
            self._hardware = enable_hardware
        self.stop_surface_backends()
        s = self.config.surfaces
        if s.osc_enabled:
            from operator_board.surfaces.osc import OscServer
            try:
                self.osc = OscServer(self.surfaces, s.osc_port)
                self.osc.start()
            except OSError as exc:
                log.warning("OSC could not listen on UDP %s: %s", s.osc_port, exc)
                self.osc = None
        self._start_midi()

    def _start_midi(self) -> None:
        """MIDI surfaces: each takes its chosen input, or the next input no other surface uses. Custom
        surfaces use MIDI only when an input is chosen for them."""
        if not self._hardware:
            return
        from operator_board.surfaces.midi import MidiBackend
        if not MidiBackend.available():
            return
        wanted = [c for c in self.surfaces.of_kind("midi", "custom")
                  if c.layout.kind == "midi" or c.layout.device]
        taken: List[str] = []
        for controller in sorted(wanted, key=lambda c: not c.layout.device):  # named inputs first
            backend = MidiBackend(controller, self.bus, controller.layout.device, taken)
            if backend.start():
                taken.append(backend.opened)
                self.midi.append(backend)

    def _stop_midi(self) -> None:
        for backend in self.midi:
            backend.stop()
        self.midi = []

    def _restart_midi(self) -> None:
        self._stop_midi()
        self._start_midi()

    def midi_for(self, controller):
        return next((m for m in self.midi if m.controller is controller), None)

    def refresh_surface_devices(self, only_if_changed: bool = False) -> str:
        """Look for control surfaces again (plugged in or unplugged since the last look): Stream
        Decks and MIDI controllers. Called by Refresh devices and when Windows reports a device change
        (``only_if_changed``: MIDI inputs are reopened only when the list of inputs changed)."""
        from operator_board.surfaces.midi import input_names
        with self._scan_lock:
            s = self.config.surfaces
            decks = 0
            if self._hardware and s.streamdeck_enabled:
                from operator_board.surfaces.streamdeck import StreamDeckBackend
                if self.streamdeck is None and StreamDeckBackend.available():
                    self.streamdeck = StreamDeckBackend(self.surfaces, self.bus, s.streamdeck_brightness)
                    decks = self.streamdeck.start()
                elif self.streamdeck is not None:
                    decks = self.streamdeck.scan()
            inputs = input_names()
            if not only_if_changed or inputs != self._midi_inputs:
                self._restart_midi()
            self._midi_inputs = inputs
        self.bus.publish("surface.devices")
        midi = len(self.midi)
        return (f"Found {decks} Stream Deck{'' if decks == 1 else 's'} and {midi} MIDI controller"
                f"{'' if midi == 1 else 's'} in use")

    def stop_surface_backends(self) -> None:
        if self.osc is not None:
            self.osc.stop()
        self.osc = None
        self._stop_midi()

    def restart_api(self) -> str:
        """Restart the HTTP API / web panel with the current settings; returns an error or ''."""
        s = self.config.surfaces
        if self.api:
            self.api.stop()
            self.api = None
        if not s.api_server_enabled:
            return ""
        try:
            self.api = ApiServer(self.actions, self.state_snapshot, s.api_bind, s.api_port, s.api_token,
                                 surface=self.surfaces)
            self.api.start()
        except OSError as exc:
            self.api = None
            return f"The web panel / API could not start on {s.api_bind}:{s.api_port}: {exc}"
        return ""

    def shutdown(self) -> None:
        self._stop.set()
        self.stop_surface_backends()
        self.timing.stop()
        self.editor.stop()
        if self.api:
            self.api.stop()
        if self.streamdeck:
            self.streamdeck.stop()
        self.devices.disconnect_all()
        for p in self.led.processors:
            p.close()

    def start_local_listener(self, engine_dir: str = "") -> str:
        """Start SwitchboardListener on this PC from the project's engine (unless one is already
        running); returns what happened. It keeps running when Operator Board closes."""
        import os
        import subprocess
        from operator_board.devices.launcher import engine_binary
        engine_dir = engine_dir or self.config.project().engine_dir
        if not engine_dir:
            raise RuntimeError("The project has no engine folder set (Stage page), so SwitchboardListener "
                               "cannot be found")
        exe = engine_binary(engine_dir, "SwitchboardListener", "Win64")
        if not os.path.isfile(exe):
            raise RuntimeError(f"SwitchboardListener was not found at {exe}")
        if os.name == "nt":
            running = subprocess.run(["tasklist", "/FI", "IMAGENAME eq SwitchboardListener.exe", "/NH"],
                                     capture_output=True, text=True, creationflags=0x08000000).stdout
            if "SwitchboardListener.exe" in running:
                return "SwitchboardListener is already running on this PC"
            flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            subprocess.Popen([exe], cwd=os.path.dirname(exe), creationflags=flags, close_fds=True)
        else:
            subprocess.Popen([exe], cwd=os.path.dirname(exe), start_new_session=True)
        log.info("Started %s", exe)
        return f"Started SwitchboardListener ({exe})"

    def reconnect_local_devices(self, names: List[str], wait_s: float = 20.0) -> List[str]:
        """Try ``names`` again until each connects or ``wait_s`` passes (a listener needs a few seconds
        to start). Returns the names that connected."""
        connected: List[str] = []
        deadline = time.time() + wait_s
        pending = list(names)
        while pending and time.time() < deadline and not self._stop.is_set():
            self._stop.wait(2.0)
            for name in list(pending):
                try:
                    self.devices.connect(name)
                except Exception:  # noqa: BLE001 - reported on the device itself
                    continue
                rt = self.devices.get(name)
                if rt and rt.client and rt.client.connected:
                    connected.append(name)
                    pending.remove(name)
                elif rt and not (rt.last_error or "").startswith("Cannot reach listener"):
                    pending.remove(name)  # it answered (e.g. it wants the password): the prompt takes over
        return connected

    # ---- change log, undo and production data ---------------------------------------------
    def _change_context(self) -> Dict[str, Any]:
        t = self.config.takes
        return {"slate": t.slate, "take": t.take, "timecode": self.timing.timecode() or "",
                "project": self.config.active_project, "level": getattr(self.editor, "level", "") or ""}

    NOT_LOGGED = ("surface.",)

    def _log_action(self, payload: Dict[str, Any]) -> None:
        """Every action run from a button, key, surface or the API goes into the change log."""
        action_id = payload.get("action_id", "")
        if any(action_id.startswith(p) for p in self.NOT_LOGGED):
            return
        params = payload.get("params") or {}
        if action_id == MACRO_ACTION:  # its steps are logged one by one
            params = {"steps": len(params.get("steps") or [])}
        item = ", ".join(show(v) for k, v in params.items() if k not in ("steps",)) or payload.get("category", "")
        self.changes.record(payload.get("category", ""), item, payload.get("label", action_id),
                            params or "", None, result="ok" if payload.get("ok") else f"failed: {payload.get('message', '')}")

    def apply_setting(self, kind: str, ref: Dict[str, Any], value: Any) -> None:
        """Apply a Remote Objects value again (Undo, presets). Raises when Unreal did not take it."""
        if kind == "quick":
            self.objects.set_quick_control(ref["path"], ref["control"], value)
        elif kind == "property":
            errors = self.objects.set_property(ref["path"], ref["prop"], value, ref.get("target", "primary"))
            if errors:
                raise RuntimeError("; ".join(errors))
        elif kind == "preset":
            errors = self.objects.set_preset_value(ref["preset"], ref["label"], value, ref.get("target", "primary"))
            if errors:
                raise RuntimeError("; ".join(errors))
        else:
            raise ValueError(f"Cannot apply a '{kind}' setting")

    def session_changes(self) -> List[ChangeRecord]:
        return self.changes.records[self._session_start:]

    def changed_settings(self) -> List[Dict[str, Any]]:
        """The latest value of every Remote Objects setting changed since Operator Board started
        (what "Export changes" and a preset's Remote Objects item hold)."""
        latest: Dict[tuple, ChangeRecord] = {}
        for rec in self.session_changes():
            if rec.kind and rec.result == "ok":
                latest[rec.key] = rec
        return [{"kind": r.kind, "ref": r.ref, "item": r.item, "setting": r.setting, "value": r.value}
                for r in latest.values()]

    def production_export(self, path: str) -> None:
        """A spreadsheet for production: the change log, takes, camera positions, LED processors,
        devices and the stage summary, one sheet each."""
        import datetime
        from operator_board import __version__
        changes = [[show(getattr(r, key)) if key in ("value", "previous") else getattr(r, key) for key, _ in COLUMNS]
                   for r in self.changes.records]
        takes = []
        for t in self.takes.log.takes:
            start = datetime.datetime.fromtimestamp(t.started) if t.started else None
            takes.append([t.slate, t.take, t.description, t.level, start.strftime("%Y-%m-%d") if start else "",
                          start.strftime("%H:%M:%S") if start else "",
                          datetime.datetime.fromtimestamp(t.stopped).strftime("%H:%M:%S") if t.stopped else "",
                          round(t.duration_s, 1), t.rating, t.notes, t.sequence, t.saved])
        cameras = [[c.name, c.target] + [round(float(v), 4) for v in list(c.location) + list(c.rotation)]
                   for c in self.config.cameras]
        leds = []
        for cfg in self.config.led_processors:
            p = self.led.get(cfg.name)
            prof = self.led.registry.get(cfg.profile_id) or {}
            leds.append([cfg.name, prof.get("name", cfg.profile_id), cfg.host, "yes" if cfg.enabled else "no",
                         p.mode if p else "", (p.state.test_pattern or "") if p else "",
                         ", ".join(g.name for g in self.config.led_groups if cfg.name in g.processors)])
        devices = [[d.name, d.role, d.address, d.rc_port, "yes" if d.enabled else "no"] for d in self.config.devices]
        mu = self.config.multiuser
        now = datetime.datetime.now()
        summary = [["Exported", now.strftime("%Y-%m-%d %H:%M:%S")], ["Timecode", self.timing.timecode() or ""],
                   ["Project", self.config.active_project], ["Level", getattr(self.editor, "level", "") or ""],
                   ["Switchboard config", self.config.switchboard.active_config],
                   ["Scene (slate)", self.config.takes.slate], ["Next take", self.config.takes.take],
                   ["Multi-user server", mu.server_name], ["Multi-user session", mu.session_name],
                   ["Operator Board", __version__]]
        export_workbook(path, [
            ("Summary", ["Field", "Value"], summary),
            ("Change log", [title for _k, title in COLUMNS], changes),
            ("Takes", ["Slate", "Take", "Description", "Level", "Date", "Start", "End", "Duration (s)", "Rating",
                       "Notes", "Level sequence", "Saved"], takes),
            ("Camera positions", ["Name", "Target", "X", "Y", "Z", "Pitch", "Yaw", "Roll"], cameras),
            ("LED processors", ["Name", "Type", "Address", "Enabled", "Mode", "Test pattern", "Groups"], leds),
            ("Unreal instances", ["Name", "Role", "Address", "Remote Control port", "Enabled"], devices),
        ])

    def run_steps(self, steps: List[Dict[str, Any]]) -> ActionResult:
        """Run a button's steps in order: {"action": id, "params": {...}} or {"delay": milliseconds}.
        A failed step is reported and the rest still run, as in Companion."""
        done, errors = 0, []
        for n, step in enumerate(steps, start=1):
            if "delay" in step:
                self._stop.wait(max(0.0, float(step.get("delay") or 0)) / 1000.0)
                continue
            action_id = step.get("action", "")
            if action_id == MACRO_ACTION:
                errors.append(f"step {n}: a multiple-actions step cannot contain another")
                continue
            result = self.actions.invoke(action_id, **(step.get("params") or {}))
            if result.ok:
                done += 1
            else:
                errors.append(f"step {n}: {result.message}")
        message = f"{done} of {sum(1 for s in steps if 'delay' not in s)} steps done"
        return ActionResult(not errors, message + ("; " + "; ".join(errors) if errors else ""))

    def _auto_start_multiuser(self, device) -> None:
        """Start the multi-user server before an editor or node that joins it (setting on by default)."""
        mu = self.config.multiuser
        if not (mu.auto_start and device.join_multiuser):
            return
        with self._mu_lock:  # "Start all" launches devices in parallel
            if self.multiuser.running:
                return
            try:
                self.multiuser.start()
                log.info("Started the multi-user server before %s", device.name)
            except Exception as exc:  # noqa: BLE001 - Unreal still starts; it retries joining
                log.warning("Could not start the multi-user server automatically: %s", exc)

    def save(self) -> str:
        self.surfaces.store()
        return self.store.save(self.config)

    def _save_quietly(self) -> None:
        """Keep a setting changed by Operator Board itself (the next session name) across restarts."""
        try:
            self.save()
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not save the configuration: %s", exc)

    # ---- stage (Switchboard config) switching ----------------------------------------------
    @property
    def switchboard_config(self) -> str:
        return self.config.switchboard.active_config

    def _switch_to(self, new: AppConfig) -> None:
        copy_into(self.config, new)
        self.surfaces.load()
        if self.streamdeck:
            self.streamdeck.assign()
        self._restart_midi()
        self.multiuser.tracker = self.multiuser._new_tracker()
        self.level_cache = []
        self.selected_level = 0
        self.reload_all()
        self.bus.publish("project.changed", project=self.config.active_project)
        self.bus.publish("stage.changed", switchboard_config=self.switchboard_config)
        self.refresh_levels_in_background()

    def refresh_levels_in_background(self) -> None:
        """Fill the level list for the current project without the operator pressing refresh."""
        def work() -> None:
            try:
                self.refresh_levels()
            except Exception as exc:  # noqa: BLE001 - no project on disk and no editor yet is normal
                log.debug("Level list not refreshed: %s", exc)
        threading.Thread(target=work, daemon=True, name="levels-refresh").start()

    def open_switchboard_config(self, path: str) -> None:
        if self.switchboard_config:
            self.save()
        self._switch_to(self.store.open_switchboard(self.config, path))

    def new_switchboard_config(self, path: str, project_name: str, uproject: str, engine_dir: str,
                               copy_current: bool = True) -> None:
        if self.switchboard_config:
            self.save()
        self._switch_to(self.store.create_switchboard(self.config, path, project_name, uproject, engine_dir,
                                                      copy_current))

    def save_switchboard_config_as(self, path: str) -> None:
        self._switch_to(self.store.save_as_switchboard(self.config, path))

    def close_switchboard_config(self) -> None:
        self._switch_to(self.store.close_switchboard(self.config))

    def reload_all(self) -> None:
        """Re-read configuration-driven services after the config was edited."""
        self.devices.reload()
        self.led.reload()
        self.bus.publish("config.changed")

    LED_CHECK_S = 15.0

    def _led_monitor(self) -> None:
        """Check LED processors at start-up, then while any is not confirmed reachable, so the
        Operate page can show when the wall cannot be controlled (and clear it when it can)."""
        first = True
        while first or not self._stop.wait(self.LED_CHECK_S):
            first = False
            try:
                if any(p.state.online is not True for p in self.led.active()):
                    self.led.check_all()
            except Exception:
                log.exception("LED connection check failed")

    def _metrics_loop(self) -> None:
        while not self._stop.wait(max(0.5, self.config.ui.metrics_poll_s)):
            try:
                self.devices.request_metrics()
                now = time.time()
                self.metrics.add("Stage", "Running instances", len(self.devices.running()), now)
                self.metrics.add("Stage", "Multi-user clients", self.multiuser.client_count, now)
                for rt in self.devices.running():
                    self.metrics.add(rt.name, "Uptime (min)", rt.uptime_s / 60.0, now)
            except Exception:
                log.exception("metrics poll failed")

    def state_snapshot(self) -> Dict[str, Any]:
        return {
            "wall": {"frozen": self.led.frozen, "blackout": self.led.blackout, "test_pattern": self.led.test_pattern},
            "takes": {"slate": self.config.takes.slate, "take": self.config.takes.take, "recording": self.takes.recording},
            "multiuser": {"running": self.multiuser.running, "server": self.multiuser.server_name,
                          "clients": self.multiuser.clients},
            "devices": {d.name: d.state for d in self.devices.devices()},
            "project": self.config.active_project,
            "selected_level": self.selected_level_path(),
        }

    # ---- level helpers ------------------------------------------------------------
    def refresh_levels(self) -> List[LevelInfo]:
        self.level_cache = self.levels.list_levels()
        self.selected_level = min(self.selected_level, max(0, len(self.level_cache) - 1))
        self.bus.publish("levels.changed")
        return self.level_cache

    def selected_level_path(self) -> str:
        if 0 <= self.selected_level < len(self.level_cache):
            return self.level_cache[self.selected_level].path
        return ""

    def select_level(self, delta: int) -> ActionResult:
        if not self.level_cache:
            try:
                self.refresh_levels()
            except Exception as exc:
                return ActionResult(False, f"Cannot list levels: {exc}")
        if not self.level_cache:
            return ActionResult(False, "No levels found")
        self.selected_level = (self.selected_level + delta) % len(self.level_cache)
        self.bus.publish("levels.selected", path=self.selected_level_path())
        return ActionResult(True, f"Selected {self.level_cache[self.selected_level].name}")

    def load_level(self, level: str = "", mode: str = "") -> ActionResult:
        level = level or self.selected_level_path()
        if not level:
            return ActionResult(False, "No level selected")
        mode = mode or self.config.remote_control.level_load_mode
        errors: List[str] = []
        if mode in ("editor", "all"):
            try:
                self.levels.load_in_editor(level)
            except Exception as exc:
                errors.append(f"editor: {exc}")
        if mode == "nodes":
            errors += self.levels.open_on_nodes(level)
        if mode in ("relaunch", "all"):
            for rt in self.devices.devices():
                if rt.config.role == "ndisplay" and rt.config.enabled:
                    threading.Thread(target=self.devices.restart, args=(rt.name, level), daemon=True).start()
        self.bus.publish("levels.loaded", path=level, errors=errors)
        return _errors_result(f"Load {level.split('/')[-1]}", errors)

    def switch_project(self, project: str, relaunch: bool = False) -> ActionResult:
        if not any(p.name == project for p in self.config.projects):
            return ActionResult(False, f"Unknown project '{project}'")
        was_running = bool(self.devices.running())
        if was_running:
            self.devices.stop_all()
        self.config.active_project = project
        self.level_cache = []
        self.selected_level = 0
        self.bus.publish("project.changed", project=project)
        self.refresh_levels_in_background()
        if relaunch and was_running:
            time.sleep(2)
            self.devices.start_all()
        return ActionResult(True, f"Active project: {project}")

    # ---- actions ----------------------------------------------------------------------
    def _register_actions(self) -> None:
        a = self.actions
        led, dev, mu, takes = self.led, self.devices, self.multiuser, self.takes

        def patterns():
            return [pid for pid, _ in self.led.all_test_patterns()]

        def next_pattern():
            ids = patterns()
            if not ids:
                return ActionResult(False, "No test patterns available")
            cur = led.test_pattern
            nxt = ids[(ids.index(cur) + 1) % len(ids)] if cur in ids else ids[0]
            return _errors_result(f"Test pattern {nxt}", led.set_test_pattern(nxt))

        # LED wall
        a.add("led.freeze", "Freeze wall (toggle)", "LED Wall", lambda: _errors_result("Freeze", led.set_freeze(None)),
              state=lambda: led.frozen)
        a.add("led.freeze_on", "Freeze wall", "LED Wall", lambda: _errors_result("Freeze", led.set_freeze(True)),
              state=lambda: led.frozen)
        a.add("led.freeze_off", "Unfreeze wall", "LED Wall", lambda: _errors_result("Unfreeze", led.set_freeze(False)))
        a.add("led.blackout", "Blackout wall (toggle)", "LED Wall",
              lambda: _errors_result("Blackout", led.set_blackout(None)), state=lambda: led.blackout,
              confirm=lambda: self.config.ui.confirm_blackout and not led.blackout)  # restoring needs no confirm
        a.add("led.blackout_on", "Blackout wall", "LED Wall", lambda: _errors_result("Blackout", led.set_blackout(True)),
              state=lambda: led.blackout, confirm=lambda: self.config.ui.confirm_blackout and not led.blackout)
        a.add("led.blackout_off", "Restore from blackout", "LED Wall",
              lambda: _errors_result("Restore", led.set_blackout(False)))
        a.add("led.restore", "Wall to normal", "LED Wall", lambda: _errors_result("Wall normal", led.restore_all()),
              state=lambda: bool(led.active()) and all(m == "normal" for m in led.modes().values()))
        a.add("led.locate", "Locate (toggle)", "LED Wall", lambda: _errors_result("Locate", led.set_locate(None)),
              state=lambda: led.locate)
        a.add("led.locate_on", "Locate on", "LED Wall", lambda: _errors_result("Locate", led.set_locate(True)),
              state=lambda: led.locate)
        a.add("led.locate_off", "Locate off", "LED Wall", lambda: _errors_result("Locate off", led.set_locate(False)))
        led_targets = lambda: (["all"] + [g.name for g in self.config.led_groups]
                               + [p.name for p in self.config.led_processors])
        a.add("led.set_mode", "Set LED processor / group mode", "LED Wall",
              lambda target="all", mode="normal", pattern="": _errors_result(
                  f"{target}: {mode}", led.set_mode(target, mode, pattern or None)),
              description="Put one processor, a group of processors or the whole wall into one mode",
              params=[ActionParam("target", "Processor or group", "choice", "all", choices_provider=led_targets),
                      ActionParam("mode", "Mode", "choice", "normal",
                                  choices_provider=lambda: ["frozen", "blackout", "pattern", "locate", "normal"]),
                      ActionParam("pattern", "Test pattern (for pattern)", "choice", choices_provider=patterns)])
        a.add("led.test_pattern", "Show test pattern", "LED Wall",
              lambda pattern="": _errors_result(f"Test pattern {pattern}", led.set_test_pattern(pattern or None)),
              params=[ActionParam("pattern", "Pattern", "choice", choices_provider=patterns)],
              state=lambda: led.test_pattern is not None)
        a.add("led.test_pattern_next", "Next test pattern", "LED Wall", next_pattern,
              state=lambda: led.test_pattern is not None)
        a.add("led.test_pattern_off", "Test pattern off", "LED Wall",
              lambda: _errors_result("Pattern off", led.set_test_pattern(None)))
        a.add("led.brightness", "Set wall brightness", "LED Wall",
              lambda percent=100: _errors_result(f"Brightness {percent}%", led.set_brightness(float(percent))),
              params=[ActionParam("percent", "Brightness %", "float", 100)])

        # Devices
        device_names = lambda: [d.name for d in self.config.devices]
        group_names = lambda: [g.name for g in self.config.groups]
        a.add("devices.connect_all", "Connect all", "Devices", dev.connect_all)
        a.add("devices.disconnect_all", "Disconnect all", "Devices", dev.disconnect_all)
        a.add("devices.start_all", "Start all", "Devices", lambda: dev.start_all(), state=lambda: bool(dev.running()))
        a.add("devices.stop_all", "Stop all", "Devices", dev.stop_all, confirm=lambda: self.config.ui.confirm_stop_all)
        a.add("devices.restart_all", "Restart all", "Devices", lambda: dev.restart_all())
        a.add("devices.connect", "Connect device", "Devices", lambda device: dev.connect(device),
              params=[ActionParam("device", "Device", "choice", choices_provider=device_names)])
        a.add("devices.disconnect", "Disconnect device", "Devices", lambda device: dev.disconnect(device),
              params=[ActionParam("device", "Device", "choice", choices_provider=device_names)])
        a.add("devices.start", "Start device", "Devices", lambda device: dev.start(device),
              params=[ActionParam("device", "Device", "choice", choices_provider=device_names)])
        a.add("devices.stop", "Stop device", "Devices", lambda device: dev.stop(device),
              params=[ActionParam("device", "Device", "choice", choices_provider=device_names)])
        a.add("group.start", "Start group", "Devices", lambda group: dev.start_group(group),
              params=[ActionParam("group", "Group", "choice", choices_provider=group_names)])
        a.add("group.stop", "Stop group", "Devices", lambda group: dev.stop_group(group),
              params=[ActionParam("group", "Group", "choice", choices_provider=group_names)])

        def group_toggle(group):
            if any(d.state in ("running", "starting") for d in dev.group_devices(group)):
                dev.stop_group(group)
            else:
                dev.start_group(group)
        a.add("group.toggle", "Start/stop group", "Devices", group_toggle,
              params=[ActionParam("group", "Group", "choice", choices_provider=group_names)])

        # Multi-user
        def start_mu(server_name="", session_name="", interactive=False) -> ActionResult:
            mu.start(server_name or None, session_name or None, interactive=bool(interactive))
            name = self.config.multiuser.session_name
            if mu.clash:
                return ActionResult(True, f"Multi-user server started. Session '{mu.clash}' already exists on it: "
                                          "choose whether to start a new session or use the existing one")
            if mu.session_warning:
                return ActionResult(False, f"Multi-user server started. Warning: {mu.session_warning}")
            if mu.session_live:
                return ActionResult(True, f"Multi-user server and session '{name}' are live")
            return ActionResult(True, f"Multi-user server started (session '{name}')")

        def start_session() -> ActionResult:
            if not mu.running:
                start_mu()
                if mu.session_live:
                    return ActionResult(True, f"Multi-user session '{self.config.multiuser.session_name}' is live")
            mu.start_session()
            return ActionResult(True, f"Multi-user session '{self.config.multiuser.session_name}' is live")

        def resolve_clash(increment=True) -> ActionResult:
            old = mu.clash
            mu.resolve_clash(str(increment).lower() not in ("false", "0", "no", ""))
            name = self.config.multiuser.session_name
            if old and name != old:
                return ActionResult(True, f"New session '{name}' (not the previous session '{old}')")
            return ActionResult(True, f"Keeping the existing session '{name}'")

        def stop_mu() -> ActionResult:
            mu.stop()
            if self.config.multiuser.auto_increment_session:
                return ActionResult(True, f"Multi-user server stopped; the next session will be "
                                          f"'{self.config.multiuser.session_name}'")
            return ActionResult(True, "Multi-user server stopped")

        a.add("multiuser.start", "Start multi-user server", "Multi-User", start_mu, state=lambda: mu.running,
              params=[ActionParam("server_name", "Server name"), ActionParam("session_name", "Session name")])
        a.add("multiuser.stop", "Stop multi-user server", "Multi-User", stop_mu)
        a.add("multiuser.start_session", "Start multi-user session", "Multi-User", start_session,
              state=lambda: mu.session_live)
        a.add("multiuser.resolve_clash", "Session name clash: new session or keep", "Multi-User", resolve_clash,
              params=[ActionParam("increment", "Start a new session (true) or keep the existing one (false)",
                                  "bool")])
        a.add("multiuser.toggle", "Multi-user server (toggle)", "Multi-User",
              lambda: stop_mu() if mu.running else start_mu(), state=lambda: mu.running)

        # Levels & projects
        level_paths = lambda: [l.path for l in self.level_cache]
        a.add("level.load", "Load level", "Levels", lambda level="", mode="": self.load_level(level, mode),
              params=[ActionParam("level", "Level", "choice", choices_provider=level_paths),
                      ActionParam("mode", "Mode", "choice", "", choices_provider=lambda: ["editor", "nodes", "relaunch", "all"])])
        a.add("level.next", "Select next level", "Levels", lambda: self.select_level(1))
        a.add("level.prev", "Select previous level", "Levels", lambda: self.select_level(-1))
        a.add("level.load_selected", "Load selected level", "Levels", lambda: self.load_level())
        a.add("project.switch", "Switch project", "Levels", lambda project, relaunch=False: self.switch_project(project, relaunch),
              params=[ActionParam("project", "Project", "choice", choices_provider=lambda: [p.name for p in self.config.projects]),
                      ActionParam("relaunch", "Relaunch running instances", "bool", False)])
        a.add("sublevel.show", "Show sub-level", "Levels",
              lambda package: _errors_result("Show " + package, self.levels.set_sublevel_visible(package, True)),
              params=[ActionParam("package", "Sub-level package")])
        a.add("sublevel.hide", "Hide sub-level", "Levels",
              lambda package: _errors_result("Hide " + package, self.levels.set_sublevel_visible(package, False)),
              params=[ActionParam("package", "Sub-level package")])

        # Cameras
        cam_names = lambda: [c.name for c in self.config.cameras]
        a.add("camera.recall", "Recall camera position", "Cameras",
              lambda preset: _errors_result(f"Camera {preset}", self.cameras.recall(preset)),
              params=[ActionParam("preset", "Camera preset", "choice", choices_provider=cam_names)])

        def capture_next():
            preset = self.cameras.capture(f"Camera {len(self.config.cameras) + 1}")
            self.bus.publish("cameras.changed")
            return ActionResult(True, f"Saved {preset.name}")
        a.add("camera.capture_next", "Save current viewport as new camera", "Cameras", capture_next)

        # Takes
        def start_take() -> ActionResult:
            errors = takes.start()
            label = f"Recording: {takes.notice}" if takes.notice else "Recording"
            if takes.warning and not errors:
                return ActionResult(False, f"{label}. Warning: {takes.warning}")
            return _errors_result(label, errors)

        def stop_take() -> ActionResult:
            label = "Countdown cancelled" if takes.counting_down else "Stopped"
            return _errors_result(label, takes.stop())

        def save_take(index=None) -> ActionResult:
            return ActionResult(True, takes.save_take(None if index in (None, "") else int(index)))

        a.add("takes.toggle_record", "Record (toggle)", "Takes",
              lambda: stop_take() if takes.recording else start_take(),
              state=lambda: takes.recording)
        a.add("takes.start", "Start recording", "Takes", start_take)
        a.add("takes.stop", "Stop recording", "Takes", stop_take)
        a.add("takes.save_last", "Save last take", "Takes", save_take)
        a.add("takes.save", "Save take", "Takes", save_take,
              params=[ActionParam("index", "Take log row (0 = first)", "int")])
        a.add("takes.next_take", "Take +1", "Takes", lambda: takes.increment(1))
        a.add("takes.prev_take", "Take -1", "Takes", lambda: takes.increment(-1))
        a.add("takes.rate_good", "Mark last take good", "Takes", lambda: takes.rate_last("good"))
        a.add("takes.rate_ng", "Mark last take NG", "Takes", lambda: takes.rate_last("ng"))
        a.add("takes.set_slate", "Set scene (slate)", "Takes", lambda slate: takes.set_slate(slate),
              params=[ActionParam("slate", "Scene / slate name")])
        a.add("takes.set_take", "Set take number", "Takes", lambda take: takes.set_take(int(take)),
              params=[ActionParam("take", "Take number", "int")])
        a.add("takes.set_description", "Set take description", "Takes",
              lambda description="": takes.set_description(str(description)),
              params=[ActionParam("description", "Description")])

        def describe_take(index, description="") -> ActionResult:
            note = takes.describe_take(int(index), str(description))
            return ActionResult(True, "Description changed" + (f" ({note})" if note else ""))
        a.add("takes.describe", "Change a recorded take's description", "Takes", describe_take,
              params=[ActionParam("index", "Take log row (0 = first)", "int"), ActionParam("description", "Description")])

        # DLSS
        a.add("dlss.set", "Set DLSS option", "DLSS",
              lambda control, value: _errors_result(f"DLSS {control}={value}", self.dlss.apply(control, value)),
              params=[ActionParam("control", "Control", "choice", choices_provider=lambda: [c["id"] for c in self.dlss.controls]),
                      ActionParam("value", "Value")])

        # Sync
        job_names = lambda: [j.name for j in self.config.sync_jobs]
        a.add("sync.run", "Run sync job", "File Sync", lambda job: self.sync.run_async(job) or ActionResult(True, f"Sync {job} started"),
              params=[ActionParam("job", "Job", "choice", choices_provider=job_names)])
        a.add("sync.run_all", "Run all sync jobs", "File Sync",
              lambda: [self.sync.run_async(j) for j in job_names()] and ActionResult(True, "Sync started"))

        # Remote objects
        a.add("object.set_preset_property", "Set exposed property", "Remote Objects",
              lambda preset, label, value: _errors_result(f"{label}={value}", self.objects.set_preset_value(preset, label, value)),
              params=[ActionParam("preset", "Preset"), ActionParam("label", "Property"), ActionParam("value", "Value")])
        a.add("object.call_preset_function", "Call exposed function", "Remote Objects",
              lambda preset, label: _errors_result(label, self.objects.call_preset_function(preset, label)),
              params=[ActionParam("preset", "Preset"), ActionParam("label", "Function")])

        # OSC messages and several actions in a row (control surface buttons)
        from operator_board.surfaces.osc_send import OSC_TYPES, send_osc
        a.add("osc.send", "Send OSC message", "OSC",
              lambda host="127.0.0.1", port=8000, address="", type="none", value="": ActionResult(
                  True, send_osc(host, port, address, type, value)),
              description="Send an OSC message over UDP to a lighting desk, media server, QLab...",
              params=[ActionParam("host", "IP address"), ActionParam("port", "Port", "int", 8000),
                      ActionParam("address", "OSC address"),
                      ActionParam("type", "Argument type", "choice", "none", choices_provider=lambda: list(OSC_TYPES)),
                      ActionParam("value", "Value")])
        a.add(MACRO_ACTION, "Multiple actions (in order)", "Control Surface",
              lambda steps=None: self.run_steps(steps or []),
              description="Run several actions one after another, with optional delays between them",
              params=[ActionParam("steps", "Steps", "steps")])

        # Surface navigation (handled by SurfaceController; registered so they appear in pickers)
        for nav_id, label in (("surface.next_page", "Next surface page"), ("surface.prev_page", "Previous surface page"),
                              ("surface.goto_page", "Go to surface page")):
            a.add(nav_id, label, "Control Surface", lambda **_: ActionResult(True, "navigation"),
                  params=[ActionParam("page", "Page")] if nav_id == "surface.goto_page" else [])
