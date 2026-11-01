"""Operator Board projects: one per shoot or show.

Everything specific to a shoot lives in its project folder, so a new project starts clean (no old
takes or camera positions) and an old one comes back exactly as it was:

    ~/.operator_board/projects/<id>/
        project.json        name and details (production, client, director, notes...), dates
        config.json         the project's settings: Unreal projects, devices, LED processors and
                            groups, camera positions, takes, presets, Remote Control... and which
                            Switchboard config it uses
        stages/<config>.opboard   its settings for each Switchboard config it has opened
        take_log.json       its recorded takes
        change_log.jsonl    its change log

Machine settings (control surfaces, the interface, where Switchboard keeps its configs) stay in
``~/.operator_board/config.json``, shared by every project.

Projects can be duplicated (a checkpoint), renamed, exported to a ``.opproject`` file (a zip of the
folder; leave out takes and the change log to make a template) and imported (as a new project, so
a studio or personal template can be used again and again).
"""
from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import zipfile
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from operator_board.core.config import app_home, atomic_write, default_config_path

PROJECT_FILE = "project.json"
PROJECT_FORMAT = "operator-board-project"
EXPORT_SUFFIX = ".opproject"
SHOOT_FILES = ("take_log.json", "change_log.jsonl")  # left out of templates


@dataclass
class ProjectInfo:
    name: str = "Untitled project"
    description: str = ""
    production: str = ""
    client: str = ""
    director: str = ""
    dop: str = ""
    vp_supervisor: str = ""
    location: str = ""
    shoot_dates: str = ""
    notes: str = ""
    created: str = ""
    modified: str = ""
    id: str = ""  # the folder name (not saved in the file)
    extra: Dict[str, str] = field(default_factory=dict)

    # Shown in the start screen and in the production export, in this order.
    DETAILS = (("production", "Production"), ("client", "Client"), ("director", "Director"),
               ("dop", "Director of photography"), ("vp_supervisor", "VP supervisor"), ("location", "Location"),
               ("shoot_dates", "Shoot dates"), ("description", "Description"), ("notes", "Notes"))


def projects_root() -> str:
    return os.path.join(app_home(), "projects")


def project_dir(project_id: str) -> str:
    return os.path.join(projects_root(), project_id)


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def _slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip()).strip("._") or "project"
    return slug[:60]


def _free_id(name: str) -> str:
    base = _slug(name)
    candidate, n = base, 2
    while os.path.exists(project_dir(candidate)):
        candidate, n = f"{base}_{n}", n + 1
    return candidate


def unique_name(name: str, exclude_id: str = "") -> str:
    taken = {p.name.lower() for p in list_projects() if p.id != exclude_id}
    if name.lower() not in taken:
        return name
    n = 2
    while f"{name} {n}".lower() in taken:
        n += 1
    return f"{name} {n}"


def read_info(project_id: str) -> ProjectInfo:
    path = os.path.join(project_dir(project_id), PROJECT_FILE)
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    known = {k: v for k, v in data.items() if k in ProjectInfo.__dataclass_fields__ and k not in ("id",)}
    info = ProjectInfo(**known)
    info.id = project_id
    return info


def write_info(info: ProjectInfo) -> None:
    data = asdict(info)
    data.pop("id", None)
    data["format"] = PROJECT_FORMAT
    atomic_write(os.path.join(project_dir(info.id), PROJECT_FILE), json.dumps(data, indent=2))


def touch(project_id: str) -> None:
    """Note that the project was just used (the start screen lists the most recent first)."""
    try:
        info = read_info(project_id)
    except (OSError, ValueError):
        return
    info.modified = _now()
    write_info(info)


def list_projects() -> List[ProjectInfo]:
    root = projects_root()
    found = []
    if os.path.isdir(root):
        for entry in os.listdir(root):
            if os.path.exists(os.path.join(root, entry, PROJECT_FILE)):
                try:
                    found.append(read_info(entry))
                except (OSError, ValueError, TypeError):
                    continue
    return sorted(found, key=lambda p: p.modified or p.created, reverse=True)


# Shoot data a new project leaves behind when it starts from another project's stage setup.
SHOOT_FIELDS = ("cameras", "setting_presets", "favourite_properties")


def _clear_shoot_data(data: dict) -> dict:
    for name in SHOOT_FIELDS:
        data.pop(name, None)
    takes = data.get("takes")
    if isinstance(takes, dict):  # back to the first take of a first scene; keep the take settings
        takes.update({"slate": "Scene_01", "take": 1, "description": "", "take_log_path": ""})
    return data


def create(info: ProjectInfo, setup_from: str = "") -> ProjectInfo:
    """A new project. Empty, or with ``setup_from``'s stage setup (Unreal projects, devices, LED
    processors and groups, Switchboard config, multi-user, Remote Control, sync jobs) but none of its
    shoot data (camera positions, takes, presets, change log)."""
    info.name = unique_name(info.name.strip() or "Untitled project")
    info.id = _free_id(info.name)
    info.created = info.modified = _now()
    target = project_dir(info.id)
    os.makedirs(target, exist_ok=True)
    if setup_from:
        source = project_dir(setup_from)
        files = [("config.json", "config.json")]
        stages = os.path.join(source, "stages")
        if os.path.isdir(stages):
            files += [(os.path.join("stages", n), os.path.join("stages", n)) for n in os.listdir(stages)
                      if n.endswith(".opboard")]
        for src, dst in files:
            path = os.path.join(source, src)
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as fh:
                data = _clear_shoot_data(json.load(fh))
            os.makedirs(os.path.dirname(os.path.join(target, dst)), exist_ok=True)
            atomic_write(os.path.join(target, dst), json.dumps(data, indent=2))
    write_info(info)
    return info


