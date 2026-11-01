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


FIND_ACTOR = """
def _find_actor(ref):
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    found = next((a for a in actors if a.get_path_name() == ref), None)
    found = found or next((a for a in actors if a.get_actor_label() == ref), None)
    if found is None:
        raise RuntimeError('Actor not found in the open level: ' + ref)
    return found
"""


# Components that drive a light's or camera's values from outside (tracking, lens files, DMX):
# their values are shown but not edited, since the next frame would overwrite the change.
QUICK_COMMON = FIND_ACTOR + """
def _drivers(actor):
    names = [c.get_class().get_name() for c in actor.get_components_by_class(unreal.ActorComponent)]
    found = []
    if any('LiveLinkComponentController' in n for n in names):
        found.append('Live Link (tracking / lens data)')
    if any('LensComponent' in n for n in names):
        found.append('a Lens Component (lens file)')
    if any(n.startswith('DMX') for n in names):
        found.append('DMX')
    return found

def _quick_target(actor):
    cam = actor.get_component_by_class(unreal.CineCameraComponent)
    if cam is not None:
        return 'camera', cam
    light = actor.get_component_by_class(unreal.LightComponent)
    if light is not None:
        return 'light', light
    return '', None
"""


def actor_quick_controls(actor_ref: str) -> str:
    """The everyday controls of a light or cine camera with their current values.

    A control driven by Live Link, a Lens Component or DMX carries ``locked`` with the reason."""
    return QUICK_COMMON + f"""
actor = _find_actor({_lit(actor_ref)})
kind, comp = _quick_target(actor)
drivers = _drivers(actor)
locked = ('Driven by ' + ' and '.join(drivers)) if drivers else ''
controls = []
def add(cid, label, ctype, value, lo=None, hi=None, unit='', lock=''):
    controls.append({{'id': cid, 'label': label, 'type': ctype, 'value': value, 'min': lo, 'max': hi,
                      'unit': unit, 'locked': lock}})
if kind == 'light':
    units = ''
    if hasattr(comp, 'intensity_units'):
        units = str(comp.get_editor_property('intensity_units')).split('.')[-1].split(':')[0].strip('<> ').title()
    elif isinstance(comp, unreal.DirectionalLightComponent):
        units = 'Lux'
    add('intensity', 'Intensity', 'float', comp.get_editor_property('intensity'), 0, None, units, locked)
    c = comp.get_editor_property('light_color')
    add('light_color', 'Colour', 'color', [c.r, c.g, c.b], lock=locked)
    add('use_temperature', 'Use temperature', 'bool', bool(comp.get_editor_property('use_temperature')), lock=locked)
    add('temperature', 'Temperature', 'float', comp.get_editor_property('temperature'), 1700, 12000, 'K', locked)
    if hasattr(comp, 'set_attenuation_radius'):
        add('attenuation_radius', 'Attenuation radius', 'float', comp.get_editor_property('attenuation_radius'),
            0, None, 'cm', locked)
    if isinstance(comp, unreal.SpotLightComponent):
        add('inner_cone_angle', 'Inner cone angle', 'float', comp.get_editor_property('inner_cone_angle'), 0, 80, 'deg', locked)
        add('outer_cone_angle', 'Outer cone angle', 'float', comp.get_editor_property('outer_cone_angle'), 1, 80, 'deg', locked)
    add('visible', 'Light on (visible)', 'bool', bool(comp.is_visible()), lock=locked)
    add('cast_shadows', 'Cast shadows', 'bool', bool(comp.get_editor_property('cast_shadows')))
elif kind == 'camera':
    fs = comp.get_editor_property('focus_settings')
    add('focal_length', 'Focal length', 'float', comp.get_editor_property('current_focal_length'), 1, 1000, 'mm', locked)
    add('aperture', 'Aperture', 'float', comp.get_editor_property('current_aperture'), 0.5, 32, 'f-stop', locked)
    add('focus_distance', 'Focus distance', 'float', fs.get_editor_property('manual_focus_distance'), 0, None, 'cm', locked)
result = {{'kind': kind, 'component': comp.get_class().get_name() if comp else '', 'controls': controls,
          'drivers': drivers}}
"""


