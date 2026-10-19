"""Loopback HTTP API and the built web UI."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .errors import CraftsError
from .filters import FilterSpec
from .runtime import frontend_dist
from .security import MAX_JSON_BYTES, assert_loopback_request
from .service import Service
from .version import APP_NAME, APP_VERSION


class ImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)


class ProjectPathBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)


class ProjectNameBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)


class MarkerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = None
    seconds: float = Field(ge=0, le=86400 * 12)
    label: str = Field(default="", max_length=200)


class ClipPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    flag: str | None = None
    notes: str | None = Field(default=None, max_length=8000)
    markers: list[MarkerBody] | None = None
    trim_in: float | None = None
    trim_out: float | None = None


class ExportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clip_id: str
    preset_id: str
    in_seconds: float = Field(ge=0)
    out_seconds: float = Field(gt=0)
    suffix: str = ""
    destination_dir: str
    audio: bool = True
    acknowledge_bit_depth_conversion: bool = False
    exposure: float = Field(default=0, ge=-1, le=1)
    saturation: float = Field(default=1, ge=0, le=3)
    lut_path: str | None = None
    rotate: str = "0"
    fit: str = "none"
    width: int | None = Field(default=None, ge=2, le=7680)
    height: int | None = Field(default=None, ge=2, le=4320)
    stabilization: str = "off"
    stab_shakiness: int = Field(default=5, ge=1, le=10)
    stab_accuracy: int = Field(default=15, ge=1, le=15)
    stab_smoothing: int = Field(default=10, ge=0, le=1000)
    stab_crop: str = "black"
    stab_zoom: int = Field(default=0, ge=-100, le=100)
    stab_optzoom: int = Field(default=0, ge=0, le=2)
    deshake_rx: int = Field(default=16, ge=0, le=64)
    deshake_ry: int = Field(default=16, ge=0, le=64)
    crf: int | None = Field(default=None, ge=0, le=51)
    use_vidstab_auto: bool = False


class GyroBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clip_id: str | None = None
    media_path: str | None = None
    project_path: str | None = None
    gyro_path: str | None = None
    preset_path: str | None = None
    output_path: str


class BatchExportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preset_id: str
    audio: bool = True
    acknowledge_bit_depth_conversion: bool = False
    exposure: float = Field(default=0, ge=-1, le=1)
    saturation: float = Field(default=1, ge=0, le=3)
    lut_path: str | None = None
    rotate: str = "0"
    fit: str = "none"
    width: int | None = Field(default=None, ge=2, le=7680)
    height: int | None = Field(default=None, ge=2, le=4320)
    stabilization: str = "off"
    stab_shakiness: int = Field(default=5, ge=1, le=10)
    stab_accuracy: int = Field(default=15, ge=1, le=15)
    stab_smoothing: int = Field(default=10, ge=0, le=1000)
    stab_crop: str = "black"
    stab_zoom: int = Field(default=0, ge=-100, le=100)
    stab_optzoom: int = Field(default=0, ge=0, le=2)
    deshake_rx: int = Field(default=16, ge=0, le=64)
    deshake_ry: int = Field(default=16, ge=0, le=64)
    crf: int | None = Field(default=None, ge=0, le=51)
    use_vidstab_auto: bool = False


class BatchGyroBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["h264_mp4", "prores_422"]
    audio: bool = True
    width: int | None = Field(default=None, ge=2, le=7680)
    height: int | None = Field(default=None, ge=2, le=4320)
    preset_path: str | None = None


class BatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["export", "gyro"]
    clip_ids: list[str] = Field(min_length=1, max_length=40)
    destination_dir: str = Field(min_length=1, max_length=4096)
    suffix: str = ""
    export: BatchExportBody | None = None
    gyro: BatchGyroBody | None = None


class SettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ffmpeg: str | None = None
    ffprobe: str | None = None
    gyroflow: str | None = None
    telemetry_parser: str | None = None


class GcsvBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str


def create_app() -> FastAPI:
    service = Service()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.service.start()
        yield
        app.state.service.shutdown()

    app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)
    app.state.service = service

    @app.middleware("http")
    async def loopback_only(request: Request, call_next):
        length = request.headers.get("content-length")
        if length is not None:
            try:
                size = int(length)
            except ValueError:
                return JSONResponse({"detail": "Bad content length.", "code": "bad_request"}, status_code=400)
            if size > MAX_JSON_BYTES:
                return JSONResponse({"detail": "Payload too large.", "code": "too_large"}, status_code=413)
        try:
            assert_loopback_request(request.headers.get("host"), request.headers.get("origin"))
        except CraftsError as exc:
            return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=exc.status)
        return await call_next(request)

    @app.exception_handler(CraftsError)
    async def crafts_error(_request: Request, exc: CraftsError):
        body: dict = {"detail": exc.message, "code": exc.code}
        if exc.details:
            body["problems"] = exc.details
        return JSONResponse(body, status_code=exc.status)

    @app.get("/api/health")
    def health():
        return {"ok": True, "name": APP_NAME, "version": APP_VERSION, "bind": "127.0.0.1"}

    @app.get("/api/capabilities")
    def capabilities():
        return service.capabilities()

    @app.put("/api/settings")
    def update_settings(body: SettingsBody):
        return service.update_settings(body.model_dump())

    @app.get("/api/project")
    def project():
        return service.project_summary()

    @app.post("/api/project/open")
    def open_project(body: ProjectPathBody):
        return service.open_project(body.path)

    @app.post("/api/project/save")
    def save_project(body: ProjectPathBody):
        return service.save_project(body.path)

    @app.post("/api/project/name")
    def rename_project(body: ProjectNameBody):
        return service.rename_project(body.name)

    @app.post("/api/media/import")
    def import_media(body: ImportBody):
        return service.import_media(body.path)

    @app.get("/api/media")
    def media():
        return {"clips": service.clips()}

    @app.get("/api/media/{clip_id}")
    def one_clip(clip_id: str):
        return service.clip(clip_id)

    @app.patch("/api/media/{clip_id}")
    def patch_clip(clip_id: str, body: ClipPatch):
        payload = body.model_dump()
        if payload.get("markers") is not None:
            payload["markers"] = body.model_dump()["markers"]
        return service.update_clip(clip_id, payload)

    @app.get("/api/media/{clip_id}/thumbnail")
    def thumbnail(
        clip_id: str,
        t: float = 0,
        exposure: float = 0,
        saturation: float = 1,
        lut: str | None = None,
        rotate: str = "0",
        fit: str = "none",
        width: int | None = None,
        height: int | None = None,
    ):
        spec = FilterSpec(
            exposure=exposure,
            saturation=saturation,
            lut_path=lut,
            rotate=rotate,
            fit=fit,
            width=width,
            height=height,
            include_temporal=False,
            stabilization="off",
        )
        path = service.thumbnail(clip_id, t, spec)
        return FileResponse(path, media_type="image/jpeg", filename="preview.jpg")

    @app.get("/api/media/{clip_id}/file")
    def media_file(clip_id: str):
        path = service.stream_path(clip_id)
        return FileResponse(path, filename=path.name)

    @app.post("/api/media/{clip_id}/proxy")
    def make_proxy_route(clip_id: str):
        return service.proxy(clip_id)

    @app.get("/api/media/{clip_id}/proxy-file")
    def proxy_file(clip_id: str):
        path = service.proxy_file(clip_id)
        return FileResponse(path, media_type="video/mp4", filename=path.name)

    @app.post("/api/media/{clip_id}/telemetry")
    def telemetry(clip_id: str):
        return service.inspect_telemetry(clip_id)

    @app.post("/api/gcsv/parse")
    def parse_gcsv(body: GcsvBody):
        return service.parse_gcsv(body.path)

    @app.get("/api/jobs")
    def jobs():
        return {"jobs": service.jobs()}

    @app.post("/api/jobs/export")
    def export_job(body: ExportBody):
        return service.enqueue_export(body.model_dump())

    @app.post("/api/jobs/gyroflow")
    def gyro_job(body: GyroBody):
        return service.enqueue_gyro(body.model_dump())

    @app.post("/api/jobs/batch")
    def batch_job(body: BatchBody):
        payload = body.model_dump()
        if body.operation == "export" and body.export is None:
            raise CraftsError("Export batch settings are missing.", code="bad_batch")
        if body.operation == "gyro" and body.gyro is None:
            raise CraftsError("Gyro batch settings are missing.", code="bad_batch")
        return service.enqueue_batch(payload)

    @app.get("/api/jobs/batch/{batch_id}")
    def batch_status(batch_id: str):
        return service.batch_status(batch_id)

    @app.post("/api/jobs/batch/{batch_id}/cancel")
    def batch_cancel(batch_id: str):
        return service.cancel_batch(batch_id)

    @app.post("/api/jobs/batch/{batch_id}/retry")
    def batch_retry(batch_id: str):
        return service.retry_batch_failures(batch_id)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        return service.cancel(job_id)

    @app.post("/api/jobs/{job_id}/retry")
    def retry(job_id: str):
        return service.retry(job_id)

    @app.post("/api/jobs/drain")
    def drain():
        service.drain()
        return {"jobs": service.jobs()}

    @app.get("/")
    def index():
        page = frontend_dist() / "index.html"
        if page.is_file():
            return FileResponse(page)
        return JSONResponse(
            {
                "detail": "The web UI has not been built. From frontend/, run npm ci && npm run build.",
                "api": "/api/health",
            },
            status_code=503,
        )

    assets = frontend_dist() / "assets"
    if assets.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    return app


def main() -> None:
    import uvicorn

    host = os.environ.get("CRAFTS_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Crafts Prepare binds to 127.0.0.1 only.")
    port = int(os.environ.get("CRAFTS_PORT", "8765"))
    if not 1 <= port <= 65535:
        raise SystemExit("CRAFTS_PORT is invalid.")
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