def last_project(config_path: Optional[str] = None) -> str:
    """The project opened last, if it still exists."""
    from operator_board.core.config import load_config
    try:
        last = load_config(config_path or default_config_path()).ui.last_project
    except (OSError, ValueError, TypeError):
        return ""
    return last if last and os.path.exists(os.path.join(project_dir(last), PROJECT_FILE)) else ""


def remember(project_id: str, config_path: Optional[str] = None) -> None:
    """Note the project as the last one opened (machine setting) and as just used."""
    from operator_board.core.config import load_config, save_config
    path = config_path or default_config_path()
    cfg = load_config(path)
    cfg.ui.last_project = project_id
    save_config(cfg, path)
    touch(project_id)


def duplicate(project_id: str, new_name: str) -> ProjectInfo:
    """A copy of the whole project under a new name (a checkpoint of where it stands)."""
    source = read_info(project_id)
    name = unique_name(new_name.strip() or f"{source.name} copy")
    new_id = _free_id(name)
    shutil.copytree(project_dir(project_id), project_dir(new_id))
    info = read_info(new_id)
    info.id, info.name = new_id, name
    info.created = info.modified = _now()
    write_info(info)
    return info


def rename(project_id: str, new_name: str) -> ProjectInfo:
    info = read_info(project_id)
    info.name = unique_name(new_name.strip() or info.name, exclude_id=project_id)
    write_info(info)
    return info


def update_details(info: ProjectInfo) -> None:
    current = read_info(info.id)
    info.created = current.created
    info.name = unique_name(info.name.strip() or current.name, exclude_id=info.id)
    write_info(info)


def export_project(project_id: str, path: str, include_shoot_data: bool = True) -> int:
    """Write the project to a ``.opproject`` file; without the takes and change log it is a template.
    Returns the number of files written."""
    root = project_dir(project_id)
    count = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for folder, _dirs, files in os.walk(root):
            for name in files:
                rel = os.path.relpath(os.path.join(folder, name), root).replace("\\", "/")
                if not include_shoot_data and rel in SHOOT_FILES:
                    continue
                if rel.endswith(".old") or "/.tmp_" in f"/{rel}":
                    continue
                zf.write(os.path.join(folder, name), rel)
                count += 1
    return count


def import_project(path: str, name: Optional[str] = None) -> ProjectInfo:
    """Add a project from a ``.opproject`` file, always as a new project (nothing is replaced)."""
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        if PROJECT_FILE not in names:
            raise ValueError("not an Operator Board project file (no project.json)")
        for member in names:  # nothing may be written outside the new folder
            if member.startswith(("/", "\\")) or ".." in member.replace("\\", "/").split("/"):
                raise ValueError(f"unsafe path in the project file: {member}")
        data = json.loads(zf.read(PROJECT_FILE).decode("utf-8"))
        wanted = unique_name((name or data.get("name") or "Imported project").strip())
        new_id = _free_id(wanted)
        target = project_dir(new_id)
        os.makedirs(target, exist_ok=True)
        zf.extractall(target)
    info = read_info(new_id)
    info.id, info.name = new_id, wanted
    info.modified = _now()
    write_info(info)
    return info


def migrate_existing_setup() -> Optional[ProjectInfo]:
    """The first time projects are used: make a project ("My stage") from the settings Operator Board
    used before, so nothing is lost. The old files are left where they were."""
    if list_projects():
        return None
    home = app_home()
    old_config = default_config_path()
    if not os.path.exists(old_config):
        return None
    info = create(ProjectInfo(name="My stage", description="Settings from before projects were added"))
    target = project_dir(info.id)
    shutil.copy2(old_config, os.path.join(target, "config.json"))
    for name in SHOOT_FILES:
        if os.path.exists(os.path.join(home, name)):
            shutil.copy2(os.path.join(home, name), os.path.join(target, name))
    try:
        with open(old_config, "r", encoding="utf-8") as fh:
            active = (json.load(fh).get("switchboard") or {}).get("active_config", "")
    except (OSError, ValueError):
        active = ""
    if active:  # its settings for the open Switchboard config come along too
        sidecar = os.path.splitext(active)[0] + ".opboard"
        if os.path.exists(sidecar):
            os.makedirs(os.path.join(target, "stages"), exist_ok=True)
            shutil.copy2(sidecar, os.path.join(target, "stages", os.path.basename(sidecar)))
    return info


def clear_shoot_data(project_id: str) -> None:
    """Leave a project's stage setup but none of its shoot data: no takes, change log, camera positions,
    presets or favourite properties (used when a new project starts from a template)."""
    root = project_dir(project_id)
    for name in SHOOT_FILES:
        path = os.path.join(root, name)
        if os.path.exists(path):
            os.remove(path)
    paths = [os.path.join(root, "config.json")]
    stages = os.path.join(root, "stages")
    if os.path.isdir(stages):
        paths += [os.path.join(stages, n) for n in os.listdir(stages) if n.endswith(".opboard")]
    for path in paths:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            atomic_write(path, json.dumps(_clear_shoot_data(data), indent=2))
