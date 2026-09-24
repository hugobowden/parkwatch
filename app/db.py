"""SQLite storage for cameras, spaces, state changes and occupancy samples."""
import json
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "parkwatch.db"

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS cameras (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    source        TEXT NOT NULL,
    detector      TEXT NOT NULL DEFAULT 'auto',
    interval_sec  REAL NOT NULL DEFAULT 3,
    sensitivity   REAL NOT NULL DEFAULT 0.5,
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS spaces (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id  INTEGER NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    label      TEXT NOT NULL,
    points     TEXT NOT NULL            -- JSON [[x,y],...] normalised 0..1
);
CREATE TABLE IF NOT EXISTS space_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    space_id  INTEGER NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
    ts        REAL NOT NULL,
    occupied  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_space_ts ON space_events(space_id, ts);
CREATE TABLE IF NOT EXISTS samples (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id  INTEGER NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    ts         REAL NOT NULL,
    occupied   INTEGER NOT NULL,
    total      INTEGER NOT NULL,
    simulated  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_samples_cam_ts ON samples(camera_id, ts);
"""


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.executescript(SCHEMA)
    return _conn


def query(sql: str, args=()) -> list[dict]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def execute(sql: str, args=()) -> int:
    with _lock:
        c = conn()
        cur = c.execute(sql, args)
        c.commit()
        return cur.lastrowid


def executemany(sql: str, rows) -> None:
    with _lock:
        c = conn()
        c.executemany(sql, rows)
        c.commit()


# --- cameras -----------------------------------------------------------------

def list_cameras() -> list[dict]:
    return query("SELECT * FROM cameras ORDER BY id")


def get_camera(camera_id: int) -> dict | None:
    rows = query("SELECT * FROM cameras WHERE id=?", (camera_id,))
    return rows[0] if rows else None


def create_camera(name, source, detector="auto", interval_sec=3.0, sensitivity=0.5) -> int:
    return execute(
        "INSERT INTO cameras (name, source, detector, interval_sec, sensitivity, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (name, source, detector, interval_sec, sensitivity, time.time()),
    )


def update_camera(camera_id: int, fields: dict) -> None:
    allowed = {"name", "source", "detector", "interval_sec", "sensitivity", "enabled"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE cameras SET {sets} WHERE id=?", (*fields.values(), camera_id))


def delete_camera(camera_id: int) -> None:
    execute("DELETE FROM cameras WHERE id=?", (camera_id,))


# --- spaces ------------------------------------------------------------------

def list_spaces(camera_id: int) -> list[dict]:
    rows = query("SELECT * FROM spaces WHERE camera_id=? ORDER BY id", (camera_id,))
    for r in rows:
        r["points"] = json.loads(r["points"])
    return rows


def replace_spaces(camera_id: int, spaces: list[dict]) -> None:
    """Replace a camera's spaces, keeping ids (and history) for spaces that still exist."""
    existing = {s["id"] for s in list_spaces(camera_id)}
    keep = set()
    with _lock:
        c = conn()
        for s in spaces:
            pts = json.dumps(s["points"])
            if s.get("id") in existing:
                c.execute("UPDATE spaces SET label=?, points=? WHERE id=?", (s["label"], pts, s["id"]))
                keep.add(s["id"])
            else:
                c.execute(
                    "INSERT INTO spaces (camera_id, label, points) VALUES (?,?,?)",
                    (camera_id, s["label"], pts),
                )
        for sid in existing - keep:
            c.execute("DELETE FROM spaces WHERE id=?", (sid,))
        c.commit()


# --- history -----------------------------------------------------------------

def add_event(space_id: int, ts: float, occupied: bool) -> None:
    execute("INSERT INTO space_events (space_id, ts, occupied) VALUES (?,?,?)", (space_id, ts, int(occupied)))


def add_sample(camera_id: int, ts: float, occupied: int, total: int, simulated=False) -> None:
    execute(
        "INSERT INTO samples (camera_id, ts, occupied, total, simulated) VALUES (?,?,?,?,?)",
        (camera_id, ts, occupied, total, int(simulated)),
    )
