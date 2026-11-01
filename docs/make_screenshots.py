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
    return ctx


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
    from operator_board.ui.main_window import MainWindow

    ctx = demo_context()
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

    window.skip_save_on_close = True
    window.close()
    shutil.rmtree(DEMO_HOME, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
