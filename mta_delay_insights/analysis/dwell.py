"""Dwell-time analysis from vehicle-position transitions.

Each dwell row is a lower bound on the time a train sat in a station (first to
last poll seen STOPPED_AT). Aggregated by stop and hour they show where and
when trains hold; compared with hourly ridership they show how strongly dwell
grows with crowding (an elasticity that the model and the recommendations can
use: long dwells at a crowded station are a capacity problem, long dwells at a
quiet one are holds or dispatching).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..sources.gtfs_static import NY_TZ, StaticGTFS


def dwell_profile(dwells: pd.DataFrame, static: StaticGTFS, min_n: int = 8) -> dict:
    out = {"n": 0, "stops": [], "system_by_hour": [None] * 24}
    if dwells is None or dwells.empty:
        return out
    d = dwells[(dwells["dwell_sec"] >= 0) & (dwells["dwell_sec"] < 1200) & (dwells["polls"] >= 1)].copy()
    if d.empty:
        return out
    local = pd.to_datetime(d["stopped_from_ts"], unit="s", utc=True).dt.tz_convert(NY_TZ)
    d["hour"] = local.dt.hour; d["weekday"] = local.dt.weekday < 5
    out["n"] = int(len(d))
    sys_h = d.groupby("hour")["dwell_sec"].median().reindex(range(24))
    out["system_by_hour"] = [None if np.isnan(v) else float(v) for v in sys_h.values]
    for sid, g in d.groupby("stop_id"):
        if len(g) < min_n:
            continue
        by_h = g.groupby("hour")["dwell_sec"].agg(["median", "count"]).reindex(range(24))
        peak = g[g["weekday"] & g["hour"].isin([7, 8, 9, 16, 17, 18, 19])]["dwell_sec"]
        off = g[~(g["weekday"] & g["hour"].isin([7, 8, 9, 16, 17, 18, 19]))]["dwell_sec"]
        out["stops"].append({"stop_id": sid, "name": static.stop_name(sid), "n": int(len(g)), "median_sec": float(g["dwell_sec"].median()),
                             "p90_sec": float(g["dwell_sec"].quantile(0.9)), "share_over_90s": float((g["dwell_sec"] > 90).mean()),
                             "peak_median_sec": float(peak.median()) if len(peak) >= 5 else None, "offpeak_median_sec": float(off.median()) if len(off) >= 5 else None,
                             "by_hour": [None if np.isnan(v) else float(v) for v in by_h["median"].values],
                             "n_by_hour": [0 if np.isnan(v) else int(v) for v in by_h["count"].values],
                             "routes": sorted(g["route_id"].dropna().astype(str).unique().tolist())})
    out["stops"].sort(key=lambda s: -s["median_sec"])
    return out


def dwell_ridership_elasticity(profile: dict, ridership_profile: pd.DataFrame | None, stop_to_target: dict[str, str]) -> list[dict]:
    """Per monitored platform: correlation of median dwell by hour with hourly ridership of the complex."""
    if ridership_profile is None or ridership_profile.empty or not profile.get("stops"):
        return []
    rp = ridership_profile
    out = []
    for s in profile["stops"]:
        tid = stop_to_target.get(s["stop_id"])
        if tid is None:
            continue
        r = rp[rp["target_id"] == tid]
        if r.empty:
            continue
        rid = r.groupby("hour")["riders_per_hour"].mean().reindex(range(24)) if "riders_per_hour" in r else None
        if rid is None:
            continue
        x = np.array([v for v in rid.values], float); y = np.array([np.nan if v is None else v for v in s["by_hour"]], float)
        m = np.isfinite(x) & np.isfinite(y)
        if m.sum() < 8 or x[m].std() == 0 or y[m].std() == 0:
            continue
        rho = float(np.corrcoef(x[m], y[m])[0, 1])
        slope = float(np.polyfit(x[m], y[m], 1)[0])           # seconds of dwell per extra rider per hour
        out.append({"target_id": tid, "stop_id": s["stop_id"], "name": s["name"], "n_hours": int(m.sum()), "correlation": rho,
                    "sec_per_1000_riders_per_hour": slope * 1000.0,
                    "reading": ("crowding-driven: dwell rises with ridership" if rho >= 0.5 else
                                "not crowding-driven: long dwells happen regardless of ridership (holds, dispatching, merges)" if rho < 0.2 else "mixed")})
    return out
