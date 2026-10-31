"""Render the Operator Board logo (packaging/logo.svg) into the app's images.

Writes operator_board/ui/icon.png and icon_small.png (window icon), operator_board/ui/logo.png
(sidebar), docs/images/logo.png (README) and packaging/operator_board.ico (exe and installer, 16-256 px).
The application icons (window and .ico) are the logo in black on a white rounded square; the sidebar
and README logos keep the cyan artwork.
Run once after changing the logo: python packaging/make_icon.py  (needs Pillow for the .ico)
"""
import io
import os
import sys

from PySide6 import QtCore, QtGui, QtSvg, QtWidgets

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SVG = os.path.join(HERE, "logo.svg")
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
# Application icon (exe, installer, shortcuts, taskbar): black logo on a white rounded square at
# every size. The margin is the smallest that keeps the artwork clear of the rounded corners.
TILE_MARGIN = 0.035
TILE_RADIUS = 0.16
# Inno Setup "modern" wizard picture sizes at 100 %, 150 % and 200 % display scale.
WIZARD_IMAGE = ((164, 314), (246, 471), (328, 628))
WIZARD_SMALL = (55, 83, 110)


def content_box(renderer: QtSvg.QSvgRenderer) -> QtCore.QRectF:
    """The drawn part of the logo in viewBox units (the artwork has empty margins)."""
    view = renderer.viewBoxF()
    probe = render_full(renderer, 1024)
    left, top, right, bottom = probe.width(), probe.height(), -1, -1
    for y in range(probe.height()):
        for x in range(probe.width()):
            if QtGui.qAlpha(probe.pixel(x, y)) > 8:
                left, right = min(left, x), max(right, x)
                top, bottom = min(top, y), max(bottom, y)
    sx, sy = view.width() / probe.width(), view.height() / probe.height()
    return QtCore.QRectF(view.x() + left * sx, view.y() + top * sy, (right - left + 1) * sx, (bottom - top + 1) * sy)


def render_full(renderer: QtSvg.QSvgRenderer, width: int) -> QtGui.QImage:
    view = renderer.viewBoxF()
    img = QtGui.QImage(width, round(width * view.height() / view.width()), QtGui.QImage.Format_ARGB32)
    img.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    renderer.render(p)
    p.end()
    return img


def render(renderer: QtSvg.QSvgRenderer, box: QtCore.QRectF, size: int, margin: float) -> QtGui.QImage:
    """The logo centred in a transparent square, ``margin`` (a fraction of ``size``) on each side.

    Small sizes use a smaller margin so the thin lines stay as large as possible.
    """
    img = QtGui.QImage(size, size, QtGui.QImage.Format_ARGB32)
    img.fill(QtCore.Qt.transparent)
    inner = size * (1 - 2 * margin)
    scale = inner / max(box.width(), box.height())
    w, h = box.width() * scale, box.height() * scale
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
    view = renderer.viewBoxF()
    full = QtCore.QRectF((size - w) / 2 - (box.x() - view.x()) * scale, (size - h) / 2 - (box.y() - view.y()) * scale,
                         view.width() * scale, view.height() * scale)
    renderer.render(p, full)
    p.end()
    return img


def render_black(renderer: QtSvg.QSvgRenderer, box: QtCore.QRectF, size: int, margin: float = TILE_MARGIN) -> QtGui.QImage:
    """The logo in black on a transparent square."""
    logo = render(renderer, box, size, margin)
    p = QtGui.QPainter(logo)  # recolour: keep the shape (alpha), paint it black
    p.setCompositionMode(QtGui.QPainter.CompositionMode_SourceIn)
    p.fillRect(logo.rect(), QtGui.QColor("#000000"))
    p.end()
    return logo


def _bmp(img: QtGui.QImage, path: str) -> None:
    """24-bit BMP (what every Inno Setup version reads)."""
    img.convertToFormat(QtGui.QImage.Format_RGB888).save(path, "BMP")


def render_tile(renderer: QtSvg.QSvgRenderer, box: QtCore.QRectF, size: int) -> QtGui.QImage:
    """The application icon: the logo in black, as large as it fits, on a white rounded-corner square."""
    img = QtGui.QImage(size, size, QtGui.QImage.Format_ARGB32)
    img.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(QtGui.QColor("#ffffff"))
    radius = size * TILE_RADIUS
    p.drawRoundedRect(QtCore.QRectF(0, 0, size, size), radius, radius)
    p.end()
    logo = render_black(renderer, box, size)
    p = QtGui.QPainter(img)
    p.drawImage(0, 0, logo)
    p.end()
    return img


def to_png_bytes(img: QtGui.QImage) -> bytes:
    buf = QtCore.QBuffer()
    buf.open(QtCore.QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def main() -> int:
    QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    renderer = QtSvg.QSvgRenderer(SVG)
    if not renderer.isValid():
        print(f"Cannot read {SVG}")
        return 1
    box = content_box(renderer)
    # Window / taskbar icon: the same black-on-white tile as the exe. The large one serves big requests
    # (Alt+Tab at high DPI), the 64 px one is drawn crisply for every small one.
    render_tile(renderer, box, 256).save(os.path.join(ROOT, "operator_board", "ui", "icon.png"))
    render_tile(renderer, box, 64).save(os.path.join(ROOT, "operator_board", "ui", "icon_small.png"))
    render(renderer, box, 256, 0.0).save(os.path.join(ROOT, "operator_board", "ui", "logo.png"))
    render(renderer, box, 320, 0.0).save(os.path.join(ROOT, "docs", "images", "logo.png"))
    try:
        from PIL import Image
    except ImportError:
        print("Pillow is not installed: PNGs written, operator_board.ico left as it was")
        return 1
    images = [Image.open(io.BytesIO(to_png_bytes(render_tile(renderer, box, s)))) for s in ICO_SIZES]
    images[-1].save(os.path.join(HERE, "operator_board.ico"), format="ICO", sizes=[(s, s) for s in ICO_SIZES],
                    append_images=images[:-1])
    # Installer wizard pictures (instead of Inno Setup's own), at 100/150/200 % display scale.
    for i, (w, h) in enumerate(WIZARD_IMAGE):
        side = render_black(renderer, box, round(w * 0.8))
        page = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32)
        page.fill(QtGui.QColor("#ffffff"))
        p = QtGui.QPainter(page)
        p.drawImage((w - side.width()) // 2, (h - side.height()) // 2, side)
        p.end()
        _bmp(page, os.path.join(HERE, f"wizard_image_{i}.bmp"))
    for i, s in enumerate(WIZARD_SMALL):
        page = QtGui.QImage(s, s, QtGui.QImage.Format_ARGB32)
        page.fill(QtGui.QColor("#ffffff"))  # the wizard header is white, so a plain square sits flush
        p = QtGui.QPainter(page)
        p.drawImage(0, 0, render_black(renderer, box, s))
        p.end()
        _bmp(page, os.path.join(HERE, f"wizard_small_{i}.bmp"))
    print("logo images written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
