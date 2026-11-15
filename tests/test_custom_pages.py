"""Custom pages: the grid and the store (no Qt), then the pages in the window (offscreen Qt)."""
import json
import os
import time

import pytest

from operator_board.core import custom_pages, presets, projects
from operator_board.core.custom_pages import (COLUMNS, KIND_BUTTON, KIND_PANEL, WHOLE_PAGE, CustomPage,
                                              CustomPageStore, PageItem)


# ---- the grid and the store ---------------------------------------------------------------------
def test_items_snap_to_free_cells_and_never_overlap():
    page = CustomPage(name="Shoot")
    a = page.add(PageItem(w=6, h=2))
    b = page.add(PageItem(w=6, h=2))
    assert a.rect == (0, 0, 6, 2) and b.rect == (6, 0, 6, 2)  # the next free place, left to right
    c = page.add(PageItem(w=20, h=3))  # too wide for what is left of the row
    assert c.rect == (0, 2, 20, 3)
    assert not page.fits((2, 0, 2, 2))                     # on top of a
    assert not page.fits((5, 0, 2, 2), ignore=a)           # a aside, it still runs into b
    assert page.fits((0, 0, 6, 2), ignore=a)               # its own place
    assert not page.fits((COLUMNS - 2, 0, 3, 1))           # off the right edge
    assert not page.fits((0, -1, 2, 2)) and not page.fits((0, 0, 0, 2))
    assert page.rows == 5
    wide = page.add(PageItem(w=99, h=1))
    assert wide.w == COLUMNS and wide.row == 5


def test_store_keeps_pages_with_unique_names_and_order(tmp_path):
    path = str(tmp_path / "custom_pages.json")
    store = CustomPageStore(path)
    store.reserved = ["Operate", "Takes"]
    first = store.add("Shoot")
    assert store.add("shoot").name == "shoot 2"      # names differ by more than case
    assert store.add("Operate").name == "Operate 2"  # a built-in page's title is taken
    first.add(PageItem(kind=KIND_BUTTON, button={"action": "led.freeze", "label": "FREEZE"}))
    first.add(PageItem(kind=KIND_PANEL, source="Operate", panel="LED Wall", w=12, h=6))
    copy = store.duplicate(first)
    assert copy.name == "Shoot copy" and store.names()[1] == "Shoot copy"
    assert {i.id for i in copy.items}.isdisjoint({i.id for i in first.items})
    store.move(copy, 5)
    assert store.names() == ["Shoot", "shoot 2", "Operate 2", "Shoot copy"]
    assert store.rename(copy, "Takes") == "Takes 2"
    store.remove(store.get("shoot 2"))
    store.changed()

    again = CustomPageStore(path)
    assert again.names() == ["Shoot", "Operate 2", "Takes 2"]
    item = again.get("shoot").items[1]
    assert (item.kind, item.source, item.panel, item.rect) == (KIND_PANEL, "Operate", "LED Wall", (3, 0, 12, 6))
    assert again.get(first.id).items[0].button["action"] == "led.freeze"


def test_export_import_and_bad_files(tmp_path):
    store = CustomPageStore(None)
    page = store.add("Client view")
    page.add(PageItem(kind=KIND_PANEL, source="Takes", panel=WHOLE_PAGE, w=24, h=8))
    path = str(tmp_path / "pages.json")
    custom_pages.write_file(path, store.pages)
    added = store.add_pages(custom_pages.read_file(path))  # importing never replaces
    assert [p.name for p in added] == ["Client view 2"] and added[0].id != page.id
    assert store.add_pages(custom_pages.read_file(path), replace=True)[0].id == page.id  # a preset does
    assert store.names() == ["Client view", "Client view 2"]

    # overlapping or oversized items in a hand-edited file are put right, unknown kinds dropped
    bad = {"format": custom_pages.FILE_FORMAT, "pages": [{"name": "Odd", "items": [
        {"kind": "button", "col": 0, "row": 0, "w": 4, "h": 2}, {"kind": "button", "col": 2, "row": 1, "w": 40, "h": 2},
        {"kind": "hologram"}]}]}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(bad, fh)
    odd = custom_pages.read_file(path)[0]
    assert [i.rect for i in odd.items] == [(0, 0, 4, 2), (0, 2, 24, 2)] and all(i.id for i in odd.items)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"format": "something-else"}, fh)
    with pytest.raises(ValueError):
        custom_pages.read_file(path)


