import subprocess
import time

from app.process import kill_tree, spawn


def test_kill_tree_stops_only_the_child_group():
    proc = spawn(["python3", "-c", "import time; time.sleep(30)"])
    child = subprocess.Popen(["python3", "-c", "import time; time.sleep(30)"])
    time.sleep(0.2)
    kill_tree(proc)
    proc.wait(timeout=5)
    assert proc.returncode is not None
    assert child.poll() is None
    child.kill()
    child.wait(timeout=5)
