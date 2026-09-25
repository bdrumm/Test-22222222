"""Network scorecard: one row per route and direction from network-wide arrivals.

Metrics per route/direction over the recent history: trips observed, mean and
p90 lateness at observed stops, share of stop arrivals ≥ 5 min late, headway
regularity (coefficient of variation of headways at the busiest stop, by
period), running-time loss per trip (sum of positive lateness changes), the
worst segment, and the share of trips whose lateness grew by ≥ 3 min end to
end. Sparklines of mean lateness by hour make the table scannable.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..analysis.line_view import _lateness
from ..sources.gtfs_static import NY_TZ, StaticGTFS

PEAK = {7, 8, 9, 16, 17, 18, 19}


def scorecard(arrivals: pd.DataFrame, static: StaticGTFS, routes: list[str] | None = None, min_trips: int = 30) -> dict:
    out = {"rows": [], "n_arrivals": 0}
    if arrivals is None or arrivals.empty:
        return out
    a = arrivals.copy()
    a["route_id"] = a["route_id"].astype(str)
    if routes:
        a = a[a["route_id"].isin(routes)]
    a["lat"] = _lateness(static, a)
    a = a.dropna(subset=["lat"])
    a = a[(a["lat"] > -900) & (a["lat"] < 5400)]
    out["n_arrivals"] = int(len(a))
    local = pd.to_datetime(a["arrival_ts"], unit="s", utc=True).dt.tz_convert(NY_TZ)
    a["hour"] = local.dt.hour; a["weekday"] = local.dt.weekday < 5
    a = a.sort_values(["trip_key", "arrival_ts"])
    a["prev_lat"] = a.groupby("trip_key")["lat"].shift(1)
    a["prev_ts"] = a.groupby("trip_key")["arrival_ts"].shift(1)
    a["delta"] = np.where(a["arrival_ts"] - a["prev_ts"] < 1800, a["lat"] - a["prev_lat"], np.nan)
    days = max(1.0, (a["arrival_ts"].max() - a["arrival_ts"].min()) / 86400)
    for (route, direction), g in a.groupby(["route_id", "direction"]):
        if direction not in ("N", "S"):
            continue
        n_trips = g["trip_key"].nunique()
        if n_trips < min_trips:
            continue
        by_trip = g.groupby("trip_key").agg(first_lat=("lat", "first"), last_lat=("lat", "last"), loss=("delta", lambda s: float(np.clip(s.dropna(), 0, None).sum())))
        seg = g.dropna(subset=["delta"]).groupby("stop_id")["delta"].agg(["mean", "count"])
        seg = seg[seg["count"] >= 10].sort_values("mean", ascending=False)
        worst = seg.index[0] if len(seg) else None
        busiest = g["stop_id"].value_counts().index[0]
        hw = g[g["stop_id"] == busiest].sort_values("arrival_ts")
        hw_vals = hw["arrival_ts"].diff()
        hw = hw.assign(hw=hw_vals)[(hw_vals > 30) & (hw_vals < 3600)]
        def cv(sub):
            return float(sub["hw"].std() / sub["hw"].mean()) if len(sub) >= 10 and sub["hw"].mean() > 0 else None
        peak = hw[hw["hour"].isin(PEAK) & hw["weekday"]]; off = hw[~(hw["hour"].isin(PEAK) & hw["weekday"])]
        hourly = g.groupby("hour")["lat"].mean().reindex(range(24))
        out["rows"].append({
            "route": route, "direction": direction, "n_trips": int(n_trips), "trips_per_day": float(n_trips / days),
            "mean_lateness_sec": float(g["lat"].mean()), "p90_lateness_sec": float(g["lat"].quantile(0.9)),
            "share_late_5min": float((g["lat"] >= 300).mean()), "share_early": float((g["lat"] <= -60).mean()),
            "loss_per_trip_sec": float(by_trip["loss"].mean()), "share_trips_grew_3min": float(((by_trip["last_lat"] - by_trip["first_lat"]) >= 180).mean()),
            "headway_cv_peak": cv(peak), "headway_cv_offpeak": cv(off), "busiest_stop": static.stop_name(busiest),
            "worst_segment_stop": static.stop_name(worst) if worst else None, "worst_segment_loss_sec": float(seg["mean"].iloc[0]) if len(seg) else None,
            "hourly_mean_lateness": [None if np.isnan(v) else round(float(v), 1) for v in hourly.values],
        })
    out["rows"].sort(key=lambda r: -(r["share_late_5min"] * 100 + r["loss_per_trip_sec"] / 60))
    out["days"] = float(days)
    return out
