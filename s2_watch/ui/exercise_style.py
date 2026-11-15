"""How exercise traffic looks wherever it is listed: a purple row tint, purple text, a badge."""

from __future__ import annotations

from PySide6.QtGui import QBrush, QColor, QPalette
from PySide6.QtWidgets import QTableWidget, QWidget

from s2_watch.exercise import COLOUR, TINT_ALPHA

DARK_TEXT = "#ce93d8"      # the same purple, lighter, for text on a dark theme
BADGE = f"color: white; background: {COLOUR}; font-weight: bold; padding: 1px 8px; border-radius: 3px;"


def exercise_brush() -> QBrush:
    tint = QColor(COLOUR)
    tint.setAlpha(TINT_ALPHA)
    return QBrush(tint)


def exercise_text(widget: QWidget) -> QColor:
    dark = widget.palette().color(QPalette.ColorRole.Base).lightness() < 128
    return QColor(DARK_TEXT if dark else COLOUR)


def tint_row(table: QTableWidget, row: int) -> None:
    """Tint a row of exercise traffic."""
    brush = exercise_brush()
    for column in range(table.columnCount()):
        item = table.item(row, column)
        if item is not None:
            item.setBackground(brush)
