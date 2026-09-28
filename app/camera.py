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
YOUTUBE_HOSTS = ("youtube.com/", "youtu.be/")
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0 Safari/537.36 ParkWatch")


def is_youtube(source: str) -> bool:
    return any(h in source.lower() for h in YOUTUBE_HOSTS)


def resolve_youtube(url: str) -> tuple[str, bool]:
    """Find the direct video stream behind a YouTube link. Returns (stream_url, is_live).

    YouTube stream links expire after a few hours, so this is called again on every reconnect.
    """
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError("yt-dlp isn't installed (pip install yt-dlp)")
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True,
            # Video only (no audio needed), H.264 where possible since OpenCV decodes it
            # reliably, and 720p is plenty for spotting cars while being light to decode.
            "format": "bestvideo[height<=720][vcodec^=avc1]/bestvideo[height<=720]"
                      "/best[height<=720][vcodec!=none]/bestvideo/best"}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("live_status") == "is_upcoming":
        raise RuntimeError("this stream hasn't started yet")
    stream = info.get("url")
    if not stream:
        raise RuntimeError("no playable video stream found")
    return stream, bool(info.get("is_live"))


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
        self._paced = False  # sleep between frames (files, recorded YouTube videos)

        if self.source == "demo":
            self._demo = DemoLot()
        elif not self._is_snapshot():
            self._thread = threading.Thread(target=self._stream_loop, daemon=True)
            self._thread.start()

    def _is_snapshot(self) -> bool:
        s = self.source.lower()
        return s.startswith("snapshot:") or s.split("?")[0].endswith(SNAPSHOT_EXTS)

    def _open_capture(self) -> cv2.VideoCapture | None:
        src: str | int = self.source
        self._paced = not self.source.isdigit() and "://" not in self.source  # video files
        if is_youtube(self.source):
            try:
                src, live = resolve_youtube(self.source)
            except Exception as e:
                # "ERROR: [youtube] abc123: This video is unavailable" -> "This video is unavailable"
                self.error = "YouTube: " + str(e).split(": ", 2)[-1]
                return None
            self._paced = not live  # recorded videos play at normal speed and loop
        elif src.isdigit():
            src = int(src)  # local webcam index
        cap = cv2.VideoCapture(src)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _stream_loop(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            # Re-resolved on every (re)connect, which also renews expired YouTube links.
            cap = self._open_capture()
            is_file = self._paced and not is_youtube(self.source)
            if cap is None or not cap.isOpened():
                self.error = self.error if cap is None else "Could not open camera source"
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
                if self._paced:
                    time.sleep(1 / fps)
            if cap is not None:
                cap.release()

    def _fetch_snapshot(self) -> None:
        url = self.source.removeprefix("snapshot:")
        try:
            if "://" in url:
                # Many webcam sites reject requests that don't look like they come from a browser.
                req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
                with urllib.request.urlopen(req, timeout=10) as r:
                    data = np.frombuffer(r.read(), np.uint8)
            else:  # a local photo, handy for trying out spaces and detection
                data = np.fromfile(url, np.uint8)
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
