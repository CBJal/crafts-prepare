"""Places column: recent folders, favorites, and local drives."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QListWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget


def local_places() -> list[tuple[str, str]]:
    places: list[tuple[str, str]] = [("Home", str(Path.home()))]
    if os.name == "nt":
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            root = Path(f"{letter}:/")
            if os.path.exists(root):
                places.append((f"Drive {letter}:", str(root)))
        return places
    places.append(("Computer", "/"))
    for base in (Path("/media"), Path("/mnt"), Path("/run/media"), Path.home() / "Desktop"):
        if not base.is_dir():
            continue
        try:
            children = sorted(path for path in base.iterdir() if path.is_dir() and not path.name.startswith("."))
        except OSError:
            continue
        for child in children[:12]:
            places.append((child.name, str(child)))
    return places


class PlacesPanel(QWidget):
    folder_chosen = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("placesPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(QLabel("Places"))
        self.places = QListWidget()
        self.places.setObjectName("placesList")
        self.places.itemClicked.connect(self._place_clicked)
        layout.addWidget(self.places, 1)
        layout.addWidget(QLabel("Folder"))
        self.tree = QTreeWidget()
        self.tree.setObjectName("folderTree")
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(self._tree_clicked)
        self.tree.itemExpanded.connect(self._expand)
        layout.addWidget(self.tree, 2)
        self.set_lists([], [])

    def set_lists(self, recent: list[str], favorites: list[str]) -> None:
        self.places.clear()
        for label, folders in (("Recent", recent), ("Favorites", favorites)):
            for folder in folders:
                self.places.addItem(f"{label}: {Path(folder).name or folder}")
                self.places.item(self.places.count() - 1).setData(Qt.ItemDataRole.UserRole, folder)
        for label, folder in local_places():
            self.places.addItem(label)
            self.places.item(self.places.count() - 1).setData(Qt.ItemDataRole.UserRole, folder)

    def show_tree(self, folder: str) -> None:
        self.tree.clear()
        root = QTreeWidgetItem([Path(folder).name or folder])
        root.setData(0, Qt.ItemDataRole.UserRole, folder)
        root.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        self.tree.addTopLevelItem(root)
        self._fill(root, Path(folder))
        root.setExpanded(True)

    def _place_clicked(self, item) -> None:
        folder = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(folder, str):
            self.show_tree(folder)
            self.folder_chosen.emit(folder)

    def _tree_clicked(self, item, _column) -> None:
        folder = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(folder, str):
            self.folder_chosen.emit(folder)

    def _expand(self, item) -> None:
        folder = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(folder, str):
            self._fill(item, Path(folder))

    def _fill(self, item: QTreeWidgetItem, folder: Path) -> None:
        if item.childCount():
            return
        try:
            children = sorted(path for path in folder.iterdir() if path.is_dir() and not path.name.startswith("."))
        except OSError:
            return
        for child in children[:80]:
            node = QTreeWidgetItem([child.name])
            node.setData(0, Qt.ItemDataRole.UserRole, str(child))
            node.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
            item.addChild(node)
