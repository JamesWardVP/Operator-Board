"""Colour grade model: controls, neutral values, overrides, hex tint and nudges."""
import pytest

from operator_board.core.grading import (ADVANCED, CONTROLS, PRIMARIES, Grade, hex_to_tint, nudge,
                                         tint_hex)


def test_neutral_grade_has_no_overrides():
    g = Grade.neutral()
    assert g.get("global.gain") == (1, 1, 1, 1) and g.get("global.offset") == (0, 0, 0, 0)
    assert g.temp == 6500.0 and g.tint == 0.0
    assert g.overrides == set()


def test_set_ticks_override_and_reset_unticks():
    g = Grade.neutral().set("global.gain", (1.1, 1.0, 0.9, 1.0))
    assert "global.gain" in g.overrides
    assert g.get("global.gain") == (1.1, 1.0, 0.9, 1.0)
    g = g.reset("global.gain")
    assert g.get("global.gain") == (1, 1, 1, 1) and "global.gain" not in g.overrides


def test_set_returns_new_grade():
    g = Grade.neutral()
    g.set("global.gain", (2, 2, 2, 2))
    assert g.get("global.gain") == (1, 1, 1, 1)


def test_temp_and_tint_keys():
    g = Grade.neutral().set("temp", 5600).set("tint", -0.2)
    assert (g.temp, g.tint) == (5600.0, -0.2) and {"temp", "tint"} <= g.overrides
    g = g.reset("temp")
    assert g.temp == 6500.0 and "temp" not in g.overrides


def test_unknown_key_rejected():
    with pytest.raises(KeyError):
        Grade.neutral().set("global.sharpness", (1, 1, 1, 1))


def test_lift_maps_to_shadows_offset():
    c = CONTROLS["lift"]
    assert (c.range, c.part, c.subtitle, c.title) == ("shadows", "offset", "Shadows · Offset", "Lift")


def test_primaries_order_and_subtitles():
    assert [c.id for c in PRIMARIES] == ["lift", "gamma", "gain", "offset", "saturation", "contrast",
                                         "temperature", "tint"]
    assert CONTROLS["gain"].subtitle == "Global · Gain"
    assert CONTROLS["temperature"].subtitle == "White Balance · Temp"
    assert CONTROLS["tint"].subtitle == "White Balance · Tint"
    assert CONTROLS["temperature"].kind == "slider" and CONTROLS["gain"].kind == "wheel"


def test_advanced_covers_three_ranges_by_five_parts():
    assert len(ADVANCED) == 15
    assert CONTROLS["midtones.gamma"].subtitle == "Midtones · Gamma"


def test_default_nudge_amounts():
    assert CONTROLS["temperature"].nudge == 100
    assert CONTROLS["tint"].nudge == 0.05
    for cid in ("lift", "gamma", "gain", "offset"):
        assert CONTROLS[cid].nudge == 0.02
    assert CONTROLS["saturation"].nudge == 0.05 and CONTROLS["contrast"].nudge == 0.05


def test_round_trip_dict():
    g = Grade.neutral().set("shadows.gamma", (0.9, 1, 1, 1.05)).set("temp", 5000)
    g.limits["shadows_max"] = 0.1
    assert Grade.from_dict(g.to_dict()) == g


def test_hex_shows_tint_without_brightness():
    assert tint_hex((1.0, 1.0, 1.0)) == "#FFFFFF"
    assert tint_hex((2.0, 2.0, 2.0)) == "#FFFFFF"  # brightness left out
    assert tint_hex((1.0, 0.5, 0.5)) == "#FF8080"
    r, g, b, y = hex_to_tint("#FF8080", 1.2)
    assert y == 1.2 and r > g == b


def test_hex_rejects_malformed():
    for bad in ("#GGG", "abc", "", "#12345"):
        with pytest.raises(ValueError):
            hex_to_tint(bad, 1.0)


def test_nudge_defaults_and_clamp():
    g = nudge(Grade.neutral(), "temperature", +CONTROLS["temperature"].nudge)
    assert g.temp == 6600.0 and "temp" in g.overrides
    g = nudge(Grade.neutral(), "gain", +0.02)
    assert g.get("global.gain")[3] == pytest.approx(1.02)
    assert nudge(Grade.neutral(), "tint", +5).tint == 1.0  # clamped to the control's range
    g = nudge(Grade.neutral(), "lift", 0.02, channel="r")
    assert g.get("shadows.offset") == pytest.approx((0.02, 0, 0, 0))


def test_nudge_unknown_control():
    with pytest.raises(KeyError):
        nudge(Grade.neutral(), "sharpness", 1)
