"""Colour grades: the controls the Colour Grading page shows and the values Unreal stores.

Every Unreal grading target (nDisplay cluster / viewport, ICVFX camera, Post Process Volume, Colour
Correct Region) uses the same shape: white balance (temperature, tint) and four tonal ranges
(global, shadows, midtones, highlights), each with saturation, contrast, gamma, gain and offset as
an (r, g, b, y) vector, where y is the brightness part. Each value has an override tick; a value
whose override is off falls back to what Unreal would do untouched.

A ``Grade`` keys its vectors ``"<range>.<part>"`` and white balance ``"temp"`` / ``"tint"``.
Grades are treated as values: ``set``, ``reset`` and ``nudge`` return a new one.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

Vec4 = Tuple[float, float, float, float]

RANGES = ("global", "shadows", "midtones", "highlights")
PARTS = ("saturation", "contrast", "gamma", "gain", "offset")
NEUTRAL: Dict[str, Vec4] = {"saturation": (1, 1, 1, 1), "contrast": (1, 1, 1, 1), "gamma": (1, 1, 1, 1),
                            "gain": (1, 1, 1, 1), "offset": (0, 0, 0, 0)}
NEUTRAL_TEMP = 6500.0
NEUTRAL_TINT = 0.0
LIMIT_KEYS = ("shadows_max", "highlights_min", "highlights_max")

# Unreal's own UI ranges (DisplayClusterConfigurationTypes_Postprocess.h): 0..2, offset -1..1.
PART_RANGE = {"saturation": (0.0, 2.0), "contrast": (0.0, 2.0), "gamma": (0.0, 2.0), "gain": (0.0, 2.0),
              "offset": (-1.0, 1.0)}
TEMP_RANGE = (1500.0, 15000.0)
TINT_RANGE = (-1.0, 1.0)


@dataclass(frozen=True)
class Control:
    id: str
    title: str        # colourist name, shown large
    subtitle: str     # the Unreal setting, shown small in italics
    kind: str         # "wheel" or "slider"
    range: str        # "global" / "shadows" / ... ; "" for white balance
    part: str         # "gain" / ... ; "temp" / "tint" for white balance
    lo: float
    hi: float
    nudge: float      # the default amount a Nudge button moves it

    @property
    def key(self) -> str:
        """The Grade key this control edits."""
        return self.part if not self.range else f"{self.range}.{self.part}"


def _wheel(cid: str, title: str, rng: str, part: str, nudge_by: float) -> Control:
    lo, hi = PART_RANGE[part]
    return Control(cid, title, f"{rng.capitalize()} · {part.capitalize()}", "wheel", rng, part, lo, hi, nudge_by)


PRIMARIES: List[Control] = [
    _wheel("lift", "Lift", "shadows", "offset", 0.02),
    _wheel("gamma", "Gamma", "global", "gamma", 0.02),
    _wheel("gain", "Gain", "global", "gain", 0.02),
    _wheel("offset", "Offset", "global", "offset", 0.02),
    _wheel("saturation", "Saturation", "global", "saturation", 0.05),
    _wheel("contrast", "Contrast", "global", "contrast", 0.05),
    Control("temperature", "Temperature", "White Balance · Temp", "slider", "", "temp", TEMP_RANGE[0], TEMP_RANGE[1],
            100.0),
    Control("tint", "Tint", "White Balance · Tint", "slider", "", "tint", TINT_RANGE[0], TINT_RANGE[1], 0.05),
]

ADVANCED: List[Control] = [
    _wheel(f"{rng}.{part}", part.capitalize(), rng, part, 0.05 if part in ("saturation", "contrast") else 0.02)
    for rng in RANGES[1:] for part in PARTS
]

CONTROLS: Dict[str, Control] = {c.id: c for c in PRIMARIES + ADVANCED}
VECTOR_KEYS = tuple(f"{r}.{p}" for r in RANGES for p in PARTS)
SCALAR_KEYS = ("temp", "tint")
ALL_KEYS = VECTOR_KEYS + SCALAR_KEYS


def _neutral_of(key: str) -> Any:
    if key == "temp":
        return NEUTRAL_TEMP
    if key == "tint":
        return NEUTRAL_TINT
    return NEUTRAL[key.split(".", 1)[1]]


def _check(key: str) -> None:
    if key not in ALL_KEYS:
        raise KeyError(f"Unknown grade setting '{key}'")


@dataclass
class Grade:
    values: Dict[str, Vec4] = field(default_factory=dict)
    temp: float = NEUTRAL_TEMP
    tint: float = NEUTRAL_TINT
    overrides: Set[str] = field(default_factory=set)
    limits: Dict[str, float] = field(default_factory=dict)

    @classmethod
    def neutral(cls) -> "Grade":
        return cls(values={k: _neutral_of(k) for k in VECTOR_KEYS})

    def get(self, key: str) -> Any:
        _check(key)
        if key == "temp":
            return self.temp
        if key == "tint":
            return self.tint
        return self.values.get(key, _neutral_of(key))

    def set(self, key: str, value: Any) -> "Grade":
        """A copy with ``key`` set and its override ticked."""
        _check(key)
        g = copy.deepcopy(self)
        if key == "temp":
            g.temp = float(value)
        elif key == "tint":
            g.tint = float(value)
        else:
            g.values[key] = tuple(float(v) for v in value)  # type: ignore[assignment]
        g.overrides.add(key)
        return g

    def reset(self, key: Optional[str] = None) -> "Grade":
        """A copy with ``key`` (or everything) neutral and its override unticked."""
        if key is None:
            return Grade.neutral()
        g = self.set(key, _neutral_of(key))
        g.overrides.discard(key)
        return g

    def to_dict(self) -> Dict[str, Any]:
        return {"values": {k: list(v) for k, v in self.values.items()}, "temp": self.temp, "tint": self.tint,
                "overrides": sorted(self.overrides), "limits": dict(self.limits)}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Grade":
        g = cls.neutral()
        for k, v in (d.get("values") or {}).items():
            if k in VECTOR_KEYS:
                g.values[k] = tuple(float(x) for x in v)  # type: ignore[assignment]
        g.temp = float(d.get("temp", NEUTRAL_TEMP))
        g.tint = float(d.get("tint", NEUTRAL_TINT))
        g.overrides = {k for k in d.get("overrides") or () if k in ALL_KEYS}
        g.limits = {k: float(v) for k, v in (d.get("limits") or {}).items() if k in LIMIT_KEYS}
        return g


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def tint_hex(rgb: Tuple[float, float, float]) -> str:
    """The colour of a wheel's tint as #RRGGBB, brightness left out (the largest channel is FF)."""
    r, g, b = (max(0.0, float(c)) for c in rgb[:3])
    top = max(r, g, b)
    if top <= 0:
        return "#FFFFFF"
    return "#" + "".join(f"{int(round(c / top * 255)):02X}" for c in (r, g, b))


_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def hex_to_tint(text: str, y: float) -> Vec4:
    """The (r, g, b, y) for a typed #RRGGBB tint, keeping the brightness ``y``. The colour is scaled
    so its largest channel is 1 (the same convention ``tint_hex`` reads back)."""
    m = _HEX.match((text or "").strip())
    if not m:
        raise ValueError(f"'{text}' is not a colour like #FFE8DD")
    h = m.group(1)
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    top = max(r, g, b)
    if top <= 0:
        raise ValueError("Black has no tint colour")
    return (r / top, g / top, b / top, float(y))


def nudge(grade: Grade, control_id: str, amount: float, channel: str = "y") -> Grade:
    """``grade`` with one control moved by ``amount`` (``channel`` r/g/b/y for wheels), clamped to
    the control's range."""
    c = CONTROLS[control_id]
    if c.kind == "slider":
        return grade.set(c.key, _clamp(grade.get(c.key) + amount, c.lo, c.hi))
    idx = "rgby".index(channel)
    vec = list(grade.get(c.key))
    vec[idx] = _clamp(vec[idx] + amount, c.lo, c.hi)
    return grade.set(c.key, tuple(vec))
