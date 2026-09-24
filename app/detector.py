"""Decide how likely each parking space is to be occupied in a frame.

Each detector returns one probability (0..1) per space. Two are available:

* texture - no model needed. Empty asphalt is smooth; a car adds edges and
  colour contrast. Works well on steady overhead views; tune with sensitivity.
* yolo    - a pretrained vehicle detector (pip install ultralytics). More
  robust on real footage, at the cost of a large install and more CPU.
"""
import logging

import cv2
import numpy as np

log = logging.getLogger("parkwatch.detector")

VEHICLE_CLASSES = {2, 3, 5, 7}  # COCO: car, motorcycle, bus, truck
_yolo_model = None
_yolo_failed = False


def _polygon_px(points, w, h) -> np.ndarray:
    return np.array([[x * w, y * h] for x, y in points], np.int32)


_mask_cache: dict = {}


def _space_masks(spaces, w, h):
    """For each space: its bounding box (x0, y0, x1, y1) plus full and inset masks cropped to it.

    The inset ignores painted lines at the edges. Masks are cached, since spaces rarely
    change, and are kept to each space's bounding box, which matters on a Raspberry Pi.
    """
    key = (w, h, tuple(tuple(map(tuple, s["points"])) for s in spaces))
    if key in _mask_cache:
        return _mask_cache[key]
    out = []
    for s in spaces:
        poly = _polygon_px(s["points"], w, h)
        x0, y0 = np.clip(poly.min(axis=0), 0, [w - 1, h - 1])
        x1, y1 = np.clip(poly.max(axis=0) + 1, 1, [w, h])
        full = np.zeros((y1 - y0, x1 - x0), np.uint8)
        cv2.fillPoly(full, [poly - [x0, y0]], 255)
        area = max(int(np.count_nonzero(full)), 1)
        k = max(3, int(np.sqrt(area) * 0.08)) | 1
        inset = cv2.erode(full, np.ones((k, k), np.uint8))
        out.append(((x0, y0, x1, y1), full, inset if inset.any() else full))
    if len(_mask_cache) > 32:
        _mask_cache.clear()
    _mask_cache[key] = out
    return out


def texture_scores(frame: np.ndarray, spaces: list[dict], sensitivity: float = 0.5) -> list[float]:
    h, w = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 40, 120)
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    probs = []
    for (x0, y0, x1, y1), _, inset in _space_masks(spaces, w, h):
        m = inset > 0
        edge_density = float(edges[y0:y1, x0:x1][m].mean() / 255)
        contrast = float(blur[y0:y1, x0:x1][m].std())
        saturation = float(hsv[y0:y1, x0:x1, 1][m].mean())
        # Combine into a single 0..1-ish score.
        score = (min(edge_density / 0.06, 1.5) * 0.5
                 + min(contrast / 35, 1.5) * 0.35
                 + min(saturation / 90, 1.0) * 0.15)
        # Higher sensitivity => lower threshold => more likely to call it occupied.
        threshold = 0.38 - (sensitivity - 0.5) * 0.4
        probs.append(float(1 / (1 + np.exp(-(score - threshold) * 12))))
    return probs


def _load_yolo():
    global _yolo_model, _yolo_failed
    if _yolo_model is None and not _yolo_failed:
        try:
            from ultralytics import YOLO  # optional dependency
            _yolo_model = YOLO("yolov8n.pt")
        except Exception as e:
            _yolo_failed = True
            log.warning("YOLO unavailable (%s); falling back to texture detector", e)
    return _yolo_model


def yolo_available() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("ultralytics") is not None
    except Exception:
        return False


def yolo_scores(frame: np.ndarray, spaces: list[dict], sensitivity: float = 0.5) -> list[float] | None:
    model = _load_yolo()
    if model is None:
        return None
    h, w = frame.shape[:2]
    conf = 0.45 - (sensitivity - 0.5) * 0.4
    result = model.predict(frame, conf=max(conf, 0.05), verbose=False)[0]
    vehicles = np.zeros((h, w), np.uint8)
    for box, cls in zip(result.boxes.xyxy.cpu().numpy(), result.boxes.cls.cpu().numpy()):
        if int(cls) in VEHICLE_CLASSES:
            x1, y1, x2, y2 = box.astype(int)
            # The lower part of a box is where the car meets the ground, which
            # matters on angled views where a car's roof overlaps the space behind it.
            y1 = y1 + (y2 - y1) // 3
            vehicles[max(y1, 0):y2, max(x1, 0):x2] = 255
    probs = []
    for (x0, y0, x1, y1), full, _ in _space_masks(spaces, w, h):
        area = np.count_nonzero(full)
        covered = np.logical_and(full > 0, vehicles[y0:y1, x0:x1] > 0).sum()
        coverage = covered / area if area else 0
        probs.append(float(min(coverage / 0.4, 1.0)))
    return probs


def score_spaces(frame: np.ndarray, spaces: list[dict], detector: str, sensitivity: float) -> list[float]:
    if not spaces:
        return []
    if detector in ("yolo", "auto") and (detector == "yolo" or yolo_available()):
        probs = yolo_scores(frame, spaces, sensitivity)
        if probs is not None:
            return probs
    return texture_scores(frame, spaces, sensitivity)
