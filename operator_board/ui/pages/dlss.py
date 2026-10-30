"""NVIDIA DLSS controls (shown when the project uses the DLSS / Streamline plugins)."""
from __future__ import annotations

from operator_board.qt import QtWidgets, Qt
from operator_board.remote.targets import TARGET_CHOICES
from operator_board.ui.pages.base import BasePage
from operator_board.ui.widgets import Card, hbox, muted


class DLSSPage(BasePage):
    title = "DLSS"

    def __init__(self, ctx, window) -> None:
        super().__init__(ctx, window)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        card = Card("NVIDIA DLSS")
        self.target = QtWidgets.QComboBox()
        for t in TARGET_CHOICES:
            self.target.addItem(t)
        self.target.setCurrentText("all")
        detect = QtWidgets.QPushButton("Detect DLSS plugin")
        detect.clicked.connect(self._detect)
        self.status = muted("Not checked yet.")
        card.add_layout(hbox(QtWidgets.QLabel("Apply to"), self.target, detect, None))
        card.add(self.status)
        form = QtWidgets.QFormLayout()
        for control in ctx.dlss.controls:
            form.addRow(control["label"], self._control_widget(control))
        card.add_layout(form)
        card.add(muted(ctx.dlss.profile.get("notes", "")))
        lay.addWidget(card)
        lay.addStretch(1)

    def _control_widget(self, control):
        cid, kind = control["id"], control.get("kind")
        value = self.ctx.dlss.values.get(cid)
        if kind == "choice":
            combo = QtWidgets.QComboBox()
            combo.addItems(control.get("choices", []))
            combo.setCurrentText(str(value))
            combo.activated.connect(lambda _=0: self._apply(cid, combo.currentText()))
            return combo
        if kind == "bool":
            box = QtWidgets.QCheckBox()
            box.setChecked(bool(value))
            box.toggled.connect(lambda v: self._apply(cid, v))
            return box
        slider = QtWidgets.QSlider(Qt.Horizontal)
        slider.setRange(int(control.get("min", 0)), int(control.get("max", 100)))
        slider.setValue(int(value or 0))
        label = QtWidgets.QLabel(str(slider.value()))
        slider.valueChanged.connect(lambda v: label.setText(str(v)))
        slider.sliderReleased.connect(lambda: self._apply(cid, slider.value()))
        holder = QtWidgets.QWidget()
        holder.setLayout(hbox(slider, label))
        return holder

    def _apply(self, cid, value) -> None:
        target = self.target.currentText()
        self.background(lambda: self.ctx.dlss.apply(cid, value, target), self.report(f"DLSS {cid} → {value}"), "DLSS")

    def _detect(self) -> None:
        def show(found):
            lines = [f"{'✔' if ok else '✘'} {path.split('.')[-1].replace('Default__', '')}" for path, ok in found.items()]
            self.status.setText("   ".join(lines) if lines else "Nothing to detect")
        self.background(self.ctx.dlss.detect, show, "DLSS detection")
