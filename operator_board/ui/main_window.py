"""Main window: navigation sidebar, pages, status bar and global stage hotkeys."""
from __future__ import annotations

import os
import time

from operator_board import __version__
from operator_board.qt import QShortcut, QtCore, QtGui, QtWidgets, Qt
from operator_board.ui.bridge import EventBridge, run_async
from operator_board.ui.pages.analytics import AnalyticsPage
from operator_board.ui.pages.devices import DevicesPage
from operator_board.ui.pages.dlss import DLSSPage
from operator_board.ui.pages.led_setup import LEDSetupPage
from operator_board.ui.pages.levels import LevelsPage
from operator_board.ui.pages.objects import ObjectsPage
from operator_board.ui.pages.operate import OperatePage
from operator_board.ui.pages.settings import SettingsPage
from operator_board.ui.pages.stage import StagePage
from operator_board.ui.pages.surfaces import SurfacesPage
from operator_board.ui.pages.sync import SyncPage
from operator_board.ui.pages.takes import TakesPage
from operator_board.ui.theme import COLORS, STYLESHEET
from operator_board.ui.widgets import scroll

PAGES = [OperatePage, LevelsPage, TakesPage, ObjectsPage, DLSSPage, StagePage, DevicesPage, LEDSetupPage, SyncPage,
         AnalyticsPage, SurfacesPage, SettingsPage]

# Stage hotkeys work from any page.
HOTKEYS = [("F9", "led.freeze"), ("F10", "led.blackout"), ("F11", "led.restore"), ("F12", "takes.toggle_record"),
           ("Ctrl+Shift+S", "devices.start_all")]


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, ctx) -> None:
        super().__init__()
        self.ctx = ctx
        self.skip_save_on_close = False
        self._update_title()
        self.resize(1500, 950)
        self.setStyleSheet(STYLESHEET)
        # Selections and sliders use the system accent colour unless the palette says otherwise.
        app = QtWidgets.QApplication.instance()
        palette = app.palette()
        roles = [QtGui.QPalette.Highlight] + ([QtGui.QPalette.Accent] if hasattr(QtGui.QPalette, "Accent") else [])
        for group in (QtGui.QPalette.Active, QtGui.QPalette.Inactive):
            for role in roles:
                palette.setColor(group, role, QtGui.QColor(COLORS["accent"]))
            palette.setColor(group, QtGui.QPalette.HighlightedText, QtGui.QColor("white"))
        app.setPalette(palette)

        central = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        side = QtWidgets.QVBoxLayout()
        brand = QtWidgets.QLabel("OPERATOR\nBOARD")
        brand.setStyleSheet(f"background: {COLORS['panel']}; font-size: 20px; font-weight: 900; padding: 16px;"
                            f" color: {COLORS['accent']}; letter-spacing: 2px;")
        side.addWidget(brand)
        self.nav = QtWidgets.QListWidget()
        self.nav.setObjectName("Nav")
        self.nav.setFixedWidth(210)
        side.addWidget(self.nav)
        side.setSpacing(0)
        lay.addLayout(side)
        self.stack = QtWidgets.QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.pages = []
        for cls in PAGES:
            page = cls(ctx, self)
            self.pages.append(page)
            # Pages scroll instead of forcing the window taller than a laptop screen.
            self.stack.addWidget(scroll(page))
            self.nav.addItem(page.title)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        self.status_label = QtWidgets.QLabel("Ready")
        self.statusBar().addWidget(self.status_label, 1)
        self.clock = QtWidgets.QLabel()
        self.statusBar().addPermanentWidget(self.clock)
        hint = QtWidgets.QLabel("F9 Freeze · F10 Blackout · F11 Wall normal · F12 Record")
        hint.setStyleSheet(f"color: {COLORS['muted']}; padding-right: 12px;")
        self.statusBar().addPermanentWidget(hint)

        self.bridge = EventBridge(ctx.bus)
        self.bridge.event.connect(self._on_event)
        for key, action in HOTKEYS:
            sc = QShortcut(QtGui.QKeySequence(key), self)
            sc.setContext(Qt.ApplicationShortcut)
            sc.activated.connect(lambda a=action: self.pages[0].invoke(a))

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick()
        if ctx.store.load_error:
            self.notify(ctx.store.load_error, False)

    def _update_title(self) -> None:
        title = f"Operator Board {__version__} — {self.ctx.config.active_project}"
        if self.ctx.switchboard_config:
            title += f"  [Switchboard: {os.path.splitext(os.path.basename(self.ctx.switchboard_config))[0]}]"
        self.setWindowTitle(title)

    def current_page(self):
        index = self.stack.currentIndex()
        return self.pages[index] if 0 <= index < len(self.pages) else None

    def notify(self, message: str, ok: bool = True) -> None:
        color = COLORS["text"] if ok else COLORS["err"]
        self.status_label.setStyleSheet(f"color: {color}; font-weight: {'400' if ok else '700'};")
        self.status_label.setText(time.strftime("%H:%M:%S  ") + message)

    def _on_event(self, topic: str, payload: dict) -> None:
        if topic in ("project.changed", "stage.changed"):
            self._update_title()
        if topic == "action.invoked" and not payload.get("ok"):
            self.notify(payload.get("message", ""), False)
        page = self.current_page()
        if page is not None:
            page.on_event(topic, payload)

    def _tick(self) -> None:
        self.clock.setText(time.strftime("%H:%M:%S  "))
        page = self.current_page()
        if isinstance(page, OperatePage):
            page.refresh()

    def closeEvent(self, event) -> None:
        if not self.skip_save_on_close:
            try:
                self.ctx.save()
            except OSError:
                pass
        self.ctx.shutdown()
        super().closeEvent(event)
