"""Instance-editable objects: Remote Control Presets, actor browsing and favourite properties."""
from __future__ import annotations

from typing import Any, Dict, List

from operator_board.core.config import AppConfig
from operator_board.remote import scripts
from operator_board.remote.rc_client import python_result_wrapper
from operator_board.remote.targets import TargetResolver


def flatten_preset(preset: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Turn a /remote/preset/<name> response into a flat list of exposed properties/functions."""
    rows: List[Dict[str, Any]] = []
    for group in preset.get("Groups", []) or []:
        gname = group.get("Name", "")
        for prop in group.get("ExposedProperties", []) or []:
            underlying = prop.get("UnderlyingProperty", {}) or {}
            rows.append({"kind": "property", "group": gname, "label": prop.get("DisplayName", ""),
                         "type": underlying.get("Type", ""), "id": prop.get("ID", ""),
                         "owners": [o.get("Path", "") for o in prop.get("OwnerObjects", []) or []]})
        for fn in group.get("ExposedFunctions", []) or []:
            rows.append({"kind": "function", "group": gname, "label": fn.get("DisplayName", ""),
                         "type": "function", "id": fn.get("ID", ""), "owners": []})
    return rows


def extract_value(response: Any) -> Any:
    """Pull the value out of a property GET response (shape differs between endpoints)."""
    if isinstance(response, dict):
        if "PropertyValues" in response and response["PropertyValues"]:
            return response["PropertyValues"][0].get("PropertyValue")
        if len(response) == 1:
            return next(iter(response.values()))
    return response


class ObjectService:
    def __init__(self, config: AppConfig, targets: TargetResolver) -> None:
        self.config = config
        self.targets = targets

    def presets(self, target: str = "primary") -> List[str]:
        client = self.targets.resolve(target)[0]
        return [p.get("Name", "") for p in client.presets()]

    def preset_rows(self, preset: str, target: str = "primary") -> List[Dict[str, Any]]:
        return flatten_preset(self.targets.resolve(target)[0].preset(preset))

    def get_preset_value(self, preset: str, label: str, target: str = "primary") -> Any:
        return extract_value(self.targets.resolve(target)[0].get_preset_property(preset, label))

    def set_preset_value(self, preset: str, label: str, value: Any, target: str = "all") -> List[str]:
        errors = []
        for client in self.targets.resolve(target):
            try:
                client.set_preset_property(preset, label, value)
            except Exception as exc:
                errors.append(f"{client.host}: {exc}")
        return errors

    def call_preset_function(self, preset: str, label: str, target: str = "all") -> List[str]:
        errors = []
        for client in self.targets.resolve(target):
            try:
                client.call_preset_function(preset, label)
            except Exception as exc:
                errors.append(f"{client.host}: {exc}")
        return errors

    def actors(self, class_filter: str = "") -> List[Dict[str, str]]:
        result = self.targets.primary().run_python(python_result_wrapper(scripts.list_actors(class_filter)))
        return list(result.get("actors", [])) if isinstance(result, dict) else []

    def quick_controls(self, actor_path: str) -> Dict[str, Any]:
        """Light / cine camera controls with current values (empty ``kind`` for other actors)."""
        result = self.targets.primary().run_python(python_result_wrapper(scripts.actor_quick_controls(actor_path)))
        return result if isinstance(result, dict) else {"kind": "", "controls": []}

    def set_quick_control(self, actor_path: str, control: str, value: Any) -> None:
        self.targets.primary().run_python(python_result_wrapper(
            scripts.set_actor_quick_control(actor_path, control, value)))

    def describe(self, object_path: str) -> Dict[str, Any]:
        return self.targets.primary().describe(object_path) or {}

    def get_property(self, object_path: str, prop: str) -> Any:
        return extract_value(self.targets.primary().get_property(object_path, prop))

    def set_property(self, object_path: str, prop: str, value: Any, target: str = "all") -> List[str]:
        errors = []
        for client in self.targets.resolve(target):
            try:
                client.set_property(object_path, prop, value)
            except Exception as exc:
                errors.append(f"{client.host}: {exc}")
        return errors