def test_custom_pages_travel_with_the_project(tmp_path):
    from operator_board.context import AppContext
    from operator_board.core.config import AppConfig

    info = projects.create(projects.ProjectInfo(name="Show A"))
    folder = projects.project_dir(info.id)
    ctx = AppContext(AppConfig(), project_dir=folder)
    ctx.custom_pages.add("Shoot").add(PageItem(kind=KIND_BUTTON, button={"action": "led.freeze"}))
    ctx.custom_pages.changed()
    ctx.save()
    ctx.shutdown()
    assert os.path.exists(os.path.join(folder, custom_pages.FILE_NAME))

    # a template (no shoot data), an import of it, and a new project made from this one's setup
    template = str(tmp_path / "template.opproject")
    projects.export_project(info.id, template, include_shoot_data=False)
    imported = projects.import_project(template, "Show B")
    fresh = projects.create(projects.ProjectInfo(name="Show C"), setup_from=info.id)
    for other in (imported, fresh):
        ctx = AppContext(AppConfig(), project_dir=projects.project_dir(other.id))
        assert ctx.custom_pages.names() == ["Shoot"]
        assert ctx.custom_pages.pages[0].items[0].button["action"] == "led.freeze"
        ctx.shutdown()


def test_show_action_and_preset_item():
    from operator_board.context import AppContext
    from operator_board.core.config import AppConfig

    ctx = AppContext(AppConfig())
    shown = []
    ctx.bus.subscribe("custom_pages.show", lambda _t, p: shown.append(p["name"]))
    assert not ctx.actions.invoke("custom_page.show", page="Nope").ok
    with pytest.raises(presets.PresetProblem):
        presets.capture(ctx, "custom_pages")  # nothing to save yet
    ctx.custom_pages.add("Shoot").add(PageItem(kind=KIND_BUTTON, button={"action": "led.freeze"}))
    ctx.custom_pages.add("Client")
    assert ctx.actions.get("custom_page.show").params[0].choices() == ["Shoot", "Client"]
    assert ctx.actions.invoke("custom_page.show", page="client").ok and shown == ["Client"]

    item = presets.capture(ctx, "custom_pages")
    assert presets.item_summary(item) == "2 items"
    ctx.custom_pages.remove(ctx.custom_pages.get("Client"))
    ctx.custom_pages.get("Shoot").items.clear()
    label, apply = presets.steps(ctx, {"name": "P", "items": [item]})[0]
    assert label.startswith("Custom pages") and apply() == "2 pages"
    assert ctx.custom_pages.names() == ["Shoot", "Client"]  # replaced by name, not doubled
    assert len(ctx.custom_pages.get("Shoot").items) == 1 and shown[-1] == "Shoot"
    ctx.shutdown()


# ---- in the window --------------------------------------------------------------------------------
qt = pytest.importorskip("operator_board.qt", exc_type=ImportError)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _window(cfg=None):
    from operator_board.context import AppContext
    from operator_board.core.config import AppConfig, LEDProcessorConfig
    from operator_board.ui.main_window import MainWindow

    app = qt.QtWidgets.QApplication.instance() or qt.QtWidgets.QApplication([])
    ctx = AppContext(cfg or AppConfig(led_processors=[LEDProcessorConfig(name="Sim", profile_id="simulator")]))
    window = MainWindow(ctx)
    window.resize(1500, 950)
    window.show()
    app.processEvents()
    return app, ctx, window


def _close(app, window):
    window.skip_save_on_close = True
    window.close()
    app.processEvents()


def test_every_page_offers_its_panels():
    app, ctx, window = _window()
    catalogue = {title: (setup, dict(panels)) for title, setup, panels, _controls in window.panel_catalogue()}
    assert "Custom Pages" not in catalogue and len(catalogue) == len(window.builtin_pages) - 1
    assert {"LED Wall", "Unreal Instances", "Multi-User", "Project & Level", "Take", "Camera Positions",
            "Wall status bar"} <= set(catalogue["Operate"][1])
    assert {"tab:Sub-levels & Data Layers", "Saved camera positions", "Streaming sub-levels"} \
        <= set(catalogue["Cameras & Levels"][1])
    assert {"Slate & Take", "Take log"} <= set(catalogue["Takes"][1])
    assert catalogue["LED Processors"][1] == {}  # its columns come and go: whole page only
    assert catalogue["Devices"][0] and catalogue["Settings"][0] and not catalogue["Operate"][0]
    for title, (_setup, panels) in catalogue.items():
        if title != "LED Processors":
            assert panels, f"{title} offers no panels"
    _close(app, window)


