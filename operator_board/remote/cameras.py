"""Saved camera locations: recall onto the editor viewport or any actor (cine camera, nDisplay root...)."""
from __future__ import annotations

from typing import List

from operator_board.core.config import AppConfig, CameraPreset
from operator_board.remote import scripts
from operator_board.remote.rc_client import python_result_wrapper
from operator_board.remote.targets import TargetResolver


def _vec(v):
    return {"X": float(v[0]), "Y": float(v[1]), "Z": float(v[2])}


def _rot(r):
    return {"Pitch": float(r[0]), "Yaw": float(r[1]), "Roll": float(r[2])}


class CameraService:
    def __init__(self, config: AppConfig, targets: TargetResolver) -> None:
        self.config = config
        self.targets = targets

    @property
    def presets(self) -> List[CameraPreset]:
        return self.config.cameras

    def get(self, name: str) -> CameraPreset:
        preset = next((c for c in self.config.cameras if c.name == name), None)
        if preset is None:
            raise KeyError(f"No camera preset named '{name}'")
        return preset

    def recall(self, name: str, target: str = "primary") -> List[str]:
        preset = self.get(name)
        if preset.target == "viewport":
            self.targets.primary().run_python(
                python_result_wrapper(scripts.set_viewport_camera(preset.location, preset.rotation)))
            return []
        errors = []
        for client in self.targets.resolve(target):
            try:
                client.call(preset.target, "K2_SetActorLocationAndRotation", {
                    "NewLocation": _vec(preset.location), "NewRotation": _rot(preset.rotation),
                    "bSweep": False, "bTeleport": True}, transaction=True)
            except Exception as exc:
                errors.append(f"{client.host}: {exc}")
        return errors

    def capture(self, name: str, target_actor: str = "viewport") -> CameraPreset:
        client = self.targets.primary()
        if target_actor == "viewport":
            data = client.run_python(python_result_wrapper(scripts.get_viewport_camera()))
            if not isinstance(data, dict) or "location" not in data:
                raise RuntimeError(f"Could not read viewport camera: {data}")
            loc, rot = data["location"], data["rotation"]
        else:
            l = client.call(target_actor, "K2_GetActorLocation").get("ReturnValue", {})
            r = client.call(target_actor, "K2_GetActorRotation").get("ReturnValue", {})
            loc = [l.get("X", 0), l.get("Y", 0), l.get("Z", 0)]
            rot = [r.get("Pitch", 0), r.get("Yaw", 0), r.get("Roll", 0)]
        existing = next((c for c in self.config.cameras if c.name == name), None)
        preset = existing or CameraPreset(name=name)
        preset.target, preset.location, preset.rotation = target_actor, list(loc), list(rot)
        if existing is None:
            self.config.cameras.append(preset)
        return preset

    def list_camera_actors(self) -> List[dict]:
        result = self.targets.primary().run_python(python_result_wrapper(scripts.list_actors("Camera")))
        actors = result.get("actors", []) if isinstance(result, dict) else []
        roots = self.targets.primary().run_python(python_result_wrapper(scripts.list_actors("DisplayClusterRootActor")))
        actors += roots.get("actors", []) if isinstance(roots, dict) else []
        return actors
