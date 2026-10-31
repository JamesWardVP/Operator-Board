"""Python snippets executed inside Unreal via Remote Control (Python Editor Script Plugin).

Each snippet assigns to `result`; rc_client.python_result_wrapper prints it back as
JSON. Snippets try several API spellings so they work across 5.2 - 5.8.
"""
from __future__ import annotations

import json


def _lit(value) -> str:
    return json.dumps(value)


EDITOR_WORLD = """
def _editor_world():
    sub = getattr(unreal, 'UnrealEditorSubsystem', None)
    if sub is not None:
        w = unreal.get_editor_subsystem(sub).get_editor_world()
        if w:
            return w
    return unreal.EditorLevelLibrary.get_editor_world()
world = _editor_world()
"""


def current_level() -> str:
    return EDITOR_WORLD + "result = {'path': world.get_path_name(), 'name': world.get_name()}\n"


def load_level(level_path: str) -> str:
    return (
        f"path = {_lit(level_path)}\n"
        "sub = getattr(unreal, 'LevelEditorSubsystem', None)\n"
        "ok = unreal.get_editor_subsystem(sub).load_level(path) if sub else unreal.EditorLevelLibrary.load_level(path)\n"
        "result = {'ok': bool(ok), 'path': path}\n"
    )


def list_sublevels() -> str:
    return EDITOR_WORLD + """
levels = []
for lvl in unreal.EditorLevelUtils.get_levels(world):
    outer = lvl.get_outer()
    package = outer.get_path_name().split('.')[0] if outer else lvl.get_path_name()
    if outer == world:
        continue  # persistent level
    visible = None
    for getter in ('is_level_visible',):
        fn = getattr(unreal.EditorLevelUtils, getter, None)
        if fn:
            try:
                visible = bool(fn(lvl))
            except Exception:
                pass
    levels.append({'name': package.split('/')[-1], 'package': package, 'visible': visible})
result = {'levels': levels}
"""


def set_sublevel_visibility(package: str, visible: bool) -> str:
    return EDITOR_WORLD + f"""
target = {_lit(package)}
done = False
for lvl in unreal.EditorLevelUtils.get_levels(world):
    outer = lvl.get_outer()
    pkg = outer.get_path_name().split('.')[0] if outer else ''
    if pkg == target or pkg.split('/')[-1] == target:
        unreal.EditorLevelUtils.set_level_visibility(lvl, {visible!r}, False)
        done = True
result = {{'ok': done}}
"""


def list_data_layers() -> str:
    return """
sub = unreal.get_editor_subsystem(unreal.DataLayerEditorSubsystem)
layers = []
getter = getattr(sub, 'get_all_data_layers', None)
for dl in (getter() if getter else []):
    label = None
    for attr in ('get_data_layer_short_name', 'get_data_layer_full_name', 'get_data_layer_label', 'get_name'):
        fn = getattr(dl, attr, None)
        if fn:
            try:
                label = str(fn()); break
            except Exception:
                pass
    vis = None
    for attr in ('is_visible', 'is_effective_visible'):
        fn = getattr(dl, attr, None)
        if fn:
            try:
                vis = bool(fn()); break
            except Exception:
                pass
    layers.append({'name': label or dl.get_name(), 'path': dl.get_path_name(), 'visible': vis})
result = {'layers': layers}
"""


def set_data_layer_visibility(path: str, visible: bool) -> str:
    return f"""
sub = unreal.get_editor_subsystem(unreal.DataLayerEditorSubsystem)
dl = unreal.load_object(None, {_lit(path)})
ok = False
if dl:
    sub.set_data_layer_visibility(dl, {visible!r})
    ok = True
result = {{'ok': ok}}
"""


def get_viewport_camera() -> str:
    return """
sub = getattr(unreal, 'UnrealEditorSubsystem', None)
info = unreal.get_editor_subsystem(sub).get_level_viewport_camera_info() if sub else unreal.EditorLevelLibrary.get_level_viewport_camera_info()
if isinstance(info, tuple) and len(info) == 3:
    info = info[1:]
loc, rot = info
result = {'location': [loc.x, loc.y, loc.z], 'rotation': [rot.pitch, rot.yaw, rot.roll]}
"""


def set_viewport_camera(location, rotation) -> str:
    x, y, z = location
    p, yaw, r = rotation
    return f"""
loc = unreal.Vector({x}, {y}, {z})
rot = unreal.Rotator(roll={r}, pitch={p}, yaw={yaw})
sub = getattr(unreal, 'UnrealEditorSubsystem', None)
if sub:
    unreal.get_editor_subsystem(sub).set_level_viewport_camera_info(loc, rot)
else:
    unreal.EditorLevelLibrary.set_level_viewport_camera_info(loc, rot)
result = {{'ok': True}}
"""


def list_actors(class_filter: str = "") -> str:
    return f"""
cls_filter = {_lit(class_filter)}.lower()
sub = getattr(unreal, 'EditorActorSubsystem', None)
actors = unreal.get_editor_subsystem(sub).get_all_level_actors() if sub else unreal.EditorLevelLibrary.get_all_level_actors()
out = []
for a in actors:
    cls = a.get_class().get_name()
    if cls_filter and cls_filter not in cls.lower():
        continue
    out.append({{'label': a.get_actor_label(), 'path': a.get_path_name(), 'class': cls}})
result = {{'actors': out}}
"""


