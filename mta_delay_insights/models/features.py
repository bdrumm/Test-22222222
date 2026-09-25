"""Training rows for the downstream arrival model.

One row per (trip, stop u, stop d) with d exactly ``k`` stops after ``u`` in the
trip's observed sequence. Everything in the row is known at the moment the
train serves ``u`` (time ``t``):

* the train: route, direction, lateness at ``u``, how its lateness changed over
  the previous 1 and 3 stops (momentum), the scheduled run time ``u -> d``,
  whether it is on a different track than scheduled;
* the traffic ahead: gap to the train in front at ``u`` and that train's
  lateness, the scheduled headway;
* the segment right now: mean excess run time of the last three trains that
  completed ``u -> d`` before ``t`` and the mean lateness at ``d`` in the
  previous 15 minutes;
* the feed's own forecast for ``d`` at that moment when an ETA sample exists;
* context: hour (cyclic), weekend, peak, unplanned alert on the route (with
  cause), planned work, daily precipitation and heat, events / news / holiday.

Target: ``delta_sec = lateness at d - lateness at u`` (the excess run time).
"""
from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pandas as pd

from ..analysis.schedule_match import match_arrivals
from ..realtime.journey import context_index
from ..sources.gtfs_static import NY_TZ, StaticGTFS
from ..sources import alerts as alerts_src

K_SET = (1, 2, 3, 5, 8, 12)
CATEGORICAL = ["route_code", "direction_code", "cause_code"]
NUMERIC = ["k", "sched_run_sec", "lateness_u", "mom1", "mom3", "gap_ahead_sec", "leader_lateness", "leader_same_route",
           "sched_headway_sec", "seg_recent_excess", "dest_recent_lateness", "feed_excess", "track_changed",
           "hour_sin", "hour_cos", "weekend", "peak", "alert_active", "planned_active",
           "precip_mm", "heat", "holiday", "venue_event_w", "street_event_w", "news_w",
           "nws_any", "nws_severe", "clim_rate"]
FEATURES = CATEGORICAL + NUMERIC
CAUSES = ["none", "signal", "track", "police", "medical", "mechanical", "crowding", "weather", "other"]
ROUTES = ["1", "2", "3", "4", "5", "6", "6X", "7", "7X", "A", "B", "C", "D", "E", "F", "FX", "G", "J", "L", "M", "N", "Q", "R", "S", "SI", "W", "Z", "FS", "GS", "H"]
ROUTE_CODE = {r: i for i, r in enumerate(ROUTES)}
CAUSE_CODE = {c: i for i, c in enumerate(CAUSES)}
PEAK_HOURS = {7, 8, 9, 16, 17, 18, 19}


def _cause_code(c) -> int:
    if not isinstance(c, str):
        return 0
    for name in CAUSES[1:]:
        if name in c:
            return CAUSE_CODE[name]
    return CAUSE_CODE["other"]


def _alert_index(alerts_df: pd.DataFrame | None) -> tuple[np.ndarray, np.ndarray, list, np.ndarray, np.ndarray]:
    """Arrays for fast 'is an alert active for route r at t' checks."""
    if alerts_df is None or alerts_df.empty:
        return np.zeros(0), np.zeros(0), [], np.zeros(0, dtype=int), np.zeros(0, dtype=bool)
    a = alerts_df
    kinds = np.array([alerts_src.alert_kind(t, h) for t, h in zip(a["alert_type"], a["header"])])
    start = pd.to_numeric(a["active_start"], errors="coerce").fillna(-np.inf).values.astype(float)
    upd = pd.to_numeric(a.get("updated_at", pd.Series(index=a.index, dtype=float)), errors="coerce")
    end = pd.to_numeric(a["active_end"], errors="coerce").fillna(upd.fillna(pd.Series(start, index=a.index)) + 3 * 3600).values.astype(float)
    routes = [set(map(str, rs)) if isinstance(rs, (list, tuple, set)) and len(rs) else None for rs in a["routes"].values]
    causes = np.array([_cause_code(c) for c in a.get("cause_category", pd.Series([None] * len(a))).values])
    planned = (kinds == "planned")
    delay = (kinds == "delay")
    keep = planned | delay
    return start[keep], end[keep], [r for r, k in zip(routes, keep) if k], causes[keep], planned[keep]


