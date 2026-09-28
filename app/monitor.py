"""Background workers: one per camera, grabbing frames and tracking space states."""
import logging
import threading
import time

import cv2
import numpy as np

from . import db
from .camera import FrameSource
from .detector import analyse

log = logging.getLogger("parkwatch.monitor")

OCCUPIED_ABOVE = 0.65   # probability needed to switch a space to occupied
FREE_BELOW = 0.35       # probability needed to switch it back to free
CONFIRM_READINGS = 2    # consecutive readings needed before a switch
SAMPLE_EVERY_SEC = 60   # how often lot-wide occupancy is written for insights


MOVED_FRACTION = 0.012  # centre shift (fraction of image width) that counts as moving


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _in_any_space(x: float, y: float, spaces: list[dict]) -> bool:
    for s in spaces:
        poly = np.array(s["points"], np.float32)
        if cv2.pointPolygonTest(poly, (x, y), False) >= 0:
            return True
    return False


def mark_moving(vehicles: list[dict], previous: list[dict] | None, spaces: list[dict]) -> None:
    """Flag each vehicle as moving or not by comparing with the previous check."""
    for v in vehicles:
        v["moving"] = False
        if previous is None:
            continue
        best = max(previous, key=lambda p: _iou(v["box"], p["box"]), default=None)
        cx, cy = (v["box"][0] + v["box"][2]) / 2, (v["box"][1] + v["box"][3]) / 2
        if best is not None and _iou(v["box"], best["box"]) > 0.2:
            px, py = (best["box"][0] + best["box"][2]) / 2, (best["box"][1] + best["box"][3]) / 2
            v["moving"] = ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5 > MOVED_FRACTION
        else:
            # Newly seen: if it's not sitting in a bay it has just driven into view.
            ground_y = v["box"][1] + (v["box"][3] - v["box"][1]) * 0.8
            v["moving"] = not _in_any_space(cx, ground_y, spaces)


class CameraWorker:
    def __init__(self, camera: dict):
        self.camera = camera
        self.source = FrameSource(camera["source"])
        self.spaces: list[dict] = []
        self.state: dict[int, dict] = {}   # space_id -> {occupied, prob, streak, since}
        self.jpeg: bytes | None = None
        self.frame_size = (0, 0)
        self.vehicles: list[dict] | None = None   # None when the detector can't box vehicles
        self._prev_vehicles: list[dict] | None = None
        self.last_run = 0.0
        self.error: str | None = None
        self._last_sample = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.reload_spaces()
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"cam-{camera['id']}")
        self._thread.start()

    def reload_spaces(self) -> None:
        spaces = db.list_spaces(self.camera["id"])
        with self._lock:
            self.spaces = spaces
            ids = {s["id"] for s in spaces}
            self.state = {k: v for k, v in self.state.items() if k in ids}

    def _detector(self) -> str:
        # The demo lot is drawn, not photographed, so a real vehicle model won't recognise it.
        return "texture" if self.camera["source"] == "demo" else self.camera["detector"]

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.time()
            try:
                self._tick()
            except Exception as e:
                log.exception("camera %s tick failed", self.camera["id"])
                self.error = f"Processing error: {e}"
            self._stop.wait(max(0.2, self.camera["interval_sec"] - (time.time() - started)))

    def _tick(self) -> None:
        frame = self.source.latest()
        self.error = self.source.error
        if frame is None:
            return
        h, w = frame.shape[:2]
        scale = min(1.0, 1280 / w)
        small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1 else frame

        with self._lock:
            spaces = list(self.spaces)
        # Spaces are stored as fractions of the image, so the downscaled frame works just as
        # well and is far cheaper to analyse on a Raspberry Pi.
        probs, vehicles = analyse(small, spaces, self._detector(), self.camera["sensitivity"])
        if vehicles is not None:
            mark_moving(vehicles, self._prev_vehicles, spaces)
        self._prev_vehicles = vehicles
        ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 80])
        now = time.time()
        with self._lock:
            # Picture and boxes are published together so the boxes always match the picture.
            if ok:
                self.jpeg = buf.tobytes()
                self.frame_size = (small.shape[1], small.shape[0])
            self.vehicles = vehicles
            for space, p in zip(spaces, probs):
                self._update_space(space["id"], p, now)
            occupied = sum(1 for s in self.state.values() if s["occupied"])
            total = len(spaces)
            self.last_run = now
        if total and now - self._last_sample >= SAMPLE_EVERY_SEC:
            db.add_sample(self.camera["id"], now, occupied, total)
            self._last_sample = now

    def _update_space(self, space_id: int, p: float, now: float) -> None:
        st = self.state.get(space_id)
        if st is None:  # first reading: trust it straight away
            occ = p >= 0.5
            self.state[space_id] = {"occupied": occ, "prob": p, "streak": 0, "since": now}
            db.add_event(space_id, now, occ)
            return
        st["prob"] = p
        wants_change = (p >= OCCUPIED_ABOVE) if not st["occupied"] else (p <= FREE_BELOW)
        st["streak"] = st["streak"] + 1 if wants_change else 0
        if st["streak"] >= CONFIRM_READINGS:
            st["occupied"] = not st["occupied"]
            st["streak"] = 0
            st["since"] = now
            db.add_event(space_id, now, st["occupied"])

    def status(self) -> dict:
        with self._lock:
            vehicles = self.vehicles
            last_run, frame_size = self.last_run, self.frame_size
            spaces = [
                {
                    "id": s["id"],
                    "label": s["label"],
                    "points": s["points"],
                    "occupied": self.state.get(s["id"], {}).get("occupied"),
                    "confidence": round(self.state.get(s["id"], {}).get("prob", 0), 3),
                    "since": self.state.get(s["id"], {}).get("since"),
                }
                for s in self.spaces
            ]
        known = [s for s in spaces if s["occupied"] is not None]
        return {
            "camera_id": self.camera["id"],
            "name": self.camera["name"],
            "online": self.jpeg is not None and self.error is None,
            "error": self.error,
            "last_update": last_run or None,
            "frame_size": frame_size,
            "total": len(spaces),
            "occupied": sum(1 for s in known if s["occupied"]),
            "free": sum(1 for s in known if not s["occupied"]),
            "spaces": spaces,
            "vehicles": vehicles,
        }

    def stop(self) -> None:
        self._stop.set()
        self.source.close()


class Monitor:
    """Owns all camera workers."""

    def __init__(self):
        self.workers: dict[int, CameraWorker] = {}
        self._lock = threading.Lock()

    def start_all(self) -> None:
        for cam in db.list_cameras():
            if cam["enabled"]:
                self.start(cam["id"])

    def start(self, camera_id: int) -> None:
        cam = db.get_camera(camera_id)
        with self._lock:
            old = self.workers.pop(camera_id, None)
            if old:
                old.stop()
            if cam and cam["enabled"]:
                self.workers[camera_id] = CameraWorker(cam)

    def stop(self, camera_id: int) -> None:
        with self._lock:
            w = self.workers.pop(camera_id, None)
        if w:
            w.stop()

    def reload_spaces(self, camera_id: int) -> None:
        w = self.workers.get(camera_id)
        if w:
            w.reload_spaces()

    def worker(self, camera_id: int) -> CameraWorker | None:
        return self.workers.get(camera_id)

    def stop_all(self) -> None:
        for cid in list(self.workers):
            self.stop(cid)