def test_custom_pages_sit_above_operate_and_follow_the_store():
    app, ctx, window = _window()
    manage = next(p for p in window.pages if p.title == "Custom Pages")
    assert window.current_page().title == "Operate" and window.nav.group_names() == ["ADVANCED", "SETUP"]
    manage._new("Shoot")
    assert window.current_page().title == "Shoot" and window.current_page().editing  # a new page opens unlocked
    manage._new("Client")
    def custom_group():
        group = window.nav.topLevelItem(0)
        return [group.child(i).text(0) for i in range(group.childCount())]
    assert window.nav.group_names() == ["CUSTOM", "ADVANCED", "SETUP"]  # the group is there, at the top
    assert custom_group() == ["Shoot", "Client"] and window.nav.topLevelItem(1).text(0) == "Operate"
    assert window.pages[0].title == "Operate" and window.nav.count() == len(window.pages)  # hotkeys' page stays first
    manage._select(ctx.custom_pages.get("Client"))
    manage._move(-1)
    assert custom_group() == ["Client", "Shoot"]
    assert window.current_page().title == "Client"  # still on the page it was on
    manage._rename("Operate")
    assert ctx.custom_pages.names() == ["Operate 2", "Shoot"]
    ctx.actions.invoke("custom_page.show", page="Shoot")
    app.processEvents()
    assert window.current_page().title == "Shoot"
    manage._select(ctx.custom_pages.get("Shoot"))
    manage._delete(confirm=False)
    assert window.current_page().title == "Operate" and len(window.custom_views) == 1
    manage._select(ctx.custom_pages.pages[0])
    manage._delete(confirm=False)
    assert window.nav.group_names() == ["ADVANCED", "SETUP"]  # no custom pages: no Custom group
    assert window.nav.topLevelItem(0).text(0) == "Operate"
    _close(app, window)
    ctx.shutdown()


def test_buttons_and_panels_work_on_a_custom_page(tmp_path):
    from operator_board.surfaces.layout import ButtonBinding
    from operator_board.ui.pages.operate import OperatePage
    from operator_board.ui.pages.takes import TakesPage

    app, ctx, window = _window()
    ctx.custom_pages.add("Shoot")
    window.show_page("Shoot")
    view = window.current_page()
    view.set_editing(True)
    assert view.canvas.editing and not view.add_btn.isHidden()
    view._fill_add_menu()
    assert [a.text() for a in view.add_menu.actions() if a.text()][:3] == ["Button…", "Operate", "Takes"]

    button = view.add_button(ButtonBinding(action="led.freeze", label="FREEZE", active_color="#1fa2ff"))
    wall = view.add_panel("Operate", "LED Wall")
    cams = view.add_panel("Operate", "Camera Positions")
    log = view.add_panel("Takes", "Take log")
    whole = view.add_panel("Takes", WHOLE_PAGE)
    tab = view.add_panel("Cameras & Levels", "tab:Camera Positions")
    assert view.add_panel("Operate", "No such panel") is None
    app.processEvents()
    items = view.page_.items
    assert len(items) == 6 and all(view.page_.fits(i.rect, ignore=i) for i in items)  # nothing overlaps
    # panels from one page share the hidden page behind them; a whole page and a tab get their own
    assert [type(c) for c in view.controllers].count(OperatePage) == 1
    assert [type(c) for c in view.controllers].count(TakesPage) == 2
    operate = next(c for c in view.controllers if isinstance(c, OperatePage))
    assert not operate.isVisible() and operate.is_live() and operate.freeze_btn.isVisible()

    # the editing overlay covers items; moving and resizing snap and refuse to overlap
    frame = view.canvas.frames[button.id]
    assert frame.overlay.isVisible()
    assert not view.canvas.place(button.id, wall.col, wall.row, button.w, button.h)   # onto the LED Wall panel
    free_row = view.page_.rows
    assert view.canvas.place(button.id, 0, free_row, 4, 3) and button.rect == (0, free_row, 4, 3)
    assert view.canvas.place(button.id, 22, free_row, 4, 3) and button.rect == (20, free_row, 4, 3)  # kept on the grid
    view.set_editing(False)
    assert not frame.overlay.isVisible() and view.add_btn.isHidden()

    # the button runs the action and lights; the carved panel shows the same live state
    view.buttons[button.id].press()
    deadline = time.time() + 5
    while not ctx.led.frozen and time.time() < deadline:
        app.processEvents()
    app.processEvents()
    assert ctx.led.frozen and view.buttons[button.id].is_active()
    view.refresh()
    assert f"background: {operate.freeze_btn.active_color}" in operate.freeze_btn.styleSheet()  # lit here too
    assert "#1fa2ff" in view.buttons[button.id].styleSheet()

    # a panel too big for its cells scrolls inside them
    area = view.canvas.frames[wall.id].area
    assert view.canvas.place(wall.id, wall.col, wall.row, 4, 2)
    app.processEvents()
    assert area.widget() is not None and area.widget().minimumSizeHint().height() > area.viewport().height()

    # saved with the layout, and rebuilt the same after a restart of the view
    saved = custom_pages.read_file(ctx.custom_pages.path)[0]
    assert [(i.kind, i.source, i.panel) for i in saved.items] == [(i.kind, i.source, i.panel) for i in items]
    view.remove_item(cams.id)
    assert len(view.page_.items) == 5 and cams.id not in view.canvas.frames
    assert {log.id, whole.id, tab.id, wall.id, button.id} == set(view.canvas.frames)

    # a panel that no longer exists shows a note instead of breaking the page
    view.page_.add(PageItem(kind=KIND_PANEL, source="Gone Page", panel="Thing", w=6, h=3))
    view.rebuild()
    app.processEvents()
    assert len(view.canvas.frames) == 6

    # visiting every page (custom ones too) still works, as in the smoke test
    for i in range(window.nav.count()):
        window.nav.setCurrentRow(i)
        app.processEvents()
        window.current_page().refresh()
    window._tick()
    _close(app, window)
    ctx.shutdown()


