"""Live instance analytics: graphs plus the raw data behind them."""
from __future__ import annotations

import json
import time

from operator_board.qt import QtCore, QtWidgets, Qt
from operator_board.ui.pages.base import BasePage
from operator_board.ui.widgets import Card, LineChart, hbox, make_table, muted, set_row

WINDOWS = [("1 min", 60), ("5 min", 300), ("15 min", 900), ("1 hour", 3600)]


class AnalyticsPage(BasePage):
    title = "Analytics"

    def __init__(self, ctx, window) -> None:
        super().__init__(ctx, window)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        side = Card("Series")
        self.sources = QtWidgets.QListWidget()
        self.sources.currentTextChanged.connect(self._fill_metrics)
        self.metrics = QtWidgets.QListWidget()
        self.metrics.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
        self.metrics.itemSelectionChanged.connect(self._redraw)
        side.add(QtWidgets.QLabel("Source"))
        side.add(self.sources)
        side.add(QtWidgets.QLabel("Metrics"))
        side.add(self.metrics)
        self.window_combo = QtWidgets.QComboBox()
        for label, secs in WINDOWS:
            self.window_combo.addItem(label, secs)
        self.window_combo.setCurrentIndex(1)
        self.window_combo.currentIndexChanged.connect(self._redraw)
        export = QtWidgets.QPushButton("Export CSV…")
        export.clicked.connect(self._export)
        side.add_layout(hbox(self.window_combo, export))
        lay.addWidget(side, 1)

        main = QtWidgets.QVBoxLayout()
        chart_card = Card("Graph")
        self.chart = LineChart()
        chart_card.add(self.chart)
        main.addWidget(chart_card, 3)
        raw = Card("Raw data")
        tabs = QtWidgets.QTabWidget()
        self.samples = make_table(["Time", "Metric", "Value"])
        self.raw_json = QtWidgets.QPlainTextEdit()
        self.raw_json.setReadOnly(True)
        tabs.addTab(self.samples, "Samples")
        tabs.addTab(self.raw_json, "Last listener payload")
        raw.add(tabs)
        raw.add(muted("CPU/GPU/memory figures come from each SwitchboardListener's sync status for running "
                      "instances. 'Stage' tracks running instance and multi-user client counts."))
        main.addWidget(raw, 2)
        lay.addLayout(main, 3)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def _tick(self) -> None:
        if self.isVisible():
            self.refresh()

    def _fill_metrics(self, source: str) -> None:
        selected = {i.text() for i in self.metrics.selectedItems()}
        self.metrics.blockSignals(True)
        self.metrics.clear()
        for m in self.ctx.metrics.metrics(source):
            item = QtWidgets.QListWidgetItem(m)
            self.metrics.addItem(item)
            item.setSelected(m in selected)
        if not self.metrics.selectedItems() and self.metrics.count():
            self.metrics.item(0).setSelected(True)
        self.metrics.blockSignals(False)
        self._redraw()

    def _redraw(self) -> None:
        source = self.sources.currentItem().text() if self.sources.currentItem() else ""
        chosen = [i.text() for i in self.metrics.selectedItems()]
        self.chart.window_s = float(self.window_combo.currentData())
        self.chart.set_series([(m, self.ctx.metrics.series(source, m)) for m in chosen])
        self.samples.setRowCount(0)
        rows = []
        for m in chosen:
            rows += [(t, m, v) for t, v in self.ctx.metrics.series(source, m)[-200:]]
        rows.sort(reverse=True)
        for i, (t, m, v) in enumerate(rows[:300]):
            set_row(self.samples, i, [time.strftime("%H:%M:%S", time.localtime(t)), m, f"{v:g}"])
        self.raw_json.setPlainText(json.dumps(self.ctx.metrics.raw.get(source, {}), indent=2, default=str))

    def _export(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export metrics", "metrics.csv", "CSV (*.csv)")
        if path:
            n = self.ctx.metrics.export_csv(path)
            self.notify(f"Exported {n} samples to {path}")

    def refresh(self) -> None:
        current = self.sources.currentItem().text() if self.sources.currentItem() else ""
        sources = self.ctx.metrics.sources()
        if [self.sources.item(i).text() for i in range(self.sources.count())] != sources:
            self.sources.blockSignals(True)
            self.sources.clear()
            self.sources.addItems(sources)
            self.sources.blockSignals(False)
            matches = self.sources.findItems(current, Qt.MatchExactly)
            if matches:
                self.sources.setCurrentItem(matches[0])
            elif sources:
                self.sources.setCurrentRow(0)
            self._fill_metrics(self.sources.currentItem().text() if self.sources.currentItem() else "")
        else:
            self._redraw()
