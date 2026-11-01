"""Saved settings presets: several kinds of settings saved together and applied in one go.

A preset holds items. Each item is one kind of setting (the level, sub-levels, data layers, Remote
Objects settings, camera positions, LED processor modes, take settings, control surfaces) with its data.
Items are always applied in ITEM_ORDER, whatever order they were added in (the level must be open
before its sub-levels or its actors can be set). Each step checks that it happened before the next
one starts; a step that fails or finds data missing raises PresetProblem, and the UI asks whether to
retry, skip it, or cancel.

Presets are kept with the stage (``AppConfig.setting_presets``) and can be exported to / imported from
``.json`` files, as can single items.
"""
from __future__ import annotations

import copy
import json
import time
from dataclasses import asdict
from typing import Any, Callable, Dict, List, Optional, Tuple

from operator_board.core.config import CameraPreset, atomic_write

PRESET_FORMAT = "operator-board-settings-preset"
ITEM_FORMAT = "operator-board-settings-item"

# type -> (label, what it does when applied)
ITEM_TYPES: Dict[str, Tuple[str, str]] = {
    "level": ("Level", "Open the level in the editor and wait until it has loaded"),
    "sublevels": ("Sub-levels", "Show or hide each sub-level"),
    "data_layers": ("Data layers", "Show or hide each data layer"),
    "object_settings": ("Remote Objects settings", "Set each light, camera or property value"),
    "cameras": ("Camera positions", "Add or update the saved camera positions"),
    "led": ("LED processor modes", "Put each LED processor into its saved mode"),
    "take": ("Take settings", "Set the scene (slate), take number and description"),
    "surfaces": ("Control surfaces", "Add the control surfaces (replacing any of the same name)"),
}
ITEM_ORDER = list(ITEM_TYPES)
LEVEL_WAIT_S = 180.0


class PresetProblem(Exception):
    """A step that could not be done (missing data, an error, or a check that did not pass)."""


def item_label(item: Dict[str, Any]) -> str:
    return ITEM_TYPES.get(item.get("type", ""), (item.get("type", "?"), ""))[0]


def item_summary(item: Dict[str, Any]) -> str:
    data = item.get("data")
    kind = item.get("type")
    if kind == "level":
        return (data or {}).get("level", "").split("/")[-1].split(".")[0] or "(no level)"
    if kind == "take":
        d = data or {}
        return f"{d.get('slate', '')} take {d.get('take', '')}" + (f" · {d['description']}" if d.get("description") else "")
    if kind == "led":
        return ", ".join(f"{n}: {v.get('mode')}" for n, v in (data or {}).items()) or "(none)"
    count = len(data or [])
    return f"{count} item{'s' if count != 1 else ''}"


