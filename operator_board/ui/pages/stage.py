"""Stage page: open, create and save Switchboard configs, the same files Switchboard uses."""
from __future__ import annotations

import os
import time

from operator_board.core.switchboard_config import (candidate_config_dirs, config_path_for_name, config_summary,
                                                    default_config_dir, find_engine_installs, list_configs)
from operator_board.core.stage import sidecar_path
from operator_board.qt import QtCore, QtGui, QtWidgets, exec_
from operator_board.ui.pages.base import BasePage
from operator_board.ui.theme import COLORS
from operator_board.ui.widgets import Card, hbox, make_table, muted, set_row

AUTO = ""  # folder combo data for "same folder as Switchboard"


class NewConfigDialog(QtWidgets.QDialog):
    def __init__(self, parent, folder: str, ctx) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Switchboard config")
        self.setMinimumWidth(620)
        self.folder = folder
        project = ctx.config.project()
        form = QtWidgets.QFormLayout(self)
        self.name = QtWidgets.QLineEdit()
        self.name.setPlaceholderText("e.g. Stage_A")
        form.addRow("Config name", self.name)
        self.uproject = QtWidgets.QLineEdit(project.uproject_path)
        browse = QtWidgets.QPushButton("Browse…")
        browse.clicked.connect(self._browse_uproject)
        form.addRow("Project (.uproject)", hbox(self.uproject, browse))
        self.engine = QtWidgets.QComboBox()
        self.engine.setEditable(True)
        for version, root in find_engine_installs([project.engine_dir] if project.engine_dir else None):
            self.engine.addItem(f"UE {version} — {root}", root)
        if project.engine_dir:
            idx = self.engine.findData(project.engine_dir)
            if idx < 0:
                self.engine.insertItem(0, project.engine_dir, project.engine_dir)
                idx = 0
            self.engine.setCurrentIndex(idx)
        browse_e = QtWidgets.QPushButton("Browse…")
        browse_e.clicked.connect(self._browse_engine)
        form.addRow("Engine", hbox(self.engine, browse_e))
        self.copy = QtWidgets.QCheckBox("Start from the current stage (devices, groups, LED processors, cameras…)")
        self.copy.setChecked(True)
        form.addRow("", self.copy)
        form.addRow("", muted(f"Saved to {folder}"))
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.path = ""

    def _browse_uproject(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Unreal project", self.uproject.text(),
                                                        "Unreal project (*.uproject)")
        if path:
            self.uproject.setText(path)
            if not self.name.text():
                self.name.setText(os.path.splitext(os.path.basename(path))[0])

    def _browse_engine(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Engine folder (UE_5.x)")
        if path:
            self.engine.insertItem(0, path, path)
            self.engine.setCurrentIndex(0)

    def engine_dir(self) -> str:
        data = self.engine.currentData()
        text = self.engine.currentText()
        return data if data and self.engine.itemText(self.engine.currentIndex()) == text else text

    def _accept(self) -> None:
        try:
            self.path = config_path_for_name(self.folder, self.name.text())
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "New config", str(exc))
            return
        if os.path.exists(self.path):
            QtWidgets.QMessageBox.warning(self, "New config", f"{os.path.basename(self.path)} already exists.")
            return
        self.accept()