def _alert_feats(idx, ts: float, route: str) -> tuple[float, float, int]:
    start, end, routes, causes, planned = idx
    if not len(start):
        return 0.0, 0.0, 0
    on = np.flatnonzero((start <= ts) & (end >= ts))
    active = planned_on = 0.0
    cause = 0
    for i in on:
        if routes[i] is None or route in routes[i]:
            if planned[i]:
                planned_on = 1.0
            else:
                active = 1.0
                cause = cause or int(causes[i])
    return active, planned_on, cause


def _nws_arrays(nws: pd.DataFrame | None):
    if nws is None or nws.empty:
        return None
    on = pd.to_numeric(nws["onset_ts"], errors="coerce").fillna(-1e12).values.astype(float)
    off = pd.to_numeric(nws["ends_ts"], errors="coerce").fillna(1e12).values.astype(float)
    sev = nws["severity"].isin(["Severe", "Extreme"]).values
    return on, off, sev


def nws_at(arrs, ts: float) -> tuple[float, float]:
    if arrs is None:
        return 0.0, 0.0
    on, off, sev = arrs
    m = (on <= ts) & (off >= ts)
    return float(m.any()), float((m & sev).any())


def clim_rate_at(clim: dict | None, route: str, ts: float) -> float:
    if not clim:
        return float("nan")
    grid = clim.get("grid_by_route", {}).get(str(route))
    if not grid:
        return float("nan")
    local = datetime.fromtimestamp(ts, NY_TZ)
    return float(grid[local.weekday()][local.hour])


