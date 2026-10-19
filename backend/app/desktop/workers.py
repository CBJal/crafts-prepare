"""One background task at a time. Results go back to the GUI thread as signals."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from ..errors import CraftsError


def error_text(exc: BaseException) -> str:
    if isinstance(exc, CraftsError):
        details = exc.details or []
        extra = []
        for item in details:
            if isinstance(item, dict) and item.get("message"):
                name = item.get("clip_id") or "clip"
                extra.append(f"{name}: {item['message']}")
        if extra:
            return exc.message + "\n" + "\n".join(extra)
        return exc.message
    return str(exc)


class TaskThread(QThread):
    succeeded = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, token: int, fn) -> None:
        super().__init__()
        self.token = token
        self._fn = fn

    def run(self) -> None:
        try:
            value = self._fn()
        except Exception as exc:  # noqa: BLE001 — the UI reports the failure
            self.failed.emit(self.token, error_text(exc))
        else:
            self.succeeded.emit(self.token, value)
