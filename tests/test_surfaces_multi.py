"""Several control surfaces at once, page navigation defaults, key text fitting and custom LED types."""
import json
import os
import urllib.request

from operator_board.core.actions import ActionRegistry
from operator_board.core.config import AppConfig
from operator_board.core.events import EventBus
from operator_board.led.profiles import ProfileRegistry
from operator_board.surfaces.api_server import ApiServer
from operator_board.surfaces.layout import (NAV_NEXT, ButtonBinding, SurfaceLayout, SurfacePage,
                                            default_layout, ensure_page_navigation, new_surface, save_layout)
from operator_board.surfaces.manager import SurfaceManager
from operator_board.surfaces.osc import OscServer
from operator_board.surfaces.render import fit_label, wrap_lines


def _manager(config=None):
    bus = EventBus()
    return SurfaceManager(config or AppConfig(), ActionRegistry(bus), bus)


def _actions(layout):
    return [b.action for p in layout.pages for b in p.buttons.values() if b.action != NAV_NEXT
            and not b.action.startswith("surface.")]


def test_small_surfaces_get_every_button_and_a_next_page_key():
    default = default_layout()
    mini = new_surface("streamdeck_mini")
    assert mini.key_count == 6 and len(mini.pages) > 3
    for page in mini.pages:
        assert page.buttons["5"].action == NAV_NEXT  # the last key of every page moves on
        assert all(int(k) < 6 for k in page.buttons)
    assert sorted(_actions(mini)) == sorted(_actions(default))  # nothing lost
    assert [p.name for p in mini.pages[:3]] == ["Main", "Main 2", "Main 3"]
    big = new_surface("streamdeck_xl")
    assert len(big.pages) == len(default.pages)  # the default fits: one page per default page


def test_new_pages_get_a_next_page_key_but_it_can_be_cleared():
    layout = SurfaceLayout(model="streamdeck_mini",
                           pages=[SurfacePage(name="A", buttons={"0": ButtonBinding(action="x")})])
    assert not ensure_page_navigation(layout)  # one page: nothing to navigate
    layout.pages.append(SurfacePage(name="B"))
    assert ensure_page_navigation(layout)
    assert layout.pages[0].buttons["5"].action == NAV_NEXT and layout.pages[1].buttons["5"].action == NAV_NEXT
    m = _manager()
    controller = m.primary
    controller.layout.pages = layout.pages
    controller.layout.pages[0].buttons.pop("5")
    m.changed(controller)  # an edit to the keys does not put it back
    assert "5" not in controller.layout.pages[0].buttons


def test_several_surfaces_with_names_import_and_export(tmp_path):
    m = _manager()
    assert len(m.controllers) == 1  # the old single layout became the first surface
    a = m.add("streamdeck_mini")
    b = m.add("streamdeck_mini")
    assert (a.layout.name, b.layout.name) == ("Stream Deck Mini", "Stream Deck Mini 2")
    assert a.layout.id != b.layout.id
    assert m.rename(b, "Stream Deck Mini") == "Stream Deck Mini 2"  # names stay unique
    assert m.rename(b, "Camera deck") == "Camera deck"
    copy = m.duplicate(a)
    assert copy.layout.name == "Stream Deck Mini 2" and copy.layout.device == ""  # the name b gave up
    assert m.find("camera DECK") is b and m.find(2) is a and m.find(b.layout.id) is b
    path = str(tmp_path / "surfaces.json")
    assert m.export(path, [a, b]) == 2
    other = _manager()
    added = other.import_file(path)
    assert [c.layout.name for c in added] == ["Stream Deck Mini", "Camera deck"]
    assert other.import_file(path)[0].layout.name == "Stream Deck Mini 2"  # importing again never replaces
    assert json.load(open(path))["format"] == "operator-board-surfaces"


