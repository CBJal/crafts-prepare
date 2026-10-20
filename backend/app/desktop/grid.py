"""Responsive thumbnail grid with one selection model."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget


class ClipCard(QFrame):
    pressed = Signal(str, int)
    double_pressed = Signal(str)

    def __init__(self, clip_id: str, name: str, meta: str) -> None:
        super().__init__()
        self.clip_id = clip_id
        self.setObjectName("clipCard")
        self.setProperty("selected", False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        self.image = QLabel("…")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setObjectName("cardImage")
        self.name = QLabel(name)
        self.name.setWordWrap(True)
        self.meta = QLabel(meta)
        self.meta.setObjectName("muted")
        self.meta.setWordWrap(True)
        layout.addWidget(self.image, 1)
        layout.addWidget(self.name)
        layout.addWidget(self.meta)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_pixmap(self, pixmap: QPixmap) -> None:
        width = max(16, self.image.width() or 160)
        height = max(16, int(width * 9 / 16))
        scaled = pixmap.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.image.setPixmap(scaled)
        self.image.setText("")

    def set_message(self, text: str) -> None:
        self.image.setPixmap(QPixmap())
        self.image.setText(text)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            mods = event.modifiers()
            flags = 0
            if mods & Qt.KeyboardModifier.ControlModifier:
                flags |= 1
            if mods & Qt.KeyboardModifier.ShiftModifier:
                flags |= 2
            self.pressed.emit(self.clip_id, flags)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_pressed.emit(self.clip_id)
        super().mouseDoubleClickEvent(event)


class ThumbnailGrid(QScrollArea):
    selection_changed = Signal()
    activated = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("thumbnailGrid")
        self.setWidgetResizable(True)
        self.card_width = 180
        self._host = QWidget()
        self._host.setObjectName("gridHost")
        self.setWidget(self._host)
        self._cards: dict[str, ClipCard] = {}
        self._order: list[str] = []
        self._items: dict[str, dict] = {}
        self._selected: list[str] = []
        self._current: str | None = None
        self._anchor: str | None = None
        self._pixmaps: dict[str, QPixmap] = {}
        self._filter = ""

    def item_count(self) -> int:
        return len(self._order)

    def visible_ids(self) -> list[str]:
        return [clip_id for clip_id in self._order if self._matches(clip_id)]

    def selected_ids(self) -> list[str]:
        visible = set(self.visible_ids())
        return [clip_id for clip_id in self._order if clip_id in self._selected and clip_id in visible]

    def current_id(self) -> str | None:
        if self._current in self._selected:
            return self._current
        chosen = self.selected_ids()
        return chosen[-1] if chosen else None

    def set_filter(self, text: str) -> None:
        self._filter = text.strip().casefold()
        self._relayout()

    def set_card_width(self, width: int) -> None:
        self.card_width = max(140, min(240, int(width)))
        self._relayout()

    def set_items(self, items: list[dict]) -> None:
        incoming = {item["id"]: item for item in items}
        if list(incoming) == self._order and all(self._items.get(key) == incoming[key] for key in incoming):
            return
        self._items = incoming
        self._order = [item["id"] for item in items]
        for clip_id in list(self._cards):
            if clip_id not in incoming:
                self._cards.pop(clip_id).deleteLater()
                self._pixmaps.pop(clip_id, None)
        self._selected = [clip_id for clip_id in self._selected if clip_id in incoming and not incoming[clip_id].get("error")]
        if self._current not in incoming:
            self._current = None
        for item in items:
            card = self._cards.get(item["id"])
            meta = item.get("meta") or ""
            if card is None:
                card = ClipCard(item["id"], item.get("name") or item["id"], meta)
                card.setParent(self._host)
                card.pressed.connect(self._on_pressed)
                card.double_pressed.connect(self.activated.emit)
                self._cards[item["id"]] = card
            else:
                card.name.setText(item.get("name") or item["id"])
                card.meta.setText(meta)
            if item.get("error"):
                card.set_message(item["error"])
            elif item["id"] in self._pixmaps:
                card.set_pixmap(self._pixmaps[item["id"]])
            else:
                card.set_message("Reading")
        self._apply_selection()
        self._relayout()

    def set_thumbnail(self, clip_id: str, pixmap: QPixmap) -> None:
        if clip_id not in self._cards or pixmap.isNull():
            return
        self._pixmaps[clip_id] = pixmap
        self._cards[clip_id].set_pixmap(pixmap)

    def set_card_error(self, clip_id: str, message: str) -> None:
        if clip_id not in self._cards:
            return
        self._pixmaps.pop(clip_id, None)
        self._cards[clip_id].set_message(message)

    def select_only(self, clip_id: str) -> None:
        if clip_id not in self._items or self._items[clip_id].get("error"):
            return
        self._selected = [clip_id]
        self._current = clip_id
        self._anchor = clip_id
        self._apply_selection()
        self.selection_changed.emit()

    def select_all(self) -> None:
        self._selected = [clip_id for clip_id in self.visible_ids() if not self._items[clip_id].get("error")]
        self._current = self._selected[-1] if self._selected else None
        self._anchor = self._selected[0] if self._selected else None
        self._apply_selection()
        self.selection_changed.emit()

    def clear_selection(self) -> None:
        self._selected = []
        self._current = None
        self._anchor = None
        self._apply_selection()
        self.selection_changed.emit()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout()

    def _matches(self, clip_id: str) -> bool:
        if not self._filter:
            return True
        item = self._items.get(clip_id) or {}
        return self._filter in (item.get("name") or "").casefold()

    def _on_pressed(self, clip_id: str, modifiers: int) -> None:
        if self._items.get(clip_id, {}).get("error"):
            return
        ctrl = bool(modifiers & 1)
        shift = bool(modifiers & 2)
        order = self.visible_ids()
        if shift and self._anchor in order and clip_id in order:
            start = order.index(self._anchor)
            end = order.index(clip_id)
            lo, hi = sorted((start, end))
            chosen = [item for item in order[lo : hi + 1] if not self._items[item].get("error")]
            self._selected = chosen
        elif ctrl:
            if clip_id in self._selected:
                self._selected = [item for item in self._selected if item != clip_id]
            else:
                self._selected = [*self._selected, clip_id]
            self._anchor = clip_id
        else:
            self._selected = [clip_id]
            self._anchor = clip_id
        self._current = clip_id if clip_id in self._selected else (self._selected[-1] if self._selected else None)
        self._apply_selection()
        self.selection_changed.emit()

    def _apply_selection(self) -> None:
        selected = set(self._selected)
        for clip_id, card in self._cards.items():
            card.set_selected(clip_id in selected)

    def _relayout(self) -> None:
        width = max(self.viewport().width(), self.card_width + 12)
        gap = 10
        columns = max(1, (width - gap) // (self.card_width + gap))
        card_h = int(self.card_width * 9 / 16) + 58
        visible = self.visible_ids()
        for card in self._cards.values():
            card.hide()
        for index, clip_id in enumerate(visible):
            row, column = divmod(index, columns)
            card = self._cards[clip_id]
            card.setGeometry(QRect(gap + column * (self.card_width + gap), gap + row * (card_h + gap), self.card_width, card_h))
            card.show()
            if clip_id in self._pixmaps:
                card.set_pixmap(self._pixmaps[clip_id])
        rows = (len(visible) + columns - 1) // columns if visible else 0
        self._host.setMinimumHeight(gap + rows * (card_h + gap))
        self._host.update()

    def card_geometry(self, clip_id: str) -> QPoint | None:
        card = self._cards.get(clip_id)
        if card is None or not card.isVisible():
            return None
        return card.mapTo(self, card.rect().center())
