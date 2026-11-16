"""Pictures on buttons: storing and placing them (no Qt), the web panel, the Stream Deck drawing,
then the libraries, the editor row and the on-screen buttons (offscreen Qt)."""
import io
import json
import os
import urllib.error
import urllib.request

import pytest

from operator_board.core.actions import ActionRegistry, ActionResult
from operator_board.core.events import EventBus
from operator_board.core.serde import from_dict, to_dict
from operator_board.surfaces import pictures
from operator_board.surfaces.api_server import ApiServer
from operator_board.surfaces.controller import SurfaceController
from operator_board.surfaces.layout import ButtonBinding, SurfaceLayout, SurfacePage
from operator_board.surfaces.manager import SurfaceManager
from operator_board.surfaces.web_panel import PANEL_HTML, surface_state

# Stand-in picture bytes (they start like a PNG file; nothing here needs to open them).
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000002000000010806000000f4220f"
    "8c0000000f49444154789c63f8cfc0f09f81818101000a0002fe8a4bd5b40000000049454e44ae426082")


def _png(width=8, height=4, color=(255, 0, 0, 255)) -> bytes:
    Image = pytest.importorskip("PIL.Image")
    out = io.BytesIO()
    Image.new("RGBA", (width, height), color).save(out, "PNG")
    return out.getvalue()


def test_picture_text_round_trips_and_damage_is_harmless():
    image = pictures.encode(PNG)
    assert pictures.decode(image) == PNG
    assert pictures.decode("") == b"" and pictures.decode("not base64 !!") == b""
    assert pictures.mime_type(PNG) == "image/png" and pictures.mime_type(b"\xff\xd8\xff\xe0") == "image/jpeg"
    assert pictures.picture_id(image) == pictures.picture_id(image) != pictures.picture_id(image + "AAAA")
    assert pictures.picture_id("") == ""
    assert pictures.source_kind("icon:camera") == "icon" and pictures.source_name("icon:camera") == "camera"
    assert pictures.source_kind("file:my:logo.png") == "file" and pictures.source_name("file:my:logo.png") == "my:logo.png"
    assert pictures.source_kind("something else") == ""


def test_a_button_keeps_its_picture_through_save_and_old_files_still_load():
    b = ButtonBinding(action="a", label="A", image=pictures.encode(PNG), image_source="icon:camera",
                      image_color="#00d5ff", image_layout="fill")
    again = from_dict(ButtonBinding, json.loads(json.dumps(to_dict(b))))
    assert again == b
    old = from_dict(ButtonBinding, {"action": "a", "label": "A"})  # saved before pictures existed
    assert (old.image, old.image_color, old.image_layout) == ("", "", "above")


def test_where_the_picture_and_label_go():
    plain = ButtonBinding(action="a", label="GO")
    assert pictures.areas(72, 72, 5, plain) == (None, (5, 5, 62, 62))
    image = pictures.encode(PNG)

    above = ButtonBinding(action="a", label="GO", image=image)
    picture, label = pictures.areas(72, 72, 5, above)
    assert picture[1] == 5 and picture[3] > label[3] and label[1] >= picture[1] + picture[3]
    assert label[1] + label[3] == 72 - 5  # the label ends at the bottom padding

    only = ButtonBinding(action="a", label="GO", image=image, image_layout="only")
    assert pictures.areas(72, 72, 5, only) == ((5, 5, 62, 62), None) and pictures.label_text(only) == ""

    fill = ButtonBinding(action="a", label="GO", image=image, image_layout="fill")
    assert pictures.areas(72, 72, 5, fill) == ((0, 0, 72, 72), (5, 5, 62, 62))

    odd = ButtonBinding(action="a", label="GO", image=image, image_layout="sideways")  # unknown: as above
    assert pictures.layout_of(odd) == "above"
    assert pictures.label_text(ButtonBinding(action="cam.go", image_layout="only")) == "cam.go"  # no picture


def test_pictures_keep_their_shape():
    assert pictures.place(200, 100, (0, 0, 60, 60)) == (0, 15, 60, 30)        # all of it, centred
    assert pictures.place(200, 100, (0, 0, 60, 60), cover=True) == (-30, 0, 120, 60)  # fills, sides cut
    assert pictures.place(0, 0, (1, 2, 3, 4)) == (1, 2, 3, 4)