def set_actor_quick_control(actor_ref: str, control: str, value) -> str:
    """Change one quick control (as an undoable editor transaction, so a multi-user session shares it)."""
    return QUICK_COMMON + f"""
actor = _find_actor({_lit(actor_ref)})
kind, comp = _quick_target(actor)
control, value = {_lit(control)}, {_lit(value)}
if comp is None:
    raise RuntimeError('Not a light or cine camera: ' + actor.get_actor_label())
if _drivers(actor) and control not in ('cast_shadows',):
    raise RuntimeError(actor.get_actor_label() + ' is driven by ' + ' and '.join(_drivers(actor)) +
                       ', which would overwrite the change')
with unreal.ScopedEditorTransaction('Operator Board: ' + control):
    comp.modify()
    if kind == 'light':
        if control == 'intensity':
            comp.set_intensity(float(value))
        elif control == 'light_color':
            # The picker gives sRGB 0-255; Unreal wants linear, which it stores back as that sRGB colour.
            lin = [((c / 255.0 + 0.055) / 1.055) ** 2.4 if c / 255.0 > 0.04045 else c / 255.0 / 12.92
                   for c in (max(0, min(255, int(v))) for v in value)]
            comp.set_light_color(unreal.LinearColor(lin[0], lin[1], lin[2], 1.0), True)
        elif control == 'use_temperature':
            comp.set_use_temperature(bool(value))
        elif control == 'temperature':
            comp.set_temperature(float(value))
        elif control == 'attenuation_radius':
            comp.set_attenuation_radius(float(value))
        elif control == 'inner_cone_angle':
            comp.set_inner_cone_angle(float(value))
        elif control == 'outer_cone_angle':
            comp.set_outer_cone_angle(float(value))
        elif control == 'visible':
            comp.set_visibility(bool(value))
        elif control == 'cast_shadows':
            comp.set_cast_shadows(bool(value))
        else:
            raise RuntimeError('Unknown light control: ' + control)
    else:
        if control == 'focal_length':
            comp.set_editor_property('current_focal_length', float(value))
        elif control == 'aperture':
            comp.set_editor_property('current_aperture', float(value))
        elif control == 'focus_distance':
            fs = comp.get_editor_property('focus_settings')
            fs.set_editor_property('manual_focus_distance', float(value))
            comp.set_editor_property('focus_settings', fs)
        else:
            raise RuntimeError('Unknown camera control: ' + control)
result = {{'ok': True}}
"""


def list_camera_targets() -> str:
    """Camera actors and nDisplay root actors, matched by class (an nDisplay setup is a Blueprint
    subclass such as nDisplayConfig_C, so its class name does not say DisplayClusterRootActor)."""
    return """
kinds = [unreal.CameraActor]
root = getattr(unreal, 'DisplayClusterRootActor', None)
if root is not None:
    kinds.append(root)
out = []
for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors():
    if any(isinstance(a, k) for k in kinds):
        out.append({'label': a.get_actor_label(), 'path': a.get_path_name(), 'class': a.get_class().get_name()})
out.sort(key=lambda a: a['label'].lower())
result = {'actors': out}
"""


def get_actor_transform(actor_ref: str) -> str:
    return FIND_ACTOR + f"""
a = _find_actor({_lit(actor_ref)})
loc, rot = a.get_actor_location(), a.get_actor_rotation()
result = {{'location': [loc.x, loc.y, loc.z], 'rotation': [rot.pitch, rot.yaw, rot.roll]}}
"""


def set_actor_transform(actor_ref: str, location, rotation) -> str:
    x, y, z = (float(v) for v in location)
    p, yaw, r = (float(v) for v in rotation)
    return FIND_ACTOR + f"""
a = _find_actor({_lit(actor_ref)})
with unreal.ScopedEditorTransaction('Operator Board camera position'):
    a.modify()
    a.set_actor_location_and_rotation(unreal.Vector({x}, {y}, {z}), unreal.Rotator(roll={r}, pitch={p}, yaw={yaw}),
                                      False, True)
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


# Multi-user copies every recorded package into a sandbox deep under Intermediate/Concert. With
# Windows long paths off, a path over 259 characters fails there with "ConcertSyncSessionDatabase:
# Unable to open a file for writing". Estimate the longest one (the take's subscene files) first.
LONG_PATH_CHECK = r"""
def _long_path_warning(panel, slate, take):
    import os
    cmd = unreal.SystemLibrary.get_command_line().upper()
    if '-CONCERTAUTOCONNECT' not in cmd and '-CONCERTSESSION' not in cmd:
        return ''
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\FileSystem')
        if winreg.QueryValueEx(key, 'LongPathsEnabled')[0]:
            return ''
    except OSError:
        pass
    project = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()).rstrip('/')
    name = os.path.basename(project)
    st = unreal.get_default_object(unreal.TakeRecorderProjectSettings).get_editor_property('settings')
    root = str(st.get_editor_property('root_take_save_dir').get_editor_property('path')).strip('/')
    take_dir = str(st.get_editor_property('take_save_dir'))
    now = __import__('datetime').date.today()
    for token, value in (('{year}', '%04d' % now.year), ('{month}', '%02d' % now.month), ('{day}', '%02d' % now.day),
                         ('{slate}', slate), ('{take}', '%02d' % take)):
        take_dir = take_dir.replace(token, value)
    if root.startswith('Game/'):
        root = root[5:]
    labels = [s.get_editor_property('target').get_actor_label() for s in panel.get_sources().get_sources_copy()
              if hasattr(s, 'get_editor_property') and s.get_editor_property('target') is not None] or ['CineCameraActor1']
    leaf = take_dir.split('/')[-1]
    sub = '%s_temp_Subscenes/%s_%s_temp.uasset' % (leaf, max(labels, key=len), leaf)
    path = '/'.join([project, 'Intermediate/Concert/MultiUser', 'X' * 32, 'X' * 32, 'Sandbox/Sandbox', name,
                     'Content', root, take_dir.rsplit('/', 1)[0], sub])
    if len(path) <= 259:
        return ''
    return ("this take's multi-user copy needs paths of about %d characters, over the Windows limit of 259, "
            'and long paths are off on this PC, so Unreal may report "Unable to open a file for writing" and '
            'leave the take out of the session. Turn on Windows long paths (an administrator setting) and restart '
            'Unreal, or use a shorter project folder or take path.' % len(path))