def ordered(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Items in the order they are applied."""
    return sorted(items, key=lambda i: ITEM_ORDER.index(i["type"]) if i.get("type") in ITEM_ORDER else 99)


# ---- files -----------------------------------------------------------------------------------
def export_preset(path: str, preset: Dict[str, Any]) -> None:
    atomic_write(path, json.dumps(dict(preset, format=PRESET_FORMAT), indent=2, default=str))


def export_item(path: str, item: Dict[str, Any]) -> None:
    atomic_write(path, json.dumps(dict(item, format=ITEM_FORMAT), indent=2, default=str))


def read_file(path: str) -> Dict[str, Any]:
    """A preset (``{"name", "items"}``) from a preset file, a single item file, a Remote Objects
    settings export, a control surfaces export or a camera positions CSV."""
    if path.lower().endswith(".csv"):
        from operator_board.remote.cameras import CameraService
        from operator_board.core.config import AppConfig
        holder = AppConfig()
        CameraService(holder, None).import_csv(path)
        return {"name": "Camera positions", "items": [{"type": "cameras", "data": [asdict(c) for c in holder.cameras]}]}
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    fmt = data.get("format") if isinstance(data, dict) else None
    if fmt == PRESET_FORMAT:
        return {"name": data.get("name", "Imported preset"), "items": list(data.get("items", []))}
    if fmt == ITEM_FORMAT:
        return {"name": item_label(data), "items": [{"type": data["type"], "data": data.get("data")}]}
    if fmt == "operator-board-surfaces":
        return {"name": "Control surfaces", "items": [{"type": "surfaces", "data": data.get("surfaces", [])}]}
    raise ValueError("not an Operator Board settings preset, settings item, control surfaces file or camera CSV")


# ---- capturing the current settings -----------------------------------------------------------
def capture(ctx, kind: str) -> Dict[str, Any]:
    """An item holding the current setting of ``kind`` (reads from the editor where needed)."""
    if kind == "level":
        level = ctx.levels.current_level()
        if not level:
            raise PresetProblem("No level is open in the editor")
        return {"type": kind, "data": {"level": level}}
    if kind == "sublevels":
        return {"type": kind, "data": [{"package": s["package"], "visible": bool(s["visible"]), "name": s.get("name", "")}
                                       for s in ctx.levels.sublevels()]}
    if kind == "data_layers":
        return {"type": kind, "data": [{"path": d["path"], "visible": bool(d["visible"]), "name": d.get("name", "")}
                                       for d in ctx.levels.data_layers()]}
    if kind == "object_settings":
        settings = ctx.changed_settings()
        if not settings:
            raise PresetProblem("No Remote Objects settings have been changed from Operator Board yet")
        return {"type": kind, "data": settings}
    if kind == "cameras":
        return {"type": kind, "data": [asdict(c) for c in ctx.config.cameras]}
    if kind == "led":
        return {"type": kind, "data": {p.name: {"mode": p.mode, "pattern": p.state.test_pattern or ""}
                                       for p in ctx.led.active()}}
    if kind == "take":
        t = ctx.config.takes
        return {"type": kind, "data": {"slate": t.slate, "take": t.take, "description": t.description}}
    if kind == "surfaces":
        return {"type": kind, "data": copy.deepcopy(ctx.config.surfaces.devices)}
    raise PresetProblem(f"Unknown kind of setting '{kind}'")


# ---- applying -----------------------------------------------------------------------------------
def steps(ctx, preset: Dict[str, Any]) -> List[Tuple[str, Callable[[], str]]]:
    """(label, function) for each item, in the order they are applied. Each function runs off the UI
    thread, returns what it did and raises PresetProblem when it could not."""
    out = []
    for item in ordered(preset.get("items", [])):
        kind = item.get("type")
        fn = _APPLY.get(kind)
        label = f"{item_label(item)}: {item_summary(item)}"
        if fn is None:
            out.append((label, lambda k=kind: _raise(f"Unknown kind of setting '{k}'")))
        else:
            out.append((label, lambda f=fn, d=item.get("data"): f(ctx, d)))
    return out


def _raise(message: str) -> str:
    raise PresetProblem(message)


def _need(data, what: str):
    if not data:
        raise PresetProblem(f"The preset has no {what}")
    return data


def _apply_level(ctx, data) -> str:
    level = _need((data or {}).get("level"), "level to open")
    current = None
    try:
        current = ctx.levels.current_level()
    except Exception as exc:  # noqa: BLE001
        raise PresetProblem(f"The editor did not answer: {exc}") from exc
    if current and current.split(".")[0] == level.split(".")[0]:
        return f"{level.split('/')[-1]} is already open"
    result = ctx.load_level(level, "editor")
    if not result.ok:
        raise PresetProblem(result.message)
    deadline = time.time() + LEVEL_WAIT_S
    while time.time() < deadline:  # wait until the editor really has the level open
        try:
            current = ctx.levels.current_level()
        except Exception:  # noqa: BLE001 - busy loading
            current = None
        if current and current.split(".")[0] == level.split(".")[0]:
            time.sleep(2.0)  # let it settle before the next step
            ctx.changes.record("Levels", "Level", "Open", level, None, source="Preset")
            return f"Opened {level.split('/')[-1]}"
        time.sleep(2.0)
    raise PresetProblem(f"The editor did not finish opening {level} within {LEVEL_WAIT_S:.0f} s")


def _apply_sublevels(ctx, data) -> str:
    wanted = _need(data, "sub-levels")
    present = {s["package"]: s for s in ctx.levels.sublevels()}
    missing = [w["package"] for w in wanted if w["package"] not in present]
    if missing:
        raise PresetProblem("Not in the open level: " + ", ".join(m.split("/")[-1] for m in missing))
    changed = 0
    for w in wanted:
        if bool(present[w["package"]]["visible"]) != bool(w["visible"]):
            errors = ctx.levels.set_sublevel_visible(w["package"], bool(w["visible"]))
            if errors:
                raise PresetProblem("; ".join(errors))
            ctx.changes.record("Levels", w["package"].split("/")[-1], "Visible", bool(w["visible"]),
                               bool(present[w["package"]]["visible"]), source="Preset")
            changed += 1
    after = {s["package"]: bool(s["visible"]) for s in ctx.levels.sublevels()}
    wrong = [w["package"].split("/")[-1] for w in wanted if after.get(w["package"]) != bool(w["visible"])]
    if wrong:
        raise PresetProblem("Did not change: " + ", ".join(wrong))
    return f"{changed} sub-level{'s' if changed != 1 else ''} changed, {len(wanted) - changed} already right"


def _apply_data_layers(ctx, data) -> str:
    wanted = _need(data, "data layers")
    present = {d["path"]: d for d in ctx.levels.data_layers()}
    missing = [w.get("name") or w["path"] for w in wanted if w["path"] not in present]
    if missing:
        raise PresetProblem("Not in the open level: " + ", ".join(missing))
    changed = 0
    for w in wanted:
        if bool(present[w["path"]]["visible"]) != bool(w["visible"]):
            ctx.levels.set_data_layer_visible(w["path"], bool(w["visible"]))
            ctx.changes.record("Levels", w.get("name") or w["path"], "Data layer visible", bool(w["visible"]),
                               bool(present[w["path"]]["visible"]), source="Preset")
            changed += 1
    return f"{changed} data layer{'s' if changed != 1 else ''} changed, {len(wanted) - changed} already right"


def _apply_object_settings(ctx, data) -> str:
    settings = _need(data, "Remote Objects settings")
    for s in settings:
        try:
            ctx.apply_setting(s["kind"], s["ref"], s["value"])
        except Exception as exc:  # noqa: BLE001
            raise PresetProblem(f"{s.get('item', '')} {s.get('setting', '')}: {exc}") from exc
        ctx.changes.record("Remote Objects", s.get("item", ""), s.get("setting", ""), s["value"], None,
                           source="Preset", kind=s["kind"], ref=s["ref"])
    return f"Set {len(settings)} value{'s' if len(settings) != 1 else ''}"


def _apply_cameras(ctx, data) -> str:
    cameras = _need(data, "camera positions")
    added = updated = 0
    for c in cameras:
        preset = CameraPreset(**{k: v for k, v in c.items() if k in CameraPreset.__dataclass_fields__})
        existing = next((x for x in ctx.config.cameras if x.name == preset.name), None)
        if existing is None:
            ctx.config.cameras.append(preset)
            added += 1
        else:
            existing.target, existing.location, existing.rotation = preset.target, preset.location, preset.rotation
            updated += 1
    ctx.bus.publish("cameras.changed")
    ctx.changes.record("Cameras", "Camera positions", "Loaded", [c.get("name") for c in cameras], source="Preset")
    return f"{added} added, {updated} updated"


def _apply_led(ctx, data) -> str:
    modes = _need(data, "LED processor modes")
    missing = [n for n in modes if not ctx.led.get(n)]
    if missing:
        raise PresetProblem("No LED processor called " + ", ".join(missing))
    for name, want in modes.items():
        errors = ctx.led.set_mode(name, want.get("mode", "normal"), want.get("pattern") or None) \
            if ctx.led.get(name).mode != want.get("mode") or want.get("mode") == "pattern" else []
        if errors:
            raise PresetProblem(f"{name}: " + "; ".join(errors))
    wrong = [n for n, want in modes.items() if ctx.led.get(n).mode != want.get("mode", "normal")]
    if wrong:
        raise PresetProblem("Not in the saved mode: " + ", ".join(wrong))
    return f"{len(modes)} processor{'s' if len(modes) != 1 else ''} set"


def _apply_take(ctx, data) -> str:
    d = _need(data, "take settings")
    if ctx.takes.recording:
        raise PresetProblem("A take is recording: stop it before changing the scene and take")
    if d.get("slate"):
        ctx.takes.set_slate(d["slate"])
    if d.get("take"):
        ctx.takes.set_take(int(d["take"]))
    ctx.takes.set_description(d.get("description", ""))
    ctx.changes.record("Takes", "Take settings", "Scene / take", d, source="Preset")
    return f"{ctx.config.takes.slate} take {ctx.config.takes.take}"


def _apply_surfaces(ctx, data) -> str:
    from operator_board.core.serde import from_dict
    from operator_board.surfaces.layout import SurfaceLayout
    layouts = [from_dict(SurfaceLayout, d) for d in _need(data, "control surfaces")]
    names = {l.name for l in layouts}
    for c in [c for c in ctx.surfaces.controllers if c.layout.name in names]:
        ctx.surfaces.remove(c)
    ctx.surfaces.add_layouts(layouts)
    ctx.changes.record("Control Surfaces", "Control surfaces", "Loaded", sorted(names), source="Preset")
    return f"{len(layouts)} surface{'s' if len(layouts) != 1 else ''}"


_APPLY: Dict[str, Callable[[Any, Any], str]] = {
    "level": _apply_level, "sublevels": _apply_sublevels, "data_layers": _apply_data_layers,
    "object_settings": _apply_object_settings, "cameras": _apply_cameras, "led": _apply_led,
    "take": _apply_take, "surfaces": _apply_surfaces,
}
