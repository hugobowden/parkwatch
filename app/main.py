"""ParkWatch web server: REST API + static dashboard."""
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import analytics, db, demo
from .camera import FrameSource
from .detector import yolo_available
from .monitor import Monitor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = Path(__file__).resolve().parent.parent / "static"
monitor = Monitor()


def add_demo_camera(name: str = "Demo lot") -> int:
    cid = db.create_camera(name, "demo", detector="texture", interval_sec=2)
    db.replace_spaces(cid, demo.demo_spaces())
    demo.seed_history(cid)
    return cid


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not db.list_cameras():
        add_demo_camera()
    monitor.start_all()
    yield
    monitor.stop_all()


app = FastAPI(title="ParkWatch", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class CameraIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    source: str = Field(min_length=1)
    detector: str = Field("auto", pattern="^(auto|texture|yolo)$")
    interval_sec: float = Field(3, ge=0.5, le=600)
    sensitivity: float = Field(0.5, ge=0, le=1)


class CameraPatch(BaseModel):
    name: str | None = None
    source: str | None = None
    detector: str | None = Field(None, pattern="^(auto|texture|yolo)$")
    interval_sec: float | None = Field(None, ge=0.5, le=600)
    sensitivity: float | None = Field(None, ge=0, le=1)
    enabled: bool | None = None


class SpaceIn(BaseModel):
    id: int | None = None
    label: str = Field(min_length=1, max_length=20)
    points: list[list[float]] = Field(min_length=3)


class SourceTest(BaseModel):
    source: str


def _camera_or_404(camera_id: int) -> dict:
    cam = db.get_camera(camera_id)
    if not cam:
        raise HTTPException(404, "Camera not found")
    return cam


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/info")
def info():
    return {"yolo_available": yolo_available()}


@app.get("/api/cameras")
def list_cameras():
    return db.list_cameras()


@app.post("/api/cameras")
def create_camera(body: CameraIn):
    if body.source.strip() == "demo":
        cid = add_demo_camera(body.name)
    else:
        cid = db.create_camera(body.name, body.source.strip(), body.detector, body.interval_sec, body.sensitivity)
    monitor.start(cid)
    return db.get_camera(cid)


@app.patch("/api/cameras/{camera_id}")
def update_camera(camera_id: int, body: CameraPatch):
    _camera_or_404(camera_id)
    fields = body.model_dump(exclude_none=True)
    if "enabled" in fields:
        fields["enabled"] = int(fields["enabled"])
    db.update_camera(camera_id, fields)
    monitor.start(camera_id)  # restart with new settings (stops it if disabled)
    return db.get_camera(camera_id)


@app.delete("/api/cameras/{camera_id}")
def delete_camera(camera_id: int):
    _camera_or_404(camera_id)
    monitor.stop(camera_id)
    db.delete_camera(camera_id)
    return {"ok": True}


@app.get("/api/cameras/{camera_id}/frame.jpg")
def camera_frame(camera_id: int):
    _camera_or_404(camera_id)
    w = monitor.worker(camera_id)
    if not w or not w.jpeg:
        raise HTTPException(503, "No frame yet")
    return Response(w.jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/cameras/{camera_id}/spaces")
def get_spaces(camera_id: int):
    _camera_or_404(camera_id)
    return db.list_spaces(camera_id)


@app.put("/api/cameras/{camera_id}/spaces")
def put_spaces(camera_id: int, spaces: list[SpaceIn]):
    _camera_or_404(camera_id)
    for s in spaces:
        if any(len(p) != 2 or not (0 <= p[0] <= 1 and 0 <= p[1] <= 1) for p in s.points):
            raise HTTPException(422, f"Space {s.label}: points must be [x, y] between 0 and 1")
    db.replace_spaces(camera_id, [s.model_dump() for s in spaces])
    monitor.reload_spaces(camera_id)
    return db.list_spaces(camera_id)


@app.get("/api/status")
def status():
    out = []
    for cam in db.list_cameras():
        w = monitor.worker(cam["id"])
        if w:
            out.append(w.status())
        else:
            out.append({"camera_id": cam["id"], "name": cam["name"], "online": False,
                        "error": "Paused", "total": 0, "occupied": 0, "free": 0, "spaces": []})
    return out


@app.get("/api/insights/{camera_id}")
def get_insights(camera_id: int, days: int = 30):
    _camera_or_404(camera_id)
    return analytics.insights(camera_id, max(1, min(days, 365)))


@app.post("/api/test-source")
def test_source(body: SourceTest):
    """Try to grab one frame from a source before saving it as a camera."""
    src = FrameSource(body.source)
    try:
        deadline = time.time() + 10
        frame = None
        while time.time() < deadline:
            frame = src.latest()
            if frame is not None or (src.error and "://" not in body.source and not body.source.isdigit()):
                break
            time.sleep(0.25)
        if frame is None:
            raise HTTPException(400, src.error or "No frames received within 10 seconds")
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return Response(buf.tobytes(), media_type="image/jpeg")
    finally:
        src.close()
