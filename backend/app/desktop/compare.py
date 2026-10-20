"""Two-up and wipe comparison locked to one frame time."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget


def _fit(pixmap: QPixmap, width: int, height: int) -> QPixmap:
    if pixmap.isNull() or width < 2 or height < 2:
        return pixmap
    return pixmap.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


class CompareView(QWidget):
    """Original on the left, processed on the right, or a wipe of the same frame."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("compareView")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(180)
        self.mode = "split"
        self.seconds = 0.0
        self._left = QPixmap()
        self._right = QPixmap()
        self._wipe = 0.5
        self._dragging = False
        self.time_label = QLabel("Same frame")
        self.time_label.setObjectName("timeLabel")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.time_label, 0, Qt.AlignmentFlag.AlignTop)

    def set_pair(self, left: QPixmap, right: QPixmap, seconds: float) -> None:
        self._left = left
        self._right = right
        self.seconds = float(seconds)
        self.time_label.setText(f"Both sides {self.seconds:.3f}s")
        self.update()

    def set_mode(self, mode: str) -> None:
        if mode in {"split", "wipe", "original", "processed"}:
            self.mode = mode
            self.update()

    def same_time(self) -> bool:
        return True

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        area = self.rect().adjusted(8, 28, -8, -8)
        if self.mode == "original":
            self._draw_one(painter, self._left, area)
        elif self.mode == "processed":
            self._draw_one(painter, self._right, area)
        elif self.mode == "wipe":
            fitted = _fit(self._left, area.width(), area.height())
            processed = _fit(self._right, area.width(), area.height())
            if fitted.isNull():
                painter.end()
                return
            x = area.x() + (area.width() - fitted.width()) // 2
            y = area.y() + (area.height() - fitted.height()) // 2
            painter.drawPixmap(x, y, fitted)
            cut = int(fitted.width() * self._wipe)
            painter.setClipRect(QRect(x, y, cut, fitted.height()))
            if not processed.isNull():
                painter.drawPixmap(x, y, processed)
            painter.setClipping(False)
            painter.setPen(Qt.GlobalColor.white)
            painter.drawLine(x + cut, y, x + cut, y + fitted.height())
        else:
            gap = 8
            half = (area.width() - gap) // 2
            self._draw_one(painter, self._left, QRect(area.x(), area.y(), half, area.height()))
            self._draw_one(painter, self._right, QRect(area.x() + half + gap, area.y(), half, area.height()))
        painter.end()

    def _draw_one(self, painter: QPainter, pixmap: QPixmap, area: QRect) -> None:
        fitted = _fit(pixmap, area.width(), area.height())
        if fitted.isNull():
            return
        x = area.x() + (area.width() - fitted.width()) // 2
        y = area.y() + (area.height() - fitted.height()) // 2
        painter.drawPixmap(x, y, fitted)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.mode == "wipe" and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._move_wipe(event.position().toPoint())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragging:
            self._move_wipe(event.position().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._dragging = False
        super().mouseReleaseEvent(event)

    def _move_wipe(self, point: QPoint) -> None:
        width = max(1, self.width() - 16)
        self._wipe = min(1.0, max(0.0, (point.x() - 8) / width))
        self.update()
