"""File sync / revision control jobs (FreeFileSync, Perforce, Git, Robocopy, custom)."""
from __future__ import annotations

import time

from operator_board.core.config import SyncJobConfig
from operator_board.qt import QtWidgets, exec_
from operator_board.sync.providers import PROVIDERS
from operator_board.ui.pages.base import BasePage
from operator_board.ui.widgets import Card, hbox, make_table, muted, set_row


class JobDialog(QtWidgets.QDialog):
    def __init__(self, parent, job: SyncJobConfig, device_names) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sync job")
        self.setMinimumWidth(680)
        self.job = job
        lay = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QFormLayout()
        self.name = QtWidgets.QLineEdit(job.name)
        self.provider = QtWidgets.QComboBox()
        for pid, prov in PROVIDERS.items():
            self.provider.addItem(prov.label, pid)
        self.provider.setCurrentIndex(max(0, self.provider.findData(job.provider)))
        self.provider.currentIndexChanged.connect(self._build_fields)
        top.addRow("Name", self.name)
        top.addRow("Tool", self.provider)
        lay.addLayout(top)
        self.fields_box = QtWidgets.QGroupBox("Settings")
        self.fields_form = QtWidgets.QFormLayout(self.fields_box)
        lay.addWidget(self.fields_box)
        tg = QtWidgets.QGroupBox("Run on")
        tl = QtWidgets.QVBoxLayout(tg)
        self.targets = {}
        for name in ["local"] + list(device_names):
            box = QtWidgets.QCheckBox("This machine" if name == "local" else f"{name} (via listener)")
            box.setChecked(name in job.targets)
            self.targets[name] = box
            tl.addWidget(box)
        lay.addWidget(tg)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.edits = {}
        self._build_fields()

    def _build_fields(self) -> None:
        while self.fields_form.rowCount():
            self.fields_form.removeRow(0)
        self.edits = {}
        prov = PROVIDERS[self.provider.currentData()]
        for f in prov.fields:
            edit = QtWidgets.QLineEdit(str(self.job.params.get(f.name, f.default)))
            edit.setCursorPosition(0)
            self.edits[f.name] = edit
            if f.kind in ("file", "path"):
                b = QtWidgets.QPushButton("Browse…")
                b.clicked.connect(lambda _=False, e=edit, k=f.kind: self._browse(e, k))
                self.fields_form.addRow(f.label, hbox(edit, b))
            else:
                self.fields_form.addRow(f.label, edit)

    def _browse(self, edit, kind) -> None:
        if kind == "path":
            path = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose folder", edit.text())
        else:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Choose file", edit.text())
        if path:
            edit.setText(path)

    def _accept(self) -> None:
        targets = [n for n, b in self.targets.items() if b.isChecked()]
        if not self.name.text().strip() or not targets:
            QtWidgets.QMessageBox.warning(self, "Sync job", "A name and at least one target are required.")
            return
        self.job.name = self.name.text().strip()
        self.job.provider = self.provider.currentData()
        self.job.params = {k: e.text() for k, e in self.edits.items()}
        self.job.targets = targets
        self.accept()


class SyncPage(BasePage):
    title = "File Sync"
    refresh_topics = ("sync.",)

    def __init__(self, ctx, window) -> None:
        super().__init__(ctx, window)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        jobs = Card("Sync jobs")
        self.jobs = make_table(["Job", "Tool", "Targets", "Status"])
        self.jobs.doubleClicked.connect(lambda _: self._edit())
        jobs.add(self.jobs)
        btns = []
        for label, fn in [("Add job", self._add), ("Edit", self._edit), ("Remove", self._remove),
                          ("▶ Run selected", self._run), ("▶ Run all", lambda: self.invoke("sync.run_all"))]:
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(fn)
            btns.append(b)
        jobs.add_layout(hbox(*btns, None))
        jobs.add(muted("Jobs on render nodes run through their SwitchboardListener, so the tool must be installed at "
                       "the same path on those machines. FreeFileSync jobs use a saved .ffs_batch file."))
        lay.addWidget(jobs, 1)
        hist = Card("History")
        self.history = make_table(["Time", "Job", "Target", "Result", "Message"])
        self.history.itemSelectionChanged.connect(self._show_output)
        hist.add(self.history)
        self.output = QtWidgets.QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumHeight(160)
        hist.add(self.output)
        lay.addWidget(hist, 1)

    def _devices(self):
        return [d.name for d in self.ctx.config.devices]

    def _selected(self):
        rows = sorted({i.row() for i in self.jobs.selectedIndexes()})
        return self.ctx.config.sync_jobs[rows[0]] if rows and rows[0] < len(self.ctx.config.sync_jobs) else None

    def _add(self) -> None:
        job = SyncJobConfig(name=f"Sync {len(self.ctx.config.sync_jobs) + 1}")
        if exec_(JobDialog(self, job, self._devices())):
            self.ctx.config.sync_jobs.append(job)
            self.save_config()
            self.refresh()

    def _edit(self) -> None:
        job = self._selected()
        if job and exec_(JobDialog(self, job, self._devices())):
            self.save_config()
            self.refresh()

    def _remove(self) -> None:
        job = self._selected()
        if job:
            self.ctx.config.sync_jobs.remove(job)
            self.save_config()
            self.refresh()

    def _run(self) -> None:
        job = self._selected()
        if job:
            self.invoke("sync.run", job=job.name)

    def _show_output(self) -> None:
        rows = sorted({i.row() for i in self.history.selectedIndexes()})
        hist = list(reversed(self.ctx.sync.history))
        if rows and rows[0] < len(hist):
            self.output.setPlainText(hist[rows[0]].output)

    def on_event(self, topic, payload) -> None:
        super().on_event(topic, payload)
        if topic == "sync.state" and not payload.get("running"):
            for target, ok, message in payload.get("results", []):
                self.notify(f"Sync {payload.get('job')} on {target}: {message}", ok)

    def refresh(self) -> None:
        self.jobs.setRowCount(0)
        for i, j in enumerate(self.ctx.config.sync_jobs):
            prov = PROVIDERS.get(j.provider)
            last = next((r for r in reversed(self.ctx.sync.history) if r.job == j.name), None)
            status = "running…" if self.ctx.sync.running.get(j.name) else (
                ("OK" if last.ok else "FAILED") if last else "never run")
            set_row(self.jobs, i, [j.name, prov.label if prov else j.provider, ", ".join(j.targets), status])
        self.history.setRowCount(0)
        for i, r in enumerate(reversed(self.ctx.sync.history)):
            set_row(self.history, i, [time.strftime("%H:%M:%S", time.localtime(r.started)), r.job, r.target,
                                      "OK" if r.ok else "FAILED", r.message])
