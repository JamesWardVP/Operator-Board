"""Saved Settings: presets of several kinds of settings applied in one go, and the change log with
exports for production (CSV, or a spreadsheet with takes, camera positions, LED processors...)."""
from __future__ import annotations

import copy
import time

from operator_board.core.changelog import COLUMNS, show
from operator_board.core.presets import (ITEM_TYPES, PresetProblem, capture, export_item, export_preset, item_label,
                                         item_summary, ordered, read_file, steps)
from operator_board.qt import QtWidgets, Qt
from operator_board.ui.pages.base import BasePage
from operator_board.ui.widgets import Card, hbox, make_table, muted, set_row

LOG_ROWS = 500  # newest changes shown in the table (the exports hold all of them)
LOG_COLUMNS = [c for c in COLUMNS if c[0] not in ("project", "level")]


def unique(name: str, taken) -> str:
    taken = {t.lower() for t in taken}
    if name.lower() not in taken:
        return name
    n = 2
    while f"{name} {n}".lower() in taken:
        n += 1
    return f"{name} {n}"


class SavedSettingsPage(BasePage):
    title = "Saved Settings"
    refresh_topics = ("changes.",)

    def __init__(self, ctx, window) -> None:
        super().__init__(ctx, window)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._presets_tab(), "Presets")
        tabs.addTab(self._log_tab(), "Change log && production data")
        lay.addWidget(tabs)
        self._run = None  # the preset being applied: {"steps", "index", "name"}

    # ---- presets --------------------------------------------------------------------------
    @property
    def presets(self):
        return self.ctx.config.setting_presets

    def _presets_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        left = Card("Presets")
        self.preset_list = QtWidgets.QListWidget()
        self.preset_list.currentRowChanged.connect(lambda _r: self._fill_items())
        left.add(self.preset_list)
        buttons = []
        for label, fn in (("New", self._new), ("Rename", self._rename), ("Duplicate", self._duplicate),
                          ("Delete", self._delete), ("Import…", self._import), ("Export…", self._export)):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(fn)
            buttons.append(b)
        left.add_layout(hbox(*buttons[:4], None))
        left.add_layout(hbox(*buttons[4:], None))
        left.add(muted("A preset holds several kinds of settings and applies them in one go. Presets are saved "
                       "with the stage; export them to use on another stage or machine."))
        lay.addWidget(left, 1)

        right = Card("Settings in this preset (applied top to bottom)")
        self.items = make_table(["Order", "Setting", "What it holds"])
        right.add(self.items)
        add = QtWidgets.QToolButton()
        add.setText("+ Add current…")
        add.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        menu = QtWidgets.QMenu(add)
        for kind, (label, what) in ITEM_TYPES.items():
            action = menu.addAction(label)
            action.setToolTip(what)
            action.triggered.connect(lambda _=False, k=kind: self._add_current(k))
        add.setMenu(menu)
        from_file = QtWidgets.QPushButton("Add from file…")
        from_file.setToolTip("A settings preset or item, a Remote Objects export, a control surfaces export or a "
                             "camera positions CSV")
        from_file.clicked.connect(self._add_from_file)
        remove = QtWidgets.QPushButton("Remove")
        remove.clicked.connect(self._remove_item)
        export = QtWidgets.QPushButton("Export setting…")
        export.clicked.connect(self._export_item)
        right.add_layout(hbox(add, from_file, remove, export, None))
        self.apply_btn = QtWidgets.QPushButton("▶  Apply preset")
        self.apply_btn.setMinimumHeight(44)
        self.apply_btn.setStyleSheet("QPushButton { font-size: 16px; font-weight: 700; }")
        self.apply_btn.clicked.connect(self._apply)
        right.add(self.apply_btn)
        self.progress = QtWidgets.QListWidget()
        self.progress.setMaximumHeight(200)
        right.add(self.progress)
        right.add(muted("Settings are applied in a fixed order (the level first, then its sub-levels, data layers, "
                        "actors, camera positions, LED processors, take and control surfaces). Each step is checked "
                        "before the next starts; if one fails or its data is missing you can retry it, skip it or "
                        "stop there."))
        lay.addWidget(right, 2)
        return w

    def _current(self):
        row = self.preset_list.currentRow()
        return self.presets[row] if 0 <= row < len(self.presets) else None

    def _fill_presets(self, select: int = None) -> None:
        current = self.preset_list.currentRow() if select is None else select
        self.preset_list.blockSignals(True)
        self.preset_list.clear()
        for p in self.presets:
            self.preset_list.addItem(f"{p.get('name', 'Preset')}  ({len(p.get('items', []))})")
        self.preset_list.blockSignals(False)
        if self.presets:
            self.preset_list.setCurrentRow(max(0, min(current, len(self.presets) - 1)))
        self._fill_items()

    def _fill_items(self) -> None:
        preset = self._current()
        items = ordered(preset.get("items", [])) if preset else []
        self.items.setRowCount(0)
        for i, item in enumerate(items):
            set_row(self.items, i, [i + 1, item_label(item), item_summary(item)])
        self.apply_btn.setEnabled(bool(items) and self._run is None)

    def _changed(self, select: int = None) -> None:
        self.save_config()
        self._fill_presets(select)

    def _new(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "New preset", "Preset name:",
                                                  text=unique("Preset", (p.get("name", "") for p in self.presets)))
        if ok and name.strip():
            self.presets.append({"name": unique(name.strip(), (p.get("name", "") for p in self.presets)), "items": []})
            self._changed(len(self.presets) - 1)

    def _rename(self) -> None:
        preset = self._current()
        if preset is None:
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "Rename preset", "Preset name:", text=preset.get("name", ""))
        if ok and name.strip():
            preset["name"] = unique(name.strip(), (p.get("name", "") for p in self.presets if p is not preset))
            self._changed()

    def _duplicate(self) -> None:
        preset = self._current()
        if preset is not None:
            copy_ = copy.deepcopy(preset)
            copy_["name"] = unique(preset.get("name", "Preset"), (p.get("name", "") for p in self.presets))
            self.presets.append(copy_)
            self._changed(len(self.presets) - 1)

    def _delete(self) -> None:
        preset = self._current()
        if preset is not None and QtWidgets.QMessageBox.question(
                self, "Delete preset", f"Delete the preset '{preset.get('name')}'?") == QtWidgets.QMessageBox.Yes:
            self.presets.remove(preset)
            self._changed()

    def _import(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import a preset", "",
                                                        "Settings (*.json *.csv);;All files (*.*)")
        if not path:
            return
        try:
            preset = read_file(path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.notify(f"Could not import: {exc}", False)
            return
        preset["name"] = unique(preset.get("name", "Imported preset"), (p.get("name", "") for p in self.presets))
        self.presets.append(preset)
        self._changed(len(self.presets) - 1)
        self.notify(f"Imported {preset['name']}")

    def _export(self) -> None:
        preset = self._current()
        if preset is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export preset", f"{preset.get('name', 'preset')}.json",
                                                        "Settings preset (*.json)")
        if path:
            try:
                export_preset(path, preset)
            except OSError as exc:
                self.notify(f"Could not export: {exc}", False)
                return
            self.notify(f"Exported {preset.get('name')} to {path}")

    def _put_item(self, item) -> None:
        """Add an item to the selected preset, replacing one of the same kind."""
        preset = self._current()
        if preset is None:
            self.presets.append({"name": unique("Preset", (p.get("name", "") for p in self.presets)), "items": []})
            self._fill_presets(len(self.presets) - 1)
            preset = self._current()
        items = preset.setdefault("items", [])
        items[:] = [i for i in items if i.get("type") != item.get("type")] + [item]
        self._changed()

    def _add_current(self, kind: str) -> None:
        def failed(exc) -> None:
            message = str(exc) if isinstance(exc, PresetProblem) else f"Could not read the current {kind}: {exc}"
            self.notify(message, False)
        self.background(lambda: capture(self.ctx, kind), lambda item: (self._put_item(item),
                                                                     self.notify(f"Added {item_label(item)}")),
                        f"Reading {ITEM_TYPES[kind][0]}", quiet=True, failed=failed)

    def _add_from_file(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Add settings from a file", "",
                                                        "Settings (*.json *.csv);;All files (*.*)")
        if not path:
            return
        try:
            found = read_file(path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.notify(f"Could not read {path}: {exc}", False)
            return
        for item in found.get("items", []):
            self._put_item(item)
        self.notify(f"Added {', '.join(item_label(i) for i in found.get('items', []))}")

    def _selected_item(self):
        preset = self._current()
        rows = sorted({i.row() for i in self.items.selectedIndexes()})
        items = ordered(preset.get("items", [])) if preset else []
        return (preset, items[rows[0]]) if rows and rows[0] < len(items) else (preset, None)

    def _remove_item(self) -> None:
        preset, item = self._selected_item()
        if item is not None:
            preset["items"].remove(item)
            self._changed()

    def _export_item(self) -> None:
        _preset, item = self._selected_item()
        if item is None:
            self.notify("Select a setting first", False)
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export setting", f"{item_label(item)}.json",
                                                        "Settings item (*.json)")
        if path:
            try:
                export_item(path, item)
            except OSError as exc:
                self.notify(f"Could not export: {exc}", False)
                return
            self.notify(f"Exported {item_label(item)} to {path}")

    # ---- applying a preset ------------------------------------------------------------------
    def _apply(self) -> None:
        preset = self._current()
        if preset is None or not preset.get("items") or self._run is not None:
            return
        plan = steps(self.ctx, preset)
        listing = "\n".join(f"{n}. {label}" for n, (label, _fn) in enumerate(plan, start=1))
        if QtWidgets.QMessageBox.question(self, "Apply preset", f"Apply '{preset.get('name')}'?\n\n{listing}") \
                != QtWidgets.QMessageBox.Yes:
            return
        self.progress.clear()
        for label, _fn in plan:
            self.progress.addItem(f"•  {label}")
        self._run = {"steps": plan, "index": 0, "name": preset.get("name", ""), "started": time.time(),
                     "done": 0, "skipped": 0}
        self.apply_btn.setEnabled(False)
        self._next_step()

    def _mark(self, index: int, symbol: str, text: str) -> None:
        item = self.progress.item(index)
        if item is not None:
            label = self._run["steps"][index][0]
            item.setText(f"{symbol}  {label}" + (f"  —  {text}" if text else ""))
            self.progress.scrollToItem(item)

    def _next_step(self) -> None:
        run = self._run
        if run is None:
            return
        if run["index"] >= len(run["steps"]):
            self.notify(f"Applied {run['name']}: {run['done']} done" + (f", {run['skipped']} skipped" if run["skipped"]
                                                                        else ""), not run["skipped"])
            self.ctx.changes.record("Saved Settings", run["name"], "Preset applied",
                                    f"{run['done']} done, {run['skipped']} skipped")
            self._finish()
            return
        index = run["index"]
        label, fn = run["steps"][index]
        self._mark(index, "…", "working")
        self.background(fn, lambda message: self._step_done(index, message), label, quiet=True,
                        failed=lambda exc: self._step_failed(index, exc))

    def _step_done(self, index: int, message: str) -> None:
        self._mark(index, "✓", message)
        self._run["done"] += 1
        self._run["index"] = index + 1
        self._next_step()

    def _step_failed(self, index: int, exc: Exception) -> None:
        run = self._run
        label = run["steps"][index][0]
        self._mark(index, "✗", str(exc))
        box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Warning, "Preset step did not complete",
                                    f"Step {index + 1} of {len(run['steps'])}: {label}", parent=self)
        box.setInformativeText(f"{exc}\n\nRetry this step, skip it and carry on, or stop here?")
        retry = box.addButton("Retry", QtWidgets.QMessageBox.AcceptRole)
        skip = box.addButton("Skip", QtWidgets.QMessageBox.DestructiveRole)
        cancel = box.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
        box.setDefaultButton(retry)
        box.exec_() if hasattr(box, "exec_") else box.exec()
        clicked = box.clickedButton()
        if clicked is retry:
            self._next_step()
        elif clicked is skip:
            self._mark(index, "↷", f"skipped ({exc})")
            run["skipped"] += 1
            run["index"] = index + 1
            self._next_step()
        else:
            for later in range(index + 1, len(run["steps"])):
                self._mark(later, "–", "not applied")
            self.notify(f"Stopped applying {run['name']} at step {index + 1}", False)
            self.ctx.changes.record("Saved Settings", run["name"], "Preset stopped", f"at step {index + 1}: {label}",
                                    result=f"failed: {exc}")
            self._finish()
        _ = cancel

    def _finish(self) -> None:
        self._run = None
        self._fill_items()

    # ---- change log ----------------------------------------------------------------------------
    def _log_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        card = Card("Change log")
        card.add(muted("Every setting changed from Operator Board, with the date and time, scene, take and timecode "
                       "it was changed at, and the value before. Export it as CSV, or as a spreadsheet that also "
                       "holds the takes, camera positions, LED processors and Unreal instances, for DITs, data "
                       "wranglers and VFX."))
        self.log_table = make_table([title for _k, title in LOG_COLUMNS])
        card.add(self.log_table)
        buttons = []
        for label, fn in (("Export CSV…", self._export_csv), ("Export spreadsheet (.xlsx)…", self._export_xlsx),
                          ("Clear log…", self._clear_log)):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(fn)
            buttons.append(b)
        self.log_count = muted("")
        card.add_layout(hbox(*buttons, None, self.log_count))
        lay.addWidget(card)
        return w

    def _fill_log(self) -> None:
        records = self.ctx.changes.records
        shown = records[-LOG_ROWS:][::-1]  # newest first
        self.log_table.setRowCount(0)
        for i, r in enumerate(shown):
            set_row(self.log_table, i, [show(getattr(r, key)) for key, _t in LOG_COLUMNS])
        self.log_count.setText(f"{len(records)} change{'s' if len(records) != 1 else ''}"
                               + (f" (newest {LOG_ROWS} shown)" if len(records) > LOG_ROWS else ""))

    def _stamp(self) -> str:
        return time.strftime("%Y%m%d_%H%M")

    def _export_csv(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export change log",
                                                        f"operator_board_changes_{self._stamp()}.csv", "CSV (*.csv)")
        if path:
            try:
                n = self.ctx.changes.export_csv(path)
            except OSError as exc:
                self.notify(f"Could not export: {exc}", False)
                return
            self.notify(f"Exported {n} changes to {path}")

    def _export_xlsx(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export production data",
                                                        f"operator_board_production_{self._stamp()}.xlsx",
                                                        "Excel workbook (*.xlsx)")
        if path:
            self.background(lambda: self.ctx.production_export(path),
                            lambda _r: self.notify(f"Exported production data to {path}"), "Export spreadsheet")

    def _clear_log(self) -> None:
        if QtWidgets.QMessageBox.question(
                self, "Clear change log", "Start a new change log? The current one is kept next to it as "
                                          "change_log.jsonl.old. Export it first if you need it.") \
                == QtWidgets.QMessageBox.Yes:
            self.ctx.changes.clear()
            self.ctx._session_start = 0
            self._fill_log()

    def refresh(self) -> None:
        if self.preset_list.count() != len(self.presets):
            self._fill_presets()
        self._fill_log()
