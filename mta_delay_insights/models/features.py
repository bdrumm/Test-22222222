"""Training rows for the downstream arrival model.

One row per (trip, stop u, stop d) with d exactly ``k`` stops after ``u`` in the
trip's observed sequence. Everything in the row is known at the moment the
train serves ``u`` (time ``t``):

* the train: route, direction, lateness at ``u``, how its lateness changed over
  the previous 1 and 3 stops (momentum), the scheduled run time ``u -> d``,
  whether it is on a different track than scheduled, where ``u`` sits on the
  line (index on the canonical stop sequence, stops left to the end, whether
  ``d`` is the last stop);
* the traffic ahead: gap to the train in front at ``u``, that train's lateness
  and momentum, the scheduled headway;
* the segment right now: mean excess run time of the last three trains that
  completed ``u -> d`` before ``t``, the last one's excess and how long ago it
  got there, and the mean lateness at ``d`` in the previous 15 minutes;
* the line right now: mean lateness of the route (same direction) over the last
  30 minutes, of the whole network over the last 15, and how many arrivals the
  route logged in the last 30 minutes (throughput);
* the feed's own forecast for ``d`` at that moment when an ETA sample exists;
* the calendar: hour (cyclic and plain), weekday, service day type, time band,
  weekend / peak / holiday;
* the day's pattern: the typical excess on this segment at this day type and
  hour and the route's typical lateness then (fitted on the training span, see
  :mod:`.arrival`), plus the alert-archive climatology;
* weather: the hour's temperature, precipitation (this hour and the last three),
  snow, wind and weather-code group, the day's total rain and heat, NWS alerts;
* events and alerts: unplanned alerts on the route (count, cause, age), alerts
  network-wide, planned work, venue / street / news event weights.

Target: ``delta_sec = lateness at d - lateness at u`` (the excess run time).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..analysis.schedule_match import match_arrivals
from ..sources.gtfs_static import StaticGTFS
from .context import FeatureContext, cached_context

K_SET = (1, 2, 3, 5, 8, 12, 16, 20)
CATEGORICAL = ["route_code", "direction_code", "cause_code"]
STATE = ["k", "sched_run_sec", "lateness_u", "mom1", "mom3", "gap_ahead_sec", "leader_lateness", "leader_same_route", "leader_mom1",
         "sched_headway_sec", "seg_recent_excess", "seg_last_excess", "seg_staleness", "dest_recent_lateness", "feed_excess", "track_changed",
         "pos_u", "stops_to_end", "d_is_last"]
TIME = ["hour_sin", "hour_cos", "hour", "dow", "daytype", "band", "weekend", "peak", "holiday"]
PATTERNS = ["prof_seg_excess", "prof_route_lateness", "route_recent_lateness", "net_recent_lateness", "route_arrivals_30", "clim_rate"]
WEATHER = ["precip_mm", "heat", "temp_c", "precip_hr_mm", "precip_3h_mm", "snow_cm", "wind_kmh", "wcode_group", "nws_any", "nws_severe"]
EVENTS = ["alert_active", "planned_active", "alert_n", "alert_age_min", "net_alert_n", "venue_event_w", "street_event_w", "news_w"]
NUMERIC = STATE + TIME + PATTERNS + WEATHER + EVENTS
FEATURES = CATEGORICAL + NUMERIC
PROFILE_FEATURES = ["prof_seg_excess", "prof_route_lateness"]
# the feature set the first deployed model used (for like-for-like comparisons)
LEGACY_FEATURES = CATEGORICAL + ["k", "sched_run_sec", "lateness_u", "mom1", "mom3", "gap_ahead_sec", "leader_lateness", "leader_same_route",
                                 "sched_headway_sec", "seg_recent_excess", "dest_recent_lateness", "feed_excess", "track_changed",
                                 "hour_sin", "hour_cos", "weekend", "peak", "alert_active", "planned_active",
                                 "precip_mm", "heat", "holiday", "venue_event_w", "street_event_w", "news_w", "nws_any", "nws_severe", "clim_rate"]
GROUPS = {"state": ["route_code", "direction_code"] + STATE, "time": TIME, "patterns": PATTERNS, "weather": WEATHER, "events": ["cause_code"] + EVENTS}
ROUTES = ["1", "2", "3", "4", "5", "6", "6X", "7", "7X", "A", "B", "C", "D", "E", "F", "FX", "G", "J", "L", "M", "N", "Q", "R", "S", "SI", "W", "Z", "FS", "GS", "H"]
ROUTE_CODE = {r: i for i, r in enumerate(ROUTES)}
PEAK_HOURS = {7, 8, 9, 16, 17, 18, 19}
KEY_COLUMNS = ["trip_key", "route_id", "direction", "u", "d", "t", "t_d", "sched_d", "source", "delta_sec"]


def _recent_mean(m: pd.DataFrame, keys: list[str] | None, window: float, value: str = "lateness_sec") -> tuple[np.ndarray, np.ndarray]:
    """Mean of ``value`` and count over the arrivals strictly before each row's time within ``window`` seconds, per key group."""
    mean = np.full(len(m), np.nan); count = np.zeros(len(m))
    groups = m.groupby(keys, sort=False).indices.values() if keys else [np.arange(len(m))]
    ts_all = m["arrival_ts"].values.astype(float); val_all = m[value].values.astype(float)
    for idx in groups:
        idx = np.asarray(idx)
        order = np.argsort(ts_all[idx], kind="stable")
        idx = idx[order]
        ts = ts_all[idx]; v = np.nan_to_num(val_all[idx])
        cs = np.concatenate([[0.0], np.cumsum(v)])
        lo = np.searchsorted(ts, ts - window, side="left"); hi = np.searchsorted(ts, ts, side="left")
        n = hi - lo
        mean[idx] = np.where(n > 0, (cs[hi] - cs[lo]) / np.maximum(n, 1), np.nan)
        count[idx] = n
    return mean, count


