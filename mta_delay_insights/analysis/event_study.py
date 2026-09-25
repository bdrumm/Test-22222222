"""Alert event study: how lateness on a route evolves around the moment an unplanned alert is posted.

For each unplanned alert with routes, the mean lateness of that route's observed
arrivals is binned in 5-minute steps from 60 minutes before the alert's creation
to 120 minutes after. Averaging the curves per cause gives the *detection lag*
(how long lateness had been rising before the alert), the *peak* and the
*recovery time* (when lateness returns within a margin of the pre-alert level).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..analysis.schedule_match import match_arrivals
from ..sources import alerts as alerts_src
from ..sources.gtfs_static import StaticGTFS

BINS = list(range(-60, 125, 5))     # minutes relative to alert creation


def _curve(lat: pd.DataFrame, t0: float) -> tuple[list, list]:
    rel = (lat["arrival_ts"] - t0) / 60.0
    vals, ns = [], []
    for b in BINS:
        m = (rel >= b) & (rel < b + 5)
        ns.append(int(m.sum()))
        vals.append(float(lat.loc[m, "lateness_sec"].mean()) if m.sum() >= 2 else None)
    return vals, ns


def event_study(arrivals: pd.DataFrame, alerts: pd.DataFrame, static: StaticGTFS, min_alerts: int = 3) -> dict:
    out = {"n_alerts": 0, "by_cause": [], "overall": None}
    if arrivals is None or arrivals.empty or alerts is None or alerts.empty:
        return out
    a = alerts.copy()
    a["kind"] = [alerts_src.alert_kind(t, h) for t, h in zip(a["alert_type"], a["header"])]
    a = a[(a["kind"] == "delay") & a["created_at"].notna()]
    a = a.drop_duplicates("alert_id")
    if a.empty:
        return out
    # only arrivals within the study windows are matched (the network history can be millions of rows)
    t0s = np.sort(a["created_at"].astype(float).values)
    ts = arrivals["arrival_ts"].values.astype(float)
    i = np.searchsorted(t0s, ts - 7500, side="left")          # first alert whose window could still contain ts
    in_window = (i < len(t0s)) & (ts >= t0s[np.clip(i, 0, len(t0s) - 1)] - 3600)
    sub = arrivals[in_window]
    if sub.empty:
        return out
    m = match_arrivals(sub, static).dropna(subset=["lateness_sec"])
    m["route_id"] = m["route_id"].astype(str)
    m = m[(m["lateness_sec"] > -600) & (m["lateness_sec"] < 5400)]
    curves = []
    for al in a.itertuples(index=False):
        routes = [str(r) for r in (al.routes or [])] if isinstance(al.routes, (list, tuple)) else []
        if not routes:
            continue
        t0 = float(al.created_at)
        win = m[m["route_id"].isin(routes) & (m["arrival_ts"] >= t0 - 3600) & (m["arrival_ts"] <= t0 + 7500)]
        if len(win) < 20:
            continue
        vals, ns = _curve(win, t0)
        pre = [v for v, b in zip(vals, BINS) if b < -15 and v is not None]
        post = [(v, b) for v, b in zip(vals, BINS) if b >= 0 and v is not None]
        base = float(np.mean(pre)) if pre else None
        peak = max(post, key=lambda x: x[0]) if post else None
        onset = None
        if base is not None:
            for v, b in zip(vals, BINS):
                if b < 0 and v is not None and v >= base + 120:
                    onset = b; break
        recovery = None
        if base is not None and peak is not None:
            for v, b in post:
                if b > peak[1] and v <= base + 60:
                    recovery = b; break
        curves.append({"alert_id": al.alert_id, "cause": str(al.cause_category), "routes": routes, "created_at": t0, "curve": vals, "n": ns,
                       "baseline_sec": base, "peak_sec": peak[0] if peak else None, "peak_min": peak[1] if peak else None,
                       "onset_min": onset, "recovery_min": recovery, "header": str(getattr(al, "header", ""))[:140]})
    out["n_alerts"] = len(curves)
    if not curves:
        return out

    def agg(cs: list[dict]) -> dict:
        arr = np.array([[np.nan if v is None else v for v in c["curve"]] for c in cs], dtype=float)
        mean = np.nanmean(arr, axis=0)
        cnt = np.sum(~np.isnan(arr), axis=0)
        onsets = [c["onset_min"] for c in cs if c["onset_min"] is not None]
        recs = [c["recovery_min"] for c in cs if c["recovery_min"] is not None]
        peaks = [c["peak_sec"] - c["baseline_sec"] for c in cs if c["peak_sec"] is not None and c["baseline_sec"] is not None]
        return {"n": len(cs), "bins": BINS, "mean_curve": [None if np.isnan(v) or n < 2 else float(v) for v, n in zip(mean, cnt)],
                "detection_lag_min": float(-np.median(onsets)) if onsets else None, "share_with_onset_before": (len(onsets) / len(cs)) if cs else None,
                "recovery_min": float(np.median(recs)) if recs else None, "share_recovered": (len(recs) / len(cs)) if cs else None,
                "peak_excess_sec": float(np.median(peaks)) if peaks else None}
    out["overall"] = agg(curves)
    by_cause = {}
    for c in curves:
        by_cause.setdefault(c["cause"], []).append(c)
    out["by_cause"] = [{"cause": k, **agg(v)} for k, v in sorted(by_cause.items(), key=lambda kv: -len(kv[1])) if len(v) >= min_alerts]
    out["examples"] = sorted(curves, key=lambda c: -(c["peak_sec"] or 0))[:6]
    return out
