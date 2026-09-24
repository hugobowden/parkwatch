"""Turn recorded history into insights: best/worst times, weekly heatmap, per-space usage."""
import time
from collections import defaultdict
from datetime import datetime

from . import db

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WAKING_HOURS = range(6, 22)  # "best time" suggestions only look at 06:00-21:59


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def insights(camera_id: int, days: int = 30) -> dict:
    now = time.time()
    since = now - days * 86400
    samples = db.query(
        "SELECT ts, occupied, total, simulated FROM samples WHERE camera_id=? AND ts>=? ORDER BY ts",
        (camera_id, since),
    )
    if not samples:
        return {"has_data": False, "days": days}

    grid = defaultdict(list)   # (weekday, hour) -> [occupancy fraction]
    by_hour = defaultdict(list)
    today = datetime.now().date()
    today_series = []
    full_count = 0
    for s in samples:
        if not s["total"]:
            continue
        frac = s["occupied"] / s["total"]
        t = datetime.fromtimestamp(s["ts"])
        grid[(t.weekday(), t.hour)].append(frac)
        by_hour[t.hour].append(frac)
        if s["occupied"] >= s["total"]:
            full_count += 1
        if t.date() == today:
            today_series.append({"ts": s["ts"], "pct": round(frac * 100, 1)})

    heatmap = [[_pct(_mean(grid[(d, h)])) for h in range(24)] for d in range(7)]
    wd = datetime.now().weekday()
    typical_today = [_pct(_mean(grid[(wd, h)])) for h in range(24)]

    def ranked(cells, reverse=False):
        cells = [c for c in cells if c["pct"] is not None]
        return sorted(cells, key=lambda c: c["pct"], reverse=reverse)

    today_cells = [{"day": DAYS[wd], "hour": h, "pct": typical_today[h]} for h in WAKING_HOURS]
    week_cells = [{"day": DAYS[d], "hour": h, "pct": heatmap[d][h]} for d in range(7) for h in WAKING_HOURS]

    total_spaces = samples[-1]["total"]
    return {
        "has_data": True,
        "days": days,
        "simulated": any(s["simulated"] for s in samples),
        "sample_count": len(samples),
        "first_ts": samples[0]["ts"],
        "total_spaces": total_spaces,
        "avg_pct": _pct(_mean([s["occupied"] / s["total"] for s in samples if s["total"]])),
        "full_pct": round(full_count / len(samples) * 100, 1),
        "heatmap": heatmap,
        "hourly": [_pct(_mean(by_hour[h])) for h in range(24)],
        "today": {"weekday": DAYS[wd], "series": today_series, "typical": typical_today},
        "best_today": ranked(today_cells)[:3],
        "busiest_today": ranked(today_cells, reverse=True)[:3],
        "best_week": ranked(week_cells)[:5],
        "busiest_week": ranked(week_cells, reverse=True)[:5],
        "spaces": space_usage(camera_id, since, now),
    }


def _pct(frac):
    return None if frac is None else round(frac * 100, 1)


def space_usage(camera_id: int, since: float, now: float) -> list[dict]:
    """Share of time each space was occupied, how often it turned over, and typical stay."""
    spaces = db.list_spaces(camera_id)
    out = []
    for sp in spaces:
        prior = db.query(
            "SELECT occupied FROM space_events WHERE space_id=? AND ts<? ORDER BY ts DESC LIMIT 1",
            (sp["id"], since),
        )
        events = db.query(
            "SELECT ts, occupied FROM space_events WHERE space_id=? AND ts>=? ORDER BY ts",
            (sp["id"], since),
        )
        state = bool(prior[0]["occupied"]) if prior else None
        cursor = since if prior else (events[0]["ts"] if events else now)
        observed = occupied_time = 0.0
        arrivals, stays = 0, []
        arrived_at = cursor if state else None
        for e in events:
            if state is not None:
                observed += e["ts"] - cursor
                if state:
                    occupied_time += e["ts"] - cursor
            new = bool(e["occupied"])
            if new and state is not True:
                arrivals += 1
                arrived_at = e["ts"]
            elif not new and state and arrived_at is not None:
                stays.append(e["ts"] - arrived_at)
                arrived_at = None
            state, cursor = new, e["ts"]
        if state is not None:
            observed += now - cursor
            if state:
                occupied_time += now - cursor
        out.append({
            "id": sp["id"],
            "label": sp["label"],
            "occupied_pct": round(occupied_time / observed * 100, 1) if observed else None,
            "arrivals": arrivals,
            "avg_stay_min": round(sum(stays) / len(stays) / 60) if stays else None,
        })
    return out
