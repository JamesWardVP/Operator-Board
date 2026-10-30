"""Slate / take control and the take log."""
from __future__ import annotations

import time

from operator_board.qt import QtWidgets
from operator_board.ui.pages.base import BasePage
from operator_board.ui.theme import COLORS
from operator_board.ui.widgets import BigButton, Card, hbox, make_table, muted, set_row

RATINGS = [("", ""), ("Good", "good"), ("Circle", "circle"), ("NG", "ng")]


class TakesPage(BasePage):
    title = "Takes"
    refresh_topics = ("takes.",)

    def __init__(self, ctx, window) -> None:
        super().__init__(ctx, window)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)

        ctl = Card("Slate & Take")
        s = ctx.config.takes
        self.slate = QtWidgets.QLineEdit(s.slate)
        self.take = QtWidgets.QSpinBox()
        self.take.setRange(1, 9999)
        self.take.setValue(s.take)
        self.desc = QtWidgets.QLineEdit(s.description)
        self.auto = QtWidgets.QCheckBox("Auto-increment take after each recording")
        self.auto.setChecked(s.auto_increment)
        self.tr = QtWidgets.QCheckBox("Drive Take Recorder in the primary editor")
        self.tr.setChecked(s.record_with_take_recorder)
        form = QtWidgets.QFormLayout()
        form.addRow("Slate", self.slate)
        form.addRow("Take", self.take)
        form.addRow("Description", self.desc)
        ctl.add_layout(form)
        ctl.add(self.auto)
        ctl.add(self.tr)
        apply = QtWidgets.QPushButton("Apply slate")
        apply.clicked.connect(self._apply)
        ctl.add(apply)
        self.big = QtWidgets.QLabel()
        self.big.setObjectName("Big")
        ctl.add(self.big)
        self.timer_label = muted("")
        ctl.add(self.timer_label)
        self.rec = BigButton("● RECORD", COLORS["err"], height=100)
        self.rec.clicked.connect(self._record)
        ctl.add(self.rec)
        ctl.layout_.addStretch(1)
        lay.addWidget(ctl, 1)

        logc = Card("Take log")
        self.table = make_table(["Slate", "Take", "Description", "Level", "Start", "Duration", "Rating", "Notes"])
        logc.add(self.table)
        self.rating = QtWidgets.QComboBox()
        for label, value in RATINGS:
            self.rating.addItem(label or "(no rating)", value)
        self.notes = QtWidgets.QLineEdit()
        self.notes.setPlaceholderText("Notes")
        rate = QtWidgets.QPushButton("Set rating / notes")
        rate.clicked.connect(self._rate)
        export = QtWidgets.QPushButton("Export CSV…")
        export.clicked.connect(self._export)
        logc.add_layout(hbox(self.rating, self.notes, rate, export))
        logc.add(muted(f"Take log is saved automatically to {ctx.takes.log_path}"))
        lay.addWidget(logc, 2)

        from operator_board.qt import QtCore
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(500)

    def _apply(self) -> None:
        s = self.ctx.config.takes
        s.auto_increment = self.auto.isChecked()
        s.record_with_take_recorder = self.tr.isChecked()
        self.ctx.takes.set_slate(self.slate.text().strip() or s.slate, self.take.value(), self.desc.text())
        self.save_config()

    def _record(self) -> None:
        if not self.ctx.takes.recording:
            self._apply()
        self.invoke("takes.toggle_record")

    def _rate(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        for r in rows:
            self.ctx.takes.rate(r, self.rating.currentData(), self.notes.text())

    def _export(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export take log", "take_log.csv", "CSV (*.csv)")
        if path:
            self.ctx.takes.export_csv(path)
            self.notify(f"Exported take log to {path}")

    def _tick(self) -> None:
        cur = self.ctx.takes.current
        self.timer_label.setText(f"Recording {time.time() - cur.started:,.0f}s" if cur else "")

    def refresh(self) -> None:
        s = self.ctx.config.takes
        self.take.setValue(s.take)
        if not self.slate.hasFocus():
            self.slate.setText(s.slate)
        self.big.setText(f"{s.slate} · T{s.take:03d}")
        rec = self.ctx.takes.recording
        self.rec.setText("■ STOP" if rec else "● RECORD")
        self.rec.set_active(rec)
        self.table.setRowCount(0)
        for i, t in enumerate(self.ctx.takes.log.takes):
            set_row(self.table, i, [t.slate, t.take, t.description, t.level.split("/")[-1],
                                    time.strftime("%H:%M:%S", time.localtime(t.started)) if t.started else "",
                                    f"{t.duration_s:.1f}s", t.rating, t.notes])
        self.table.scrollToBottom()
