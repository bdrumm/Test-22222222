"""Match observed arrivals to the static schedule to compute lateness.

Two strategies, in order:

1. trip-id match: the realtime trip id suffix equals a scheduled trip at the stop;
2. nearest scheduled arrival of the same route/direction within a tolerance.

Subway service is headway-based, so lateness against the nearest scheduled slot
is a *regularity* measure rather than a strict punctuality measure; the headway
metrics in :mod:`metrics` complement it.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from .. import config
from ..sources.gtfs_static import NY_TZ, StaticGTFS, rt_trip_stem, rt_trip_suffix

MATCH_COLUMNS = ["sched_arrival_ts", "sched_trip_id", "sched_headway_sec", "lateness_sec", "match_method"]


def service_date_of(arrival_ts: float, start_date: str | None) -> date:
    if isinstance(start_date, str) and len(start_date) == 8 and start_date.isdigit():
        return date(int(start_date[:4]), int(start_date[4:6]), int(start_date[6:]))
    # Fall back: service day rolls over at 03:00 local, like MTA operations.
    local = datetime.fromtimestamp(arrival_ts, NY_TZ) - timedelta(hours=3)
    return local.date()


def match_arrivals(arrivals: pd.DataFrame, static: StaticGTFS, tolerance_sec: int | None = None) -> pd.DataFrame:
    """Return ``arrivals`` with schedule columns added (one stop_id or many)."""
    tol = tolerance_sec or config.DEFAULTS.schedule_match_tolerance_sec
    if arrivals.empty:
        out = arrivals.copy()
        for c in MATCH_COLUMNS:
            out[c] = np.nan if c != "match_method" else None
        return out
    arr = arrivals.copy()
    arr["service_date"] = [service_date_of(t, s) for t, s in zip(arr["arrival_ts"], arr.get("start_date", [None] * len(arr)))]
    arr["_suffix"] = arr["trip_id"].map(rt_trip_suffix)
    arr["_stem"] = arr["trip_id"].map(rt_trip_stem)
    pieces = []
    for (sd, stop_id), grp in arr.groupby(["service_date", "stop_id"], sort=False):
        sched = static.scheduled_stop_events(stop_id, sd, route_ids=sorted(grp["route_id"].dropna().unique()))
        pieces.append(_match_group(grp, sched, tol))
    out = pd.concat(pieces).sort_values("arrival_ts")
    return out.drop(columns=["_suffix", "_stem"]).reset_index(drop=True)


def _match_group(grp: pd.DataFrame, sched: pd.DataFrame, tol: int) -> pd.DataFrame:
    g = grp.copy()
    g["sched_arrival_ts"] = np.nan
    g["sched_trip_id"] = None
    g["sched_headway_sec"] = np.nan
    g["match_method"] = None
    if sched.empty:
        g["lateness_sec"] = np.nan
        return g
    sched = sched.sort_values("arrival_ts").copy()
    # Scheduled headway of each trip within its own route: the reference for gap/bunching flags.
    sched["sched_headway_sec"] = sched.groupby("route_id")["arrival_ts"].diff()
    sched["_suffix"] = sched["trip_id"].map(rt_trip_suffix)
    # 1) trip id match
    by_suffix = sched.drop_duplicates("_suffix").set_index("_suffix")
    hit = g["_suffix"].isin(by_suffix.index)
    if hit.any():
        g.loc[hit, "sched_arrival_ts"] = g.loc[hit, "_suffix"].map(by_suffix["arrival_ts"]).astype(float)
        g.loc[hit, "sched_trip_id"] = g.loc[hit, "_suffix"].map(by_suffix["trip_id"])
        g.loc[hit, "sched_headway_sec"] = g.loc[hit, "_suffix"].map(by_suffix["sched_headway_sec"]).astype(float)
        g.loc[hit, "match_method"] = "trip_id"
    # 1b) realtime ids without a path code (the L, some G and 7 trips): match on origin time + route + direction
    sched["_stem"] = sched["trip_id"].map(rt_trip_stem)
    by_stem = sched.drop_duplicates("_stem").set_index("_stem")
    hit2 = ~hit & g["_stem"].isin(by_stem.index)
    if hit2.any():
        g.loc[hit2, "sched_arrival_ts"] = g.loc[hit2, "_stem"].map(by_stem["arrival_ts"]).astype(float)
        g.loc[hit2, "sched_trip_id"] = g.loc[hit2, "_stem"].map(by_stem["trip_id"])
        g.loc[hit2, "sched_headway_sec"] = g.loc[hit2, "_stem"].map(by_stem["sched_headway_sec"]).astype(float)
        g.loc[hit2, "match_method"] = "trip_stem"
        hit = hit | hit2
    # 2) nearest by route
    rest = g[~hit]
    if len(rest):
        left = rest[["route_id", "arrival_ts"]].reset_index().sort_values("arrival_ts")
        right = sched[["route_id", "arrival_ts", "trip_id", "sched_headway_sec"]].rename(
            columns={"arrival_ts": "sched_arrival_ts", "trip_id": "sched_trip_id"}).sort_values("sched_arrival_ts")
        left["route_id"] = left["route_id"].astype(str)
        right["route_id"] = right["route_id"].astype(str)
        m = pd.merge_asof(left, right, left_on="arrival_ts", right_on="sched_arrival_ts", by="route_id",
                          direction="nearest", tolerance=float(tol))
        m = m.set_index("index")
        g.loc[m.index, "sched_arrival_ts"] = m["sched_arrival_ts"].astype(float)
        g.loc[m.index, "sched_trip_id"] = m["sched_trip_id"]
        g.loc[m.index, "sched_headway_sec"] = m["sched_headway_sec"].astype(float)
        g.loc[m.index[m["sched_arrival_ts"].notna()], "match_method"] = "nearest"
    g["lateness_sec"] = g["arrival_ts"] - g["sched_arrival_ts"]
    return g


def scheduled_counts(static: StaticGTFS, stop_id: str, routes: list[str], dates: list[date]) -> pd.DataFrame:
    """Scheduled arrivals per (service_date, hour) for coverage / service-delivered metrics."""
    rows = []
    for d in dates:
        ev = static.scheduled_stop_events(stop_id, d, routes)
        if ev.empty:
            continue
        ev["hour"] = (ev["arrival_sec"] // 3600) % 24
        ev = ev.sort_values("arrival_ts")
        ev["headway_sec"] = ev.groupby("route_id")["arrival_ts"].diff()
        agg = ev.groupby("hour").agg(n_sched=("trip_id", "count"), sched_headway_sec=("headway_sec", "median")).reset_index()
        agg["service_date"] = d
        rows.append(agg)
    if not rows:
        return pd.DataFrame(columns=["service_date", "hour", "n_sched", "sched_headway_sec"])
    return pd.concat(rows, ignore_index=True)