def test_single_controls_can_be_placed_on_their_own():
    from operator_board.ui.pages.operate import OperatePage

    app, ctx, window = _window()
    controls = {title: {key: (card, label) for card, key, label in found}
                for title, _setup, _panels, found in window.panel_catalogue()}
    operate = controls["Operate"]
    assert operate["ctl:freeze_btn"] == ("LED Wall", "FREEZE")
    assert operate["ctl:normal_btn"] == ("LED Wall", "WALL NORMAL (resume)")
    assert {"ctl:blackout_btn", "ctl:pattern_row", "ctl:wall_buttons", "ctl:rec_btn", "ctl:tc_value",
            "ctl:Unreal Instances/▶ Start all", "ctl:Take/Take +"} <= set(operate)
    assert "ctl:wall_overlay" not in operate and "ctl:no_devices" not in operate
    assert not any(key.endswith("/↗") for key in operate)  # the cards' own link buttons are not controls
    assert controls["Takes"]["ctl:rec"][1] == "RECORD / STOP (big button)"
    assert controls["LED Processors"] == {}
    for title, found in controls.items():
        if title != "LED Processors":
            assert found, f"{title} offers no single controls"

    ctx.custom_pages.add("Wall")
    window.show_page("Wall")
    view = window.current_page()
    view.set_editing(True)
    freeze = view.add_panel("Operate", "ctl:freeze_btn")
    blackout = view.add_panel("Operate", "ctl:blackout_btn")
    assert len(view.controllers) == 1                    # two buttons from one page share it
    row = view.add_panel("Operate", "ctl:wall_buttons")  # the row those buttons came out of needs a whole one
    card = view.add_panel("Operate", "LED Wall")         # and so does the card that row sits in
    start = view.add_panel("Operate", "ctl:Unreal Instances/▶ Start all")
    assert view.add_panel("Operate", "ctl:no_such_thing") is None
    assert [type(c) for c in view.controllers] == [OperatePage] * 3
    assert freeze.h >= 1 and row.w > freeze.w and card.h > row.h and start is not None
    assert "FREEZE" in view.canvas.frames[freeze.id].title
    view.set_editing(False)
    app.processEvents()

    first, second, third = view.controllers
    assert first.freeze_btn.isVisible() and second.freeze_btn.isVisible() and third.freeze_btn.isVisible()
    assert not first.normal_btn.isVisible()  # only what was placed is on the page
    first.freeze_btn.click()
    deadline = time.time() + 5
    while not ctx.led.frozen and time.time() < deadline:
        app.processEvents()
    app.processEvents()
    view.refresh()
    for controller in view.controllers:  # every copy of the control shows the same state
        assert f"background: {controller.freeze_btn.active_color}" in controller.freeze_btn.styleSheet()

    # the layout comes back the same after the page is rebuilt (as after a restart)
    view.rebuild()
    app.processEvents()
    assert len(view.canvas.frames) == 5 and len(view.controllers) == 3
    view.remove_item(blackout.id)
    assert len(view.canvas.frames) == 4
    _close(app, window)
    ctx.shutdown()


def test_button_dialog_checks_what_is_typed():
    from operator_board.surfaces.layout import ButtonBinding
    from operator_board.ui.button_dialog import ButtonDialog

    app, ctx, window = _window()
    dialog = ButtonDialog(window, ctx, ButtonBinding())
    with pytest.raises(ValueError):
        dialog.binding()  # no action chosen
    dialog.action.setCurrentIndex(dialog.action.findData("camera.recall"))
    dialog.params.setText("{not json")
    dialog._accept()
    assert dialog.result_binding is None and "Parameters" in dialog.error.text()
    dialog.params.setText('{"preset": "Wide"}')
    dialog.label.setPlainText("WIDE")
    dialog.text_color.setText("#ffd43b")
    dialog.font_size.setCurrentText("18")
    dialog._accept()
    b = dialog.result_binding
    assert (b.action, b.params, b.label, b.text_color, b.font_size) == \
        ("camera.recall", {"preset": "Wide"}, "WIDE", "#ffd43b", 18)
    _close(app, window)
    ctx.shutdown()
