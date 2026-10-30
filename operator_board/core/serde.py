"""Minimal dataclass <-> JSON-compatible dict conversion.

Unknown keys are ignored and missing keys fall back to field defaults, so config
files written by older or newer versions of Operator Board still load.
"""
from __future__ import annotations

import dataclasses
import enum
import typing
from typing import Any, Dict, Type, TypeVar

T = TypeVar("T")

_HINT_CACHE: Dict[type, Dict[str, Any]] = {}


def _hints(cls: type) -> Dict[str, Any]:
    if cls not in _HINT_CACHE:
        _HINT_CACHE[cls] = typing.get_type_hints(cls)
    return _HINT_CACHE[cls]


def to_dict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [to_dict(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): to_dict(v) for k, v in obj.items()}
    return obj


def _convert(tp: Any, value: Any) -> Any:
    if value is None:
        return None
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin is typing.Union:
        non_none = [a for a in args if a is not type(None)]
        return _convert(non_none[0], value) if len(non_none) == 1 else value
    if origin in (list, typing.List):
        return [_convert(args[0], v) for v in value] if args else list(value)
    if origin in (dict, typing.Dict):
        return {k: _convert(args[1], v) for k, v in value.items()} if args else dict(value)
    if isinstance(tp, type):
        if dataclasses.is_dataclass(tp):
            return from_dict(tp, value)
        if issubclass(tp, enum.Enum):
            try:
                return tp(value)
            except ValueError:
                return list(tp)[0]
        if tp is float and isinstance(value, (int, float)):
            return float(value)
    return value


def from_dict(cls: Type[T], data: Dict[str, Any]) -> T:
    if not isinstance(data, dict):
        raise TypeError(f"Expected mapping for {cls.__name__}, got {type(data).__name__}")
    hints = _hints(cls)
    kwargs = {}
    for f in dataclasses.fields(cls):
        if f.name in data:
            kwargs[f.name] = _convert(hints.get(f.name, Any), data[f.name])
    return cls(**kwargs)
