"""Builds every service and registers the operator actions shared by UI, surfaces and API."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from operator_board.analytics.metrics import MetricStore
from operator_board.core.actions import ActionParam, ActionRegistry, ActionResult
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
from operator_board.remote.targets import TargetResolver
from operator_board.surfaces.api_server import ApiServer
from operator_board.surfaces.controller import SurfaceController
from operator_board.surfaces.layout import default_layout, load_layout
from operator_board.sync.manager import SyncManager

log = logging.getLogger(__name__)


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
        self.sync = SyncManager(self.config, self.bus)
        self.metrics = MetricStore()
        self.level_cache: List[LevelInfo] = []
        self.selected_level = 0
        self.surface = SurfaceController(self._load_surface_layout(), self.actions, self.bus)
        self.api: Optional[ApiServer] = None
        self.streamdeck = None
        self._stop = threading.Event()
        self.bus.subscribe("device.metrics", lambda _t, p: self.metrics.add_raw(p["name"], p.get("metrics") or {}))
        self._register_actions()

    # ---- lifecycle ------------------------------------------------------------
    def _load_surface_layout(self):
        path = self.config.surfaces.layout_path
        if path:
            try:
                return load_layout(path)
            except (OSError, ValueError, TypeError) as exc:
                log.warning("Could not load surface layout %s (%s); using default", path, exc)
        return default_layout()

    def start_services(self, enable_hardware: bool = True) -> None:
        s = self.config.surfaces
        if s.api_server_enabled:
            try:
                self.api = ApiServer(self.actions, self.state_snapshot, s.api_bind, s.api_port, s.api_token)
                self.api.start()
            except OSError as exc:
                log.warning("API server could not start on %s:%s: %s", s.api_bind, s.api_port, exc)
                self.api = None
        if enable_hardware and s.streamdeck_enabled:
            from operator_board.surfaces.streamdeck import StreamDeckBackend
            if StreamDeckBackend.available():
                self.streamdeck = StreamDeckBackend(self.surface, self.bus, s.streamdeck_brightness)
                self.streamdeck.start()
        threading.Thread(target=self._metrics_loop, daemon=True, name="metrics-poll").start()

    def shutdown(self) -> None:
        self._stop.set()
        if self.api:
            self.api.stop()
        if self.streamdeck:
            self.streamdeck.stop()
        self.devices.disconnect_all()
        for p in self.led.processors:
            p.close()

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
        return self.store.save(self.config)

    # ---- stage (Switchboard config) switching ----------------------------------------------
    @property
    def switchboard_config(self) -> str:
        return self.config.switchboard.active_config

    def _switch_to(self, new: AppConfig) -> None:
        copy_into(self.config, new)
        self.multiuser.tracker = self.multiuser._new_tracker()
        self.level_cache = []
        self.selected_level = 0
        self.reload_all()
        self.bus.publish("project.changed", project=self.config.active_project)
        self.bus.publish("stage.changed", switchboard_config=self.switchboard_config)

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
        a.add("led.restore", "Wall to normal", "LED Wall", lambda: _errors_result("Wall normal", led.restore_all()))
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
        a.add("multiuser.start", "Start multi-user server", "Multi-User", lambda server_name="", session_name="":
              mu.start(server_name or None, session_name or None), state=lambda: mu.running,
              params=[ActionParam("server_name", "Server name"), ActionParam("session_name", "Session name")])
        a.add("multiuser.stop", "Stop multi-user server", "Multi-User", mu.stop)
        a.add("multiuser.toggle", "Multi-user server (toggle)", "Multi-User",
              lambda: mu.stop() if mu.running else mu.start(), state=lambda: mu.running)

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
            name = f"Camera {len(self.config.cameras) + 1}"
            self.cameras.capture(name)
            self.bus.publish("cameras.changed")
            return ActionResult(True, f"Saved {name}")
        a.add("camera.capture_next", "Save current viewport as new camera", "Cameras", capture_next)

        # Takes
        a.add("takes.toggle_record", "Record (toggle)", "Takes",
              lambda: _errors_result("Recording" if not takes.recording else "Stopped", takes.toggle()),
              state=lambda: takes.recording)
        a.add("takes.start", "Start recording", "Takes", lambda: _errors_result("Recording", takes.start()))
        a.add("takes.stop", "Stop recording", "Takes", lambda: _errors_result("Stopped", takes.stop()))
        a.add("takes.next_take", "Take +1", "Takes", lambda: takes.increment(1))
        a.add("takes.prev_take", "Take -1", "Takes", lambda: takes.increment(-1))
        a.add("takes.rate_good", "Mark last take good", "Takes", lambda: takes.rate_last("good"))
        a.add("takes.rate_ng", "Mark last take NG", "Takes", lambda: takes.rate_last("ng"))
        a.add("takes.set_slate", "Set slate", "Takes", lambda slate: takes.set_slate(slate),
              params=[ActionParam("slate", "Slate")])

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

        # Surface navigation (handled by SurfaceController; registered so they appear in pickers)
        for nav_id, label in (("surface.next_page", "Next surface page"), ("surface.prev_page", "Previous surface page"),
                              ("surface.goto_page", "Go to surface page")):
            a.add(nav_id, label, "Control Surface", lambda **_: ActionResult(True, "navigation"),
                  params=[ActionParam("page", "Page")] if nav_id == "surface.goto_page" else [])
