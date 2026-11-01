"""LED Processors: the wall status bar, then a column for each LED group and each processor with
buttons for its five modes (the current one lit) and its own test pattern choice."""
from __future__ import annotations

from operator_board.qt import QtWidgets, Qt
from operator_board.ui.group_dialog import edit_led_group
from operator_board.ui.led_controls import ModeButtons
from operator_board.ui.led_status import status_groups
from operator_board.ui.pages.base import BasePage
from operator_board.ui.widgets import Card, WallStatusBar, hbox, muted

COLUMN_WIDTH = 190
STATE_WORDS = {"frozen": "Frozen", "blackout": "Blacked out", "pattern": "Test pattern", "locate": "Locating",
               "normal": "Normal"}


class LEDProcessorsPage(BasePage):
    title = "LED Processors"
    refresh_topics = ("led.",)

    def __init__(self, ctx, window) -> None:
        super().__init__(ctx, window)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        self.banner = WallStatusBar()
        lay.addWidget(self.banner)
        new_group = QtWidgets.QPushButton("+ New group")
        new_group.setToolTip("A group of LED processors controlled together (the same groups as on LED Processor Setup)")
        new_group.clicked.connect(lambda: self._edit_group(None))
        setup = QtWidgets.QPushButton("LED Processor Setup…")
        setup.clicked.connect(lambda: self.window_.show_page("LED Processor Setup"))
        lay.addLayout(hbox(new_group, setup, None,
                           muted("Each button puts that group or processor into the mode; pressing the lit one "
                                 "returns it to normal.")))
        self.columns_box = QtWidgets.QWidget()
        self.columns_box.setObjectName("PlainRow")
        self.columns = QtWidgets.QHBoxLayout(self.columns_box)
        self.columns.setContentsMargins(0, 0, 0, 0)
        self.columns.setSpacing(12)
        area = QtWidgets.QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QtWidgets.QFrame.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        area.setWidget(self.columns_box)
        lay.addWidget(area, 1)
        self._key = None
        self._strips = []
        self._mode_labels = {}

    def _edit_group(self, group) -> None:
        if edit_led_group(self, self.ctx.config, group):
            self.save_config()
            self.ctx.bus.publish("led.groups_changed")
            self._key = None
            self.refresh()

    def _column(self, title: str, target: str, colour: str = "", group=None) -> Card:
        card = Card(title)
        card.setFixedWidth(COLUMN_WIDTH)
        if colour:
            card.setStyleSheet(f"QFrame#Card {{ border-top: 4px solid {colour}; }}")
        mode = QtWidgets.QLabel("")
        mode.setObjectName("Muted")
        card.add(mode)
        self._mode_labels[target] = mode
        strip = ModeButtons(self, target, vertical=True, pattern_picker=True)
        self._strips.append(strip)
        card.add(strip)
        if group is not None:
            members = muted(", ".join(group.processors) or "No processors")
            members.setWordWrap(True)
            card.add(members)
            edit = QtWidgets.QPushButton("Edit group")
            edit.clicked.connect(lambda _=False, g=group: self._edit_group(g))
            card.add(edit)
        card.layout_.addStretch(1)
        return card

    def refresh(self) -> None:
        led = self.ctx.led
        cfg = self.ctx.config
        groups = status_groups(led)
        if not led.active():
            self.banner.show_wall("unavailable")
        else:
            self.banner.show_processors(groups)
        key = ([(g.name, g.color, tuple(g.processors)) for g in cfg.led_groups],
               [(p.name, p.enabled) for p in cfg.led_processors])
        if key != self._key:
            self._key = key
            while self.columns.count():
                item = self.columns.takeAt(0)
                w = item.widget()
                if w:
                    w.hide()
                    w.deleteLater()
            self._strips, self._mode_labels = [], {}
            for g in cfg.led_groups:
                self.columns.addWidget(self._column(g.name, g.name, g.color, g))
            for p in cfg.led_processors:
                if p.enabled:
                    self.columns.addWidget(self._column(p.name, p.name))
            if not cfg.led_processors:
                self.columns.addWidget(muted("No LED processors yet: add them on LED Processor Setup."))
            self.columns.addStretch(1)
        labels = dict(led.all_test_patterns())
        for strip in self._strips:
            strip.refresh()
            names = strip.names()
            mode = led.mode_of(names)
            text = "mixed" if mode is None else STATE_WORDS.get(mode, mode)
            if mode == "pattern":
                pattern = led.pattern_of(names)
                text = f"Test pattern: {labels.get(pattern, pattern) if pattern else 'mixed'}"
            label = self._mode_labels.get(strip.target)
            if label is not None:
                label.setText(f"Now: {text}" if names else "No enabled processors")
