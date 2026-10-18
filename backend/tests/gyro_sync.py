"""Bounded waits for a fake Gyroflow process, with the job's terminal state included."""

import os
import threading
import time
from pathlib import Path

_TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}


def job_view(client, job_id: str) -> dict:
    service = client.app.state.service
    with service.lock:
        job = next(item for item in service.project["jobs"] if item["id"] == job_id)
        error = job.get("error")
        if isinstance(error, str) and len(error) > 500:
            error = error[:500]
        return {"status": job.get("status"), "code": job.get("code"), "error": error}


def wait_for_fake_render(client, job_id: str, ready: Path, *, timeout: float = 20.0) -> dict:
    """Wait until `ready` exists while the job is still active, or fail with job diagnostics."""
    started = time.monotonic()
    last = {"status": None, "code": None, "error": None}
    while True:
        elapsed = time.monotonic() - started
        if ready.is_file():
            last = job_view(client, job_id)
            if last["status"] in _TERMINAL:
                raise AssertionError(_diag("fake render file appeared after the job finished", last, ready, elapsed))
            return last
        last = job_view(client, job_id)
        if last["status"] in _TERMINAL:
            raise AssertionError(_diag("job finished before the fake render started", last, ready, elapsed))
        if elapsed >= timeout:
            raise AssertionError(_diag(f"fake render did not start within {timeout:.0f}s", last, ready, elapsed))
        threading.Event().wait(0.05)


def _diag(reason: str, job: dict, ready: Path, elapsed: float) -> str:
    return (
        f"{reason} after {elapsed:.2f}s: status={job.get('status')} code={job.get('code')} "
        f"error={job.get('error')!r} mode={os.environ.get('GYRO_MODE')!r} "
        f"ready={ready} exists={ready.exists()}"
    )
