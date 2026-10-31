"""Saved camera locations: recall onto the editor viewport or any actor (cine camera, nDisplay root...)."""
from __future__ import annotations

import re
from typing import Iterable, List

from operator_board.core.config import AppConfig, CameraPreset
from operator_board.remote import scripts
from operator_board.remote.rc_client import python_result_wrapper
from operator_board.remote.targets import TargetResolver


def unique_name(name: str, taken: Iterable[str]) -> str:
    """``name`` if free, else the next free ``name_2``, ``name_3``... (never overwrite a saved position)."""
    taken = set(taken)
    if name not in taken:
        return name
    base = re.sub(r"_\d+$", "", name) or name
    number = 2
    while f"{base}_{number}" in taken:
        number += 1
    return f"{base}_{number}"


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
        if target == "primary":
            # Python, not a Remote Control function call: UE 5.8 blocks calls such as
            # K2_SetActorLocationAndRotation unless the editor was started with the unlock switch.
            self.targets.primary().run_python(python_result_wrapper(
                scripts.set_actor_transform(preset.target, preset.location, preset.rotation)))
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
        """Save the viewport's or an actor's position as a new preset (``name_2``... if the name is taken)."""
        client = self.targets.primary()
        script = scripts.get_viewport_camera() if target_actor == "viewport" else scripts.get_actor_transform(target_actor)
        data = client.run_python(python_result_wrapper(script))
        if not isinstance(data, dict) or "location" not in data:
            raise RuntimeError(f"Could not read the position of {target_actor}: {data}")
        preset = CameraPreset(name=unique_name(name, (c.name for c in self.config.cameras)), target=target_actor,
                              location=list(data["location"]), rotation=list(data["rotation"]))
        self.config.cameras.append(preset)
        return preset

    def list_camera_actors(self) -> List[dict]:
        """Camera actors (cine cameras included) and nDisplay setups (their Blueprint classes, e.g. nDisplayConfig_C)."""
        result = self.targets.primary().run_python(python_result_wrapper(scripts.list_camera_targets()))
        return result.get("actors", []) if isinstance(result, dict) else []
