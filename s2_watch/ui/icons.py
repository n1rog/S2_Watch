from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ICONS_DIR = Path(__file__).resolve().parent.parent / "data" / "icons"
# The detailed icon blurs below about 40 px, so tray and taskbar sizes use a simplified drawing.
SMALL_SIZES = (16, 22, 24, 32)
LARGE_SIZES = (48, 64, 128, 256)


def _pixmap(renderer: QSvgRenderer, size: int) -> QPixmap:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return QPixmap.fromImage(image)


def app_icon() -> QIcon:
    """S2 Watch's icon, rendered here so it does not depend on Qt's SVG icon plugin."""
    icon = QIcon()
    for file_name, sizes in (("s2-watch-small.svg", SMALL_SIZES), ("s2-watch.svg", LARGE_SIZES)):
        renderer = QSvgRenderer(str(ICONS_DIR / file_name))
        if renderer.isValid():
            for size in sizes:
                icon.addPixmap(_pixmap(renderer, size))
    return icon
