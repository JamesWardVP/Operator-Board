"""Main window: navigation sidebar, pages, status bar and global stage hotkeys."""
from __future__ import annotations

import os
import sys
import time

from operator_board import __version__
from operator_board.qt import QShortcut, QtCore, QtGui, QtWidgets, Qt
from operator_board.ui.bridge import EventBridge, run_async
from operator_board.ui.listener_login import ListenerLogin
from operator_board.ui.nav import NavTree
from operator_board.ui.pages.analytics import AnalyticsPage
from operator_board.ui.pages.devices import DevicesPage
from operator_board.ui.pages.dlss import DLSSPage
from operator_board.ui.pages.led_setup import LEDSetupPage
from operator_board.ui.pages.led_walls import LEDProcessorsPage
from operator_board.ui.pages.saved_settings import SavedSettingsPage
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

PAGES = [OperatePage, TakesPage, LevelsPage, ObjectsPage, LEDProcessorsPage, DLSSPage, AnalyticsPage, SurfacesPage,
         SyncPage, SavedSettingsPage, LEDSetupPage, StagePage, DevicesPage, SettingsPage]
# The sidebar: ungrouped pages at the top and bottom, collapsible groups in between (open by default).
NAV = ["Operate", "Takes", "Cameras & Levels",
       ("Advanced", ["Remote Objects", "LED Processors", "DLSS", "Analytics", "Control Surfaces", "File Sync",
                     "Saved Settings"]),
       ("Setup", ["LED Processor Setup", "Stage", "Devices"]),
       "Settings"]

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
        side.addWidget(self._brand())
        self.nav = NavTree()
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
        self.nav.build(NAV, [p.title for p in self.pages])
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        self.status_label = QtWidgets.QLabel("Ready")
        self.statusBar().addWidget(self.status_label, 1)
        self.clock = QtWidgets.QLabel()
        self.statusBar().addPermanentWidget(self.clock)
        hint = QtWidgets.QLabel("F9 Freeze · F10 Blackout · F11 Wall normal · F12 Record")
        hint.setStyleSheet(f"color: {COLORS['muted']}; padding-right: 12px;")
        self.statusBar().addPermanentWidget(hint)

        self.login = ListenerLogin(self)
        self.bridge = EventBridge(ctx.bus)
        self.bridge.event.connect(self._on_event)
        for key, action in HOTKEYS:
            sc = QShortcut(QtGui.QKeySequence(key), self)
            sc.setContext(Qt.ApplicationShortcut)
            sc.activated.connect(lambda a=action: self.pages[0].invoke(a))

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        # Windows sends WM_DEVICECHANGE to every window when a USB device comes or goes; look for
        # control surfaces again once the burst of messages has settled.
        self._listener_asking = False
        self._listener_answered = 0.0
        self._listener_unreachable = set()
        self._device_timer = QtCore.QTimer(self)
        self._device_timer.setSingleShot(True)
        self._device_timer.setInterval(1500)
        self._device_timer.timeout.connect(self._devices_changed)
        self._tick()
        if ctx.store.load_error:
            self.notify(ctx.store.load_error, False)
        for message in getattr(ctx.devices, "port_changes", []):
            self.notify(message, False)

    def _update_title(self) -> None:
        title = f"Operator Board {__version__} — {self.ctx.config.active_project}"
        if self.ctx.switchboard_config:
            title += f"  [Switchboard: {os.path.splitext(os.path.basename(self.ctx.switchboard_config))[0]}]"
        self.setWindowTitle(title)

    def show_page(self, title: str, tab: str = "") -> None:
        """Switch to the page with this title (and, on a page with tabs, to ``tab``)."""
        titles = [p.title for p in self.pages]
        if title in titles:
            self.nav.setCurrentRow(titles.index(title))
            page = self.pages[titles.index(title)]
            if tab and hasattr(page, "show_tab"):
                page.show_tab(tab)

    LOGO_COLOUR = "#00d5ff"  # the logo's own cyan

    def _brand(self) -> QtWidgets.QWidget:
        """The logo and the name at the top of the sidebar."""
        brand = QtWidgets.QWidget()
        brand.setObjectName("Brand")
        brand.setAttribute(Qt.WA_StyledBackground, True)
        brand.setStyleSheet(f"QWidget#Brand {{ background: {COLORS['panel']}; }}")
        row = QtWidgets.QHBoxLayout(brand)
        row.setContentsMargins(14, 14, 12, 12)
        row.setSpacing(10)
        logo = QtWidgets.QLabel()
        pix = QtGui.QPixmap(os.path.join(os.path.dirname(__file__), "logo.png"))
        if not pix.isNull():
            ratio = self.devicePixelRatioF()
            pix = pix.scaled(round(54 * ratio), round(54 * ratio), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pix.setDevicePixelRatio(ratio)
            logo.setPixmap(pix)
        logo.setFixedSize(54, 54)
        name = QtWidgets.QLabel("OPERATOR\nBOARD")
        name.setStyleSheet(f"font-size: 18px; font-weight: 900; color: {self.LOGO_COLOUR}; letter-spacing: 2px;")
        row.addWidget(logo)
        row.addWidget(name, 1)
        return brand

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
        if topic == "device.auth":
            self.login.handle(payload)
        if topic == "device.rc_port_changed":
            self.notify(payload.get("message", ""), False)
        if topic == "device.listener_unreachable" and payload.get("local"):
            self._offer_listener(payload)
        if topic == "multiuser.sessions_archived":
            self.notify("Archived the previous multi-user session(s): " + ", ".join(payload.get("names", [])))
        if topic.startswith("editor."):  # let every page fill its lists, not only the one on screen
            for p in self.pages:
                if p is not self.current_page() and hasattr(p, "EDITOR_EVENTS"):
                    p.on_event(topic, payload)
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

    LISTENER_ASK_AGAIN_S = 120.0

    def _offer_listener(self, payload: dict) -> None:
        """No SwitchboardListener answered for a device on this PC: offer to start one."""
        self._listener_unreachable.add(payload.get("name", ""))
        if self._listener_asking or time.time() - self._listener_answered < self.LISTENER_ASK_AGAIN_S:
            return
        self._listener_asking = True
        try:
            answer = QtWidgets.QMessageBox.question(
                self, "SwitchboardListener",
                f"No SwitchboardListener answered for {payload.get('name')} "
                f"({payload.get('address')}:{payload.get('port')}), which is on this PC.\n\n"
                "Start SwitchboardListener on this PC now? Operator Board then connects again.")
        finally:
            self._listener_asking = False
            self._listener_answered = time.time()
        names = sorted(n for n in self._listener_unreachable if n)
        self._listener_unreachable = set()
        if answer != QtWidgets.QMessageBox.Yes:
            return
        device = self.ctx.config.device(names[0]) if names else None
        engine = device.engine_dir_override if device and getattr(device, "engine_dir_override", "") else ""

        def work():
            message = self.ctx.start_local_listener(engine)
            return message, self.ctx.reconnect_local_devices(names)

        def done(result) -> None:
            message, connected = result
            missing = [n for n in names if n not in connected]
            self.notify(message + (f"; connected {', '.join(connected)}" if connected else "")
                        + (f"; still not connected: {', '.join(missing)}" if missing else ""), not missing)
        run_async(work, done, lambda exc: self.notify(f"Could not start SwitchboardListener: {exc}", False))

    WM_DEVICECHANGE = 0x0219

    def nativeEvent(self, event_type, message):  # noqa: N802 - Qt override
        if sys.platform == "win32" and bytes(event_type) == b"windows_generic_MSG":
            try:
                import ctypes.wintypes
                msg = ctypes.wintypes.MSG.from_address(int(message))
                if msg.message == self.WM_DEVICECHANGE:
                    self._device_timer.start()
            except (ValueError, TypeError, AttributeError, OSError):
                pass
        return False, 0

    def _devices_changed(self) -> None:
        run_async(lambda: self.ctx.refresh_surface_devices(only_if_changed=True))

    def closeEvent(self, event) -> None:
        if not self.skip_save_on_close:
            try:
                self.ctx.save()
            except OSError:
                pass
        self.ctx.shutdown()
        super().closeEvent(event)