def build_training_rows(arrivals: pd.DataFrame, static: StaticGTFS, alerts_df: pd.DataFrame | None = None,
                        weather_daily: pd.DataFrame | None = None, events_df: pd.DataFrame | None = None,
                        eta_samples: pd.DataFrame | None = None, k_set=K_SET, min_confidence: float = 0.6,
                        nws_df: pd.DataFrame | None = None, climatology: dict | None = None, *,
                        weather_hourly: pd.DataFrame | None = None, alerts_archive: pd.DataFrame | None = None,
                        ctx: FeatureContext | None = None) -> pd.DataFrame:
    """Vectorised construction of the training table from observed arrivals."""
    empty = pd.DataFrame(columns=KEY_COLUMNS + FEATURES)
    if arrivals is None or arrivals.empty:
        return empty
    a = arrivals[arrivals["confidence"] >= min_confidence].copy()
    a = a.drop_duplicates(["trip_key", "stop_id"]).sort_values(["trip_key", "arrival_ts"])
    m = match_arrivals(a, static).dropna(subset=["lateness_sec"])
    m["route_id"] = m["route_id"].astype(str)
    m = m[(m["lateness_sec"] > -900) & (m["lateness_sec"] < 5400)]
    m = m.sort_values(["trip_key", "arrival_ts"]).reset_index(drop=True)
    if "source" not in m.columns:
        m["source"] = "unknown"
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
    m["leader_mom1"] = gs["mom1"].shift(1)
    m.loc[m["gap_ahead_sec"] > 3600, ["gap_ahead_sec", "leader_lateness", "leader_mom1"]] = np.nan
    m = m.reset_index(drop=True)
    # where the stop sits on the line: index on the route's canonical sequence and stops left to its end
    m["pos"] = np.nan; m["line_len"] = np.nan
    seq_cache: dict[tuple[str, str], dict[str, int]] = {}
    for (r, dr), idx in m.groupby(["route_id", "direction"], sort=False).indices.items():
        key = (str(r), str(dr or "N"))
        if key not in seq_cache:
            try:
                seq_cache[key] = {sid: i for i, sid in enumerate(static.canonical_stop_sequence(*key))}
            except Exception:
                seq_cache[key] = {}
        sq = seq_cache[key]
        if sq:
            m.loc[m.index[idx], "pos"] = [sq.get(sid, np.nan) for sid in m["stop_id"].values[idx]]
            m.loc[m.index[idx], "line_len"] = float(len(sq))
    # recent lateness at each stop over the previous 15 minutes (trains that already arrived)
    m["dest_recent_lateness"], _ = _recent_mean(m, ["stop_id"], 900.0)
    # the line and the network right now
    m["route_recent_lateness"], m["route_arrivals_30"] = _recent_mean(m, ["route_id", "direction"], 1800.0)
    m["net_recent_lateness"], _ = _recent_mean(m, None, 900.0)
    m = m.sort_values(["trip_key", "seq"]).reset_index(drop=True)
    tracks = ("sched_track" in m.columns) and ("actual_track" in m.columns)
    m["track_changed"] = ((m["actual_track"].notna()) & (m["sched_track"].notna()) & (m["actual_track"] != m["sched_track"])).astype(float) if tracks else 0.0

    base_cols = ["trip_key", "route_id", "direction", "stop_id", "arrival_ts", "lateness_sec", "sched_arrival_ts", "sched_headway_sec",
                 "mom1", "mom3", "gap_ahead_sec", "leader_lateness", "leader_same_route", "leader_mom1", "track_changed", "seq", "source",
                 "route_recent_lateness", "route_arrivals_30", "net_recent_lateness", "pos", "line_len"]
    u = m[base_cols].rename(columns={"stop_id": "u", "arrival_ts": "t", "lateness_sec": "lateness_u", "sched_arrival_ts": "sched_u", "pos": "pos_u"})
    parts = []
    for k in k_set:
        d = m[["trip_key", "stop_id", "arrival_ts", "lateness_sec", "sched_arrival_ts", "seq", "pos"]].copy()
        d["seq"] = d["seq"] - k
        d = d.rename(columns={"stop_id": "d", "arrival_ts": "t_d", "lateness_sec": "lateness_d", "sched_arrival_ts": "sched_d", "pos": "pos_d"})
        j = u.merge(d, on=["trip_key", "seq"], how="inner")
        j["k"] = k
        parts.append(j)
    rows = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if rows.empty:
        return empty
    rows["sched_run_sec"] = rows["sched_d"] - rows["sched_u"]
    rows = rows[(rows["sched_run_sec"] > 0) & (rows["sched_run_sec"] < 3 * 3600)]
    rows["delta_sec"] = (rows["lateness_d"] - rows["lateness_u"]).clip(-900, 3600)
    rows["stops_to_end"] = rows["line_len"] - 1 - rows["pos_u"]
    rows["d_is_last"] = (rows["pos_d"] >= rows["line_len"] - 1).astype(float).where(rows["pos_d"].notna() & rows["line_len"].notna(), np.nan)
    # dest_recent_lateness as of t (not t_d): look up the destination's state at the moment the train left u
    dest = m[["stop_id", "arrival_ts", "dest_recent_lateness"]].rename(columns={"stop_id": "d", "arrival_ts": "t_ref"}).sort_values("t_ref")
    rows = rows.sort_values("t")
    rows = pd.merge_asof(rows, dest, left_on="t", right_on="t_ref", by="d", direction="backward", allow_exact_matches=False)
    rows = rows.drop(columns=["t_ref"])
    # recent excess over the same (u, d) pair: mean delta of the last 3 trains that reached d before t, the last
    # one's own excess and how long ago it got to d
    seg = rows[["u", "d", "t_d", "delta_sec"]].sort_values("t_d").copy()
    seg["seg_recent_excess"] = seg.groupby(["u", "d"])["delta_sec"].transform(lambda s: s.rolling(3, min_periods=1).mean())
    seg = seg.rename(columns={"t_d": "t_ref", "delta_sec": "seg_last_excess"})[["u", "d", "t_ref", "seg_recent_excess", "seg_last_excess"]].sort_values("t_ref")
    rows = rows.sort_values("t")
    rows = pd.merge_asof(rows, seg, left_on="t", right_on="t_ref", by=["u", "d"], direction="backward", allow_exact_matches=False)
    rows["seg_staleness"] = (rows["t"] - rows["t_ref"]).clip(upper=7200.0)
    rows = rows.drop(columns=["t_ref"])
    stale = rows["seg_staleness"].isna() | (rows["seg_staleness"] > 3600)
    rows.loc[stale, ["seg_recent_excess", "seg_last_excess"]] = np.nan
    # the feed's own forecast at that moment, when sampled
    rows["feed_excess"] = np.nan
    if eta_samples is not None and not eta_samples.empty:
        # the feed's forecast for d from the last poll before the train reached u (within 20 minutes)
        es = eta_samples.rename(columns={"stop_id": "d", "at_stop": "u"})[["trip_key", "u", "d", "at_ts", "eta_ts"]].dropna()
        es = es[es["trip_key"].isin(rows["trip_key"].unique())].sort_values("at_ts")
        if not es.empty:
            rows = rows.sort_values("t")
            rows = pd.merge_asof(rows, es, left_on="t", right_on="at_ts", by=["trip_key", "u", "d"], direction="backward", tolerance=1200.0)
            rows["feed_excess"] = rows["eta_ts"] - (rows["sched_d"] + rows["lateness_u"])
            rows = rows.drop(columns=["eta_ts", "at_ts"])
    rows = rows.reset_index(drop=True)
    # context: calendar, alerts, events, weather, NWS, climatology (vectorised)
    ctx = ctx or FeatureContext.build(alerts_df, weather_daily, events_df, nws_df, climatology, weather_hourly, alerts_archive)
    cf = ctx.frame(rows["t"].values, rows["route_id"].values)
    for c in cf.columns:
        rows[c] = cf[c].values
    for c in PROFILE_FEATURES:
        rows[c] = np.nan            # filled by the model's day-pattern profiles (fitted on the training span)
    rows["route_code"] = rows["route_id"].map(lambda r: ROUTE_CODE.get(str(r), len(ROUTES)))
    rows["direction_code"] = rows["direction"].map({"N": 0, "S": 1}).fillna(2).astype(int)
    return rows[KEY_COLUMNS + FEATURES].reset_index(drop=True)


