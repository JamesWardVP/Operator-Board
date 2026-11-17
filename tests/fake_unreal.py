"""A small stand-in for Unreal's ``unreal`` Python module, shaped like the colour grading data
probed on UE 5.8 (see CLAUDE.md, "Colour grading in Unreal"), so the grading scripts can be run.

Structs are copied when read (``get_editor_property``), which is stricter than Unreal: a script
only passes if it writes every changed struct back to its owner.
"""
import contextlib
import copy
import io
import json
import sys
import types


class _Props:
    _fields = {}

    def __init__(self, **kw):
        for k, v in self._fields.items():
            setattr(self, k, copy.deepcopy(v))
        for k, v in kw.items():
            self.set_editor_property(k, v)

    def get_editor_property(self, name):
        if name not in self._fields and name not in getattr(self, "_extra", ()):
            raise Exception(f"{type(self).__name__}: Failed to find property '{name}'")
        value = getattr(self, name)
        return copy.deepcopy(value) if isinstance(value, (_Props, list)) else value

    def set_editor_property(self, name, value):
        if name not in self._fields and name not in getattr(self, "_extra", ()):
            raise Exception(f"{type(self).__name__}: Failed to find property '{name}'")
        setattr(self, name, copy.deepcopy(value))

    def modify(self):
        self.modified = True


class Vector4(_Props):
    def __init__(self, x=0.0, y=0.0, z=0.0, w=0.0):
        self.x, self.y, self.z, self.w = float(x), float(y), float(z), float(w)

    def __eq__(self, other):
        return isinstance(other, Vector4) and (self.x, self.y, self.z, self.w) == (other.x, other.y, other.z, other.w)


def _one():
    return Vector4(1, 1, 1, 1)


class _Range(_Props):
    _fields = {"saturation": _one(), "contrast": _one(), "gamma": _one(), "gain": _one(), "offset": Vector4(),
               "override_saturation": False, "override_contrast": False, "override_gamma": False,
               "override_gain": False, "override_offset": False}


class _WhiteBalance(_Props):
    _fields = {"white_temp": 6500.0, "white_tint": 0.0, "override_white_temp": False, "override_white_tint": False}


class DisplayClusterColorGradingSettings(_Props):
    _fields = {"white_balance": _WhiteBalance(), "global_": _Range(), "shadows": _Range(), "midtones": _Range(),
               "highlights": _Range(), "color_correction_shadows_max": 0.0, "color_correction_highlights_min": 0.0,
               "color_correction_highlights_max": 1.0, "override_color_correction_shadows_max": False,
               "override_color_correction_highlights_min": False, "override_color_correction_highlights_max": False}


class EntireCluster(_Props):
    _fields = {"enable_entire_cluster_color_grading": True, "color_grading_settings": DisplayClusterColorGradingSettings()}


class PerViewport(_Props):
    _fields = {"name": "", "is_enabled": True, "is_entire_cluster_enabled": True,
               "color_grading_settings": DisplayClusterColorGradingSettings(), "apply_post_process_to_objects": []}


class AllNodes(_Props):
    _fields = {"enable_inner_frustum_all_nodes_color_grading": True, "enable_entire_cluster_color_grading": True,
               "color_grading_settings": DisplayClusterColorGradingSettings()}


class PerNode(_Props):
    _fields = {"name": "", "is_enabled": True, "entire_cluster_color_grading": True, "all_nodes_color_grading": True,
               "color_grading_settings": DisplayClusterColorGradingSettings(), "apply_post_process_to_objects": []}


class StageSettings(_Props):
    _fields = {"enable_color_grading": True, "entire_cluster_color_grading": EntireCluster(),
               "per_viewport_color_grading": []}


class CameraSettings(_Props):
    _fields = {"enable_inner_frustum_color_grading": True, "all_nodes_color_grading": AllNodes(),
               "per_node_color_grading": []}


class ConfigData(_Props):
    _fields = {"stage_settings": StageSettings()}


class PostProcessSettings(_Props):
    _fields = {"white_temp": 6500.0, "white_tint": 0.0, "override_white_temp": False, "override_white_tint": False,
               "color_correction_shadows_max": 0.0, "color_correction_highlights_min": 0.0,
               "color_correction_highlights_max": 1.0, "override_color_correction_shadows_max": False,
               "override_color_correction_highlights_min": False, "override_color_correction_highlights_max": False}
    for _p in ("saturation", "contrast", "gamma", "gain", "offset"):
        for _r in ("", "_shadows", "_midtones", "_highlights"):
            _fields[f"color_{_p}{_r}"] = Vector4() if _p == "offset" else _one()
            _fields[f"override_color_{_p}{_r}"] = False


class _CCRRange(_Props):
    _fields = {"saturation": _one(), "contrast": _one(), "gamma": _one(), "gain": _one(), "offset": Vector4()}


