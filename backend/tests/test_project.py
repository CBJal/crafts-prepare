import json
import threading

from app.security import MAX_PROJECT_BYTES
from app.service import Service
from tests.test_prepare import import_clip


def test_malformed_project_leaves_the_open_one(client, media_dir, tmp_path):
    clip = import_clip(client, media_dir / "h264_8.mp4")
    bad = tmp_path / "bad.crafts.json"
    bad.write_text(json.dumps({"schema": "crafts-prepare.project", "version": 1, "name": "Bad", "clips": "nope", "jobs": []}))
    response = client.post("/api/project/open", json={"path": str(bad)})
    assert response.status_code == 400
    assert response.json()["code"] == "bad_project"
    still = client.get("/api/media").json()["clips"]
    assert still[0]["id"] == clip["id"]
    unknown = tmp_path / "unknown-status.crafts.json"
    current = tmp_path / "current.crafts.json"
    assert client.post("/api/project/save", json={"path": str(current)}).status_code == 200
    payload = json.loads(current.read_text())
    payload["jobs"].append(
        {"id": "evil", "kind": "export", "status": "launch-now", "progress": 0, "request": {"clip_id": clip["id"]}}
    )
    unknown.write_text(json.dumps(payload))
    response = client.post("/api/project/open", json={"path": str(unknown)})
    assert response.status_code == 400
    assert client.get("/api/jobs").json()["jobs"] == []
    assert client.get("/api/media").json()["clips"][0]["id"] == clip["id"]
    assert client.get("/api/project").json()["clips"][0]["id"] == clip["id"]


def _saved_payload(client, tmp_path, clip_id):
    path = tmp_path / "baseline.crafts.json"
    assert client.post("/api/project/save", json={"path": str(path)}).status_code == 200
    return path, json.loads(path.read_text(encoding="utf-8"))


def _reject_open(client, path, clip_id):
    response = client.post("/api/project/open", json={"path": str(path)})
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "bad_project"
    media = client.get("/api/media")
    project = client.get("/api/project")
    assert media.status_code == 200
    assert project.status_code == 200
    assert media.json()["clips"][0]["id"] == clip_id
    assert project.json()["clips"][0]["id"] == clip_id
    assert project.json()["path"]


def test_malformed_probe_trim_markers_and_job_do_not_replace_state(client, media_dir, tmp_path):
    clip = import_clip(client, media_dir / "h264_8.mp4")
    base, payload = _saved_payload(client, tmp_path, clip["id"])
    original_path = client.get("/api/project").json()["path"]

    nested = json.loads(json.dumps(payload))
    nested["clips"][0]["probe"]["video"] = ["invalid-video-entry"]
    nested["clips"][0]["trim_in"] = -8
    nested["clips"][0]["trim_out"] = -2
    bad_probe = tmp_path / "bad-probe.crafts.json"
    bad_probe.write_text(json.dumps(nested), encoding="utf-8")
    _reject_open(client, bad_probe, clip["id"])

    bad_markers = json.loads(json.dumps(payload))
    bad_markers["clips"][0]["markers"] = ["not-a-marker", {"label": "missing time"}]
    marker_path = tmp_path / "bad-markers.crafts.json"
    marker_path.write_text(json.dumps(bad_markers), encoding="utf-8")
    _reject_open(client, marker_path, clip["id"])

    bad_job = json.loads(json.dumps(payload))
    bad_job["jobs"] = [
        {
            "id": "empty-export",
            "kind": "export",
            "status": "paused",
            "progress": 0,
            "attempt": 1,
            "request": {},
        }
    ]
    job_path = tmp_path / "bad-job.crafts.json"
    job_path.write_text(json.dumps(bad_job), encoding="utf-8")
    _reject_open(client, job_path, clip["id"])

    garbled = tmp_path / "garbled.crafts.json"
    garbled.write_bytes(b"\xff\xfe not utf-8")
    response = client.post("/api/project/open", json={"path": str(garbled)})
    assert response.status_code == 400
    assert response.json()["code"] == "bad_project"
    assert client.get("/api/project").json()["path"] == original_path
    assert client.get("/api/media").json()["clips"][0]["id"] == clip["id"]
    assert base.is_file()


