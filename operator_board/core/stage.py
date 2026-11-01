"""Where the stage configuration lives: standalone, or linked to a Switchboard config.

Standalone
    Everything is in ``~/.operator_board/config.json`` (as in 0.1).

Switchboard stage
    ``<configs>/<Name>.json``     the Switchboard config: project, engine, Unreal/nDisplay
                                  devices and multi-user settings. Switchboard can open it.
    ``<configs>/<Name>.opboard``  Operator Board's per-stage settings: LED processors,
                                  groups, cameras, takes, sync jobs, Remote Control...
                                  (JSON, but not ``.json`` so Switchboard never lists it).
    ``~/.operator_board/config.json`` keeps the machine settings (control surfaces, API,
                                  interface, which Switchboard config is open) and the
                                  standalone stage, untouched, for when no config is open.

Inside an Operator Board project (core/projects.py) the stage settings live in the project folder
instead: ``<project>/config.json`` for the project's own settings (and which Switchboard config it
uses) and ``<project>/stages/<Name>.opboard`` for its settings with each Switchboard config, so two
projects using the same Switchboard config keep their own cameras, takes, LED processors...
``~/.operator_board/config.json`` still holds the machine settings.

On save the Switchboard file is re-read from disk and only Operator Board's keys are
merged in, so changes made in Switchboard in between are kept.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import fields
from typing import Optional

from operator_board.core.config import AppConfig, atomic_write, default_config_path, load_config, save_config
from operator_board.core.serde import from_dict, to_dict
from operator_board.core.switchboard_config import (apply_switchboard, merge_into_switchboard,
                                                    new_switchboard_config, read_switchboard_config,
                                                    write_switchboard_config)

log = logging.getLogger(__name__)

SIDECAR_SUFFIX = ".opboard"
SIDECAR_FORMAT = "operator-board-stage"
# Per-machine settings that stay in the global file even when a Switchboard config is open.
MACHINE_FIELDS = ("surfaces", "ui", "switchboard")


def sidecar_path(switchboard_path: str) -> str:
    return os.path.splitext(switchboard_path)[0] + SIDECAR_SUFFIX


def copy_into(target: AppConfig, source: AppConfig) -> None:
    """Replace target's contents in place, so services holding ``target`` see the change."""
    for f in fields(AppConfig):
        setattr(target, f.name, getattr(source, f.name))