def build_training_rows(arrivals: pd.DataFrame, static: StaticGTFS, alerts_df: pd.DataFrame | None = None,
                        weather_daily: pd.DataFrame | None = None, events_df: pd.DataFrame | None = None,
                        eta_samples: pd.DataFrame | None = None, k_set=K_SET, min_confidence: float = 0.6,
                        nws_df: pd.DataFrame | None = None, climatology: dict | None = None) -> pd.DataFrame:
    """Vectorised construction of the training table from observed arrivals."""
    if arrivals is None or arrivals.empty:
        return pd.DataFrame(columns=["trip_key", "route_id", "u", "d", "t", "delta_sec"] + FEATURES)
    a = arrivals[arrivals["confidence"] >= min_confidence].copy()
    a = a.drop_duplicates(["trip_key", "stop_id"]).sort_values(["trip_key", "arrival_ts"])
    m = match_arrivals(a, static).dropna(subset=["lateness_sec"])
    m["route_id"] = m["route_id"].astype(str)
    m = m[(m["lateness_sec"] > -900) & (m["lateness_sec"] < 5400)]
    m = m.sort_values(["trip_key", "arrival_ts"]).reset_index(drop=True)
    m["seq"] = m.groupby("trip_key").cumcount()
    # momentum: lateness change over the previous 1 and 3 observed stops of the same trip
    g = m.groupby("trip_key")["lateness_sec"]
    m["mom1"] = m["lateness_sec"] - g.shift(1)
    m["mom3"] = m["lateness_sec"] - g.shift(3)
    # traffic ahead at the same stop: previous arrival (any route) at that stop
    m = m.sort_values(["stop_id", "arrival_ts"])
    gs = m.groupby("stop_id")
    m["gap_ahead_sec"] = m["arrival_ts"] - gs["arrival_ts"].shift(1)
    m["leader_lateness"] = gs["lateness_sec"].shift(1)
    m["leader_same_route"] = (gs["route_id"].shift(1) == m["route_id"]).astype(float)
    m.loc[m["gap_ahead_sec"] > 3600, ["gap_ahead_sec", "leader_lateness"]] = np.nan
    # recent lateness at each stop over the previous 15 minutes (trains that already arrived)
    m["dest_recent_lateness"] = np.nan
    for sid, grp in m.groupby("stop_id"):
        ts = grp["arrival_ts"].values; lat = grp["lateness_sec"].values
        cs = np.concatenate([[0.0], np.cumsum(lat)])
        lo = np.searchsorted(ts, ts - 900, side="left")
        idx = np.arange(len(ts))
        n = idx - lo
        vals = np.where(n > 0, (cs[idx] - cs[lo]) / np.maximum(n, 1), np.nan)
        m.loc[grp.index, "dest_recent_lateness"] = vals
    m = m.sort_values(["trip_key", "seq"]).reset_index(drop=True)
    tracks = ("sched_track" in m.columns) and ("actual_track" in m.columns)
    m["track_changed"] = ((m["actual_track"].notna()) & (m["sched_track"].notna()) & (m["actual_track"] != m["sched_track"])).astype(float) if tracks else 0.0

    base_cols = ["trip_key", "route_id", "direction", "stop_id", "arrival_ts", "lateness_sec", "sched_arrival_ts", "sched_headway_sec",
                 "mom1", "mom3", "gap_ahead_sec", "leader_lateness", "leader_same_route", "track_changed", "seq"]
    u = m[base_cols].rename(columns={"stop_id": "u", "arrival_ts": "t", "lateness_sec": "lateness_u", "sched_arrival_ts": "sched_u"})
    parts = []
    for k in k_set:
        d = m[["trip_key", "stop_id", "arrival_ts", "lateness_sec", "sched_arrival_ts", "seq", "dest_recent_lateness"]].copy()
        d["seq"] = d["seq"] - k
        d = d.rename(columns={"stop_id": "d", "arrival_ts": "t_d", "lateness_sec": "lateness_d", "sched_arrival_ts": "sched_d"})
        j = u.merge(d, on=["trip_key", "seq"], how="inner")
        j["k"] = k
        parts.append(j)
    rows = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if rows.empty:
        return pd.DataFrame(columns=["trip_key", "route_id", "u", "d", "t", "delta_sec"] + FEATURES)
    rows["sched_run_sec"] = rows["sched_d"] - rows["sched_u"]
    rows = rows[(rows["sched_run_sec"] > 0) & (rows["sched_run_sec"] < 3 * 3600)]
    rows["delta_sec"] = (rows["lateness_d"] - rows["lateness_u"]).clip(-900, 3600)
    # dest_recent_lateness must be computed as of t (not t_d): recompute by merge_asof on the destination stop
    rows = rows.drop(columns=["dest_recent_lateness"])
    dest = m[["stop_id", "arrival_ts", "dest_recent_lateness"]].rename(columns={"stop_id": "d", "arrival_ts": "t_ref"}).sort_values("t_ref")
    rows = rows.sort_values("t")
    rows = pd.merge_asof(rows, dest, left_on="t", right_on="t_ref", by="d", direction="backward", allow_exact_matches=False)
    rows = rows.drop(columns=["t_ref"])
    # recent excess over the same (u, d) pair: mean delta of the last 3 trains that reached d before t
    seg = rows[["u", "d", "t_d", "delta_sec"]].sort_values("t_d").copy()
    seg["seg_recent_excess"] = seg.groupby(["u", "d"])["delta_sec"].transform(lambda s: s.rolling(3, min_periods=1).mean())
    seg = seg.rename(columns={"t_d": "t_ref"})[["u", "d", "t_ref", "seg_recent_excess"]].sort_values("t_ref")
    rows = rows.sort_values("t")
    rows = pd.merge_asof(rows, seg, left_on="t", right_on="t_ref", by=["u", "d"], direction="backward", allow_exact_matches=False)
    rows = rows.drop(columns=["t_ref"])
    rows.loc[rows["t"] - rows["t_d"].groupby([rows["u"], rows["d"]]).shift(1).fillna(rows["t"]) > 3600, "seg_recent_excess"] = np.nan
    # the feed's own forecast at that moment, when sampled
    rows["feed_excess"] = np.nan
    if eta_samples is not None and not eta_samples.empty:
        es = eta_samples.rename(columns={"stop_id": "d", "at_stop": "u"})[["trip_key", "u", "d", "eta_ts"]].drop_duplicates(["trip_key", "u", "d"])
        rows = rows.merge(es, on=["trip_key", "u", "d"], how="left")
        rows["feed_excess"] = rows["eta_ts"] - (rows["sched_d"] + rows["lateness_u"])
        rows = rows.drop(columns=["eta_ts"])
    # context
    local = pd.to_datetime(rows["t"], unit="s", utc=True).dt.tz_convert(NY_TZ)
    hour = local.dt.hour + local.dt.minute / 60.0
    rows["hour_sin"] = np.sin(2 * np.pi * hour / 24); rows["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    rows["weekend"] = (local.dt.weekday >= 5).astype(float)
    rows["peak"] = (local.dt.hour.isin(PEAK_HOURS) & (local.dt.weekday < 5)).astype(float)
    aidx = _alert_index(alerts_df)
    af = [_alert_feats(aidx, t, r) for t, r in zip(rows["t"].values, rows["route_id"].values)]
    rows["alert_active"] = [x[0] for x in af]; rows["planned_active"] = [x[1] for x in af]; rows["cause_code"] = [x[2] for x in af]
    ctx = context_index(None, weather_daily, events_df)
    day = local.dt.date.astype(str)
    w = [ctx.weather.get(dd) for dd in day]
    rows["precip_mm"] = [x[0] if x else 0.0 for x in w]; rows["heat"] = [x[1] if x else 0.0 for x in w]
    ef = [ctx.event_feats(t, r) for t, r in zip(rows["t"].values, rows["route_id"].values)] if ctx.events is not None else None
    for key in ("holiday", "venue_event_w", "street_event_w", "news_w"):
        rows[key] = [e[key] for e in ef] if ef else 0.0
    nws_arr = _nws_arrays(nws_df)
    nw = [nws_at(nws_arr, t) for t in rows["t"].values] if nws_arr is not None else None
    rows["nws_any"] = [x[0] for x in nw] if nw else 0.0
    rows["nws_severe"] = [x[1] for x in nw] if nw else 0.0
    rows["clim_rate"] = [clim_rate_at(climatology, r, t) for r, t in zip(rows["route_id"].values, rows["t"].values)] if climatology else np.nan
    rows["route_code"] = rows["route_id"].map(lambda r: ROUTE_CODE.get(str(r), len(ROUTES)))
    rows["direction_code"] = rows["direction"].map({"N": 0, "S": 1}).fillna(2).astype(int)
    keep = ["trip_key", "route_id", "u", "d", "t", "t_d", "sched_d", "delta_sec"] + FEATURES
    return rows[keep].reset_index(drop=True)


def live_features(route_id: str, direction: str | None, k: int, sched_run_sec: float, lateness_u: float, mom1: float | None,
                  mom3: float | None, gap_ahead_sec: float | None, leader_lateness: float | None, leader_same_route: float | None,
                  sched_headway_sec: float | None, seg_recent_excess: float | None, dest_recent_lateness: float | None,
                  feed_excess: float | None, track_changed: float, t: float, alerts_df=None, weather_daily=None, events_df=None,
                  nws_df=None, climatology: dict | None = None) -> dict:
    """A single feature row for serving, mirroring build_training_rows."""
    nws_any, nws_severe = nws_at(_nws_arrays(nws_df), t)
    clim = clim_rate_at(climatology, str(route_id), t)
    local = datetime.fromtimestamp(t, NY_TZ)
    hour = local.hour + local.minute / 60.0
    aidx = _alert_index(alerts_df)
    active, planned, cause = _alert_feats(aidx, t, str(route_id))
    ctx = context_index(None, weather_daily, events_df)
    w = ctx.weather.get(local.date().isoformat())
    ef = ctx.event_feats(t, str(route_id))
    nan = float("nan")
    return {"route_code": ROUTE_CODE.get(str(route_id), len(ROUTES)), "direction_code": {"N": 0, "S": 1}.get(direction or "", 2), "cause_code": cause,
            "k": k, "sched_run_sec": sched_run_sec, "lateness_u": lateness_u, "mom1": nan if mom1 is None else mom1, "mom3": nan if mom3 is None else mom3,
            "gap_ahead_sec": nan if gap_ahead_sec is None else gap_ahead_sec, "leader_lateness": nan if leader_lateness is None else leader_lateness,
            "leader_same_route": nan if leader_same_route is None else leader_same_route, "sched_headway_sec": nan if sched_headway_sec is None else sched_headway_sec,
            "seg_recent_excess": nan if seg_recent_excess is None else seg_recent_excess, "dest_recent_lateness": nan if dest_recent_lateness is None else dest_recent_lateness,
            "feed_excess": nan if feed_excess is None else feed_excess, "track_changed": track_changed,
            "hour_sin": math.sin(2 * math.pi * hour / 24), "hour_cos": math.cos(2 * math.pi * hour / 24),
            "weekend": float(local.weekday() >= 5), "peak": float(local.hour in PEAK_HOURS and local.weekday() < 5),
            "alert_active": active, "planned_active": planned, "precip_mm": w[0] if w else 0.0, "heat": w[1] if w else 0.0,
            "holiday": ef["holiday"], "venue_event_w": ef["venue_event_w"], "street_event_w": ef["street_event_w"], "news_w": ef["news_w"],
            "nws_any": nws_any, "nws_severe": nws_severe, "clim_rate": clim}
