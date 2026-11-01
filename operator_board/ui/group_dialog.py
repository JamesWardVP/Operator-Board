"""Create or edit a group of Unreal instances or of LED processors: name, colour and members."""
from __future__ import annotations

from typing import List, Optional

from operator_board.core.config import GroupConfig, LEDGroupConfig
from operator_board.qt import QtGui, QtWidgets, Qt

# Quick picks that read well on the dark theme; "Custom…" opens the full colour picker.
SWATCHES = ["#3d8bfd", "#7950f2", "#e64980", "#e03131", "#f08c00", "#fab005", "#2f9e44", "#12b886", "#15aabf",
            "#868e96"]


class GroupDialog(QtWidgets.QDialog):
    """Returns the edited group with ``result_group`` after ``exec`` (``deleted`` if the group was deleted)."""

    def __init__(self, parent, group: Optional[GroupConfig], device_names: List[str], taken_names: List[str],
                 led: bool = False) -> None:
        super().__init__(parent)
        self.led = led
        self.setWindowTitle(f"Edit group {group.name}" if group else ("New LED group" if led else "New group"))
        self.setMinimumWidth(420)
        self.deleted = False
        self._taken = {n.lower() for n in taken_names}
        if group is None:
            cls = LEDGroupConfig if led else GroupConfig
            group_source = cls(name=self._free_name("LED group" if led else "Group"),
                               color=SWATCHES[len(taken_names) % len(SWATCHES)])
        source = group or group_source
        members_now = source.processors if led else source.devices
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

        lay.addWidget(QtWidgets.QLabel("LED processors in this group" if led else "Unreal instances in this group"))
        self.members = QtWidgets.QListWidget()
        for name in device_names:
            item = QtWidgets.QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if name in members_now else Qt.Unchecked)
            self.members.addItem(item)
        if not device_names:
            self.members.addItem("No LED processors yet: add them above." if led else
                                 "No devices yet: add them on the Devices page.")
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
                                          f"Delete the group '{self._original.name}'? Its "
                                          + ("LED processors" if self.led else "Unreal instances")
                                          + " are not affected.") == QtWidgets.QMessageBox.Yes:
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
        if self.led:
            return LEDGroupConfig(name=self.name.text().strip(), processors=members, color=self._color)
        return GroupConfig(name=self.name.text().strip(), devices=members, color=self._color)


def edit_led_group(parent, config, group: Optional[LEDGroupConfig] = None) -> bool:
    """The same dialog for a group of LED processors (``config.led_groups``). True if anything changed."""
    taken = [g.name for g in config.led_groups if g is not group] + [p.name for p in config.led_processors]
    dialog = GroupDialog(parent, group, [p.name for p in config.led_processors], taken, led=True)
    if not (dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()):
        return False
    if dialog.deleted:
        config.led_groups.remove(group)
        return True
    result = dialog.result_group
    if group is None:
        config.led_groups.append(result)
    else:
        group.name, group.processors, group.color = result.name, result.processors, result.color
    return True


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