def test_missing_probe_collections_and_unrenderable_job_fields(client, media_dir, tmp_path):
    clip = import_clip(client, media_dir / "h264_8.mp4")
    _base, payload = _saved_payload(client, tmp_path, clip["id"])
    original_path = client.get("/api/project").json()["path"]

    def reject(name: str, mutate) -> None:
        body = json.loads(json.dumps(payload))
        mutate(body)
        path = tmp_path / f"{name}.crafts.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        _reject_open(client, path, clip["id"])
        assert client.get("/api/project").json()["path"] == original_path

    reject("empty-probe", lambda body: body["clips"][0].__setitem__("probe", {}))
    for key in ("video", "audio", "data"):
        reject(f"missing-{key}", lambda body, key=key: body["clips"][0]["probe"].pop(key))
        reject(f"null-{key}", lambda body, key=key: body["clips"][0]["probe"].__setitem__(key, None))
        reject(f"type-{key}", lambda body, key=key: body["clips"][0]["probe"].__setitem__(key, {"not": "a list"}))
    reject("null-video-entry", lambda body: body["clips"][0]["probe"].__setitem__("video", [None]))
    reject("empty-video", lambda body: body["clips"][0]["probe"].__setitem__("video", []))

    reject(
        "bad-kind",
        lambda body: body["jobs"].append(
            {"id": "bad-kind", "kind": {"export": True}, "status": "paused", "progress": 0, "attempt": 1, "request": {}}
        ),
    )
    reject(
        "bad-status",
        lambda body: body["jobs"].append(
            {"id": "bad-status", "kind": "export", "status": ["paused"], "progress": 0, "attempt": 1, "request": {}}
        ),
    )
    request = {
        "clip_id": clip["id"],
        "preset_id": {"id": "h264_mp4"},
        "in_seconds": 0,
        "out_seconds": 0.4,
        "destination_dir": str(tmp_path),
        "filename": "clip.mp4",
        "audio": False,
    }
    reject(
        "bad-preset",
        lambda body: body["jobs"].append(
            {"id": "bad-preset", "kind": "export", "status": "paused", "progress": 0, "attempt": 1, "request": request}
        ),
    )
    bad_result = {
        "output_path": {"path": "out.mp4"},
        "conversion": {"message": {"text": "converted"}},
        "stabilization": {"label": {"text": "vidstab"}, "mode": "vidstab", "kind": "visual"},
    }
    named = dict(request)
    named["preset_id"] = "h264_mp4"
    named["filename"] = {"name": "clip.mp4"}
    reject(
        "bad-rendered-fields",
        lambda body: body["jobs"].append(
            {
                "id": "bad-rendered",
                "kind": "export",
                "status": "succeeded",
                "progress": 1,
                "attempt": 1,
                "request": named,
                "result": bad_result,
            }
        ),
    )


def test_save_refuses_empty_foreign_and_oversize_files(client, tmp_path):
    empty = tmp_path / "empty.crafts.json"
    empty.write_bytes(b"")
    response = client.post("/api/project/save", json={"path": str(empty)})
    assert response.status_code == 400
    assert empty.read_bytes() == b""
    foreign = tmp_path / "notes.crafts.json"
    foreign.write_text("not a project", encoding="utf-8")
    response = client.post("/api/project/save", json={"path": str(foreign)})
    assert response.status_code == 400
    assert foreign.read_text(encoding="utf-8") == "not a project"
    huge = tmp_path / "huge.crafts.json"
    huge.write_bytes(b"x" * (MAX_PROJECT_BYTES + 1))
    response = client.post("/api/project/save", json={"path": str(huge)})
    assert response.status_code == 400
    assert huge.stat().st_size == MAX_PROJECT_BYTES + 1


def test_opened_queue_stays_paused_until_retry(client, media_dir, tmp_path):
    clip = import_clip(client, media_dir / "h264_8.mp4")
    dest = tmp_path / "out"
    dest.mkdir()
    queued = client.post(
        "/api/jobs/export",
        json={
            "clip_id": clip["id"],
            "preset_id": "h264_mp4",
            "in_seconds": 0,
            "out_seconds": 0.4,
            "destination_dir": str(dest),
            "suffix": "_later",
            "audio": False,
        },
    )
    assert queued.status_code == 200, queued.text
    path = tmp_path / "paused.crafts.json"
    assert client.post("/api/project/save", json={"path": str(path)}).status_code == 200
    restored = Service().open_project(str(path))
    assert restored["jobs"][0]["status"] == "paused"
    assert restored["jobs"][0]["code"] == "paused"
    other = Service()
    other.open_project(str(path))
    other.drain()
    assert other.jobs()[0]["status"] == "paused"


def test_project_switch_rejected_while_a_job_is_running(client, media_dir, tmp_path, monkeypatch):
    from app.errors import Cancelled

    clip = import_clip(client, media_dir / "h264_8.mp4")
    dest = tmp_path / "out"
    dest.mkdir()
    project = tmp_path / "live.crafts.json"
    assert client.post("/api/project/save", json={"path": str(project)}).status_code == 200
    service = client.app.state.service
    started = threading.Event()

    def slow(request, cancel, job_id):
        started.set()
        for _ in range(200):
            if cancel.is_set():
                raise Cancelled()
            threading.Event().wait(0.02)
        raise AssertionError("cancel did not arrive")

    monkeypatch.setattr(service, "_run_export_job", slow)
    queued = client.post(
        "/api/jobs/export",
        json={
            "clip_id": clip["id"],
            "preset_id": "h264_mp4",
            "in_seconds": 0,
            "out_seconds": 0.4,
            "destination_dir": str(dest),
            "suffix": "_busy",
            "audio": False,
        },
    )
    assert queued.status_code == 200, queued.text
    worker = threading.Thread(target=service.drain)
    worker.start()
    assert started.wait(3)
    response = client.post("/api/project/open", json={"path": str(project)})
    assert response.status_code == 409
    assert response.json()["code"] == "busy_project"
    assert client.get(f"/api/media/{clip['id']}").status_code == 200
    running = client.get("/api/jobs").json()["jobs"]
    assert any(job["status"] == "running" for job in running)
    client.post(f"/api/jobs/{queued.json()['id']}/cancel")
    worker.join(5)