class ColorGradingSettings(_Props):
    _fields = {"global_": _CCRRange(), "shadows": _CCRRange(), "midtones": _CCRRange(), "highlights": _CCRRange(),
               "shadows_max": 0.09, "highlights_min": 0.5, "highlights_max": 1.0}


class Object(_Props):
    def __init__(self, name="", **kw):
        self._name = name
        super().__init__(**kw)

    def get_name(self):
        return self._name

    def get_path_name(self):
        return "/Game/Level.Level:PersistentLevel." + self._name

    def get_class(self):
        return types.SimpleNamespace(get_name=lambda: type(self).__name__)


class ActorComponent(Object):
    pass


class DisplayClusterICVFXCameraComponent(ActorComponent):
    _fields = {"camera_settings": CameraSettings()}


class Actor(Object):
    def __init__(self, name="", label="", **kw):
        super().__init__(name, **kw)
        self._label = label or name
        self.components = []

    def get_actor_label(self):
        return self._label

    def get_components_by_class(self, cls):
        return [c for c in self.components if isinstance(c, cls)]


class DisplayClusterRootActor(Actor):
    _fields = {"current_config_data": ConfigData()}

    def get_editor_property(self, name):  # the config data is a UObject: returned by reference
        if name == "current_config_data":
            return self.current_config_data
        return super().get_editor_property(name)


class PostProcessVolume(Actor):
    _fields = {"settings": PostProcessSettings(), "enabled": True, "unbound": True}


class ColorCorrectRegion(Actor):
    _fields = {"type": "SPHERE", "enabled": True, "temperature": 6500.0, "tint": 0.0,
               "color_grading_settings": ColorGradingSettings()}


class ColorCorrectionRegion(ColorCorrectRegion):
    pass


class ColorCorrectionWindow(ColorCorrectRegion):
    _fields = dict(ColorCorrectRegion._fields, window_type="SQUARE")


class World:
    def __init__(self, actors):
        self.actors = actors


class _State:
    world = None
    editor = True
    transactions = []


class ScopedEditorTransaction:
    def __init__(self, description):
        self.description = description

    def __enter__(self):
        _State.transactions.append(self.description)
        return self

    def __exit__(self, *exc):
        return False


class EditorActorSubsystem:
    def get_all_level_actors(self):
        return list(_State.world.actors)


class UnrealEditorSubsystem:
    def get_editor_world(self):
        return _State.world


def get_editor_subsystem(cls):
    if not _State.editor:
        return None
    return cls()


class GameplayStatics:
    @staticmethod
    def get_all_actors_of_class(world, cls):
        return [a for a in world.actors if isinstance(a, cls)]


def ObjectIterator(cls):
    return iter([_State.world] if cls is World else [])


class EditorLevelLibrary:
    @staticmethod
    def get_editor_world():
        return _State.world if _State.editor else None


def build_module(with_ccr=True):
    """A fresh fake ``unreal`` module (classes above) and its state holder."""
    mod = types.ModuleType("unreal")
    for name, value in dict(globals()).items():
        if isinstance(value, type) or callable(value):
            setattr(mod, name, value)
    if not with_ccr:
        for name in ("ColorCorrectRegion", "ColorCorrectionRegion", "ColorCorrectionWindow"):
            delattr(mod, name)
    mod._State = _State
    return mod


def stage(with_ccr=True, editor=True):
    """A level with an nDisplay root (one per-viewport entry, an ICVFX camera with one per-node entry),
    a global and a local Post Process Volume and a sphere region."""
    root = DisplayClusterRootActor("nDisplayConfig_C_1", "nDisplay Root")
    cfg = root.current_config_data
    cfg.stage_settings.per_viewport_color_grading = [PerViewport(apply_post_process_to_objects=["VP_1", "VP_0"])]
    cam = DisplayClusterICVFXCameraComponent("ICVFXCamera")
    cam.camera_settings.per_node_color_grading = [PerNode(apply_post_process_to_objects=["Node_0"])]
    root.components.append(cam)
    actors = [PostProcessVolume("PPV_1", "Stage PPV"), PostProcessVolume("PPV_2", "Local PPV", unbound=False), root]
    if with_ccr:
        actors.append(ColorCorrectionRegion("CCR_1", "Face Sphere"))
    _State.world = World(actors)
    _State.editor = editor
    _State.transactions = []
    return types.SimpleNamespace(root=root, cam=cam, actors=actors)


def run(script, module):
    """Run a script made by ``python_result_wrapper`` against ``module``; returns its result."""
    old = sys.modules.get("unreal")
    sys.modules["unreal"] = module
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            exec(compile(script, "<unreal>", "exec"), {"__name__": "__unreal__"})
    finally:
        if old is None:
            sys.modules.pop("unreal", None)
        else:
            sys.modules["unreal"] = old
    line = [ln for ln in out.getvalue().splitlines() if ln.startswith("OPB_RESULT:")][-1]
    return json.loads(line[len("OPB_RESULT:"):])
