"""A splitter whose handle can be seen and grabbed: a wide bar with a grip in the middle."""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWidgets import QSplitter, QSplitterHandle


class GripHandle(QSplitterHandle):
    def __init__(self, orientation, parent) -> None:
        super().__init__(orientation, parent)
        self.setToolTip("Drag to resize")
        self.setCursor(Qt.CursorShape.SplitVCursor if orientation == Qt.Orientation.Vertical
                       else Qt.CursorShape.SplitHCursor)
        self._hover = False

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        palette = self.palette()
        painter.fillRect(self.rect(), palette.color(QPalette.ColorRole.Midlight if self._hover else QPalette.ColorRole.Window))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(palette.color(QPalette.ColorRole.Highlight if self._hover else QPalette.ColorRole.Mid))
        if self.orientation() == Qt.Orientation.Vertical:
            grip = QRect(0, 0, 72, 4)
        else:
            grip = QRect(0, 0, 4, 72)
        grip.moveCenter(self.rect().center())
        painter.drawRoundedRect(grip, 2, 2)


class GripSplitter(QSplitter):
    def __init__(self, orientation, parent=None) -> None:
        super().__init__(orientation, parent)
        self.setHandleWidth(10)

    def createHandle(self) -> QSplitterHandle:
        return GripHandle(self.orientation(), self)
