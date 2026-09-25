"""Where trains get held, and how long it takes for an alert to follow.

The hold log is derived from vehicle positions at every stop: a train reported ``STOPPED_AT`` a
station for ``HOLD_SEC`` or more (a normal dwell is 30-60 s). Holds are the earliest visible
symptom of most incidents, so matching long holds to the unplanned alerts posted for the same
route measures the *alert latency*: how long riders on the platform knew before the MTA said so.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from ..collect.dwells import HOLD_SEC
from ..sources.alerts import alert_kind
from ..sources.gtfs_static import NY_TZ, StaticGTFS

LONG_HOLD_SEC = 300.0            # holds this long are matched to alerts
ALERT_BEFORE_SEC = 1800.0        # an alert already active this long before the hold counts as "posted before"
ALERT_AFTER_SEC = 3600.0         # an alert posted within this long after the hold started counts as "posted after"


def _hour(ts: float) -> int:
    return datetime.fromtimestamp(float(ts), NY_TZ).hour


def _unplanned_delays(alerts: pd.DataFrame | None) -> pd.DataFrame:
    if alerts is None or alerts.empty:
        return pd.DataFrame(columns=["alert_id", "routes", "active_start", "end_ts", "header"])
    a = alerts.copy()
    a = a[~a["planned"].astype(bool)]
    kinds = [alert_kind(t, hd) for t, hd in zip(a["alert_type"], a["header"])]
    a = a[[k == "delay" for k in kinds]]
    a["end_ts"] = a["active_end"].fillna(a["updated_at"].fillna(a["active_start"]) + 3 * 3600)
    a["routes"] = a["routes"].map(lambda r: list(r) if isinstance(r, (list, tuple)) else ([] if r is None or (isinstance(r, float) and np.isnan(r)) else [str(r)]))
    return a[["alert_id", "routes", "active_start", "end_ts", "header"]].dropna(subset=["active_start"])


def match_holds_to_alerts(holds: pd.DataFrame, alerts: pd.DataFrame | None) -> pd.DataFrame:
    """For each hold: the first unplanned delay alert naming its route that overlaps the hold window,
    and the latency from the hold's start to the alert's start (negative = alert came first)."""
    out = holds.copy()
    out["alert_id"] = None
    out["alert_latency_sec"] = np.nan
    ua = _unplanned_delays(alerts)
    if out.empty or ua.empty:
        return out
    for i, r in out.iterrows():
        cand = ua[ua["routes"].map(lambda rs: str(r["route_id"]) in rs or len(rs) == 0)]
        cand = cand[(cand["active_start"] <= r["stopped_from_ts"] + ALERT_AFTER_SEC) & (cand["end_ts"] >= r["stopped_from_ts"] - ALERT_BEFORE_SEC)]
        if cand.empty:
            continue
        # the alert whose start is closest to the hold's start
        j = (cand["active_start"] - r["stopped_from_ts"]).abs().idxmin()
        out.at[i, "alert_id"] = cand.at[j, "alert_id"]
        out.at[i, "alert_latency_sec"] = float(cand.at[j, "active_start"] - r["stopped_from_ts"])
    return out


def terminals(static: StaticGTFS | None, routes) -> set[str]:
    """Both ends of each route/direction's canonical sequence: trains wait to depart or relay there by design."""
    out: set[str] = set()
    if static is None:
        return out
    for r in routes:
        for d in ("N", "S"):
            try:
                seq = static.canonical_stop_sequence(str(r), d)
            except Exception:
                seq = []
            if seq:
                out.add(seq[0]); out.add(seq[-1])
    return out


origin_terminals = terminals   # backwards-compatible name


def hold_summary(holds: pd.DataFrame | None, alerts: pd.DataFrame | None, static: StaticGTFS | None,
                 hold_sec: float = HOLD_SEC, long_sec: float = LONG_HOLD_SEC) -> dict:
    empty = {"n": 0, "days": 0, "by_stop": [], "by_route": [], "by_hour": [0] * 24, "long": None, "longest": [], "n_terminal": 0}
    if holds is None or holds.empty:
        return empty
    h = holds[holds["dwell_sec"] >= hold_sec].copy()
    origins = terminals(static, h["route_id"].dropna().unique())
    n_terminal = int(h["stop_id"].isin(origins).sum())
    h = h[~h["stop_id"].isin(origins)]
    if h.empty:
        return {**empty, "n_terminal": n_terminal}
    name = static.stop_name if static is not None else (lambda s: s)
    days = max(1, int(pd.to_datetime(h["stopped_from_ts"], unit="s", utc=True).dt.tz_convert("America/New_York").dt.date.nunique()))
    h["hour"] = h["stopped_from_ts"].map(_hour)
    by_stop = []
    for sid, g in h.groupby("stop_id"):
        by_stop.append({"stop_id": sid, "name": name(sid), "routes": sorted({str(x) for x in g["route_id"].dropna()}), "n": int(len(g)), "per_day": round(len(g) / days, 2),
                        "median_sec": round(float(g["dwell_sec"].median())), "p90_sec": round(float(g["dwell_sec"].quantile(0.9))), "total_min": round(float(g["dwell_sec"].sum()) / 60, 1),
                        "worst_hours": [int(x) for x in g["hour"].value_counts().index[:3]]})
    by_stop.sort(key=lambda x: -x["total_min"])
    by_route = []
    for rid, g in h.groupby("route_id"):
        by_route.append({"route": str(rid), "n": int(len(g)), "per_day": round(len(g) / days, 2), "median_sec": round(float(g["dwell_sec"].median())),
                         "total_min_per_day": round(float(g["dwell_sec"].sum()) / 60 / days, 1)})
    by_route.sort(key=lambda x: -x["total_min_per_day"])
    by_hour = [round(int((h["hour"] == k).sum()) / days, 2) for k in range(24)]
    long = h[h["dwell_sec"] >= long_sec]
    long_out = None
    if not long.empty:
        m = match_holds_to_alerts(long, alerts)
        with_alert = m.dropna(subset=["alert_latency_sec"])
        after = with_alert[with_alert["alert_latency_sec"] > 0]
        long_out = {"n": int(len(m)), "per_day": round(len(m) / days, 2), "share_with_alert": round(float(len(with_alert) / len(m)), 3),
                    "share_alert_after": round(float(len(after) / len(m)), 3), "share_alert_before": round(float((with_alert["alert_latency_sec"] <= 0).sum() / len(m)), 3),
                    "median_latency_sec": round(float(after["alert_latency_sec"].median())) if len(after) else None,
                    "p75_latency_sec": round(float(after["alert_latency_sec"].quantile(0.75))) if len(after) else None,
                    "median_hold_sec": round(float(m["dwell_sec"].median()))}
        top = m.sort_values("dwell_sec", ascending=False).head(10)
        longest = [{"route": str(r.route_id), "stop_id": r.stop_id, "name": name(r.stop_id), "start_ts": float(r.stopped_from_ts), "dwell_sec": float(r.dwell_sec),
                    "alert_latency_sec": None if pd.isna(r.alert_latency_sec) else float(r.alert_latency_sec), "alert_id": r.alert_id}
                   for r in top.itertuples(index=False)]
    else:
        longest = []
    return {"n": int(len(h)), "n_terminal": n_terminal, "days": days, "per_day": round(len(h) / days, 1), "hold_sec": hold_sec, "long_sec": long_sec,
            "median_sec": round(float(h["dwell_sec"].median())), "by_stop": by_stop[:25], "by_route": by_route, "by_hour": by_hour,
            "long": long_out, "longest": longest}
