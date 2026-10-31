"""Render the Operator Board logo (packaging/logo.svg) into the app's images.

Writes operator_board/ui/icon.png (window icon), operator_board/ui/logo.png (sidebar),
docs/images/logo.png (README) and packaging/operator_board.ico (exe and installer, 16-256 px).
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
    render(renderer, box, 256, 0.04).save(os.path.join(ROOT, "operator_board", "ui", "icon.png"))
    render(renderer, box, 256, 0.0).save(os.path.join(ROOT, "operator_board", "ui", "logo.png"))
    render(renderer, box, 320, 0.0).save(os.path.join(ROOT, "docs", "images", "logo.png"))
    try:
        from PIL import Image
    except ImportError:
        print("Pillow is not installed: PNGs written, operator_board.ico left as it was")
        return 1
    images = [Image.open(io.BytesIO(to_png_bytes(render(renderer, box, s, 0.0 if s <= 32 else 0.03))))
              for s in ICO_SIZES]
    images[-1].save(os.path.join(HERE, "operator_board.ico"), format="ICO", sizes=[(s, s) for s in ICO_SIZES],
                    append_images=images[:-1])
    print("logo images written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
