"""Realized inter-station running times and speeds.

The subway feeds carry no GPS or speed, but each vehicle's timestamp is the moment it entered its state
(departed for / arrived at a stop), so a transit state followed by the stop gives the segment's run time at
feed precision. With track distances from the static shapes this becomes a speed per segment, compared with
the scheduled running time: where trains run slower than planned, by hour.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from ..sources.gtfs_static import NY_TZ, StaticGTFS

SLOW_RATIO = 1.2       # realized / scheduled run above this = a slow segment
MIN_N = 5


def _hour(ts: float) -> int:
    return datetime.fromtimestamp(float(ts), NY_TZ).hour


def segment_profile(runs: pd.DataFrame | None, static: StaticGTFS | None, min_n: int = MIN_N) -> dict:
    empty = {"n": 0, "days": 0, "segments": [], "by_key": {}, "slow": [], "n_segments": 0}
    if runs is None or runs.empty:
        return empty
    r = runs.dropna(subset=["from_stop", "to_stop", "run_sec"]).copy()
    r = r[(r["run_sec"] > 10) & (r["run_sec"] < 1800)]          # a poll gap or a reroute, not a run
    if r.empty:
        return empty
    r["hour"] = r["depart_ts"].map(_hour)
    days = max(1, int(pd.to_datetime(r["depart_ts"], unit="s", utc=True).dt.tz_convert("America/New_York").dt.date.nunique()))
    name = static.stop_name if static is not None else (lambda s: s)
    seg_cache: dict[tuple, tuple[list, list, list]] = {}
    def canon(route, direction):
        k = (str(route), str(direction))
        if k not in seg_cache:
            if static is None:
                seg_cache[k] = ([], [], [])
            else:
                try:
                    seq = static.canonical_stop_sequence(k[0], k[1]); seg_cache[k] = (seq, static.segment_lengths(k[0], k[1]), static.canonical_run_sec(k[0], k[1]))
                except Exception:
                    seg_cache[k] = ([], [], [])
        return seg_cache[k]
    segments, by_key = [], {}
    for (route, direction, a, b), g in r.groupby(["route_id", "direction", "from_stop", "to_stop"]):
        if len(g) < min_n:
            continue
        seq, dist, sched = canon(route, direction)
        i = seq.index(a) if a in seq else -1
        consecutive = i >= 0 and i + 1 < len(seq) and seq[i + 1] == b
        d_m = dist[i] if consecutive and i < len(dist) else None
        s_run = sched[i] if consecutive and i < len(sched) else None
        med = float(g["run_sec"].median())
        by_hour = []
        for hh in range(24):
            gh = g[g["hour"] == hh]
            by_hour.append(round(float(gh["run_sec"].median())) if len(gh) >= 3 else None)
        row = {"route": str(route), "direction": str(direction), "from_stop": a, "from_name": name(a), "to_stop": b, "to_name": name(b), "n": int(len(g)),
               "median_run_sec": round(med), "p90_run_sec": round(float(g["run_sec"].quantile(0.9))), "sched_run_sec": round(float(s_run)) if s_run else None,
               "ratio": round(med / s_run, 3) if s_run else None, "dist_m": round(float(d_m)) if d_m else None,
               "speed_kmh": round(d_m / med * 3.6, 1) if d_m else None, "sched_speed_kmh": round(d_m / s_run * 3.6, 1) if d_m and s_run else None,
               "by_hour_run_sec": by_hour, "consecutive": bool(consecutive)}
        segments.append(row)
        by_key[f"{route}_{direction}|{b}"] = row
    slow = sorted([x for x in segments if x["ratio"] and x["ratio"] >= SLOW_RATIO and x["consecutive"]], key=lambda x: -(x["ratio"] or 0))[:20]
    fast = sorted([x for x in segments if x["ratio"] and x["consecutive"]], key=lambda x: x["ratio"])[:10]
    return {"n": int(len(r)), "days": days, "n_segments": len(segments), "segments": segments, "by_key": by_key, "slow": slow, "fast": fast,
            "median_ratio": round(float(np.median([x["ratio"] for x in segments if x["ratio"]])), 3) if any(x["ratio"] for x in segments) else None}
