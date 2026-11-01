"""Projects, levels, sub-levels / data layers and saved camera positions."""
from __future__ import annotations

from operator_board.core.config import CameraPreset, ProjectConfig
from operator_board.core.ue_versions import SUPPORTED_VERSIONS, detect_engine_version
from operator_board.qt import QtCore, QtWidgets, Qt
from operator_board.remote.levels import discover_projects
from operator_board.ui.forms import edit_dataclass, unique_name_validator
from operator_board.ui.pages.base import BasePage
from operator_board.ui.widgets import Card, hbox, make_table, muted, set_row

LOAD_MODES = [("Editor (load level in primary editor)", "editor"),
              ("Render nodes (runtime 'open' command)", "nodes"),
              ("Relaunch nDisplay nodes on the level", "relaunch"),
              ("Editor + relaunch nodes", "all")]


class LevelsPage(BasePage):
    title = "Cameras & Levels"
    refresh_topics = ("levels.", "project.", "cameras.", "config.")
    EDITOR_EVENTS = ("editor.ready", "editor.level_changed")


    def __init__(self, ctx, window) -> None:
        super().__init__(ctx, window)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        tabs = self.tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._levels_tab(), "Projects && Levels")
        tabs.addTab(self._sublevels_tab(), "Sub-levels && Data Layers")
        tabs.addTab(self._cameras_tab(), "Camera Positions")
        lay.addWidget(tabs)

    def show_tab(self, name: str) -> None:
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i).replace("&&", "&") == name:
                self.tabs.setCurrentIndex(i)

    # ---- projects & levels ---------------------------------------------------------
    def _levels_tab(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        proj = Card("Projects")
        self.projects = make_table(["Name", "UE", "uproject"])
        self.projects.doubleClicked.connect(lambda _: self._edit_project())
        proj.add(self.projects)
        btns = []
        for label, fn in [("Add", self._add_project), ("Edit", self._edit_project), ("Remove", self._remove_project),
                          ("Make active", self._activate_project), ("Find projects…", self._find_projects)]:
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(fn)
            btns.append(b)
        proj.add_layout(hbox(*btns, None))
        lay.addWidget(proj, 1)

        lv = Card("Levels in active project")
        self.filter = QtWidgets.QLineEdit()
        self.filter.setPlaceholderText("Filter levels…")
        self.filter.textChanged.connect(self._fill_levels)
        refresh = QtWidgets.QPushButton("↻ Refresh")
        refresh.clicked.connect(self._refresh_levels)
        lv.add_layout(hbox(self.filter, refresh))
        self.levels = QtWidgets.QListWidget()
        self.levels.itemDoubleClicked.connect(lambda _: self._load())
        lv.add(self.levels)
        self.mode = QtWidgets.QComboBox()
        for label, value in LOAD_MODES:
            self.mode.addItem(label, value)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        load = QtWidgets.QPushButton("Load selected level")
        load.setMinimumHeight(44)
        load.clicked.connect(self._load)
        default = QtWidgets.QPushButton("Set as launch level")
        default.clicked.connect(self._set_default)
        lv.add_layout(hbox(QtWidgets.QLabel("Mode"), self.mode, None))
        lv.add_layout(hbox(load, default))
        lv.add(muted("Levels are read from the project's Content folder when it is reachable from this machine, "
                     "otherwise from the primary editor via Remote Control. The launch level is used when "
                     "instances are started."))
        lay.addWidget(lv, 1)
        return w

    def _project_kwargs(self):
        return dict(choices={"ue_version": list(SUPPORTED_VERSIONS)},
                    labels={"ue_version": "Unreal Engine version", "uproject_path": ".uproject",
                            "ndisplay_config": "nDisplay config (.ndisplay)", "default_map": "Launch level"},
                    browse={"uproject_path": "file", "engine_dir": "dir", "ndisplay_config": "file"})

    def _project_validate(self, existing, original):
        names = unique_name_validator(existing, original)

        def check(p):
            problem = names(p)
            if problem:
                return problem
            detected = detect_engine_version(p.engine_dir)
            if detected and detected != p.ue_version and detected in SUPPORTED_VERSIONS:
                p.ue_version = detected
            return None
        return check

    def _add_project(self) -> None:
        p = ProjectConfig(name=f"Project {len(self.ctx.config.projects) + 1}")
        if edit_dataclass(self, "Add project", p, self._project_validate([x.name for x in self.ctx.config.projects], ""),
                          **self._project_kwargs()):
            self.ctx.config.projects.append(p)
            self._saved()

    def _selected_project(self):
        rows = sorted({i.row() for i in self.projects.selectedIndexes()})
        return self.ctx.config.projects[rows[0]] if rows and rows[0] < len(self.ctx.config.projects) else None

    def _edit_project(self) -> None:
        p = self._selected_project()
        if not p:
            return
        old = p.name
        if edit_dataclass(self, f"Edit {p.name}", p,
                          self._project_validate([x.name for x in self.ctx.config.projects], old), **self._project_kwargs()):
            if self.ctx.config.active_project == old:
                self.ctx.config.active_project = p.name
            self._saved()

    def _remove_project(self) -> None:
        p = self._selected_project()
        if p and len(self.ctx.config.projects) > 1:
            self.ctx.config.projects.remove(p)
            if self.ctx.config.active_project == p.name:
                self.ctx.config.active_project = self.ctx.config.projects[0].name
            self._saved()

    def _activate_project(self) -> None:
        p = self._selected_project()
        if p:
            self.invoke("project.switch", done=lambda _: self._refresh_levels(), project=p.name)

    def _find_projects(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Search for .uproject files under…")
        if not folder:
            return
        known = {p.uproject_path for p in self.ctx.config.projects}
        added = 0
        for path in discover_projects([folder]):
            if path in known:
                continue
            name = path.replace("\\", "/").split("/")[-1][: -len(".uproject")]
            base = self.ctx.config.project()
            self.ctx.config.projects.append(ProjectConfig(name=name, uproject_path=path, engine_dir=base.engine_dir,
                                                          ue_version=base.ue_version))
            added += 1
        self.notify(f"Found {added} new project(s)")
        self._saved()

    def _saved(self) -> None:
        self.save_config()
        self.ctx.bus.publish("config.changed")
        self.refresh()

    def _refresh_levels(self) -> None:
        self.background(self.ctx.refresh_levels, lambda _: self._fill_levels(), "Listing levels")

    def _fill_levels(self) -> None:
        text = self.filter.text().lower()
        self.levels.clear()
        default = self.ctx.config.project().default_map
        for i, lvl in enumerate(self.ctx.level_cache):
            if text and text not in lvl.path.lower():
                continue
            item = QtWidgets.QListWidgetItem(("★ " if lvl.path == default else "") + lvl.path)
            item.setData(Qt.UserRole, i)
            self.levels.addItem(item)

    def _selected_level(self):
        item = self.levels.currentItem()
        if item is None:
            return None
        idx = item.data(Qt.UserRole)
        self.ctx.selected_level = idx
        return self.ctx.level_cache[idx]

    def _load(self) -> None:
        lvl = self._selected_level()
        if lvl:
            self.invoke("level.load", level=lvl.path, mode=self.mode.currentData())

    def _set_default(self) -> None:
        lvl = self._selected_level()
        if lvl:
            self.ctx.config.project().default_map = lvl.path
            self.save_config()
            self._fill_levels()

    def _mode_changed(self) -> None:
        self.ctx.config.remote_control.level_load_mode = self.mode.currentData()
        self.save_config()

    # ---- sub-levels ---------------------------------------------------------------------
    def _sublevels_tab(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        sub = Card("Streaming sub-levels")
        self.sublevels = QtWidgets.QListWidget()
        self.sublevels.itemChanged.connect(self._sublevel_toggled)
        b = QtWidgets.QPushButton("↻ Refresh")
        b.setToolTip("Read the sub-levels again (they are read automatically when the editor is ready)")
        b.clicked.connect(lambda: self._load_sublevels())
        self.apply_nodes = QtWidgets.QCheckBox("Also apply on nDisplay nodes")
        self.apply_nodes.setChecked(True)
        sub.add_layout(hbox(b, self.apply_nodes, None))
        sub.add(self.sublevels)
        sub.add(muted("Tick to show, untick to hide - ideal for switching lighting setups. Editor changes use the "
                      "Python Editor Script Plugin; nodes use GameplayStatics streaming via Remote Control and need "
                      "the project's launch level set."))
        lay.addWidget(sub)
        dl = Card("Data layers (World Partition)")
        self.layers = QtWidgets.QListWidget()
        self.layers.itemChanged.connect(self._layer_toggled)
        b2 = QtWidgets.QPushButton("↻ Refresh")
        b2.setToolTip("Read the data layers again (they are read automatically when the editor is ready)")
        b2.clicked.connect(lambda: self._load_layers())
        dl.add_layout(hbox(b2, None))
        dl.add(self.layers)
        lay.addWidget(dl)
        self._placeholder(self.sublevels, "Sub-levels are read when the editor has finished loading.")
        self._placeholder(self.layers, "Data layers are read when the editor has finished loading.")
        return w

    @staticmethod
    def _placeholder(widget, text: str) -> None:
        widget.blockSignals(True)
        widget.clear()
        item = QtWidgets.QListWidgetItem(text)
        item.setFlags(Qt.NoItemFlags)  # a message, not a level
        widget.addItem(item)
        widget.blockSignals(False)

    def on_event(self, topic, payload) -> None:
        super().on_event(topic, payload)
        if topic in self.EDITOR_EVENTS:  # the editor is ready or opened another level: fill the lists
            self._load_sublevels(quiet=True)
            self._load_layers(quiet=True)
            self._find_cameras(quiet=True)

    def _fill_checklist(self, widget, rows, key, empty: str = "Nothing found"):
        if not rows:
            self._placeholder(widget, empty)
            return
        widget.blockSignals(True)
        widget.clear()
        for row in rows:
            item = QtWidgets.QListWidgetItem(row.get("name", row.get(key, "")))
            item.setData(Qt.UserRole, row.get(key))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if row.get("visible") in (True, None) else Qt.Unchecked)
            if row.get("visible") is None:
                item.setToolTip("Current visibility unknown")
            widget.addItem(item)
        widget.blockSignals(False)

    def _load_sublevels(self, quiet: bool = False) -> None:
        self.background(self.ctx.levels.sublevels,
                        lambda rows: self._fill_checklist(self.sublevels, rows, "package", "No sub-levels found"),
                        "Reading sub-levels", quiet=quiet,
                        failed=lambda exc: self._placeholder(self.sublevels, "Could not read sub-levels from the "
                                                                             "editor; press Refresh to try again."))

    def _load_layers(self, quiet: bool = False) -> None:
        self.background(self.ctx.levels.data_layers,
                        lambda rows: self._fill_checklist(self.layers, rows, "path", "No data layers found"),
                        "Reading data layers", quiet=quiet,
                        failed=lambda exc: self._placeholder(self.layers, "Could not read data layers from the "
                                                                          "editor; press Refresh to try again."))

    def _sublevel_toggled(self, item) -> None:
        pkg, visible = item.data(Qt.UserRole), item.checkState() == Qt.Checked
        nodes = self.apply_nodes.isChecked()
        name = item.text()

        def done(errors) -> None:
            self.report(f"{'Showing' if visible else 'Hiding'} {name}")(errors)
            self.ctx.changes.record("Levels", name, "Sub-level visible", visible, not visible,
                                    result="ok" if not errors else "failed: " + "; ".join(errors))
        self.background(lambda: self.ctx.levels.set_sublevel_visible(pkg, visible, nodes), done, "Sub-level")

    def _layer_toggled(self, item) -> None:
        path, visible = item.data(Qt.UserRole), item.checkState() == Qt.Checked
        name = item.text()

        def done(_r) -> None:
            self.notify(f"{'Showing' if visible else 'Hiding'} {name}")
            self.ctx.changes.record("Levels", name, "Data layer visible", visible, not visible)
        self.background(lambda: self.ctx.levels.set_data_layer_visible(path, visible), done, "Data layer")

    # ---- cameras -----------------------------------------------------------------------
    def _cameras_tab(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        card = Card("Saved camera positions")
        self.cams = make_table(["Name", "Target", "Location (X, Y, Z)", "Rotation (P, Y, R)"], even=True)
        self.cams.doubleClicked.connect(lambda _: self._recall())
        card.add(self.cams)
        self.cam_target = QtWidgets.QComboBox()
        self.cam_target.setEditable(True)
        self.cam_target.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cam_target.setMinimumContentsLength(24)
        self.cam_target.addItem("viewport")
        find = QtWidgets.QPushButton("↻ Refresh cameras")
        find.setToolTip("List the level's cameras and nDisplay setups again (done automatically when the editor "
                        "is ready)")
        find.clicked.connect(lambda: self._find_cameras())
        self.cam_name = QtWidgets.QLineEdit()
        self.cam_name.setPlaceholderText("New position name")
        capture = QtWidgets.QPushButton("Save current position")
        capture.clicked.connect(self._capture)
        target_row = hbox(QtWidgets.QLabel("Target"), self.cam_target, find)
        target_row.setStretch(1, 1)
        name_row = hbox(QtWidgets.QLabel("Name"), self.cam_name, capture)
        name_row.setStretch(1, 1)
        card.add_layout(target_row)
        card.add_layout(name_row)
        btns = []
        for label, fn in [("Recall", self._recall), ("Edit", self._edit_cam), ("Remove", self._remove_cam),
                          ("Move up", lambda: self._move_cam(-1)), ("Move down", lambda: self._move_cam(1))]:
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(fn)
            btns.append(b)
        export = QtWidgets.QPushButton("Export CSV…")
        export.setToolTip("Back up the saved camera positions to a CSV file")
        export.clicked.connect(self._export_cams)
        imp = QtWidgets.QPushButton("Import CSV…")
        imp.setToolTip("Add camera positions from a CSV file (names already in use get _2, _3...)")
        imp.clicked.connect(self._import_cams)
        card.add_layout(hbox(*btns, None, imp, export))
        self.cam_status = muted("")
        card.add(self.cam_status)
        card.add(muted("'viewport' moves the primary editor's level viewport. Choose a CineCameraActor or "
                       "nDisplay root actor to move it on every instance (editor and render nodes)."))
        lay.addWidget(card)
        return w

    def _find_cameras(self, quiet: bool = False) -> None:
        def fill(actors):
            current = self.cam_target.currentText()
            self.cam_target.clear()
            self.cam_target.addItem("viewport")
            for a in actors:
                self.cam_target.addItem(f"{a['label']} ({a['class']})", a["path"])
            self.cam_target.setCurrentText(current)
            self.cam_status.setText(f"{len(actors)} camera(s) / nDisplay setup(s) in the level" if actors else
                                    "No cameras or nDisplay setups found in the level (the viewport still works)")
        self.background(self.ctx.cameras.list_camera_actors, fill, "Finding cameras", quiet=quiet)

    def _export_cams(self) -> None:
        if not self.ctx.config.cameras:
            self.notify("There are no saved camera positions to export", False)
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export camera positions", "camera_positions.csv",
                                                        "CSV (*.csv)")
        if path:
            count = self.ctx.cameras.export_csv(path)
            self.notify(f"Exported {count} camera position(s) to {path}")

    def _import_cams(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import camera positions", "", "CSV (*.csv)")
        if not path:
            return
        try:
            added = self.ctx.cameras.import_csv(path)
        except (OSError, ValueError) as exc:
            self.notify(f"Could not import {path}: {exc}", False)
            return
        self.save_config()
        self.ctx.bus.publish("cameras.changed")
        self.notify(f"Imported {len(added)} camera position(s)")

    def _target_value(self) -> str:
        data = self.cam_target.currentData()
        return data or self.cam_target.currentText() or "viewport"

    def _capture(self) -> None:
        name = self.cam_name.text().strip() or f"Camera {len(self.ctx.config.cameras) + 1}"
        target = self._target_value()

        def done(preset):
            self.save_config()
            self.ctx.bus.publish("cameras.changed")
            self.notify(f"Saved {preset.name}" + (f" ('{name}' was already taken)" if preset.name != name else ""))
        self.background(lambda: self.ctx.cameras.capture(name, target), done, "Capturing camera")

    def _selected_cam(self):
        rows = sorted({i.row() for i in self.cams.selectedIndexes()})
        return rows[0] if rows and rows[0] < len(self.ctx.config.cameras) else None

    def _recall(self) -> None:
        i = self._selected_cam()
        if i is not None:
            self.invoke("camera.recall", preset=self.ctx.config.cameras[i].name)

    def _edit_cam(self) -> None:
        i = self._selected_cam()
        if i is None:
            return
        cam = self.ctx.config.cameras[i]
        names = [c.name for c in self.ctx.config.cameras]
        if edit_dataclass(self, f"Edit {cam.name}", cam, unique_name_validator(names, cam.name),
                          labels={"location": "Location [x, y, z]", "rotation": "Rotation [pitch, yaw, roll]"}):
            self.save_config()
            self.ctx.bus.publish("cameras.changed")

    def _remove_cam(self) -> None:
        i = self._selected_cam()
        if i is not None:
            del self.ctx.config.cameras[i]
            self.save_config()
            self.ctx.bus.publish("cameras.changed")

    def _move_cam(self, delta: int) -> None:
        i = self._selected_cam()
        cams = self.ctx.config.cameras
        if i is None or not 0 <= i + delta < len(cams):
            return
        cams[i], cams[i + delta] = cams[i + delta], cams[i]
        self.save_config()
        self.refresh()
        self.cams.selectRow(i + delta)

    # ---- refresh ------------------------------------------------------------------------
    def refresh(self) -> None:
        self.projects.setRowCount(0)
        for i, p in enumerate(self.ctx.config.projects):
            active = "▶ " if p.name == self.ctx.config.active_project else ""
            set_row(self.projects, i, [active + p.name, p.ue_version, p.uproject_path])
        idx = self.mode.findData(self.ctx.config.remote_control.level_load_mode)
        self.mode.blockSignals(True)
        self.mode.setCurrentIndex(max(0, idx))
        self.mode.blockSignals(False)
        self._fill_levels()
        self.cams.setRowCount(0)
        for i, c in enumerate(self.ctx.config.cameras):
            set_row(self.cams, i, [c.name, c.target, ", ".join(f"{v:.1f}" for v in c.location),
                                   ", ".join(f"{v:.1f}" for v in c.rotation)])
        self.cams.refit()