def _controller(binding):
    bus = EventBus()
    actions = ActionRegistry(bus)
    actions.add("test.go", "Go", "Test", lambda: ActionResult(True, "went"))
    layout = SurfaceLayout(model="custom", custom_columns=2, custom_rows=1,
                           pages=[SurfacePage(name="Main", buttons={"0": binding})])
    return SurfaceController(layout, actions, bus)


def test_web_panel_gets_the_picture_once_not_every_second():
    image = pictures.encode(PNG)
    surface = _controller(ButtonBinding(action="test.go", label="GO", image=image, image_color="#ff0000",
                                        image_layout="only"))
    key = surface_state(surface)["keys"][0]
    assert key["image"] == pictures.picture_id(image) and image not in json.dumps(surface_state(surface))
    assert (key["label"], key["image_color"], key["image_layout"]) == ("", "#ff0000", "only")
    assert surface_state(surface)["keys"][1]["image"] == ""
    assert "/api/surface/key-image/" in PANEL_HTML

    api = ApiServer(surface.actions, lambda: {}, "127.0.0.1", 0, token="t0k", surface=surface)
    api.start()
    try:
        base = f"http://127.0.0.1:{api.port}"
        reply = urllib.request.urlopen(base + "/api/surface/key-image/0?token=t0k&v=" + key["image"])
        assert reply.read() == PNG and reply.headers["Content-Type"] == "image/png"
        for path in ("/api/surface/key-image/1?token=t0k", "/api/surface/key-image/0"):  # no picture; no token
            with pytest.raises(urllib.error.HTTPError):
                urllib.request.urlopen(base + path)
    finally:
        api.stop()


def test_surface_export_carries_the_pictures(tmp_path):
    from operator_board.core.config import AppConfig

    bus = EventBus()
    manager = SurfaceManager(AppConfig(), ActionRegistry(bus), bus)
    controller = manager.controllers[0] if manager.controllers else manager.add("streamdeck_15")
    image = pictures.encode(PNG)
    controller.layout.pages[0].buttons["0"] = ButtonBinding(action="x", label="X", image=image,
                                                            image_source="file:logo.png")
    path = str(tmp_path / "surfaces.json")
    manager.export(path, [controller])
    added = manager.import_file(path)
    button = added[0].layout.pages[0].button(0)
    assert (button.image, button.image_source) == (image, "file:logo.png")


def test_stream_deck_draws_the_picture_recoloured_and_in_place():
    Image = pytest.importorskip("PIL.Image")
    from operator_board.surfaces import streamdeck

    if not streamdeck.AVAILABLE:
        pytest.skip("Stream Deck support is not installed")
    red = pictures.encode(_png(40, 40, (255, 0, 0, 255)))

    def drawn(**fields):
        key = Image.new("RGB", (72, 72))
        streamdeck.draw_key(key, ButtonBinding(action="a", label="GO", color="#000000", image=red, **fields), False)
        return key

    only = drawn(image_layout="only")
    assert only.getpixel((36, 36)) == (255, 0, 0) and only.getpixel((1, 1)) == (0, 0, 0)  # padding stays clear
    assert drawn(image_layout="fill").getpixel((1, 1)) == (255, 0, 0)                     # edge to edge
    blue = drawn(image_layout="only", image_color="#0000ff")
    assert blue.getpixel((36, 36)) == (0, 0, 255)
    above = drawn(image_layout="above")
    assert above.getpixel((36, 20)) == (255, 0, 0) and above.getpixel((6, 64)) != (255, 0, 0)  # label area below
    broken = Image.new("RGB", (72, 72))
    streamdeck.draw_key(broken, ButtonBinding(action="a", label="GO", color="#101010", image="AAAA"), False)
    assert broken.getpixel((2, 2)) == (16, 16, 16)  # a damaged picture is left out, the key still draws


# ---- with Qt --------------------------------------------------------------------------------------
qt = pytest.importorskip("operator_board.qt", exc_type=ImportError)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def app():
    return qt.QtWidgets.QApplication.instance() or qt.QtWidgets.QApplication([])


