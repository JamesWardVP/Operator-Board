"""Colour grading looks: named grades saved with the project, and the saved bypass state.

A look holds the grade of every target it was saved with, keyed by ``target_key``: the kind of
target, the actor's label and, for nDisplay per-viewport / ICVFX per-node entries, the viewport or
node names the entry applies to (never its position in the list, which differs between levels and
is all a ``-game`` node knows besides those names). Looks are not tied to a level.

``looks.json`` lives in the project folder, so it travels in project exports and templates.
``grading_bypass.json`` remembers which targets Operator Board has bypassed, so they can be
restored after Operator Board closes while something is bypassed.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from operator_board.core.config import atomic_write
from operator_board.core.grading import Grade

FILE_NAME = "looks.json"
FILE_FORMAT = "operator-board-looks"
BYPASS_FILE = "grading_bypass.json"


def target_key(kind: str, actor: str, applies_to: Sequence[str] = ()) -> str:
    parts = [kind, actor]
    if applies_to:
        parts.append(",".join(sorted(str(a) for a in applies_to)))
    return "|".join(parts)


@dataclass
class Look:
    name: str
    grades: Dict[str, Grade] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "grades": {k: g.to_dict() for k, g in self.grades.items()}}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Look":
        grades = d.get("grades") if isinstance(d.get("grades"), dict) else {}
        return cls(str(d.get("name", "")).strip() or "Look", {str(k): Grade.from_dict(v) for k, v in grades.items()})


def _read(path: str) -> List[Look]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or data.get("format") != FILE_FORMAT:
        raise ValueError("This is not an Operator Board looks file")
    looks = data.get("looks") or []
    if not isinstance(looks, list):
        raise ValueError("The looks file is damaged (its looks are not a list)")
    return [Look.from_dict(d) for d in looks if isinstance(d, dict)]


def _write(path: str, looks: List[Look]) -> None:
    atomic_write(path, json.dumps({"format": FILE_FORMAT, "looks": [lk.to_dict() for lk in looks]}, indent=2))


class LookStore:
    """The project's looks. ``path`` None keeps them in memory only."""

    def __init__(self, path: Optional[str] = None, bus=None) -> None:
        self.path = path
        self.bus = bus
        self.looks: List[Look] = []
        self.load_error = ""
        self.load()

    def load(self) -> None:
        self.looks = []
        self.load_error = ""
        if not self.path or not os.path.exists(self.path):
            return
        try:
            self.looks = _read(self.path)
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            self.load_error = f"Could not read the looks ({self.path}): {exc}"

    def _changed(self) -> None:
        if self.path:
            if self.load_error and os.path.exists(self.path):
                # Keep the unreadable file rather than writing over the looks it may still hold.
                os.replace(self.path, self.path + ".bad")
                self.load_error = ""
            _write(self.path, self.looks)
        if self.bus:
            self.bus.publish("grading.looks_changed")

    def names(self) -> List[str]:
        return [lk.name for lk in self.looks]

    def get(self, name: str) -> Optional[Look]:
        wanted = (name or "").strip().lower()
        return next((lk for lk in self.looks if lk.name.lower() == wanted), None)

    def save(self, look: Look) -> None:
        """Add ``look``, replacing one of the same name (any case)."""
        look.name = look.name.strip() or "Look"
        old = self.get(look.name)
        if old is not None:
            self.looks[self.looks.index(old)] = look
        else:
            self.looks.append(look)
        self._changed()

    def delete(self, name: str) -> None:
        old = self.get(name)
        if old is not None:
            self.looks.remove(old)
            self._changed()

    def export(self, path: str) -> None:
        _write(path, self.looks)

    def import_file(self, path: str) -> List[str]:
        """Add the looks in ``path`` (replacing any of the same name); returns their names."""
        looks = _read(path)
        for lk in looks:
            old = self.get(lk.name)
            if old is not None:
                self.looks[self.looks.index(old)] = lk
            else:
                self.looks.append(lk)
        self._changed()
        return [lk.name for lk in looks]


class BypassState:
    """Which targets are bypassed by Operator Board, kept on disk as they change."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path
        self._keys: List[str] = []
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                keys = data.get("bypassed") if isinstance(data, dict) else None
                self._keys = [k for k in keys if isinstance(k, str)] if isinstance(keys, list) else []
            except (OSError, ValueError, RecursionError):
                self._keys = []

    def _save(self) -> None:
        if self.path:
            atomic_write(self.path, json.dumps({"bypassed": self._keys}, indent=2))

    def set(self, key: str, bypassed: bool) -> None:
        if bypassed and key not in self._keys:
            self._keys.append(key)
        elif not bypassed and key in self._keys:
            self._keys.remove(key)
        else:
            return
        self._save()

    def bypassed(self) -> List[str]:
        return list(self._keys)

    def clear(self) -> None:
        self._keys = []
        self._save()
