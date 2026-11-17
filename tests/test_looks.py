"""Saved colour grading looks, target keys and the saved bypass state."""
import json

import pytest

from operator_board.core.grading import Grade
from operator_board.core.looks import BypassState, Look, LookStore, target_key


class Bus:
    def __init__(self):
        self.events = []

    def publish(self, topic, **payload):
        self.events.append((topic, payload))


def test_target_key_ignores_viewport_order():
    assert target_key("ndisplay_viewport", "Root", ["B", "A"]) == target_key("ndisplay_viewport", "Root", ["A", "B"])
    assert target_key("ndisplay_cluster", "Root") == "ndisplay_cluster|Root"
    assert target_key("ndisplay_viewport", "Root", ["VP_1", "VP_0"]) == "ndisplay_viewport|Root|VP_0,VP_1"


def test_look_save_get_round_trip(tmp_path):
    bus = Bus()
    store = LookStore(str(tmp_path / "looks.json"), bus)
    warm = Grade.neutral().set("temp", 5600)
    store.save(Look("Day ext warm", {"ndisplay_cluster|Root": warm}))
    again = LookStore(str(tmp_path / "looks.json"))
    assert again.names() == ["Day ext warm"]
    assert again.get("day ext warm").grades["ndisplay_cluster|Root"] == warm
    assert ("grading.looks_changed", {}) in bus.events


def test_save_same_name_replaces(tmp_path):
    store = LookStore(str(tmp_path / "looks.json"))
    store.save(Look("Night", {"a|A": Grade.neutral()}))
    store.save(Look("night", {"b|B": Grade.neutral()}))
    assert store.names() == ["night"]
    assert list(store.get("Night").grades) == ["b|B"]


def test_delete(tmp_path):
    store = LookStore(str(tmp_path / "looks.json"))
    store.save(Look("Night", {}))
    store.delete("Night")
    assert store.names() == [] and LookStore(str(tmp_path / "looks.json")).names() == []


def test_file_has_format_id(tmp_path):
    store = LookStore(str(tmp_path / "looks.json"))
    store.save(Look("Night", {}))
    data = json.loads((tmp_path / "looks.json").read_text(encoding="utf-8"))
    assert data["format"] == "operator-board-looks"


def test_import_rejects_other_format(tmp_path):
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"format": "operator-board-custom-pages", "pages": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        LookStore(str(tmp_path / "looks.json")).import_file(str(other))


def test_export_import_round_trip(tmp_path):
    a = LookStore(str(tmp_path / "a.json"))
    a.save(Look("Night", {"a|A": Grade.neutral().set("tint", 0.1)}))
    a.export(str(tmp_path / "out.json"))
    b = LookStore(str(tmp_path / "b.json"))
    assert b.import_file(str(tmp_path / "out.json")) == ["Night"]
    assert b.get("Night").grades["a|A"].tint == 0.1


def test_corrupt_file_reports_and_keeps_empty(tmp_path):
    (tmp_path / "looks.json").write_text("{not json", encoding="utf-8")
    store = LookStore(str(tmp_path / "looks.json"))
    assert store.names() == [] and store.load_error


def test_bypass_state_survives_reload(tmp_path):
    path = str(tmp_path / "grading_bypass.json")
    state = BypassState(path)
    state.set("ppv|Probe PPV", True)
    state.set("ndisplay_cluster|Root", True)
    state.set("ppv|Probe PPV", False)
    assert BypassState(path).bypassed() == ["ndisplay_cluster|Root"]
    BypassState(path).clear()
    assert BypassState(path).bypassed() == []