def _opaque_pixels(image):
    return sum(1 for y in range(0, image.height(), 4) for x in range(0, image.width(), 4)
               if image.pixelColor(x, y).alpha() > 128)


def test_icon_library_is_searchable_and_drawable(app):
    from operator_board.ui import pictures as ui

    library = ui.icon_library()
    assert len(library) > 3000 and "camera" in library
    assert not any(name.startswith("brand-") for name in library)  # no company logos
    assert "camera" in ui.find_icons("camera") and "player-record" in ui.find_icons("record")
    assert ui.find_icons("zzzz no such icon") == []
    suggested = ui.find_icons("", ui.SUGGESTED)
    assert len(suggested) > 80 and suggested[0] == "player-record"
    assert set(ui.find_icons("", "Weather")) < set(ui.find_icons("", ui.ALL))
    image = ui.icon_image("camera")
    assert image.width() == pictures.MAX_SIDE and image.hasAlphaChannel() and _opaque_pixels(image) > 50
    assert ui.icon_image("no-such-icon").isNull()


def test_pictures_are_stored_small(app, tmp_path):
    from operator_board.ui import pictures as ui

    photo = qt.QtGui.QImage(1200, 600, qt.QtGui.QImage.Format_RGB32)
    photo.fill(qt.QtGui.QColor("#336699"))
    path = str(tmp_path / "photo.png")
    photo.save(path)
    stored = ui.store(ui.load_file(path))
    data = pictures.decode(stored)
    assert pictures.mime_type(data) == "image/jpeg" and len(data) < 20000  # no clear parts: the small format
    back = ui.image_from(stored)
    assert (back.width(), back.height()) == (256, 128)
    icon = ui.store(ui.icon_image("camera"))
    assert pictures.mime_type(pictures.decode(icon)) == "image/png"  # clear background kept
    assert ui.store(ui.load_file(str(tmp_path / "missing.png"))) == ""
    assert ui.is_single_colour(ui.icon_image("camera")) and not ui.is_single_colour(photo)

    svg = tmp_path / "logo.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4"/></svg>')
    assert not ui.load_file(str(svg)).isNull()


def test_picture_field_chooses_recolours_and_clears(app, tmp_path):
    from operator_board.ui.pictures import PictureField

    field = PictureField()
    assert field.values()["image"] == ""
    field.kind.setCurrentIndex(field.kind.findData("icon"))
    assert field.choose.text() == "Choose icon…" and field.values()["image"] == ""  # nothing chosen yet
    assert field.pick("camera")
    field.color.setText("#00d5ff")
    field.layout_box.setCurrentIndex(field.layout_box.findData("only"))
    got = field.values()
    assert got["image"] and (got["image_source"], got["image_color"], got["image_layout"]) == \
        ("icon:camera", "#00d5ff", "only")
    field.color.setText("nonsense")
    with pytest.raises(ValueError):
        field.values()
    field.color.setText("")

    # Custom: the button becomes Upload, and the icon is not kept for it
    field.kind.setCurrentIndex(field.kind.findData("file"))
    assert field.choose.text() == "Upload…" and field.values()["image"] == ""
    photo = qt.QtGui.QImage(64, 64, qt.QtGui.QImage.Format_RGB32)
    photo.fill(qt.QtGui.QColor("#aa3300"))
    path = str(tmp_path / "set photo.jpg")
    photo.save(path)
    assert field.upload(path)
    assert field.values()["image_source"] == "file:set photo.jpg"
    assert not field.can_recolour and field.values()["image_color"] == ""  # a photo is not recoloured
    assert not field.upload(str(tmp_path / "nothing.jpg")) and "not a picture" in field.name.text()

    # filling from a saved button, then No picture
    field.set_binding(ButtonBinding(action="a", image=got["image"], image_source="icon:camera",
                                    image_color="#ff0000", image_layout="fill"))
    assert field.kind.currentData() == "icon" and field.values()["image_color"] == "#ff0000"
    assert field.values()["image_layout"] == "fill"
    field.kind.setCurrentIndex(0)
    assert field.values() == {"image": "", "image_source": "", "image_color": "", "image_layout": "above"}


