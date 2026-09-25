import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_app_icon_has_small_and_large_renderings() -> None:
    from PySide6.QtWidgets import QApplication

    from s2_watch.ui.icons import LARGE_SIZES, SMALL_SIZES, app_icon

    QApplication.instance() or QApplication([])
    icon = app_icon()
    sizes = sorted(size.width() for size in icon.availableSizes())
    assert sizes == sorted(SMALL_SIZES + LARGE_SIZES)
    # Not a blank image: the centre of the radar scope is drawn.
    image = icon.pixmap(64, 64).toImage()
    assert image.pixelColor(32, 32).alpha() == 255
