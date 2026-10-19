import os
import subprocess
import sys
import threading
import time

from app.process import CREATE_NO_WINDOW, isolate_external_dll_search, kill_tree, spawn, windows_creation_flags, windows_no_window_flag


def test_windows_flags_include_create_no_window():
    assert windows_no_window_flag() & CREATE_NO_WINDOW == CREATE_NO_WINDOW
    assert windows_creation_flags() & CREATE_NO_WINDOW == CREATE_NO_WINDOW


def test_taskkill_requests_no_window(monkeypatch):
    captured: dict = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0)

    class Child:
        pid = 4321

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(subprocess, "run", fake_run)
    kill_tree(Child())
    assert captured["args"][:4] == ["taskkill", "/F", "/T", "/PID"]
    assert captured["kwargs"]["creationflags"] & CREATE_NO_WINDOW == CREATE_NO_WINDOW
    assert captured["kwargs"].get("shell") is None


def test_spawn_clears_and_restores_the_dll_directory(monkeypatch):
    class Controller:
        def __init__(self) -> None:
            self.value = r"C:\bundle"
            self.seen: list[str | None] = []

        def get(self):
            return self.value

        def set(self, value) -> None:
            self.seen.append(value)
            self.value = value

    controller = Controller()
    monkeypatch.setattr("app.process._dll_controller", controller)
    captured: dict = {}

    class FakeProcess:
        def __init__(self, args, **kwargs):
            captured["during"] = controller.value
            captured["shell"] = kwargs.get("shell")
            captured["args"] = args
            self.stdout = None
            self.stderr = None

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    spawn(["ffmpeg", "-version"])
    assert captured["during"] is None
    assert captured["shell"] is None
    assert captured["args"] == ["ffmpeg", "-version"]
    assert controller.value == r"C:\bundle"
    assert controller.seen == [None, r"C:\bundle"]


def test_concurrent_dll_resets_do_not_tear_the_directory(monkeypatch):
    class Controller:
        def __init__(self) -> None:
            self.value = "bundle"
            self.guard = threading.Lock()

        def get(self):
            return self.value

        def set(self, value) -> None:
            time.sleep(0.02)
            with self.guard:
                self.value = value

    controller = Controller()
    monkeypatch.setattr("app.process._dll_controller", controller)
    seen: list[str | None] = []
    seen_lock = threading.Lock()

    def work() -> None:
        with isolate_external_dll_search():
            with seen_lock:
                seen.append(controller.value)
            time.sleep(0.05)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert seen == [None, None, None, None]
    assert controller.value == "bundle"


def test_kill_tree_stops_only_the_child_group():
    proc = spawn([sys.executable, "-c", "import time; time.sleep(30)"])
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    time.sleep(0.2)
    kill_tree(proc)
    proc.wait(timeout=5)
    assert proc.returncode is not None
    assert child.poll() is None
    child.kill()
    child.wait(timeout=5)