def test_picker_lists_searches_and_returns_a_choice(app):
    from operator_board.ui import pictures as ui

    picker = ui.PicturePicker(None, "icon", "camera")
    assert picker.list.count() == len(ui.find_icons("", ui.SUGGESTED))
    assert picker.list.currentItem().data(qt.Qt.UserRole) == "camera"
    picker.search.setText("snowflake")
    assert 0 < picker.list.count() < 20
    picker.list.setCurrentRow(0)
    picker._accept()
    assert "snowflake" in picker.choice
    picker = ui.PicturePicker(None, "icon")
    picker.group.setCurrentText(ui.ALL)
    assert picker.list.count() == ui.SHOWN_AT_ONCE and "first" in picker.count.text()
    picker.search.setText("zzzz")
    assert picker.list.count() == 0 and "Nothing" in picker.count.text()

    emojis = ui.PicturePicker(None, "emoji")  # the list depends on the PC's emoji font
    assert emojis.list.count() == len(ui.emoji_list())
    if ui.emoji_list():
        assert ui.find_emojis("clapper") and not ui.find_emojis("zzzz")
    assert not ui.emoji_image("\U0001F3AC").isNull()


def test_on_screen_buttons_show_the_picture(app):
    from operator_board.ui import pictures as ui
    from operator_board.ui.pages.surfaces import KeyButton

    image = ui.store(ui.icon_image("camera"))
    key = KeyButton(96)
    key.show_binding(ButtonBinding(action="a", label="CAM", image=image))
    assert not key.picture_label.isHidden() and not key.text_label.isHidden()
    assert key.picture_label.geometry().bottom() < key.text_label.geometry().top()
    assert key.text_label.text() == "CAM"
    key.show_binding(ButtonBinding(action="a", label="CAM", image=image, image_layout="only"))
    assert key.text_label.isHidden() and key.picture_label.height() > 60
    key.show_binding(ButtonBinding(action="a", label="CAM", image=image, image_layout="fill"))
    assert key.picture_label.width() >= 90 and not key.text_label.isHidden()
    key.show_binding(ButtonBinding(action="a", label="CAM"))
    assert key.picture_label.isHidden() and key.text_label.text() == "CAM"

    red = ui.pixmap(image, "#ff0000", 40, 40).toImage()
    colours = {red.pixelColor(x, y).name() for y in range(40) for x in range(40) if red.pixelColor(x, y).alpha() > 200}
    assert colours == {"#ff0000"}  # recoloured: every drawn pixel is the chosen colour


def test_both_button_editors_save_the_picture(app):
    from operator_board.context import AppContext
    from operator_board.core.config import AppConfig
    from operator_board.ui.button_dialog import ActionButton, ButtonDialog
    from operator_board.ui.main_window import MainWindow

    ctx = AppContext(AppConfig())
    window = MainWindow(ctx)
    window.resize(1500, 950)
    window.show()
    app.processEvents()

    # a control surface key
    window.show_page("Control Surfaces")
    page = window.current_page()
    page._key_clicked(0)
    page.action.setCurrentIndex(page.action.findData("led.freeze"))
    page.label.setPlainText("FREEZE")
    page.picture.kind.setCurrentIndex(page.picture.kind.findData("icon"))
    assert page.picture.pick("snowflake")
    page.picture.color.setText("#00d5ff")
    page._apply()
    saved = page._page().button(0)
    assert saved.image and (saved.image_source, saved.image_color, saved.label) == \
        ("icon:snowflake", "#00d5ff", "FREEZE")
    page._key_clicked(1)
    assert page.picture.values()["image"] == ""       # another key: its own (empty) picture
    page._key_clicked(0)
    assert page.picture.values()["image"] == saved.image

    # a custom page button
    dialog = ButtonDialog(window, ctx, saved)
    assert dialog.binding().image == saved.image
    dialog.picture.layout_box.setCurrentIndex(dialog.picture.layout_box.findData("only"))
    button = ActionButton(page, dialog.binding())
    button.resize(200, 120)
    button.refresh()
    assert not button.picture_label.isHidden() and button.text_label.isHidden()

    window.skip_save_on_close = True
    window.close()
    app.processEvents()
    ctx.shutdown()