"""


def take_recorder_start(slate: str, take: int, description: str) -> str:
    # A panel that was only just opened has no take data until the editor has ticked
    # (UE 5.8), so report 'retry' and let the caller call again a moment later.
    return LONG_PATH_CHECK + f"""
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
    warning = ''
    try:
        warning = _long_path_warning(panel, {_lit(slate)}, {int(take)})
    except Exception:
        pass
    countdown = 0.0
    try:
        countdown = float(unreal.get_default_object(unreal.TakeRecorderUserSettings).get_editor_property('settings')
                          .get_editor_property('countdown_seconds'))
    except Exception:
        pass
    panel.start_recording()
    result = {{'ok': True, 'warning': warning, 'countdown': countdown}}
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
icvfx = []
for a in actors:  # any actor: nDisplay roots, and Blueprints that carry an ICVFX camera component
    try:
        icvfx.extend((a, c) for c in a.get_components_by_class(unreal.DisplayClusterICVFXCameraComponent))
    except Exception:
        pass
for root, comp in icvfx:
    if chosen is not None:
        break
    ext = None
    try:
        ext = comp.get_editor_property('camera_settings').get_editor_property('external_camera_actor')
        if ext is not None and not isinstance(ext, unreal.Actor) and hasattr(ext, 'get'):
            ext = ext.get()  # a soft reference
    except Exception:
        pass
    if isinstance(ext, unreal.Actor):
        chosen, why = ext, 'the ICVFX camera of ' + root.get_actor_label()
    else:
        chosen, why = root, 'it holds the ICVFX camera ' + comp.get_name()
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
    """Where the recorder is: 'counting_down' (with the seconds left), 'starting', 'recording',
    'stopped', 'cancelled' or 'idle' (no recorder)."""
    return """
lib = unreal.TakeRecorderBlueprintLibrary
rec = lib.get_active_recorder()
phase, left = 'idle', 0.0
if rec is not None:
    st = rec.get_state()
    names = {'COUNTING_DOWN': 'counting_down', 'PRE_INITIALIZATION': 'starting', 'PRE_RECORD': 'starting',
             'TICKING_AFTER_PRE': 'starting', 'STARTED': 'recording', 'STOPPED': 'stopped', 'CANCELLED': 'cancelled'}
    phase = next((v for k, v in names.items() if getattr(unreal.TakeRecorderState, k, None) == st), 'starting')
    left = float(rec.get_countdown_seconds())
result = {'phase': phase, 'countdown': left, 'recording': bool(lib.is_recording())}
"""


def take_recorder_last_sequence() -> str:
    return """
lib = unreal.TakeRecorderBlueprintLibrary
panel = lib.get_take_recorder_panel()
seq = panel.get_last_recorded_level_sequence() if panel is not None else None
result = {'sequence': seq.get_path_name() if seq is not None else '', 'recording': bool(lib.is_recording())}
"""


def take_set_description(sequence_path: str, slate: str, take: int, description: str) -> str:
    """Set a recorded take's description in its Take Recorder metadata (found like take_recorder_save)."""
    return f"""
path, slate, take = {_lit(sequence_path)}, {_lit(slate)}, {int(take)}
seq = unreal.load_asset(path) if path else None
if seq is None and slate:
    st = unreal.get_default_object(unreal.TakeRecorderProjectSettings).get_editor_property('settings')
    root = str(st.get_editor_property('root_take_save_dir').get_editor_property('path')).strip('/')
    root = '/' + root if root.startswith('Game/') else '/Game/' + root
    names = set(slate + '_' + str(take).zfill(n) for n in (1, 2, 3, 4))
    found = sorted(a for a in unreal.EditorAssetLibrary.list_assets(root, True, False) if a.split('.')[-1] in names)
    seq = unreal.load_asset(found[-1]) if found else None
if seq is None:
    raise RuntimeError('Recorded take not found in the editor')
md = seq.find_meta_data_by_class(unreal.TakeMetaData)
if md is None:
    raise RuntimeError('The take has no Take Recorder metadata')
md.set_description({_lit(description)})
seq.modify()
result = {{'ok': True, 'sequence': seq.get_path_name()}}
"""