class StagePage(BasePage):
    title = "Stage"
    refresh_topics = ("stage.", "config.")

    def __init__(self, ctx, window) -> None:
        super().__init__(ctx, window)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)

        cur = Card("Current stage")
        self.current = QtWidgets.QLabel()
        self.current.setObjectName("Big")
        self.current.setWordWrap(True)
        cur.add(self.current)
        self.current_detail = muted("")
        cur.add(self.current_detail)
        save_as = QtWidgets.QPushButton("Save as new Switchboard config…")
        save_as.clicked.connect(self._save_as)
        self.close_btn = QtWidgets.QPushButton("Close config (use standalone stage)")
        self.close_btn.clicked.connect(self._close)
        reveal = QtWidgets.QPushButton("Show in folder")
        reveal.clicked.connect(self._reveal)
        cur.add_layout(hbox(save_as, self.close_btn, reveal, None))
        lay.addWidget(cur)

        lib = Card("Switchboard configs")
        self.folder = QtWidgets.QComboBox()
        self.folder.currentIndexChanged.connect(self._folder_changed)
        browse = QtWidgets.QPushButton("Other folder…")
        browse.clicked.connect(self._browse_folder)
        lib.add_layout(hbox(QtWidgets.QLabel("Folder"), self.folder, browse, spacing=10))
        self.folder_path = muted("")
        lib.add(self.folder_path)
        self.table = make_table(["Config", "Project", "Editors", "nDisplay nodes", "Operator Board settings",
                                 "Modified"])
        self.table.doubleClicked.connect(lambda _: self._open())
        lib.add(self.table)
        btns = []
        for label, fn in [("Open", self._open), ("New…", self._new), ("Refresh", self.refresh)]:
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(fn)
            btns.append(b)
        lib.add_layout(hbox(*btns, None))
        lib.add(muted(
            "These are the same files Switchboard lists, so a config made or changed here opens in Switchboard "
            "and the other way round. Operator Board keeps its own settings for each config (LED processors, "
            "groups, cameras, takes, sync jobs) in a matching .opboard file next to it, which Switchboard "
            "ignores. Switchboard rewrites a config whenever you change a setting in it, so avoid editing the "
            "same config in both programs at the same time."))
        lay.addWidget(lib, 1)
        self._paths = []
        self._fill_folders()

    # ---- folder selection ------------------------------------------------------------------
    def _fill_folders(self) -> None:
        sb = self.ctx.config.switchboard
        self.folder.blockSignals(True)
        self.folder.clear()
        auto = default_config_dir(_without_choice(self.ctx.config))
        self.folder.addItem(f"Same as Switchboard ({_short(auto) or 'no engine found'})", AUTO)
        for label, path in candidate_config_dirs([p.engine_dir for p in self.ctx.config.projects if p.engine_dir]):
            self.folder.addItem(label, path)
        if sb.config_dir:
            idx = self.folder.findData(sb.config_dir)
            if idx < 0:
                self.folder.addItem(sb.config_dir, sb.config_dir)
                idx = self.folder.count() - 1
            self.folder.setCurrentIndex(idx)
        self.folder.blockSignals(False)

    def _folder_changed(self) -> None:
        self.ctx.config.switchboard.config_dir = self.folder.currentData() or ""
        self.save_config()
        self.refresh()

    def _browse_folder(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Switchboard configs folder", self._folder())
        if path:
            self.ctx.config.switchboard.config_dir = path
            self._fill_folders()
            self.save_config()
            self.refresh()

    def _folder(self) -> str:
        return default_config_dir(self.ctx.config)

    # ---- actions ------------------------------------------------------------------------------
    def _selected_path(self) -> str:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return self._paths[rows[0]] if rows and rows[0] < len(self._paths) else ""

    def _confirm_switch(self) -> bool:
        running = self.ctx.devices.running()
        if not running:
            return True
        return QtWidgets.QMessageBox.question(
            self, "Switch stage", f"{len(running)} Unreal instance(s) are running. Switch stage anyway? They keep "
            "running but are no longer controlled from here.") == QtWidgets.QMessageBox.Yes

    def _run(self, label: str, fn) -> None:
        try:
            fn()
        except (OSError, ValueError, TypeError) as exc:
            self.notify(f"{label} failed: {exc}", False)
            return
        self.notify(label)
        self.refresh()

    def _open(self) -> None:
        path = self._selected_path()
        if not path or not self._confirm_switch():
            return
        self._run(f"Opened {os.path.basename(path)}", lambda: self.ctx.open_switchboard_config(path))

    def _new(self) -> None:
        folder = self._folder()
        if not folder:
            self.notify("No Switchboard configs folder found. Choose one with 'Other folder…'.", False)
            return
        dlg = NewConfigDialog(self, folder, self.ctx)
        if exec_(dlg) != QtWidgets.QDialog.Accepted or not self._confirm_switch():
            return
        name = os.path.splitext(os.path.basename(dlg.path))[0]
        self._run(f"Created {os.path.basename(dlg.path)}", lambda: self.ctx.new_switchboard_config(
            dlg.path, name, dlg.uproject.text().strip(), dlg.engine_dir().strip(), dlg.copy.isChecked()))

    def _save_as(self) -> None:
        folder = self._folder()
        name, ok = QtWidgets.QInputDialog.getText(self, "Save as Switchboard config",
                                                  f"Config name (saved in {folder}):")
        if not ok:
            return
        try:
            path = config_path_for_name(folder, name)
        except ValueError as exc:
            self.notify(str(exc), False)
            return
        self._run(f"Saved {os.path.basename(path)}", lambda: self.ctx.save_switchboard_config_as(path))

    def _close(self) -> None:
        if self.ctx.switchboard_config and self._confirm_switch():
            self._run("Switched to the standalone stage", self.ctx.close_switchboard_config)

    def _reveal(self) -> None:
        target = os.path.dirname(self.ctx.switchboard_config) if self.ctx.switchboard_config else self._folder()
        if target and os.path.isdir(target):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(target))

    # ---- display ---------------------------------------------------------------------------------
    def refresh(self) -> None:
        cfg = self.ctx.config
        active = self.ctx.switchboard_config
        if active:
            self.current.setText(f"Switchboard config: {os.path.splitext(os.path.basename(active))[0]}")
            self.current_detail.setText(f"{active}\nOperator Board settings: {sidecar_path(active)}\n"
                                        f"Project: {cfg.project().name} · {len(cfg.devices)} device(s)")
        else:
            self.current.setText("Standalone stage (no Switchboard config open)")
            self.current_detail.setText(f"Settings are kept in {self.ctx.store.global_path}. Open or create a "
                                        "Switchboard config below to share the stage with Switchboard.")
        self.close_btn.setEnabled(bool(active))
        self.current.setStyleSheet(f"color: {COLORS['accent'] if active else COLORS['text']};")

        folder = self._folder()
        self.folder_path.setText(folder or "No Switchboard installation found.")
        self._paths = list_configs(folder)
        self.table.setRowCount(0)
        for i, path in enumerate(self._paths):
            info = config_summary(path)
            counts = info.get("devices", {})
            name = os.path.relpath(path, folder)
            if active and os.path.normcase(os.path.abspath(path)) == os.path.normcase(active):
                name += "  (open)"
            set_row(self.table, i, [
                name,
                info.get("project", "") if info.get("valid") else "unreadable",
                counts.get("Unreal", ""), counts.get("nDisplay", ""),
                "yes" if os.path.exists(sidecar_path(path)) else "",
                time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path))),
            ])


def _short(path: str) -> str:
    marker = os.sep + "Engine" + os.sep
    return path.split(marker)[0] if marker in path else path


def _without_choice(config):
    """A view of the config with no folder chosen, to show where 'auto' points."""
    from operator_board.core.config import AppConfig, SwitchboardLinkConfig
    view = AppConfig(projects=config.projects, active_project=config.active_project)
    view.switchboard = SwitchboardLinkConfig(active_config=config.switchboard.active_config)
    return view