def take_recorder_start(slate: str, take: int, description: str) -> str:
    # A panel that was only just opened has no take data until the editor has ticked
    # (UE 5.8), so report 'retry' and let the caller call again a moment later.
    return f"""
lib = unreal.TakeRecorderBlueprintLibrary
panel = lib.get_take_recorder_panel()
if panel is None:
    lib.open_take_recorder_panel()
    panel = lib.get_take_recorder_panel()
md = panel.get_take_meta_data() if panel is not None else None
if md is None:
    result = {{'ok': False, 'retry': True}}
else:
    md.set_slate({_lit(slate)})
    md.set_take_number({int(take)})
    if hasattr(md, 'set_description'):
        md.set_description({_lit(description)})
    panel.start_recording()
    result = {{'ok': True}}
"""


def take_recorder_apply_preset(asset_path: str) -> str:
    """Load a Take Preset (its sources and settings) into the Take Recorder panel."""
    return f"""
lib = unreal.TakeRecorderBlueprintLibrary
panel = lib.get_take_recorder_panel()
if panel is None:
    lib.open_take_recorder_panel()
    panel = lib.get_take_recorder_panel()
preset = unreal.load_asset({_lit(asset_path)})
if preset is None:
    raise RuntimeError('Take Preset not found: ' + {_lit(asset_path)})
panel.setup_for_recording_take_preset(preset)
result = {{'ok': True}}
"""


def take_recorder_add_camera(preferred_label: str = "") -> str:
    """Add the stage camera as a Take Recorder source and say which actor it was.

    Choice, first match wins: the actor labelled ``preferred_label``; the external CineCameraActor
    of an nDisplay ICVFX camera; the nDisplay root actor holding an ICVFX camera (records the
    component); a CineCameraActor in the level (one with "main" in its label, else the first by
    label); the nDisplay root actor itself (its viewpoint).
    """
    return f"""
lib = unreal.TakeRecorderBlueprintLibrary
panel = lib.get_take_recorder_panel()
if panel is None:
    lib.open_take_recorder_panel()
    panel = lib.get_take_recorder_panel()
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
preferred = {_lit(preferred_label)}
roots = [a for a in actors if isinstance(a, unreal.DisplayClusterRootActor)]
cines = sorted((a for a in actors if isinstance(a, unreal.CineCameraActor)), key=lambda a: a.get_actor_label())
chosen, why = None, ''
if preferred:
    chosen = next((a for a in actors if a.get_actor_label() == preferred), None)
    if chosen is None:
        raise RuntimeError('Take camera not found in the level: ' + preferred)
    why = 'the take camera set in Operator Board'
for root in roots:
    if chosen is not None:
        break
    for comp in root.get_components_by_class(unreal.DisplayClusterICVFXCameraComponent):
        ext = None
        try:
            ext = comp.get_editor_property('camera_settings').get_editor_property('external_camera_actor')
        except Exception:
            pass
        if isinstance(ext, unreal.Actor):
            chosen, why = ext, 'the ICVFX camera of ' + root.get_actor_label()
        else:
            chosen, why = root, 'it holds the ICVFX camera ' + comp.get_name()
        break
if chosen is None and cines:
    chosen = next((c for c in cines if 'main' in c.get_actor_label().lower()), cines[0])
    why = 'the cine camera in the level' if len(cines) == 1 else 'one of %d cine cameras in the level' % len(cines)
if chosen is None and roots:
    chosen, why = roots[0], 'the nDisplay setup (its viewpoint)'
if chosen is None:
    raise RuntimeError('no nDisplay setup or cine camera found in the level to record')
unreal.TakeRecorderActorSource.add_source_for_actor(chosen, panel.get_sources())
result = {{'added': chosen.get_actor_label(), 'why': why}}
"""


def take_recorder_set_metadata(slate: str, take: int, description: str) -> str:
    """Show Operator Board's slate / take in the Take Recorder panel, if it is open (never opens it)."""
    return f"""
panel = unreal.TakeRecorderBlueprintLibrary.get_take_recorder_panel()
md = panel.get_take_meta_data() if panel is not None else None
if md is None or unreal.TakeRecorderBlueprintLibrary.is_recording():
    result = {{'synced': False}}
else:
    md.set_slate({_lit(slate)})
    md.set_take_number({int(take)})
    if hasattr(md, 'set_description'):
        md.set_description({_lit(description)})
    result = {{'synced': True}}
"""


def take_recorder_stop() -> str:
    return """
lib = unreal.TakeRecorderBlueprintLibrary
panel = lib.get_take_recorder_panel()
if panel is not None and hasattr(panel, 'stop_recording'):
    panel.stop_recording()
else:
    lib.stop_recording()
result = {'ok': True}
"""


def take_recorder_status() -> str:
    return "result = {'recording': bool(unreal.TakeRecorderBlueprintLibrary.is_recording())}\n"