def take_recorder_cancel() -> str:
    return "unreal.TakeRecorderBlueprintLibrary.cancel_recording()\nresult = {'ok': True}\n"


def take_recorder_save(sequence_path: str = "", slate: str = "", take: int = 0) -> str:
    """Save a recorded take: its level sequence and the subscenes recorded with it.

    Only unsaved packages are written (Take Recorder normally saves when it stops). In a multi-user
    session a save goes to the session, so every client, nDisplay nodes included, receives it; the
    take is then *persisted*: written into this machine's real project files, which sends nothing
    to the other clients and leaves the wall alone. Without a known sequence it uses the panel's last
    recording, else looks the take up by name ("<slate>_<take>") under Take Recorder's save folder
    (the panel forgets its last recording when it is closed).
    """
    return f"""
path = {_lit(sequence_path)}
slate, take = {_lit(slate)}, {int(take)}
seq = unreal.load_asset(path) if path else None
if seq is None and not path:
    panel = unreal.TakeRecorderBlueprintLibrary.get_take_recorder_panel()
    seq = panel.get_last_recorded_level_sequence() if panel is not None else None
if seq is None and slate and take:
    st = unreal.get_default_object(unreal.TakeRecorderProjectSettings).get_editor_property('settings')
    root = str(st.get_editor_property('root_take_save_dir').get_editor_property('path')).strip('/')
    root = '/' + root if root.startswith('Game/') else '/Game/' + root
    names = set(slate + '_' + str(take).zfill(n) for n in (1, 2, 3, 4))
    found = sorted(a for a in unreal.EditorAssetLibrary.list_assets(root, True, False) if a.split('.')[-1] in names)
    seq = unreal.load_asset(found[-1]) if found else None
if seq is None:
    raise RuntimeError('Recorded take not found in the editor' + (': ' + path if path else
                       (' (looked for ' + slate + '_' + str(take).zfill(2) + ')' if slate else '')))
if unreal.TakeRecorderBlueprintLibrary.is_recording():
    raise RuntimeError('Stop recording before saving the take')
package = seq.get_outermost().get_name()
folder = package.rsplit('/', 1)[0]
assets = [a for a in unreal.EditorAssetLibrary.list_assets(folder, True, False)
          if a.split('.')[0] == package or a.startswith(package + '_')]
unsaved = [a for a in assets if not unreal.EditorAssetLibrary.save_asset(a, True)]
packages = sorted({{a.split('.')[0] for a in assets}})
mu = getattr(unreal, 'MultiUserClientStatics', None) or getattr(unreal, 'MultiUserClientLibrary', None)
in_session = False
if mu is not None:
    try:
        status = str(mu.get_multi_user_connection_status_detail()).upper()
        in_session = 'CONNECTED' in status and 'DISCONNECTED' not in status
    except Exception:
        pass
persisted = []
if in_session:
    changed = set(str(p) for p in mu.gather_session_changes(True))
    persisted = [p for p in packages if p in changed]
    if persisted:
        mu.persist_specified_packages([unreal.Name(p) for p in persisted])
result = {{'sequence': seq.get_path_name(), 'packages': packages, 'unsaved': unsaved,
          'multi_user': in_session, 'persisted': persisted}}
"""


def editor_timing() -> str:
    """The engine timecode and custom time step (the editor's genlock setting), read every second."""
    return """
lib = getattr(unreal, 'TimeManagementLibrary', None) or unreal.TimeManagementBlueprintLibrary
tc = lib.get_timecode()
fr = lib.get_timecode_frame_rate()
ts = lib.get_engine_custom_time_step()
ts_fr = lib.get_custom_time_step_frame_rate() if ts is not None else None
result = {'timecode': [tc.hours, tc.minutes, tc.seconds, tc.frames], 'drop_frame': bool(tc.drop_frame_format),
          'rate': [fr.numerator, fr.denominator],
          'time_step': ts.get_class().get_name().replace('CustomTimeStep', '') if ts is not None else '',
          'time_step_fps': (ts_fr.numerator / ts_fr.denominator) if ts_fr is not None and ts_fr.denominator else 0}
"""