def test_surfaces_are_stored_in_the_config_and_old_layout_files_migrate(tmp_path):
    old = default_layout()
    old.model = "web_tablet"
    old.name = "Old tablet"
    path = str(tmp_path / "old.json")
    save_layout(old, path)
    cfg = AppConfig()
    cfg.surfaces.layout_path = path
    m = _manager(cfg)
    assert m.primary.layout.name == "Old tablet" and m.primary.layout.id
    m.add("midi_apc_mini")
    m.store()
    again = AppConfig.from_json(cfg.to_json())
    assert [d["model"] for d in again.surfaces.devices] == ["web_tablet", "midi_apc_mini"]
    assert len(_manager(again).controllers) == 2
    cfg.surfaces.devices = []  # everything removed on purpose: stays empty
    assert _manager(cfg).controllers == []


def test_osc_and_web_panel_pick_a_surface():
    m = _manager()
    first = m.primary
    osc_surface = m.add("osc_touchosc")
    web = m.add("web_phone")
    server = OscServer(m, port=0)
    assert server._target() is osc_surface
    server.handle("/operator/surface/3/page", ["next"])
    assert web.page == 1 and osc_surface.page == 0
    api = ApiServer(m.actions, lambda: {}, "127.0.0.1", 0, surface=m)
    api.start()
    try:
        base = f"http://127.0.0.1:{api.port}"
        assert json.loads(urllib.request.urlopen(base + "/api/surface").read())["id"] == web.layout.id
        state = json.loads(urllib.request.urlopen(base + f"/api/surface?surface={first.layout.id}").read())
        assert state["id"] == first.layout.id and state["keys"][0]["wrap"] is True
        assert len(json.loads(urllib.request.urlopen(base + "/api/surfaces").read())) == 3
    finally:
        api.stop()


def _measure(text, size):  # every character 0.6 x the size wide
    return len(text) * size * 0.6, size


def test_labels_wrap_and_shrink_to_fit_the_key():
    assert wrap_lines("TAKE RECORDER START", 60, 10, _measure) == ["TAKE", "RECORDER", "START"]
    assert wrap_lines("FREEZE\nWALL", 200, 10, _measure) == ["FREEZE", "WALL"]
    assert wrap_lines("ABCDEFGHIJKL", 30, 10, _measure) == ["ABCDE", "FGHIJ", "KL"]  # one long word
    lines, px = fit_label("MULTI-USER SESSION", 60, 60, _measure)
    assert len(lines) == 2 and max(_measure(l, px)[0] for l in lines) <= 60
    lines, px = fit_label("MULTI-USER SESSION", 60, 60, _measure, wrap=False)
    assert lines == ["MULTI-USER SESSION"] and px < 8 + 1  # no wrap: smaller text on one line
    assert fit_label("GO", 60, 60, _measure, size=18, key_px=144)[1] == 36  # sizes scale with the key


def test_editing_a_builtin_led_type_makes_a_custom_copy(tmp_path):
    registry = ProfileRegistry(user_dir=str(tmp_path))
    original = json.dumps(registry.get("brompton_tessera"), sort_keys=True)
    edited = json.loads(original)
    edited["commands"]["freeze_on"] = {"method": "GET", "path": "/api/override/freeze/enabled?set=1"}
    saved = registry.save_custom(edited, "brompton_tessera")
    assert saved["id"] == "brompton_tessera_custom" and saved["based_on"] == "brompton_tessera"
    assert saved["name"].endswith("(custom)") and saved["commands"]["freeze_on"]["confidence"] == "user"
    assert json.dumps(registry.get("brompton_tessera"), sort_keys=True) == original  # untouched
    assert os.path.exists(tmp_path / "brompton_tessera_custom.json")
    saved["commands"]["freeze_off"] = {"method": "GET", "path": "/x"}
    assert registry.save_custom(saved, saved["id"])["id"] == "brompton_tessera_custom"  # in place
    reloaded = ProfileRegistry(user_dir=str(tmp_path))
    assert reloaded.get("brompton_tessera_custom")["commands"]["freeze_off"]["path"] == "/x"
    assert not reloaded.is_builtin("brompton_tessera_custom") and reloaded.is_builtin("brompton_tessera")
    assert reloaded.delete_custom("brompton_tessera_custom") == "brompton_tessera"
    assert reloaded.delete_custom("brompton_tessera") is None  # built-ins cannot be deleted
    assert not os.path.exists(tmp_path / "brompton_tessera_custom.json")
