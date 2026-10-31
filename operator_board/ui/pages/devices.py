"""Device (Unreal instance) and group configuration plus per-device control and logs."""
from __future__ import annotations

from operator_board.core.config import ROLES, DeviceConfig
from operator_board.core.switchboard_config import default_config_dir, import_switchboard_config
from operator_board.qt import QtWidgets
from operator_board.ui.forms import edit_dataclass, unique_name_validator
from operator_board.ui.pages.base import BasePage
from operator_board.ui.widgets import Card, hbox, make_table, muted, set_row


class DevicesPage(BasePage):
    title = "Devices"
    refresh_topics = ("device.", "devices.", "config.")

    def __init__(self, ctx, window) -> None:
        super().__init__(ctx, window)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)

        dev_card = Card("Unreal Instances (via SwitchboardListener)")
        self.table = make_table(["Name", "Role", "Address", "nDisplay node", "State", "Listener", "Last error"])
        self.table.itemSelectionChanged.connect(self._show_log)
        self.table.doubleClicked.connect(lambda _: self._edit())
        dev_card.add(self.table)
        btns = []
        for label, fn in [("Add", self._add), ("Edit", self._edit), ("Remove", self._remove),
                          ("Connect", lambda: self._each("connect")), ("Disconnect", lambda: self._each("disconnect")),
                          ("Start", lambda: self._each("start")), ("Stop", lambda: self._each("stop")),
                          ("Restart", lambda: self._each("restart")),
                          ("Import Switchboard config…", self._import)]:
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(fn)
            btns.append(b)
        dev_card.add_layout(hbox(*btns, None))
        lay.addWidget(dev_card, 3)

        bottom = QtWidgets.QHBoxLayout()
        grp_card = Card("Groups")
        self.groups = make_table(["Group", "Devices"])
        self.groups.doubleClicked.connect(lambda _: self._edit_group())
        grp_card.add(self.groups)
        gb = []
        for label, fn in [("Add group", self._add_group), ("Edit", self._edit_group), ("Remove", self._remove_group)]:
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(fn)
            gb.append(b)
        grp_card.add_layout(hbox(*gb, None))
        bottom.addWidget(grp_card, 1)

        log_card = Card("Device log")
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        log_card.add(self.log)
        log_card.add(muted("Shows launch command lines and listener messages. Enable 'update clients with stdout' "
                           "in the listener for full Unreal logs."))
        bottom.addWidget(log_card, 2)
        lay.addLayout(bottom, 2)

    # ---- device editing -------------------------------------------------------------
    def _names(self):
        return [d.name for d in self.ctx.config.devices]

    def _selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.ctx.config.devices[r] for r in rows if r < len(self.ctx.config.devices)]

    def _form_kwargs(self):
        return dict(choices={"role": list(ROLES), "platform": ["Win64", "Linux"],
                             "listener_protocol": [("Automatic (QUIC for UE 5.4+, TCP before)", "auto"),
                                                   ("QUIC with password (UE 5.4+)", "quic"),
                                                   ("TCP (UE 5.2 - 5.3)", "tcp")]},
                    labels={"rc_enabled": "Remote Control enabled", "rc_port": "Remote Control port",
                            "listener_protocol": "Listener connection",
                            "join_multiuser": "Join multi-user session"},
                    browse={"uproject_path_override": "file", "engine_dir_override": "dir"})

    def _add(self) -> None:
        dev = DeviceConfig(name=f"Device {len(self.ctx.config.devices) + 1}")
        if edit_dataclass(self, "Add device", dev, unique_name_validator(self._names(), ""), **self._form_kwargs()):
            self.ctx.config.devices.append(dev)
            self._changed()

    def _edit(self) -> None:
        sel = self._selected()
        if not sel:
            return
        dev = sel[0]
        old = dev.name
        if edit_dataclass(self, f"Edit {dev.name}", dev, unique_name_validator(self._names(), old), **self._form_kwargs()):
            if dev.name != old:
                for g in self.ctx.config.groups:
                    g.devices = [dev.name if n == old else n for n in g.devices]
            self._changed()

    def _remove(self) -> None:
        sel = self._selected()
        if not sel or QtWidgets.QMessageBox.question(self, "Remove", f"Remove {len(sel)} device(s)?") != QtWidgets.QMessageBox.Yes:
            return
        names = {d.name for d in sel}
        self.ctx.config.devices = [d for d in self.ctx.config.devices if d.name not in names]
        for g in self.ctx.config.groups:
            g.devices = [n for n in g.devices if n not in names]
        self._changed()

    def _each(self, op: str) -> None:
        for dev in self._selected():
            fn = getattr(self.ctx.devices, op)
            self.background(lambda fn=fn, n=dev.name: fn(n), what=f"{op} {dev.name}")

    def _import(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Switchboard config", default_config_dir(self.ctx.config),
                                                        "Switchboard config (*.json)")
        if not path:
            return
        try:
            project, devices = import_switchboard_config(path)
        except (OSError, ValueError) as exc:
            self.notify(f"Import failed: {exc}", False)
            return
        existing = set(self._names())
        added = [d for d in devices if d.name not in existing]
        self.ctx.config.devices.extend(added)
        if project.uproject_path and not any(p.uproject_path == project.uproject_path for p in self.ctx.config.projects):
            self.ctx.config.projects.append(project)
        self._changed()
        self.notify(f"Imported {len(added)} device(s) from Switchboard config")

    # ---- groups -------------------------------------------------------------------------
    def _add_group(self) -> None:
        from operator_board.ui.group_dialog import edit_group
        before = len(self.ctx.config.groups)
        if edit_group(self, self.ctx.config, None):
            selected = [d.name for d in self._selected()]
            new = self.ctx.config.groups[before:]
            if new and selected and not new[0].devices:
                new[0].devices = selected  # the devices selected in the table, if none were ticked
            self._changed()

    def _selected_group(self):
        rows = sorted({i.row() for i in self.groups.selectedIndexes()})
        return self.ctx.config.groups[rows[0]] if rows and rows[0] < len(self.ctx.config.groups) else None

    def _edit_group(self) -> None:
        from operator_board.ui.group_dialog import edit_group
        g = self._selected_group()
        if g and edit_group(self, self.ctx.config, g):
            self._changed()

    def _remove_group(self) -> None:
        g = self._selected_group()
        if g:
            self.ctx.config.groups.remove(g)
            self._changed()

    def _changed(self) -> None:
        self.ctx.devices.reload()
        self.save_config()
        self.refresh()

    # ---- display ------------------------------------------------------------------------
    def _show_log(self) -> None:
        sel = self._selected()
        if sel:
            rt = self.ctx.devices.get(sel[0].name)
            self.log.setPlainText("\n".join(rt.log_tail) if rt else "")

    def refresh(self) -> None:
        self.table.setRowCount(0)
        for i, d in enumerate(self.ctx.config.devices):
            rt = self.ctx.devices.get(d.name)
            listener = " ".join(x for x in (rt.listener_version, rt.transport.upper()) if x) if rt else ""
            set_row(self.table, i, [d.name, d.role, f"{d.address}:{d.listener_port}", d.ndisplay_node,
                                    rt.state if rt else "", listener,
                                    rt.last_error if rt else ""])
        self.groups.setRowCount(0)
        for i, g in enumerate(self.ctx.config.groups):
            set_row(self.groups, i, [g.name, ", ".join(g.devices)])
        self._show_log()
