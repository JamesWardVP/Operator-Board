"""What the wall status bar shows: processors grouped by the state they are in."""
from __future__ import annotations

from typing import List, Tuple

from operator_board.led.processor import MODES

# Button colours for each mode (lit when the processor or group is in it).
MODE_COLORS = {"frozen": "#1fa2ff", "blackout": "#e03131", "pattern": "#fab005", "locate": "#7048e8",
               "normal": "#2f9e44"}
MODE_LABELS = {"frozen": "Freeze", "blackout": "Blackout", "pattern": "Test pattern", "locate": "Locate",
               "normal": "Normal"}


def status_groups(led) -> List[Tuple[str, str, str, List[str]]]:
    """(state, pattern id, pattern label, processor names) for each state in use, in a fixed order.
    A processor that cannot be reached is "unavailable"."""
    labels = dict(led.all_test_patterns())
    by_key = {}
    for p in led.active():
        if p.state.online is False:
            key = ("unavailable", "")
        else:
            key = (p.mode, p.state.test_pattern or "" if p.mode == "pattern" else "")
        by_key.setdefault(key, []).append(p.name)
    order = list(MODES) + ["unavailable"]
    keys = sorted(by_key, key=lambda k: (order.index(k[0]) if k[0] in order else 99, k[1]))
    return [(state, pattern, labels.get(pattern, pattern), by_key[(state, pattern)]) for state, pattern in keys]
