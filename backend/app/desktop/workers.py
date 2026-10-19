"""One background thread for import, probe, proxy, and still work.

A new request with the same key cancels the previous one. Close and project
switches cancel the current child process and wait until the thread is idle.
"""

from __future__ import annotations

import queue
import threading
import time

from PySide6.QtCore import QThread, Signal

from ..errors import Cancelled, CraftsError


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


class TaskRunner(QThread):
    succeeded = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self) -> None:
        super().__init__()
        self._queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._current: threading.Event | None = None
        self._keyed: dict[str, threading.Event] = {}
        self.running_pid: int | None = None

    def submit(self, token: int, fn, key: str | None = None) -> threading.Event:
        event = threading.Event()
        with self._lock:
            if key:
                previous = self._keyed.get(key)
                if previous is not None:
                    previous.set()
                self._keyed[key] = event
            if self._current is not None and key and self._keyed.get(key) is event:
                pass
        self._queue.put((token, fn, event, key))
        return event

    def cancel_pending(self) -> None:
        with self._lock:
            if self._current is not None:
                self._current.set()
            for event in self._keyed.values():
                event.set()
            self._keyed.clear()
        tokens: list[int] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                continue
            token, _fn, event, _key = item
            event.set()
            tokens.append(token)
        for token in tokens:
            self.failed.emit(token, "cancelled")

    def settle(self, timeout: float = 8.0) -> bool:
        """Cancel queued and active work and wait until none is running."""
        self.cancel_pending()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                idle = self._current is None and self._queue.empty()
            if idle:
                return True
            time.sleep(0.02)
        with self._lock:
            return self._current is None and self._queue.empty()

    def shutdown(self, timeout: float = 8.0) -> bool:
        self.settle(timeout=max(0.2, timeout / 2))
        self._stop.set()
        self._queue.put(None)
        self.wait(int(timeout * 1000))
        return not self.isRunning()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is None:
                break
            token, fn, event, key = item
            if event.is_set():
                self.failed.emit(token, "cancelled")
                continue
            with self._lock:
                self._current = event
            try:
                value = fn(event)
            except Cancelled:
                self.failed.emit(token, "cancelled")
            except Exception as exc:  # noqa: BLE001 — shown in the window
                self.failed.emit(token, error_text(exc))
            else:
                if event.is_set():
                    self.failed.emit(token, "cancelled")
                else:
                    self.succeeded.emit(token, value)
            finally:
                with self._lock:
                    if self._keyed.get(key) is event:
                        self._keyed.pop(key, None)
                    self._current = None
                    self.running_pid = None
