"""Detect developing incidents from the last few minutes of observed arrivals, before an alert exists.

An incident on a segment shows up as consecutive trains all losing time
between the same two stops. The detector scans the recent arrivals for
(route, direction, segment) groups where most trains in the window lost at
least ``min_loss_sec`` and reports them with the mean loss, the number of
trains, when it started, and whether an unplanned alert already covers the
route (so riders and dispatchers see the "no alert yet" cases first).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..analysis.schedule_match import service_date_of
from ..sources.gtfs_static import StaticGTFS
from ..storage.db import Store


def developing_incidents(store: Store | None, static: StaticGTFS, now: float, alerts_now: pd.DataFrame | None = None,
                         window_sec: float = 1200.0, min_trains: int = 2, min_loss_sec: float = 120.0, min_share: float = 0.6) -> list[dict]:
    if store is None:
        return []
    a = store.arrivals(None, now - 3600, now + 1)
    if a.empty:
        return []
    a = a.sort_values(["trip_key", "arrival_ts"]).copy()
    sched = [static.scheduled_arrival(t, s, service_date_of(ts, sd)) for t, s, ts, sd in
             zip(a["trip_id"], a["stop_id"], a["arrival_ts"], a.get("start_date", pd.Series([None] * len(a), index=a.index)))]
    a["lat"] = a["arrival_ts"] - pd.to_numeric(pd.Series(sched, index=a.index), errors="coerce")
    a = a.dropna(subset=["lat"])
    g = a.groupby("trip_key")
    a["prev_stop"] = g["stop_id"].shift(1); a["prev_lat"] = g["lat"].shift(1); a["prev_ts"] = g["arrival_ts"].shift(1)
    a = a.dropna(subset=["prev_stop"])
    a = a[(a["arrival_ts"] - a["prev_ts"] < 1800) & (a["arrival_ts"] >= now - window_sec)]
    a["delta"] = a["lat"] - a["prev_lat"]
    alerted_routes: set[str] = set()
    if alerts_now is not None and not alerts_now.empty and "kind" in alerts_now:
        for r in alerts_now[alerts_now["kind"] == "delay"]["routes"]:
            alerted_routes |= {str(x) for x in (r or [])}
    out = []
    for (route, direction, prev, stop), grp in a.groupby(["route_id", "direction", "prev_stop", "stop_id"]):
        n = len(grp); slow = grp[grp["delta"] >= min_loss_sec]
        if n < min_trains or len(slow) < min_trains or len(slow) / n < min_share:
            continue
        out.append({"route_id": str(route), "direction": direction, "from_stop": prev, "to_stop": stop,
                    "from_name": static.stop_name(prev), "to_name": static.stop_name(stop), "n_trains": int(n), "n_slow": int(len(slow)),
                    "mean_loss_sec": float(slow["delta"].mean()), "max_loss_sec": float(slow["delta"].max()),
                    "first_seen_ts": float(slow["arrival_ts"].min()), "last_seen_ts": float(slow["arrival_ts"].max()),
                    "alerted": str(route) in alerted_routes,
                    "text": f"{route} {'northbound' if direction == 'N' else 'southbound'}: the last {len(slow)} of {n} trains lost "
                            f"{slow['delta'].mean() / 60:.1f} min between {static.stop_name(prev)} and {static.stop_name(stop)}"
                            + ("" if str(route) in alerted_routes else " (no alert posted yet)")})
    out.sort(key=lambda x: (x["alerted"], -x["mean_loss_sec"] * x["n_slow"]))
    return out[:12]
