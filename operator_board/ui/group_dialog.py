"""Create or edit a group of Unreal instances: name, colour and members."""
from __future__ import annotations

from typing import List, Optional

from operator_board.core.config import GroupConfig
from operator_board.qt import QtGui, QtWidgets, Qt

# Quick picks that read well on the dark theme; "Custom…" opens the full colour picker.
SWATCHES = ["#3d8bfd", "#7950f2", "#e64980", "#e03131", "#f08c00", "#fab005", "#2f9e44", "#12b886", "#15aabf",
            "#868e96"]


class GroupDialog(QtWidgets.QDialog):
    """Returns the edited group with ``result_group`` after ``exec`` (``deleted`` if the group was deleted)."""

    def __init__(self, parent, group: Optional[GroupConfig], device_names: List[str], taken_names: List[str]) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Edit group {group.name}" if group else "New group")
        self.setMinimumWidth(420)
        self.deleted = False
        self._taken = {n.lower() for n in taken_names}
        source = group or GroupConfig(name=self._free_name("Group"), color=SWATCHES[len(taken_names) % len(SWATCHES)])
        self._color = source.color

        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.name = QtWidgets.QLineEdit(source.name)
        form.addRow("Name", self.name)
        swatches = QtWidgets.QHBoxLayout()
        swatches.setSpacing(6)
        self._swatch_buttons = []
        for colour in SWATCHES:
            b = QtWidgets.QPushButton()
            b.setFixedSize(26, 26)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(colour)
            b.clicked.connect(lambda _=False, c=colour: self._set_color(c))
            self._swatch_buttons.append((colour, b))
            swatches.addWidget(b)
        custom = QtWidgets.QPushButton("Custom…")
        custom.clicked.connect(self._pick_color)
        swatches.addWidget(custom)
        swatches.addStretch(1)
        form.addRow("Colour", swatches)
        lay.addLayout(form)

        lay.addWidget(QtWidgets.QLabel("Unreal instances in this group"))
        self.members = QtWidgets.QListWidget()
        for name in device_names:
            item = QtWidgets.QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if name in source.devices else Qt.Unchecked)
            self.members.addItem(item)
        if not device_names:
            self.members.addItem("No devices yet: add them on the Devices page.")
        lay.addWidget(self.members, 1)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        if group is not None:
            delete = buttons.addButton("Delete group", QtWidgets.QDialogButtonBox.DestructiveRole)
            delete.clicked.connect(self._delete)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._original = group
        self._set_color(self._color)

    def _free_name(self, base: str) -> str:
        n = 1
        while f"{base} {n}".lower() in self._taken:
            n += 1
        return f"{base} {n}"

    def _set_color(self, colour: str) -> None:
        self._color = colour
        for c, b in self._swatch_buttons:
            border = "#ffffff" if c.lower() == colour.lower() else "transparent"
            b.setStyleSheet(f"background: {c}; border: 2px solid {border}; border-radius: 13px;")

    def _pick_color(self) -> None:
        chosen = QtWidgets.QColorDialog.getColor(QtGui.QColor(self._color), self, "Group colour")
        if chosen.isValid():
            self._set_color(chosen.name())

    def _delete(self) -> None:
        if QtWidgets.QMessageBox.question(self, "Delete group",
                                          f"Delete the group '{self._original.name}'? "
                                          "Its Unreal instances are not affected.") == QtWidgets.QMessageBox.Yes:
            self.deleted = True
            self.accept()

    def _accept(self) -> None:
        name = self.name.text().strip()
        if not name:
            QtWidgets.QMessageBox.warning(self, "Group", "Give the group a name.")
            return
        if name.lower() in self._taken:
            QtWidgets.QMessageBox.warning(self, "Group", f"There is already a group called '{name}'.")
            return
        self.accept()

    @property
    def result_group(self) -> GroupConfig:
        members = [self.members.item(i).text() for i in range(self.members.count())
                   if self.members.item(i).flags() & Qt.ItemIsUserCheckable
                   and self.members.item(i).checkState() == Qt.Checked]
        return GroupConfig(name=self.name.text().strip(), devices=members, color=self._color)


def edit_group(parent, config, group: Optional[GroupConfig] = None) -> bool:
    """Show the dialog and apply the result to ``config.groups``. True if anything changed."""
    taken = [g.name for g in config.groups if g is not group]
    dialog = GroupDialog(parent, group, [d.name for d in config.devices], taken)
    if not (dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()):
        return False
    if dialog.deleted:
        config.groups.remove(group)
        return True
    result = dialog.result_group
    if group is None:
        config.groups.append(result)
    else:
        group.name, group.devices, group.color = result.name, result.devices, result.color
    return True
