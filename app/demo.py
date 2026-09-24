"""A simulated top-down parking lot, so the app can be tried without a real camera.

Use source "demo" when adding a camera. Cars arrive and leave following a
realistic time-of-day pattern, and the frames go through the normal detector.
"""
import math
import random
import time
from datetime import datetime

import cv2
import numpy as np

from . import db

W, H = 960, 540
COLS = 8
SPACE_W, X0 = 100, 80
ROWS = [(50, 225), (315, 490)]  # (top, bottom) pixel extents of each row
CAR_COLOURS = [(40, 40, 200), (200, 200, 205), (30, 30, 30), (160, 90, 20), (60, 140, 60),
               (230, 230, 230), (40, 110, 190), (90, 90, 100), (20, 60, 120)]


def demo_spaces() -> list[dict]:
    """Space polygons (normalised) matching the painted bays."""
    spaces = []
    for r, (top, bottom) in enumerate(ROWS):
        for c in range(COLS):
            x1, x2 = X0 + c * SPACE_W + 6, X0 + (c + 1) * SPACE_W - 6
            pts = [[x1, top + 4], [x2, top + 4], [x2, bottom - 4], [x1, bottom - 4]]
            spaces.append({
                "label": f"{'AB'[r]}{c + 1}",
                "points": [[round(x / W, 4), round(y / H, 4)] for x, y in pts],
            })
    return spaces


def target_occupancy(ts: float) -> float:
    """Typical fraction of the lot in use at a given time (a workplace-style lot)."""
    t = datetime.fromtimestamp(ts)
    h = t.hour + t.minute / 60
    weekend = t.weekday() >= 5

    def bump(centre, width, height):
        return height * math.exp(-((h - centre) / width) ** 2)

    if weekend:
        v = 0.08 + bump(12.5, 3.0, 0.45) + bump(17, 2.0, 0.15)
    else:
        v = 0.06 + bump(10, 2.2, 0.72) + bump(13, 1.2, 0.12) + bump(15, 2.0, 0.55)
        if t.weekday() == 4:  # Fridays empty out early
            v -= bump(16, 1.5, 0.25)
    return max(0.02, min(0.97, v))


class DemoLot:
    """Keeps the simulated lot's state and renders frames from it."""

    def __init__(self, seed: int = 7):
        self.rng = random.Random(seed)
        n = COLS * len(ROWS)
        now = time.time()
        target = target_occupancy(now)
        self.cars = [self._new_car() if self.rng.random() < target else None for _ in range(n)]
        self.next_change = [now + self.rng.uniform(3, 30) for _ in range(n)]
        self.base = self._draw_background()

    def _new_car(self):
        return {
            "colour": self.rng.choice(CAR_COLOURS),
            "dx": self.rng.randint(-8, 8),
            "dy": self.rng.randint(-10, 10),
            "len": self.rng.randint(120, 150),
            "wid": self.rng.randint(58, 70),
        }

    def _draw_background(self):
        rng = np.random.default_rng(1)
        img = np.full((H, W, 3), 78, np.uint8)
        noise = rng.normal(0, 6, (H, W, 1)).astype(np.int16)
        img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        img = cv2.GaussianBlur(img, (3, 3), 0)
        for top, bottom in ROWS:
            for c in range(COLS + 1):
                x = X0 + c * SPACE_W
                cv2.line(img, (x, top), (x, bottom), (225, 225, 225), 3)
        # kerbs, grass and an aisle arrow
        cv2.rectangle(img, (0, 0), (W, 40), (70, 120, 70), -1)
        cv2.rectangle(img, (0, H - 40), (W, H), (70, 120, 70), -1)
        cv2.arrowedLine(img, (380, 270), (580, 270), (200, 200, 200), 4, tipLength=0.15)
        return img

    def step(self, now: float | None = None) -> None:
        now = now or time.time()
        target = target_occupancy(now)
        for i in range(len(self.cars)):
            if now < self.next_change[i]:
                continue
            occupied = self.cars[i] is not None
            # Leave more readily when the lot should be emptying, and vice versa.
            if occupied and self.rng.random() < (1 - target) * 0.9 + 0.05:
                self.cars[i] = None
            elif not occupied and self.rng.random() < target * 0.9 + 0.05:
                self.cars[i] = self._new_car()
            self.next_change[i] = now + self.rng.uniform(8, 60)

    def render(self) -> np.ndarray:
        self.step()
        img = self.base.copy()
        for i, car in enumerate(self.cars):
            if car is None:
                continue
            r, c = divmod(i, COLS)
            top, bottom = ROWS[r]
            cx = X0 + c * SPACE_W + SPACE_W // 2 + car["dx"]
            cy = (top + bottom) // 2 + car["dy"]
            hw, hl = car["wid"] // 2, car["len"] // 2
            # shadow, body, windscreens, roof
            cv2.rectangle(img, (cx - hw + 5, cy - hl + 6), (cx + hw + 5, cy + hl + 6), (45, 45, 45), -1)
            cv2.rectangle(img, (cx - hw, cy - hl), (cx + hw, cy + hl), car["colour"], -1)
            cv2.rectangle(img, (cx - hw, cy - hl), (cx + hw, cy + hl), (25, 25, 25), 2)
            front = -1 if r == 0 else 1
            ws = cy + front * (hl - 38)
            cv2.rectangle(img, (cx - hw + 6, min(ws, ws + front * 22)), (cx + hw - 6, max(ws, ws + front * 22)),
                          (60, 50, 40), -1)
            rw = cy - front * (hl - 22)
            cv2.rectangle(img, (cx - hw + 8, min(rw, rw - front * 14)), (cx + hw - 8, max(rw, rw - front * 14)),
                          (60, 50, 40), -1)
            shade = tuple(int(v * 0.85) for v in car["colour"])
            cv2.rectangle(img, (cx - hw + 8, cy - 22), (cx + hw - 8, cy + 22), shade, -1)
        # sensor noise so frames aren't identical
        noise = np.random.normal(0, 3, img.shape).astype(np.int16)
        img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(img, "DEMO CAM  " + stamp, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        return img


def seed_history(camera_id: int, days: int = 21, step_min: int = 5) -> None:
    """Back-fill simulated history for a demo camera so Insights has something to show."""
    spaces = db.list_spaces(camera_id)
    if not spaces:
        return
    rng = random.Random(camera_id)
    end = time.time()
    start = end - days * 86400
    state = [False] * len(spaces)
    events, samples = [], []
    t = start
    tick = 0
    while t < end:
        target = target_occupancy(t) + rng.uniform(-0.05, 0.05)
        for i, s in enumerate(spaces):
            p_change = 0.25
            if rng.random() < p_change:
                want = rng.random() < target
                if want != state[i]:
                    state[i] = want
                    events.append((s["id"], t, int(want)))
        if tick % 3 == 0:  # a sample every 15 minutes
            samples.append((camera_id, t, sum(state), len(spaces), 1))
        t += step_min * 60
        tick += 1
    db.executemany("INSERT INTO space_events (space_id, ts, occupied) VALUES (?,?,?)", events)
    db.executemany(
        "INSERT INTO samples (camera_id, ts, occupied, total, simulated) VALUES (?,?,?,?,?)", samples
    )