def batch_live_features(rows: list[dict], ctx: FeatureContext) -> pd.DataFrame:
    """Feature frame for serving rows that carry the state columns plus route_id, direction, u, d and t; the
    calendar, weather, alert and event columns are added in one vectorised pass. Profile columns are left NaN
    for the model to fill from its day-pattern profiles."""
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    cf = ctx.frame(df["t"].values.astype(float), df["route_id"].astype(str).values)
    for c in cf.columns:
        df[c] = cf[c].values
    for c in PROFILE_FEATURES:
        if c not in df.columns:
            df[c] = np.nan
    df["route_code"] = df["route_id"].map(lambda r: ROUTE_CODE.get(str(r), len(ROUTES)))
    df["direction_code"] = df["direction"].map({"N": 0, "S": 1}).fillna(2).astype(int)
    for c in FEATURES:
        if c not in df.columns:
            df[c] = np.nan
    return df


def live_features(route_id: str, direction: str | None, k: int, sched_run_sec: float, lateness_u: float, mom1: float | None,
                  mom3: float | None, gap_ahead_sec: float | None, leader_lateness: float | None, leader_same_route: float | None,
                  sched_headway_sec: float | None, seg_recent_excess: float | None, dest_recent_lateness: float | None,
                  feed_excess: float | None, track_changed: float, t: float, alerts_df=None, weather_daily=None, events_df=None,
                  nws_df=None, climatology: dict | None = None, *, weather_hourly=None, alerts_archive=None, ctx: FeatureContext | None = None,
                  route_recent_lateness: float | None = None, net_recent_lateness: float | None = None, route_arrivals_30: float | None = None,
                  u: str | None = None, d: str | None = None, leader_mom1: float | None = None, seg_last_excess: float | None = None,
                  seg_staleness: float | None = None, pos_u: float | None = None, stops_to_end: float | None = None, d_is_last: float | None = None) -> dict:
    """A single feature row for serving, mirroring build_training_rows (profile features are added by the model)."""
    ctx = ctx or cached_context(alerts_df, weather_daily, events_df, nws_df, climatology, weather_hourly, alerts_archive)
    cf = ctx.frame(np.array([float(t)]), np.array([str(route_id)])).iloc[0].to_dict()
    nan = float("nan")

    def f(v):
        return nan if v is None else float(v)
    row = {"route_id": str(route_id), "direction": direction, "u": u, "d": d, "t": float(t),
           "route_code": ROUTE_CODE.get(str(route_id), len(ROUTES)), "direction_code": {"N": 0, "S": 1}.get(direction or "", 2),
           "k": k, "sched_run_sec": sched_run_sec, "lateness_u": lateness_u, "mom1": f(mom1), "mom3": f(mom3),
           "gap_ahead_sec": f(gap_ahead_sec), "leader_lateness": f(leader_lateness), "leader_same_route": f(leader_same_route),
           "sched_headway_sec": f(sched_headway_sec), "seg_recent_excess": f(seg_recent_excess), "dest_recent_lateness": f(dest_recent_lateness),
           "feed_excess": f(feed_excess), "track_changed": float(track_changed or 0.0),
           "route_recent_lateness": f(route_recent_lateness), "net_recent_lateness": f(net_recent_lateness), "route_arrivals_30": f(route_arrivals_30),
           "leader_mom1": f(leader_mom1), "seg_last_excess": f(seg_last_excess), "seg_staleness": f(seg_staleness),
           "pos_u": f(pos_u), "stops_to_end": f(stops_to_end), "d_is_last": f(d_is_last)}
    row.update({c: float(cf[c]) for c in cf})
    for c in PROFILE_FEATURES:
        row[c] = nan
    return row
