"""The "Advanced: groups and individual control" button: a drop-down over the page with a row of
mode buttons for every LED group and every processor, and + New group / Edit to manage the groups.
Used on LED Processor Setup and on Operate."""
from __future__ import annotations

from operator_board.qt import QtCore, QtWidgets, Qt
from operator_board.ui.group_dialog import edit_led_group
from operator_board.ui.led_controls import ModeButtons
from operator_board.ui.widgets import hbox, muted


class LEDAdvancedButton(QtWidgets.QPushButton):
    """``page`` is the page it sits on: it needs ``ctx``, ``invoke``, ``save_config`` and ``refresh``.
    The page calls ``refresh_popup()`` from its own refresh so open rows stay lit for their mode."""

    def __init__(self, page, label: str = "Advanced: groups and individual control  ▾") -> None:
        super().__init__(label)
        self.page = page
        self.setToolTip("Groups of LED processors, and buttons to control each group and processor on its own")
        self.clicked.connect(self.show_popup)
        self.popup = QtWidgets.QFrame(page, Qt.Popup)
        self.popup.setObjectName("Card")
        body = QtWidgets.QVBoxLayout(self.popup)
        body.setContentsMargins(14, 12, 14, 14)
        title = QtWidgets.QLabel("ADVANCED: GROUPS AND INDIVIDUAL CONTROL")
        title.setObjectName("CardTitle")
        body.addWidget(title)
        new_group = QtWidgets.QPushButton("+ New group")
        new_group.setToolTip("A group of LED processors that you can freeze, black out... together")
        new_group.clicked.connect(lambda: self._edit_group(None))
        body.addLayout(hbox(new_group, None))
        self.rows = QtWidgets.QVBoxLayout()
        self.rows.setSpacing(6)
        body.addLayout(self.rows)
        note = muted("Each button puts that group or processor into the mode (pressing a lit one returns it to "
                     "normal). The LED Processors page shows the same as columns.")
        note.setWordWrap(True)
        body.addWidget(note)
        self._key = None
        self._strips = []

    def show_popup(self) -> None:
        self._key = None
        self.refresh_popup(force=True)
        self.popup.adjustSize()
        width = max(720, self.popup.sizeHint().width())
        self.popup.resize(width, self.popup.sizeHint().height())
        pos = self.mapToGlobal(QtCore.QPoint(0, self.height() + 4))
        screen = QtWidgets.QApplication.screenAt(pos) or QtWidgets.QApplication.primaryScreen()
        area = screen.availableGeometry()
        x = max(area.left(), min(pos.x(), area.right() - width))
        y = pos.y() if pos.y() + self.popup.height() <= area.bottom() else \
            self.mapToGlobal(QtCore.QPoint(0, 0)).y() - self.popup.height() - 4  # open upwards
        self.popup.move(x, max(area.top(), y))
        self.popup.show()

    def _edit_group(self, group) -> None:
        reopen = self.popup.isVisible()
        self.popup.hide()  # the dialog takes over; the drop-down comes back afterwards
        ctx = self.page.ctx
        if edit_led_group(self.page, ctx.config, group):
            self.page.save_config()
            ctx.bus.publish("led.groups_changed")
            self._key = None
            self.page.refresh()
        if reopen:
            self.show_popup()

    def refresh_popup(self, force: bool = False) -> None:
        if not (force or self.popup.isVisible()):
            return
        cfg = self.page.ctx.config
        key = ([(g.name, g.color, tuple(g.processors)) for g in cfg.led_groups], [p.name for p in cfg.led_processors])
        if key != self._key:
            self._key = key
            while self.rows.count():
                item = self.rows.takeAt(0)
                w = item.widget()
                if w:
                    w.hide()
                    w.deleteLater()
            self._strips = []
            for g in cfg.led_groups:
                name = QtWidgets.QLabel(f"  {g.name}  ")
                name.setStyleSheet(f"background: {g.color}; color: white; border-radius: 9px; font-weight: 700;")
                name.setToolTip(", ".join(g.processors) or "No processors")
                edit = QtWidgets.QPushButton("Edit")
                edit.clicked.connect(lambda _=False, g=g: self._edit_group(g))
                self._add_row(name, g.name, edit)
            for p in cfg.led_processors:
                self._add_row(QtWidgets.QLabel(p.name), p.name, None)
            if not cfg.led_processors:
                self.rows.addWidget(muted("Add LED processors first."))
        for strip in self._strips:
            strip.refresh()

    def _add_row(self, label: QtWidgets.QWidget, target: str, extra) -> None:
        row = QtWidgets.QWidget()
        row.setObjectName("PlainRow")
        lay = QtWidgets.QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        label.setMinimumWidth(110)
        lay.addWidget(label)
        strip = ModeButtons(self.page, target, compact=True, pattern_picker=True)
        self._strips.append(strip)
        lay.addWidget(strip, 1)
        if extra is not None:
            lay.addWidget(extra)
        self.rows.addWidget(row)
