import json
import os
import urllib.request

import pytest

from operator_board.context import AppContext
from operator_board.core.config import AppConfig, CameraPreset, LEDProcessorConfig
from operator_board.surfaces.layout import NAV_GOTO, NAV_NEXT, NAV_PREV, default_layout, load_layout, save_layout


@pytest.fixture
def ctx():
    cfg = AppConfig(led_processors=[LEDProcessorConfig(name="Sim", profile_id="simulator")],
                    cameras=[CameraPreset(name="Camera 1")])
    cfg.surfaces.api_port = 0  # ephemeral port
    cfg.surfaces.streamdeck_enabled = False
    c = AppContext(cfg)
    yield c
    c.shutdown()


def test_default_layout_actions_exist(ctx):
    layout = default_layout()
    assert layout.key_count == 15 and len(layout.pages) >= 2
    for page in layout.pages:
        for key, binding in page.buttons.items():
            assert int(key) < layout.key_count
            assert ctx.actions.get(binding.action), binding.action


def test_layout_save_load_round_trip(tmp_path):
    layout = default_layout()
    layout.model = "streamdeck_xl"
    path = str(tmp_path / "deck.json")
    save_layout(layout, path)
    loaded = load_layout(path)
    assert loaded.model == "streamdeck_xl" and loaded.key_count == 32
    assert json.load(open(path))["pages"][0]["buttons"]["0"]["action"] == "led.freeze"


def test_surface_press_and_navigation(ctx):
    surface = ctx.surface
    assert surface.press(0).ok and ctx.led.frozen and surface.is_active(0)
    nav_next = next(int(k) for k, b in surface.current_page.buttons.items() if b.action == NAV_NEXT)
    surface.press(nav_next)
    assert surface.current_page.name == "Cameras"
    nav_prev = next(int(k) for k, b in surface.current_page.buttons.items() if b.action == NAV_PREV)
    surface.press(nav_prev)
    assert surface.page == 0


def test_api_server(ctx):
    ctx.start_services(enable_hardware=False)
    base = f"http://127.0.0.1:{ctx.api.port}"
    with urllib.request.urlopen(base + "/api/action/led.blackout_on") as r:
        assert json.load(r)["ok"]
    with urllib.request.urlopen(base + "/api/state") as r:
        assert json.load(r)["wall"]["blackout"] is True
    with urllib.request.urlopen(base + "/api/actions") as r:
        ids = {a["id"] for a in json.load(r)}
    assert {"led.freeze", "takes.toggle_record", "camera.recall", "group.start"} <= ids
    req = urllib.request.Request(base + "/api/action/led.test_pattern", data=json.dumps({"pattern": "red"}).encode(),
                                 method="POST")
    with urllib.request.urlopen(req) as r:
        assert json.load(r)["ok"] and ctx.led.test_pattern == "red"


def test_api_token(ctx):
    ctx.config.surfaces.api_token = "secret"
    ctx.start_services(enable_hardware=False)
    base = f"http://127.0.0.1:{ctx.api.port}"
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(base + "/api/state")
    assert err.value.code == 401
    with urllib.request.urlopen(base + "/api/state?token=secret") as r:
        assert r.status == 200


def test_level_selection_from_project_folder(ctx, tmp_path):
    project_dir = tmp_path / "Stage"
    for rel in ("Maps/Main.umap", "Maps/Night.umap", "Maps/__ExternalActors__/x.umap"):
        p = project_dir / "Content" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")
    (project_dir / "Stage.uproject").write_text("{}")
    ctx.config.project().uproject_path = str(project_dir / "Stage.uproject")
    levels = ctx.refresh_levels()
    assert [l.path for l in levels] == ["/Game/Maps/Main", "/Game/Maps/Night"]
    assert ctx.actions.invoke("level.next").ok and ctx.selected_level_path() == "/Game/Maps/Night"
    assert ctx.actions.invoke("level.next").ok and ctx.selected_level_path() == "/Game/Maps/Main"


def test_project_switch(ctx):
    from operator_board.core.config import ProjectConfig
    ctx.config.projects.append(ProjectConfig(name="Other"))
    assert ctx.actions.invoke("project.switch", project="Other").ok
    assert ctx.config.active_project == "Other"
    assert not ctx.actions.invoke("project.switch", project="Missing").ok