class ConfigStore:
    def __init__(self, global_path: Optional[str] = None, project_dir: Optional[str] = None) -> None:
        self.global_path = global_path or default_config_path()
        self.project_dir = project_dir  # an Operator Board project folder, or None (settings as before)
        # Set when the linked Switchboard config could not be opened on load.
        self.load_error = ""

    @property
    def stage_path(self) -> str:
        """Where the settings are kept when no Switchboard config is open."""
        return os.path.join(self.project_dir, "config.json") if self.project_dir else self.global_path

    def sidecar_for(self, sb_path: str) -> str:
        """Operator Board's settings for a Switchboard config (in the project, or next to the config)."""
        if self.project_dir:
            name = os.path.splitext(os.path.basename(sb_path))[0] + SIDECAR_SUFFIX
            return os.path.join(self.project_dir, "stages", name)
        return sidecar_path(sb_path)

    # ---- loading -------------------------------------------------------------------------
    def load(self) -> AppConfig:
        machine = load_config(self.global_path)
        if self.project_dir:
            cfg = load_config(self.stage_path)
            active = cfg.switchboard.active_config  # the project's own choice of Switchboard config
            for name in MACHINE_FIELDS:
                setattr(cfg, name, from_dict(type(getattr(machine, name)), to_dict(getattr(machine, name))))
            cfg.switchboard.active_config = active
        else:
            cfg = machine
        sb_path = cfg.switchboard.active_config
        if not sb_path:
            return cfg
        try:
            return self.load_stage(cfg, sb_path)
        except (OSError, ValueError, TypeError) as exc:
            self.load_error = f"Could not open Switchboard config {sb_path}: {exc}"
            log.warning(self.load_error)
            cfg.switchboard.active_config = ""
            return cfg

    def load_stage(self, machine: AppConfig, sb_path: str) -> AppConfig:
        """Build the stage for a Switchboard config: sidecar + Switchboard file + machine settings."""
        data = read_switchboard_config(sb_path)
        stage = self._read_sidecar(sb_path)
        for name in MACHINE_FIELDS:
            setattr(stage, name, getattr(machine, name))
        stage.switchboard.active_config = os.path.abspath(sb_path)
        apply_switchboard(data, stage)
        return stage

    def _read_sidecar(self, sb_path: str) -> AppConfig:
        path = self.sidecar_for(sb_path)
        if not os.path.exists(path):
            stage = AppConfig()
            stage.projects = []
            return stage
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        data.pop("format", None)
        data.pop("switchboard_config", None)
        return from_dict(AppConfig, data)

    # ---- saving ------------------------------------------------------------------------------
    def save(self, cfg: AppConfig) -> str:
        sb_path = cfg.switchboard.active_config
        if not sb_path:
            path = save_config(cfg, self.stage_path)
            if self.project_dir:
                self._save_machine(cfg)
            return path
        try:
            data = read_switchboard_config(sb_path)
        except FileNotFoundError:
            data = new_switchboard_config(cfg.project())
        write_switchboard_config(sb_path, merge_into_switchboard(data, cfg))
        self._write_sidecar(sb_path, cfg)
        self._save_machine(cfg)
        if self.project_dir:  # remember in the project which Switchboard config it uses
            project = load_config(self.stage_path)
            if project.switchboard.active_config != sb_path:
                project.switchboard.active_config = sb_path
                save_config(project, self.stage_path)
        return sb_path

    def _write_sidecar(self, sb_path: str, cfg: AppConfig) -> None:
        data = {"format": SIDECAR_FORMAT, "switchboard_config": os.path.basename(sb_path)}
        stage = to_dict(cfg)
        for name in MACHINE_FIELDS:
            stage.pop(name, None)
        data.update(stage)
        atomic_write(self.sidecar_for(sb_path), json.dumps(data, indent=2))

    def _save_machine(self, cfg: AppConfig) -> None:
        """Update only the machine settings in the global file, keeping its standalone stage."""
        machine = load_config(self.global_path)
        for name in MACHINE_FIELDS:
            setattr(machine, name, getattr(cfg, name))
        save_config(machine, self.global_path)

    # ---- switching -----------------------------------------------------------------------------
    def open_switchboard(self, current: AppConfig, sb_path: str) -> AppConfig:
        stage = self.load_stage(current, sb_path)
        self.save(stage)
        return stage

    def create_switchboard(self, current: AppConfig, sb_path: str, project_name: str, uproject: str,
                           engine_dir: str, copy_current: bool = True) -> AppConfig:
        """Create a new Switchboard config (and sidecar) and make it the open stage."""
        if os.path.exists(sb_path):
            raise FileExistsError(f"{sb_path} already exists")
        if copy_current:
            stage = from_dict(AppConfig, to_dict(current))
        else:
            stage = AppConfig()
            for name in MACHINE_FIELDS:
                setattr(stage, name, from_dict(type(getattr(current, name)), to_dict(getattr(current, name))))
        project = stage.project()
        project.name = project_name
        project.uproject_path = uproject
        project.engine_dir = engine_dir
        stage.projects = [project] + [p for p in stage.projects if p is not project]
        stage.active_project = project_name
        stage.switchboard.active_config = os.path.abspath(sb_path)
        os.makedirs(os.path.dirname(os.path.abspath(sb_path)), exist_ok=True)
        write_switchboard_config(sb_path, merge_into_switchboard(new_switchboard_config(project), stage))
        self.save(stage)
        return stage

    def save_as_switchboard(self, current: AppConfig, sb_path: str) -> AppConfig:
        """Write the current stage as a new Switchboard config and switch to it."""
        if os.path.exists(sb_path):
            raise FileExistsError(f"{sb_path} already exists")
        stage = from_dict(AppConfig, to_dict(current))
        old = current.switchboard.active_config
        base = read_switchboard_config(old) if old and os.path.exists(old) else new_switchboard_config(stage.project())
        stage.switchboard.active_config = os.path.abspath(sb_path)
        write_switchboard_config(sb_path, merge_into_switchboard(base, stage))
        self.save(stage)
        return stage

    def close_switchboard(self, current: AppConfig) -> AppConfig:
        """Save the open Switchboard stage and go back to the standalone stage."""
        if current.switchboard.active_config:
            self.save(current)
        cfg = load_config(self.stage_path)
        for name in MACHINE_FIELDS:
            setattr(cfg, name, getattr(current, name))
        cfg.switchboard.active_config = ""
        save_config(cfg, self.stage_path)
        if self.project_dir:
            self._save_machine(cfg)
        return cfg
