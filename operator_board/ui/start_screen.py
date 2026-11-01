"""The start screen: choose the Operator Board project to work in, or start a new one.

Projects can also be duplicated (a checkpoint), renamed, have their details edited, and be exported
or imported (templates, or sharing a project with someone else)."""
from __future__ import annotations

import os
from typing import Optional

from operator_board import __version__
from operator_board.core import projects
from operator_board.core.projects import ProjectInfo
from operator_board.qt import QtGui, QtWidgets, Qt
from operator_board.ui.theme import COLORS
from operator_board.ui.widgets import hbox, muted


class ProjectDetailsDialog(QtWidgets.QDialog):
    """Name and details of a project; for a new one, optionally where its stage setup comes from."""

    def __init__(self, parent, info: ProjectInfo, title: str, new: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        self.info = info
        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.name = QtWidgets.QLineEdit(info.name)
        form.addRow("Project name", self.name)
        self.fields = {}
        for key, label in ProjectInfo.DETAILS:
            if key in ("description", "notes"):
                w = QtWidgets.QPlainTextEdit(getattr(info, key))
                w.setFixedHeight(60)
            else:
                w = QtWidgets.QLineEdit(getattr(info, key))
            self.fields[key] = w
            form.addRow(label, w)
        self.setup_from = None
        if new:
            self.setup_from = QtWidgets.QComboBox()
            self.setup_from.addItem("Nothing (an empty project)", "")
            for p in projects.list_projects():
                self.setup_from.addItem(f"The stage setup of {p.name}", p.id)
            self.setup_from.setToolTip("Copy another project's devices, LED processors, Unreal projects and "
                                       "Switchboard config, without its camera positions, takes, presets or "
                                       "change log")
            form.addRow("Start with", self.setup_from)
        lay.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _accept(self) -> None:
        if not self.name.text().strip():
            QtWidgets.QMessageBox.warning(self, "Project", "Give the project a name.")
            return
        self.info.name = self.name.text().strip()
        for key, w in self.fields.items():
            setattr(self.info, key, w.toPlainText() if isinstance(w, QtWidgets.QPlainTextEdit) else w.text().strip())
        self.accept()

    @property
    def source(self) -> str:
        return self.setup_from.currentData() if self.setup_from is not None else ""


class StartScreen(QtWidgets.QDialog):
    """Returns the chosen project's id in ``chosen`` after ``exec`` (empty if the window was closed)."""

    def __init__(self, parent=None, current: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Operator Board {__version__} · Projects")
        self.resize(1000, 640)
        self.chosen = ""
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(20)

        side = QtWidgets.QVBoxLayout()
        logo = QtWidgets.QLabel()
        pix = QtGui.QPixmap(os.path.join(os.path.dirname(__file__), "logo.png"))
        if not pix.isNull():
            ratio = self.devicePixelRatioF()
            pix = pix.scaled(round(120 * ratio), round(120 * ratio), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pix.setDevicePixelRatio(ratio)
            logo.setPixmap(pix)
        logo.setAlignment(Qt.AlignCenter)
        side.addWidget(logo)
        title = QtWidgets.QLabel("OPERATOR\nBOARD")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"font-size: 24px; font-weight: 900; color: #00d5ff; letter-spacing: 3px;")
        side.addWidget(title)
        side.addSpacing(16)
        for label, fn, tip in (
                ("+  New project…", self._new, "A new project: no camera positions, takes or presets from before"),
                ("New from template…", self._from_template,
                 "A new project from an exported project or template (.opproject)"),
                ("Import project…", self._import, "Add a project someone exported (.opproject)")):
            b = QtWidgets.QPushButton(label)
            b.setMinimumHeight(40)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            side.addWidget(b)
        side.addStretch(1)
        side.addWidget(muted(f"Version {__version__}"))
        lay.addLayout(side, 0)

        main = QtWidgets.QVBoxLayout()
        heading = QtWidgets.QLabel("PROJECTS")
        heading.setObjectName("CardTitle")
        main.addWidget(heading)
        main.addWidget(muted("Each project keeps its own takes, camera positions, presets, devices, LED processors "
                             "and Switchboard config. Open one, or start a new project for a new shoot."))
        self.list = QtWidgets.QTreeWidget()
        self.list.setHeaderLabels(["Project", "Production", "Last used"])
        self.list.setRootIsDecorated(False)
        self.list.setUniformRowHeights(True)
        self.list.header().setStretchLastSection(False)
        self.list.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.list.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.list.header().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.list.itemSelectionChanged.connect(self._show_details)
        self.list.itemDoubleClicked.connect(lambda *_: self._open())
        main.addWidget(self.list, 2)
        self.details = QtWidgets.QLabel("")
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.RichText)
        self.details.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.details.setStyleSheet(f"background: {COLORS['panel']}; border: 1px solid {COLORS['border']}; "
                                   "border-radius: 10px; padding: 10px;")
        self.details.setMinimumHeight(110)
        main.addWidget(self.details, 1)
        self.open_btn = QtWidgets.QPushButton("Open project  ▶")
        self.open_btn.setMinimumHeight(44)
        self.open_btn.setStyleSheet("QPushButton { font-size: 16px; font-weight: 700; }")
        self.open_btn.clicked.connect(self._open)
        actions = []
        for label, fn, tip in (("Duplicate…", self._duplicate, "A copy of the project as it stands (a checkpoint)"),
                               ("Rename…", self._rename, ""),
                               ("Details…", self._details, "Production, client, director, dates and notes"),
                               ("Export…", self._export, "Save the project to a file, to share it or keep it as a "
                                                         "template")):
            b = QtWidgets.QPushButton(label)
            if tip:
                b.setToolTip(tip)
            b.clicked.connect(fn)
            actions.append(b)
        self.action_buttons = actions
        main.addLayout(hbox(*actions, None, self.open_btn))
        lay.addLayout(main, 1)
        self._fill(select=current or projects.last_project())

    # ---- list ------------------------------------------------------------------------------
    def _fill(self, select: str = "") -> None:
        self.list.clear()
        for p in projects.list_projects():
            item = QtWidgets.QTreeWidgetItem([p.name, p.production, p.modified or p.created])
            item.setData(0, Qt.UserRole, p.id)
            self.list.addTopLevelItem(item)
            if p.id == select:
                self.list.setCurrentItem(item)
        if self.list.currentItem() is None and self.list.topLevelItemCount():
            self.list.setCurrentItem(self.list.topLevelItem(0))
        self._show_details()

    def _selected(self) -> Optional[str]:
        item = self.list.currentItem()
        return item.data(0, Qt.UserRole) if item is not None else None

    def _show_details(self) -> None:
        pid = self._selected()
        has = pid is not None
        self.open_btn.setEnabled(has)
        for b in self.action_buttons:
            b.setEnabled(has)
        if not has:
            self.details.setText("No projects yet: start a <b>new project</b>, or import one.")
            return
        info = projects.read_info(pid)
        rows = [f"<b style='font-size:16px'>{info.name}</b>"]
        for key, label in ProjectInfo.DETAILS:
            value = getattr(info, key)
            if value:
                rows.append(f"<span style='color:{COLORS['muted']}'>{label}:</span> "
                            f"{value.replace(chr(10), '<br>')}")
        rows.append(f"<span style='color:{COLORS['muted']}'>Created {info.created}"
                    + (f" · last used {info.modified}" if info.modified else "") + "</span>")
        self.details.setText("<br>".join(rows))

    # ---- actions -------------------------------------------------------------------------------
    def _open(self) -> None:
        pid = self._selected()
        if pid:
            self.chosen = pid
            self.accept()

    def _new(self) -> None:
        dialog = ProjectDetailsDialog(self, ProjectInfo(name=projects.unique_name("New project")), "New project",
                                      new=True)
        if dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec():
            info = projects.create(dialog.info, setup_from=dialog.source)
            self._fill(select=info.id)
            self._open()

    def _duplicate(self) -> None:
        pid = self._selected()
        if not pid:
            return
        source = projects.read_info(pid)
        name, ok = QtWidgets.QInputDialog.getText(self, "Duplicate project", "Name of the copy:",
                                                  text=projects.unique_name(f"{source.name} copy"))
        if ok and name.strip():
            info = projects.duplicate(pid, name)
            self._fill(select=info.id)

    def _rename(self) -> None:
        pid = self._selected()
        if not pid:
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "Rename project", "Project name:",
                                                  text=projects.read_info(pid).name)
        if ok and name.strip():
            projects.rename(pid, name)
            self._fill(select=pid)

    def _details(self) -> None:
        pid = self._selected()
        if not pid:
            return
        dialog = ProjectDetailsDialog(self, projects.read_info(pid), "Project details")
        if dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec():
            projects.update_details(dialog.info)
            self._fill(select=pid)

    def _export(self) -> None:
        pid = self._selected()
        if not pid:
            return
        info = projects.read_info(pid)
        box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Question, "Export project",
                                    f"Export '{info.name}' with its takes and change log?", parent=self)
        box.setInformativeText("Yes: everything, to share the project or keep a copy.\n"
                               "No: without takes and the change log, as a template for new projects.")
        box.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No |
                               QtWidgets.QMessageBox.Cancel)
        answer = box.exec_() if hasattr(box, "exec_") else box.exec()
        if answer == QtWidgets.QMessageBox.Cancel:
            return
        full = answer == QtWidgets.QMessageBox.Yes
        suffix = "" if full else " template"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export project", f"{info.name}{suffix}"
                                                        f"{projects.EXPORT_SUFFIX}",
                                                        f"Operator Board project (*{projects.EXPORT_SUFFIX})")
        if not path:
            return
        try:
            projects.export_project(pid, path, include_shoot_data=full)
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "Export project", f"Could not export: {exc}")
            return
        QtWidgets.QMessageBox.information(self, "Export project", f"Exported '{info.name}' to {path}")

    def _pick_file(self, title: str) -> str:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, title, "",
                                                        f"Operator Board project (*{projects.EXPORT_SUFFIX})")
        return path

    def _import(self) -> None:
        path = self._pick_file("Import project")
        if not path:
            return
        try:
            info = projects.import_project(path)
        except (OSError, ValueError, KeyError) as exc:
            QtWidgets.QMessageBox.warning(self, "Import project", f"Could not import: {exc}")
            return
        self._fill(select=info.id)

    def _from_template(self) -> None:
        path = self._pick_file("New project from a template")
        if not path:
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "New project from a template", "Name of the new project:",
                                                  text=projects.unique_name("New project"))
        if not (ok and name.strip()):
            return
        try:
            info = projects.import_project(path, name=name)
            projects.clear_shoot_data(info.id)  # a template starts a fresh shoot
        except (OSError, ValueError, KeyError) as exc:
            QtWidgets.QMessageBox.warning(self, "New project", f"Could not use the template: {exc}")
            return
        self._fill(select=info.id)
        self._open()


def choose_project(parent=None, current: str = "") -> str:
    """Show the start screen; returns the chosen project's id, or "" if it was closed."""
    screen = StartScreen(parent, current)
    accepted = screen.exec_() if hasattr(screen, "exec_") else screen.exec()
    return screen.chosen if accepted else ""
