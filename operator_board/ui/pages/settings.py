"""Global settings, multi-user advanced options, integrations and config import/export."""
from __future__ import annotations

import shutil

from operator_board import __version__
from operator_board.core.config import AppConfig, default_config_path, save_config
from operator_board.core.ue_versions import SUPPORTED_VERSIONS
from operator_board.qt import BINDING, QtWidgets
from operator_board.ui.forms import DataclassForm
from operator_board.ui.pages.base import BasePage
from operator_board.ui.widgets import Card, hbox, muted, scroll


class SettingsPage(BasePage):
    title = "Settings"

    def __init__(self, ctx, window) -> None:
        super().__init__(ctx, window)
        root = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(root)
        lay.setContentsMargins(16, 16, 16, 16)
        cfg = ctx.config
        hosts = lambda: [("This machine", "local")] + [(d.name, d.name) for d in cfg.devices]
        editors = lambda: [("(first editor)", "")] + [(d.name, d.name) for d in cfg.devices]
        self.forms = []

        def section(title, obj, **kw):
            card = Card(title)
            form = DataclassForm(obj, **kw)
            self.forms.append(form)
            card.add(form)
            lay.addWidget(card)
            return card

        section("Multi-user server", cfg.multiuser, choices={"host": hosts},
                labels={"host": "Run server on", "use_slate_server": "Use Slate (GUI) server",
                        "auto_start": "Auto start multi-user (start the server before Unreal)",
                        "clean_history": "Clean history (forget saved sessions on next start)",
                        "auto_increment_session": "Number sessions (next session name after every stop, "
                                                  "and when the name is still on the server)",
                        "client_join_pattern": "Client joined log pattern (regex)",
                        "client_leave_pattern": "Client left log pattern (regex)"})
        section("Remote Control", cfg.remote_control, choices={
            "primary_target": editors,
            "level_load_mode": ["editor", "nodes", "relaunch", "all"]},
            labels={"primary_target": "Primary editor", "timeout_s": "Request timeout (s)",
                    "allow_remote_features": "Allow Take Recorder, levels and cameras on launched instances "
                                             "(remote Python and console commands)"})
        section("Control surfaces & API", cfg.surfaces,
                labels={"api_bind": "API bind address (0.0.0.0 = all networks)", "api_token": "API token (optional)",
                        "layout_path": "Surface layout file"}, browse={"layout_path": "file"})
        section("Operator interface", cfg.ui, labels={"metrics_poll_s": "Metrics poll interval (s)",
                                                      "confirm_blackout": "Confirm before blackout",
                                                      "confirm_stop_all": "Confirm before Stop all"})
        section("Takes", cfg.takes, browse={"take_log_path": "file"},
                labels={"take_preset": "Take preset (optional asset path, e.g. /Game/Takes/StagePreset)",
                        "auto_add_camera": "If the take is empty, add the stage camera and record",
                        "take_camera": "Take camera (optional actor name; default: nDisplay ICVFX / cine camera)",
                        "record_with_take_recorder": "Record with Take Recorder in the primary editor"})

        save = QtWidgets.QPushButton("Save settings")
        save.setMinimumHeight(44)
        save.clicked.connect(self._save)
        lay.addWidget(save)

        io = Card("Configuration file")
        export = QtWidgets.QPushButton("Export configuration…")
        export.clicked.connect(self._export)
        imp = QtWidgets.QPushButton("Import configuration…")
        imp.clicked.connect(self._import)
        io.add_layout(hbox(export, imp, None))
        io.add(muted("Export copies the whole stage setup (projects, devices, groups, LED processors, cameras, sync "
                     "jobs) to a file you can load on another machine. Some changes (API server, Stream Deck) "
                     "take effect after a restart."))
        lay.addWidget(io)

        about = Card("About")
        about.add(muted(f"Operator Board {__version__} · Qt binding {BINDING} · "
                        f"Unreal Engine {SUPPORTED_VERSIONS[0]}–{SUPPORTED_VERSIONS[-1]}"))
        lay.addWidget(about)
        lay.addStretch(1)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll(root))

    def _save(self) -> None:
        try:
            for f in self.forms:
                f.apply()
        except ValueError as exc:
            self.notify(str(exc), False)
            return
        self.ctx.multiuser.tracker = self.ctx.multiuser._new_tracker()
        self.save_config()
        self.ctx.bus.publish("config.changed")
        self.notify("Settings saved")

    def _export(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export configuration", "operator_board_config.json",
                                                        "JSON (*.json)")
        if path:
            save_config(self.ctx.config, path)
            self.notify(f"Exported configuration to {path}")

    def _import(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import configuration", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                AppConfig.from_json(fh.read())  # validate before replacing
        except (OSError, ValueError, TypeError) as exc:
            self.notify(f"Not a valid Operator Board configuration: {exc}", False)
            return
        if QtWidgets.QMessageBox.question(self, "Import", "Replace the current configuration? Operator Board will "
                                          "close so the new configuration can load cleanly.") != QtWidgets.QMessageBox.Yes:
            return
        target = self.ctx.config_path or default_config_path()
        shutil.copyfile(path, target)
        self.window_.skip_save_on_close = True
        self.window_.close()
