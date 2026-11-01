"""Buttons that put one LED processor or a group of them into a mode (freeze, blackout, test pattern,
locate, normal), lit for the mode it is in. Used on the LED Processor Setup page (in a row) and on the
LED Processors page (in a column)."""
from __future__ import annotations

from typing import List, Optional

from operator_board.led.processor import CAP_BLACKOUT, CAP_FREEZE, CAP_LOCATE, CAP_TEST_PATTERN
from operator_board.qt import QtWidgets, Qt
from operator_board.ui.led_status import MODE_COLORS, MODE_LABELS
from operator_board.ui.theme import COLORS

ORDER = ("frozen", "blackout", "pattern", "locate", "normal")
NEEDS = {"frozen": CAP_FREEZE, "blackout": CAP_BLACKOUT, "pattern": CAP_TEST_PATTERN, "locate": CAP_LOCATE}


def mode_button_style(mode: str, lit: bool, compact: bool = False) -> str:
    colour = MODE_COLORS[mode]
    pad = "4px 8px" if compact else "8px 10px"
    if lit:
        return (f"QPushButton {{ background: {colour}; border: 2px solid #ffffff; border-radius: 8px; color: white;"
                f" font-weight: 800; padding: {pad}; }}")
    return (f"QPushButton {{ background: {COLORS['panel2']}; border: 1px solid {COLORS['border']}; border-radius: 8px;"
            f" padding: {pad}; }} QPushButton:hover {{ border-color: {colour}; }}"
            f" QPushButton:disabled {{ color: {COLORS['muted']}; }}")


class ModeButtons(QtWidgets.QWidget):
    """Five mode buttons for ``target`` (a processor name or a group name)."""

    def __init__(self, page, target: str, vertical: bool = False, compact: bool = False,
                 pattern_picker: bool = False, modes=ORDER) -> None:
        super().__init__()
        self.setObjectName("PlainRow")
        self.page, self.target, self.compact = page, target, compact
        lay = QtWidgets.QVBoxLayout(self) if vertical else QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6 if vertical else 4)
        self.buttons = {}
        for mode in modes:
            # A lone Normal button (the setup table's column) reads "Resume", as in the column heading.
            label = {"normal": "Resume" if len(modes) == 1 else "Normal"}.get(mode, MODE_LABELS[mode])
            b = QtWidgets.QPushButton(label)
            b.setCursor(Qt.PointingHandCursor)
            if vertical:
                b.setMinimumHeight(44)
            b.clicked.connect(lambda _=False, m=mode: self._clicked(m))
            self.buttons[mode] = b
            lay.addWidget(b)
        self.pattern = None
        if pattern_picker:
            self.pattern = QtWidgets.QComboBox()
            self.pattern.setToolTip("The test pattern the Test pattern button shows")
            lay.addWidget(self.pattern)
        self._lit = None

    def names(self) -> List[str]:
        try:
            return self.page.ctx.led.resolve(self.target)
        except KeyError:
            return []

    def _clicked(self, mode: str) -> None:
        ctx = self.page.ctx
        if mode == "blackout" and ctx.config.ui.confirm_blackout and ctx.led.mode_of(self.names()) != "blackout":
            if QtWidgets.QMessageBox.question(self, "Confirm", f"Black out {self.target}?") \
                    != QtWidgets.QMessageBox.Yes:
                return
        pattern = self.pattern.currentData() if self.pattern is not None and mode == "pattern" else ""
        self.page.invoke("led.set_mode", target=self.target, mode=mode, pattern=pattern or "")

    def refresh(self) -> None:
        led = self.page.ctx.led
        names = self.names()
        procs = [p for p in led.active() if p.name in names]
        mode: Optional[str] = led.mode_of(names) if procs else None
        caps = set().union(*[set(p.capabilities()) for p in procs]) if procs else set()
        for m, b in self.buttons.items():
            supported = m == "normal" or NEEDS[m] in caps
            b.setEnabled(bool(procs) and supported)
            b.setToolTip("" if supported else "Not supported by this processor type (add the commands with "
                                              "Edit commands… on LED Processor Setup)")
            style = mode_button_style(m, m == mode, self.compact)
            if b.styleSheet() != style:
                b.setStyleSheet(style)
        if self.pattern is not None:
            patterns = []
            seen = set()
            for p in procs:
                for t in p.test_patterns:
                    if t["id"] not in seen:
                        seen.add(t["id"])
                        patterns.append((t["id"], t.get("label", t["id"])))
            if [self.pattern.itemData(i) for i in range(self.pattern.count())] != [pid for pid, _ in patterns]:
                current = self.pattern.currentData()
                self.pattern.blockSignals(True)
                self.pattern.clear()
                for pid, label in patterns:
                    self.pattern.addItem(label, pid)
                self.pattern.setCurrentIndex(max(0, self.pattern.findData(current)))
                self.pattern.blockSignals(False)
            showing = led.pattern_of(names)
            if mode == "pattern" and showing and self.pattern.findData(showing) >= 0 and not self.pattern.hasFocus():
                self.pattern.setCurrentIndex(self.pattern.findData(showing))
            self.pattern.setEnabled(bool(patterns))
