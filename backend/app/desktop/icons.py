"""Original action pictograms. Same 24px stroke, drawn for this app.

These are not from an icon font or a remote set. Qt paints them at several
pixel sizes so high-DPI screens stay sharp.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_WRAP = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
    'fill="none" stroke="{color}" stroke-width="1.75" stroke-linecap="round" '
    'stroke-linejoin="round">{body}</svg>'
)

_BODIES: dict[str, str] = {
    "new": '<path d="M7 3.5h7l4 4V20a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1z"/><path d="M14 3.5V8h4"/><path d="M12 11.5v6M9 14.5h6"/>',
    "open": '<path d="M3.5 8.5V6.8A1.3 1.3 0 0 1 4.8 5.5h4l1.6 1.8h8.8a1.3 1.3 0 0 1 1.3 1.3v9.1a1.3 1.3 0 0 1-1.3 1.3H4.8a1.3 1.3 0 0 1-1.3-1.3z"/>',
    "save": '<path d="M5 4.5h11.2L19.5 8v11.5a1 1 0 0 1-1 1H5.5a1 1 0 0 1-1-1v-14a1 1 0 0 1 1-1z"/><path d="M8 4.5V9h7"/><path d="M8 20v-5.5h8V20"/>',
    "save-as": '<path d="M4 5.5h8.2L15 8.2V13"/><path d="M7 5.5V8.5h5"/><path d="M14 15.5h6M17 12.5v6"/><path d="M4.5 19.5h8"/>',
    "import": '<path d="M5 6.5h14v11H5z"/><path d="M8 6.5v11M12 10v5M10 12.5h4"/>',
    "import-folder": '<path d="M3.5 8V6.8A1.2 1.2 0 0 1 4.7 5.6h3.6l1.5 1.6h8.5A1.2 1.2 0 0 1 19.5 8.4v8.2a1.2 1.2 0 0 1-1.2 1.2H4.7a1.2 1.2 0 0 1-1.2-1.2z"/><path d="M12 11v4.5M9.8 13.2H14.2"/>',
    "select": '<path d="M5 6.5h4M5 12h4M5 17.5h4"/><path d="M11 6.5l1.4 1.4L16 4.5"/><path d="M11 12l1.4 1.4L16 10"/><path d="M11 17.5l1.4 1.4L16 15.5"/>',
    "clear": '<path d="M7 7l10 10M17 7L7 17"/>',
    "play": '<path d="M9 7.2v9.6L16.5 12z"/>',
    "pause": '<path d="M8.5 7v10M15.5 7v10"/>',
    "frame-back": '<path d="M14.5 7.2L9 12l5.5 4.8z"/><path d="M8 7v10"/>',
    "frame-forward": '<path d="M9.5 7.2L15 12l-5.5 4.8z"/><path d="M16 7v10"/>',
    "mark-in": '<path d="M8 5.5v13"/><path d="M8 12h9"/><path d="M14 8.5L17.5 12 14 15.5"/>',
    "mark-out": '<path d="M16 5.5v13"/><path d="M16 12H7"/><path d="M10 8.5L6.5 12 10 15.5"/>',
    "playback": '<path d="M4.5 6.5h15v9h-15z"/><path d="M9 18.5h6"/><path d="M10.5 10.2v3.2L13.6 11.8z"/>',
    "still": '<path d="M5 6.5h14v11H5z"/><path d="M8 14.5l2.2-2.4 1.6 1.6 2.2-2.5 2 3.3"/><path d="M9 9.2h.1"/>',
    "adjusted": '<path d="M5 8h9M16.5 8h2.5"/><path d="M14 6.2v3.6"/><path d="M5 16h2.5M10 16h9"/><path d="M7.5 14.2v3.6"/>',
    "export": '<path d="M12 14.5V5"/><path d="M8.5 8.2L12 4.8l3.5 3.4"/><path d="M5.5 15.5v3h13v-3"/>',
    "gyro": '<path d="M7 8.5A5.5 5.5 0 0 1 16.2 7"/><path d="M14.2 4.8L16.4 7l-2.4 1.4"/><path d="M17 15.5A5.5 5.5 0 0 1 7.8 17"/><path d="M9.8 19.2L7.6 17l2.4-1.4"/>',
    "cancel": '<path d="M8 8l8 8M16 8l-8 8"/><circle cx="12" cy="12" r="7.25"/>',
    "retry": '<path d="M7 9.5A5 5 0 1 1 6.2 14"/><path d="M7 5.8v4h4"/>',
    "tools": '<path d="M14.5 6.2a3 3 0 0 0-3.8 3.8L5.2 15.5l3.3 3.3 5.5-5.5a3 3 0 0 0 3.8-3.8L15.6 11l-2.6-2.6z"/>',
    "sun": '<circle cx="12" cy="12" r="3.2"/><path d="M12 4.5v1.8M12 17.7v1.8M4.5 12h1.8M17.7 12h1.8M6.6 6.6l1.3 1.3M16.1 16.1l1.3 1.3M17.4 6.6l-1.3 1.3M7.9 16.1l-1.3 1.3"/>',
    "moon": '<path d="M15.5 5.2A6.8 6.8 0 1 0 18.8 14 5.4 5.4 0 0 1 15.5 5.2z"/>',
    "system": '<path d="M5 6.5h14v9H5z"/><path d="M9 18.5h6"/><path d="M12 15.5v3"/>',
    "help": '<circle cx="12" cy="12" r="7.25"/><path d="M9.6 9.4a2.4 2.4 0 1 1 3.2 2.2c-.7.3-1.3.9-1.3 1.7"/><path d="M12 16.8h.1"/>',
    "about": '<circle cx="12" cy="12" r="7.25"/><path d="M12 11v5"/><path d="M12 8h.1"/>',
    "log": '<path d="M7 4.5h8l3 3V19.5H7z"/><path d="M15 4.5V8h3"/><path d="M9.5 12h6M9.5 15.5h4"/>',
    "browse": '<circle cx="11" cy="11" r="4.5"/><path d="M14.5 14.5L18.5 18.5"/>',
    "copy": '<path d="M9 8.5h9V19H9z"/><path d="M6 15.5H5.5A.5.5 0 0 1 5 15V5.5h8.5"/>',
    "layout": '<path d="M5 5.5h6v13H5z"/><path d="M13 5.5h6v6h-6z"/><path d="M13 13.5h6v5h-6z"/>',
    "marker": '<path d="M12 4.5c2.4 0 4.2 1.8 4.2 4.1 0 3-4.2 8.4-4.2 8.4S7.8 11.6 7.8 8.6c0-2.3 1.8-4.1 4.2-4.1z"/><circle cx="12" cy="8.6" r="1.3"/>',
}

_SIZES = (16, 24, 32, 48)


def known_icons() -> tuple[str, ...]:
    return tuple(_BODIES)


def action_icon(name: str, theme: str) -> QIcon:
    """Theme-sensitive icon. Missing names return an empty icon."""
    body = _BODIES.get(name)
    icon = QIcon()
    if body is None:
        return icon
    dark = theme == "dark"
    normal = "#e8eef4" if dark else "#1c2430"
    active = "#ffffff" if dark else "#0e141b"
    disabled = "#6e7c8c" if dark else "#8b97a3"
    for size in _SIZES:
        icon.addPixmap(_render(body, normal, size), QIcon.Mode.Normal, QIcon.State.Off)
        icon.addPixmap(_render(body, active, size), QIcon.Mode.Active, QIcon.State.Off)
        icon.addPixmap(_render(body, disabled, size), QIcon.Mode.Disabled, QIcon.State.Off)
    return icon


def _render(body: str, color: str, size: int) -> QPixmap:
    svg = _WRAP.format(color=color, body=body)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return pixmap
