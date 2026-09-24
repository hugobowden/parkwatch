"""Frame sources: RTSP/HTTP streams, JPEG snapshot URLs, webcams, video files and the demo lot."""
import logging
import threading
import time
import urllib.request

import cv2
import numpy as np

from .demo import DemoLot

log = logging.getLogger("parkwatch.camera")

SNAPSHOT_EXTS = (".jpg", ".jpeg", ".png")


class FrameSource:
    """Keeps the most recent frame from a source available via latest().

    Streams are read continuously on a background thread so we always analyse
    the newest frame rather than a stale one sitting in OpenCV's buffer.
    """

    def __init__(self, source: str):
        self.source = source.strip()
        self.frame: np.ndarray | None = None
        self.frame_ts = 0.0
        self.error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._demo: DemoLot | None = None

        if self.source == "demo":
            self._demo = DemoLot()
        elif not self._is_snapshot():
            self._thread = threading.Thread(target=self._stream_loop, daemon=True)
            self._thread.start()

    def _is_snapshot(self) -> bool:
        s = self.source.lower()
        return s.startswith("snapshot:") or (
            s.startswith(("http://", "https://")) and s.split("?")[0].endswith(SNAPSHOT_EXTS)
        )

    def _open_capture(self) -> cv2.VideoCapture:
        src: str | int = self.source
        if isinstance(src, str) and src.isdigit():
            src = int(src)  # local webcam index
        cap = cv2.VideoCapture(src)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _stream_loop(self) -> None:
        backoff = 1.0
        is_file = not self.source.isdigit() and "://" not in self.source
        while not self._stop.is_set():
            cap = self._open_capture()
            if not cap.isOpened():
                self.error = "Could not open camera source"
                log.warning("cannot open %s, retrying in %.0fs", self.source, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30)
                continue
            fps = cap.get(cv2.CAP_PROP_FPS) or 25
            backoff = 1.0
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    if is_file:  # loop video files so they behave like a live feed
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    self.error = "Stream dropped, reconnecting"
                    break
                self.frame, self.frame_ts, self.error = frame, time.time(), None
                if is_file:
                    time.sleep(1 / fps)
            cap.release()

    def _fetch_snapshot(self) -> None:
        url = self.source.removeprefix("snapshot:")
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                data = np.frombuffer(r.read(), np.uint8)
            frame = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if frame is None:
                raise ValueError("response was not an image")
            self.frame, self.frame_ts, self.error = frame, time.time(), None
        except Exception as e:  # network errors, bad image, etc.
            self.error = f"Snapshot failed: {e}"

    def latest(self) -> np.ndarray | None:
        """Return the newest frame (fetching one first for demo/snapshot sources)."""
        if self._demo is not None:
            self.frame, self.frame_ts = self._demo.render(), time.time()
        elif self._thread is None:
            self._fetch_snapshot()
        elif self.frame is not None and time.time() - self.frame_ts > 30:
            self.error = self.error or "No new frames for 30s"
        return self.frame

    def close(self) -> None:
        self._stop.set()
