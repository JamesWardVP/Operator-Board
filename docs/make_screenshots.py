"""Render the README screenshots into docs/images/ from a simulated demo stage.

    python docs/make_screenshots.py

Uses config/example_stage.json and fakes live state (running instances, multi-user
clients, metrics, takes) so the pages look as they do on a working stage. Nothing
connects to real hardware: no actions are invoked. Run on Windows or a desktop
session so real fonts are available (the offscreen Qt platform has none).
"""
from __future__ import annotations

import math
import os
import random
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "images")
sys.path.insert(0, ROOT)

DEMO_HOME = tempfile.mkdtemp(prefix="opb_demo_")
os.environ["OPERATOR_BOARD_HOME"] = DEMO_HOME

from operator_board.qt import Qt, QtWidgets  # noqa: E402

SB_DIR_SHOWN = r"C:\Program Files\Epic Games\UE_5.6\Engine\Plugins\VirtualProduction\Switchboard\Source\Switchboard\configs"


def demo_context():
    from operator_board.context import AppContext
    from operator_board.core.config import AppConfig, CameraPreset
    from operator_board.remote.levels import LevelInfo
    from operator_board.remote.takes import TakeRecord

    with open(os.path.join(ROOT, "config", "example_stage.json"), "r", encoding="utf-8") as fh:
        cfg = AppConfig.from_json(fh.read())
    if not cfg.cameras:
        cfg.cameras = [CameraPreset(name=n) for n in ("Wide", "Hero car", "Driver close", "Overhead")]
    ctx = AppContext(cfg)
    now = time.time()

    class _ShownAsConnected:
        """Stands in for a listener connection; nothing is sent anywhere."""
        connected = True
        transport = "quic"

        def disconnect(self, reason=""):
            self.connected = False

    # Unreal instances running for a while
    for i, rt in enumerate(ctx.devices.devices()):
        rt.client = _ShownAsConnected()
        rt.transport = "quic"
        rt.state = "running"
        rt.started_at = now - 3600 - i * 420
        rt.listener_version = "5.6"
        rt.log_tail.extend([f"[{rt.name}] Launching UnrealEditor.exe ...", f"[{rt.name}] Program started"])

    # Multi-user session with everyone joined
    ctx.multiuser.running = True
    ctx.multiuser.session_live = True  # every device has joined the session
    ctx.multiuser.tracker.clients.update(d.name for d in cfg.devices)

    # Levels found in the project
    ctx.level_cache = [LevelInfo(name=n, path=f"/Game/Maps/{n}") for n in
                       ("Stage_Main", "Desert_Highway", "City_Night", "Forest_Dawn", "Studio_Grey")]
    ctx.selected_level = 0

    # LED processors online
    for p in ctx.led.processors:
        p.state.online = p.config.enabled
        p.state.brightness = 80.0
        p.state.last_ok = now

    # Take log
    cfg.takes.slate = "Scene_12"
    cfg.takes.take = 5
    ctx.takes.log.takes = [
        TakeRecord(slate="Scene_12", take=t, description=d, level="/Game/Maps/Desert_Highway",
                   started=now - 1800 + t * 240, stopped=now - 1800 + t * 240 + 45 + t * 7, rating=r)
        for t, d, r in [(1, "Rehearsal", "ng"), (2, "Full pass", "good"), (3, "Pickup on the turn", ""),
                        (4, "Full pass, faster", "circle")]
    ]

    # 15 minutes of analytics
    random.seed(4)
    for d in cfg.devices:
        base = random.uniform(35, 55)
        for k in range(450):
            t = now - (450 - k) * 2
            wave = math.sin(k / 25.0)
            ctx.metrics.add(d.name, "CPU %", max(0, base + 8 * wave + random.uniform(-3, 3)), t)
            ctx.metrics.add(d.name, "GPU %", min(100, base + 30 + 6 * wave + random.uniform(-2, 2)), t)
            ctx.metrics.add(d.name, "GPU temp (C)", 64 + 4 * wave + random.uniform(-0.5, 0.5), t)
            ctx.metrics.add(d.name, "Free RAM (GB)", 41 + random.uniform(-0.3, 0.3), t)
    for k in range(450):
        t = now - (450 - k) * 2
        ctx.metrics.add("Stage", "Running instances", len(cfg.devices), t)
        ctx.metrics.add("Stage", "Multi-user clients", len(cfg.devices) if k > 30 else k // 8, t)

    # Colour grading: targets of every kind, a warm grade on the whole wall and two saved looks
    from operator_board.core.looks import Look
    ctx.grading.stop()
    ctx.grading = SimulatedGrading(ctx.bus, cfg)
    ctx.grading.looks = ctx.looks
    first = ctx.grading.targets_list[0]["key"]
    for name in ("Day ext warm", "Night city blue"):
        ctx.looks.save(Look(name, {first: ctx.grading.current[first]}))
    cfg.grading.active_look = "Day ext warm"
    return ctx


class SimulatedGrading:
    """Stands in for GradingService on the demo stage: grading targets of all four kinds, kept in
    memory. Nothing is sent anywhere."""

    def __init__(self, bus, config):
        from operator_board.core.grading import Grade
        from operator_board.core.looks import BypassState
        self.bus = bus
        self.config = config
        dot = " · "
        self.targets_list = []
        for kind, actor, name, applies_to in [
                ("ndisplay_cluster", "nDisplay_Stage", "nDisplay_Stage" + dot + "Entire cluster", []),
                ("ndisplay_viewport", "nDisplay_Stage", "nDisplay_Stage" + dot + "VP_Wall_L, VP_Wall_R", ["VP_Wall_L", "VP_Wall_R"]),
                ("ndisplay_viewport", "nDisplay_Stage", "nDisplay_Stage" + dot + "VP_Ceiling", ["VP_Ceiling"]),
                ("icvfx_all_nodes", "nDisplay_Stage/ICVFXCamera", "ICVFXCamera" + dot + "All nodes", []),
                ("ppv", "PPV_Global", "PPV_Global (global)", []),
                ("ppv", "PPV_CarInterior", "PPV_CarInterior (local)", []),
                ("ccr", "CCR_SkyWarm", "CCR_SkyWarm (sphere)", []),
                ("ccr", "CCW_Window", "CCW_Window (window, box)", [])]:
            from operator_board.core.looks import target_key
            key = target_key(kind, actor, applies_to)
            self.targets_list.append({"key": key, "kind": kind, "actor": actor, "applies_to": applies_to, "nth": 0,
                                      "name": name, "enabled": kind != "ndisplay_viewport" or "Ceiling" not in name})
        self.missing = {"ndisplay": "", "ccr": ""}
        self.current = {t["key"]: Grade.neutral() for t in self.targets_list}
        warm = (Grade.neutral().set("global.gain", (1.08, 1.0, 0.9, 1.04)).set("shadows.offset", (0.0, 0.01, 0.03, -0.01))
                .set("global.saturation", (1.0, 1.0, 1.0, 1.1)).set("temp", 5900.0).set("tint", 0.02))
        warm.limits = {"shadows_max": 0.09, "highlights_min": 0.5, "highlights_max": 1.0}
        self.current[self.targets_list[0]["key"]] = warm
        self.enabled = {t["key"]: t["enabled"] for t in self.targets_list}
        self.bypass = BypassState(None)

    @property
    def active_look(self):
        return self.config.grading.active_look

    @active_look.setter
    def active_look(self, name):
        self.config.grading.active_look = name
        self.bus.publish("grading.look", name=name)

    @property
    def selected(self):
        return self.config.grading.selected or self.targets_list[0]["key"]

    def select(self, key):
        self.config.grading.selected = key
        self.bus.publish("grading.selected", key=key)

    def refresh_targets(self):
        self.bus.publish("grading.targets", targets=self.targets_list, missing=self.missing)
        return self.targets_list

    def read(self, key):
        return self.current[key]

    def begin_gesture(self, key):
        return self.current[key]

    def send(self, key, values):
        grade = self.current[key]
        for k, v in values.items():
            grade = grade.reset(k) if v is None else grade.set(k, v)
        self.current[key] = grade

    def end_gesture(self, key, label):
        self.active_look = ""
        self.bus.publish("grading.changed", key=key, grade=self.current[key])

    def reset(self, key="", control_id=None):
        from operator_board.core.grading import CONTROLS
        key = key or self.selected
        self.send(key, {CONTROLS[control_id].key: None} if control_id else {k: None for k in self.current[key].overrides})
        self.bus.publish("grading.changed", key=key, grade=self.current[key])
        return True, "Reset grade"

    def is_bypassed(self, key):
        return key in self.bypass.bypassed()

    def set_bypass(self, key="", on=None):
        key = key or self.selected
        on = (not self.is_bypassed(key)) if on is None else on
        self.bypass.set(key, on)
        self.bus.publish("grading.bypass", key=key, on=on)
        return on

    def enable(self, key=""):
        self.enabled[key or self.selected] = True
        self.bus.publish("grading.bypass", key=key or self.selected, on=False)
        return True, "Grading enabled"

    def restore_bypassed(self):
        for key in self.bypass.bypassed():
            self.set_bypass(key, False)
        return True, "Restored"

    def save_look(self, name, keys):
        from operator_board.core.looks import Look
        look = Look(name, {k: self.current[k] for k in keys})
        self.looks.save(look)
        return look

    def recall_look(self, name):
        self.active_look = name
        return []

    def stop(self):
        pass


def link_switchboard_config(ctx):
    """Open the demo stage as a Switchboard config in a temporary folder."""
    folder = os.path.join(DEMO_HOME, "configs")
    os.makedirs(folder, exist_ok=True)
    from operator_board.core.switchboard_config import new_switchboard_config, merge_into_switchboard, \
        write_switchboard_config
    for name, project in (("Stage_Main", "Stage Project"), ("Car_Commercial", "Car Commercial"),
                          ("Music_Video", "Music Video")):
        data = merge_into_switchboard(new_switchboard_config(ctx.config.project()), ctx.config)
        data["project_name"] = project
        write_switchboard_config(os.path.join(folder, name + ".json"), data)
    ctx.config.switchboard.config_dir = folder
    ctx.config.switchboard.active_config = os.path.join(folder, "Stage_Main.json")
    ctx.store.save(ctx.config)
    return folder


def hide_local_paths(page, folder):
    """Show the usual Switchboard folder instead of the temporary demo folder."""
    page.folder.blockSignals(True)  # changing the combo would save and refresh the page
    page.folder.setItemText(0, r"Same as Switchboard (C:\Program Files\Epic Games\UE_5.6)")
    page.folder.setCurrentIndex(0)
    page.folder.blockSignals(False)
    for label in (page.folder_path, page.current_detail):
        label.setText(label.text().replace(folder, SB_DIR_SHOWN))


def main() -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    from operator_board.core.projects import ProjectInfo
    from operator_board.ui.main_window import MainWindow

    ctx = demo_context()
    ctx.project = ProjectInfo(name="Car Commercial", production="Car Commercial")  # the sidebar's project button
    folder = link_switchboard_config(ctx)
    window = MainWindow(ctx)
    window.resize(1600, 1060)
    window.notify("Ready")
    os.makedirs(OUT, exist_ok=True)
    titles = [p.title for p in window.pages]

    def shot(title, filename, prepare=None):
        idx = titles.index(title)
        window.nav.setCurrentRow(idx)
        page = window.pages[idx]
        page.refresh()
        app.processEvents()  # let queued stage/config events refresh the page first
        if prepare:
            prepare(page)
        for label in window.findChildren(QtWidgets.QLabel):  # no local temp paths in the images
            if DEMO_HOME in label.text():
                label.setText(label.text().replace(DEMO_HOME, r"C:\Users\Operator\.operator_board"))
        window.grab().save(os.path.join(OUT, filename))
        print("wrote", filename)

    def select_first(page, attr="table"):
        table = getattr(page, attr, None)
        if table is not None and table.rowCount():
            table.selectRow(0)

    def analytics(page):
        items = page.sources.findItems("Node_Wall_L", Qt.MatchExactly)
        if items:
            page.sources.setCurrentItem(items[0])
        for i in range(page.metrics.count()):
            if page.metrics.item(i).text() in ("CPU %", "GPU %"):
                page.metrics.item(i).setSelected(True)

    shot("Operate", "operate.png")
    for p in ctx.led.processors:
        p.state.frozen = True
    ctx.led._publish()
    shot("Operate", "operate_frozen.png")
    for p in ctx.led.processors:
        p.state.frozen = False
    ctx.led._publish()
    shot("Cameras & Levels", "levels.png")
    shot("Takes", "takes.png")
    shot("Switchboard Configs", "stage.png", lambda p: (p.refresh(), select_first(p), hide_local_paths(p, folder)))
    shot("Devices", "devices.png", select_first)
    shot("LED Processor Setup", "led_processors.png", select_first)
    shot("File Sync", "file_sync.png")
    shot("Analytics", "analytics.png", analytics)
    shot("Control Surfaces", "control_surfaces.png")
    shot("DLSS", "dlss.png")
    shot("Colour Grading", "colour_grading.png")

    window.skip_save_on_close = True
    window.close()
    shutil.rmtree(DEMO_HOME, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
