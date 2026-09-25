"""Whole-line views from network-wide arrivals: Marey (time-distance) snapshots,
where trains lose time along the line, and how far ahead the countdown clock
can be trusted.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from ..analysis.schedule_match import service_date_of
from ..sources.gtfs_static import NY_TZ, StaticGTFS


def _lateness(static: StaticGTFS, df: pd.DataFrame) -> pd.Series:
    sched = [static.scheduled_arrival(t, s, service_date_of(ts, sd)) for t, s, ts, sd in
             zip(df["trip_id"], df["stop_id"], df["arrival_ts"], df.get("start_date", pd.Series([None] * len(df), index=df.index)))]
    sched = pd.to_numeric(pd.Series(sched, index=df.index), errors="coerce")
    return df["arrival_ts"] - sched


def line_snapshot(arrivals: pd.DataFrame, static: StaticGTFS, route: str, direction: str, now: float,
                  live_trains: list | None = None, hours_back: float = 2.0, hours_ahead: float = 1.0) -> dict:
    """Actual trajectories (observed arrivals), live projections (feed ETAs) and the schedule on one line."""
    stops = static.canonical_stop_sequence(route, direction)
    idx = {s: i for i, s in enumerate(stops)}
    out = {"route": route, "direction": direction, "now": now, "stops": [{"stop_id": s, "name": static.stop_name(s)} for s in stops],
           "actual": [], "live": [], "scheduled": []}
    if not stops:
        return out
    a = arrivals
    if a is not None and not a.empty:
        a = a[(a["route_id"].astype(str) == str(route)) & (a["stop_id"].isin(idx)) & (a["arrival_ts"] >= now - hours_back * 3600) & (a["arrival_ts"] <= now + 60)].copy()
        if not a.empty:
            a["lateness"] = _lateness(static, a)
            for key, g in a.sort_values("arrival_ts").groupby("trip_key"):
                pts = [[idx[s], float(t), (None if pd.isna(l) else float(l))] for s, t, l in zip(g["stop_id"], g["arrival_ts"], g["lateness"])]
                if len(pts) >= 2:
                    tid = g["train_id"].dropna().iloc[0] if "train_id" in g and g["train_id"].notna().any() else None
                    out["actual"].append({"trip_id": str(g["trip_id"].iloc[0]), "train_id": tid, "points": pts,
                                          "last_lateness": pts[-1][2]})
    for t in live_trains or []:
        if str(t.route_id) != str(route) or (t.direction and t.direction != direction):
            continue
        pts = [[idx[s], float(e)] for s, e in t.stops if s in idx and e <= now + hours_ahead * 3600]
        if len(pts) >= 1:
            out["live"].append({"trip_id": t.trip_id, "train_id": getattr(t, "train_id", None), "points": pts, "lateness": t.lateness_sec if t.started else None,
                                "started": t.started, "track_changed": bool(getattr(t, "track_changed", 0))})
    sd = (datetime.fromtimestamp(now, NY_TZ) - timedelta(hours=3)).date()
    ev = static.scheduled_stop_events(stops, sd, [route])
    ev = ev[(ev["arrival_ts"] >= now - hours_back * 3600) & (ev["arrival_ts"] <= now + hours_ahead * 3600)]
    for tid, g in ev.sort_values("arrival_ts").groupby("trip_id"):
        pts = [[idx[s], float(t)] for s, t in zip(g["stop_id"], g["arrival_ts"]) if s in idx]
        if len(pts) >= 2:
            out["scheduled"].append({"trip_id": tid, "points": pts})
    return out


def deviation_grid(arrivals: pd.DataFrame, static: StaticGTFS, route: str, direction: str) -> dict:
    """Mean lateness change per stop (vs the previous observed stop) by hour: where the line loses or gains time."""
    stops = static.canonical_stop_sequence(route, direction)
    idx = {s: i for i, s in enumerate(stops)}
    out = {"route": route, "direction": direction, "stops": [{"stop_id": s, "name": static.stop_name(s)} for s in stops], "grid": [], "n": [], "mean": []}
    if arrivals is None or arrivals.empty or not stops:
        return out
    a = arrivals[(arrivals["route_id"].astype(str) == str(route)) & (arrivals["stop_id"].isin(idx))].copy()
    if a.empty:
        return out
    a["lateness"] = _lateness(static, a)
    a = a.dropna(subset=["lateness"]).sort_values(["trip_key", "arrival_ts"])
    a["prev_lat"] = a.groupby("trip_key")["lateness"].shift(1)
    a["prev_ts"] = a.groupby("trip_key")["arrival_ts"].shift(1)
    a = a[(a["arrival_ts"] - a["prev_ts"] < 1800)].dropna(subset=["prev_lat"])
    a["delta"] = (a["lateness"] - a["prev_lat"]).clip(-600, 900)
    a["hour"] = pd.to_datetime(a["arrival_ts"], unit="s", utc=True).dt.tz_convert(NY_TZ).dt.hour
    grid = np.full((len(stops), 24), np.nan); n = np.zeros((len(stops), 24), dtype=int)
    for (s, h), g in a.groupby(["stop_id", "hour"]):
        if len(g) >= 3:
            grid[idx[s], int(h)] = float(g["delta"].mean()); n[idx[s], int(h)] = len(g)
    means = a.groupby("stop_id")["delta"].mean()
    out["grid"] = [[None if np.isnan(v) else round(float(v), 1) for v in row] for row in grid]
    out["n"] = n.tolist()
    out["mean"] = [round(float(means.get(s, np.nan)), 1) if s in means.index else None for s in stops]
    out["n_trips"] = int(a["trip_key"].nunique())
    worst = sorted([(float(means[s]), s) for s in means.index], reverse=True)[:5]
    out["worst_stops"] = [{"stop_id": s, "name": static.stop_name(s), "mean_delta_sec": round(v, 1)} for v, s in worst if v > 0]
    return out


def eta_trust(eta_samples: pd.DataFrame, arrivals: pd.DataFrame) -> dict:
    """How the feed's ETA error grows with the number of stops ahead, per route."""
    out = {"n": 0, "by_route": {}, "overall": []}
    if eta_samples is None or eta_samples.empty or arrivals is None or arrivals.empty:
        return out
    j = eta_samples.merge(arrivals[["trip_key", "stop_id", "arrival_ts"]].drop_duplicates(["trip_key", "stop_id"]), on=["trip_key", "stop_id"])
    if j.empty:
        return out
    j["err"] = j["arrival_ts"] - j["eta_ts"]          # positive: the train came later than promised
    j = j[j["err"].abs() < 3600]
    out["n"] = int(len(j))
    def summ(g):
        return {"n": int(len(g)), "median_abs_err_sec": float(g["err"].abs().median()), "p90_abs_err_sec": float(g["err"].abs().quantile(0.9)),
                "bias_sec": float(g["err"].median()), "share_late_over_2min": float((g["err"] > 120).mean())}
    for k, g in j.groupby("stops_ahead"):
        out["overall"].append({"stops_ahead": int(k), **summ(g)})
    for r, gr in j.groupby("route_id"):
        if len(gr) < 30:
            continue
        out["by_route"][str(r)] = [{"stops_ahead": int(k), **summ(g)} for k, g in gr.groupby("stops_ahead") if len(g) >= 10]
    return out
