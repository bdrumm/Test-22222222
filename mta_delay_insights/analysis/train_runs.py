"""Physical train runs: what happens to lateness at the terminal.

NYCT train ids carry the trip's own origin time, so a physical train gets a new
id every trip. Runs are therefore chained by *terminal turns*: a trip that ends
at station Y is matched, first-in-first-out, with the next trip of the same
route that starts from Y in the opposite direction within a plausible layover.
Comparing the lateness at the end of the inbound trip with the lateness at the
start of the outbound one shows whether the schedule's terminal recovery time
absorbs delays or they are carried into the next trip, the mechanism behind
afternoon delays that began in the morning.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..analysis.line_view import _lateness
from ..sources.gtfs_static import StaticGTFS

MIN_LAYOVER_SEC = 60.0
MAX_LAYOVER_SEC = 40 * 60.0


def terminal_pairs(arrivals: pd.DataFrame, static: StaticGTFS) -> pd.DataFrame:
    """Inbound/outbound trip pairs matched FIFO at each (route, terminal station)."""
    cols = ["route", "terminal", "in_trip", "out_trip", "in_last_ts", "out_first_ts", "layover_sec", "in_last_lat", "out_first_lat", "in_n", "out_n"]
    if arrivals is None or arrivals.empty:
        return pd.DataFrame(columns=cols)
    a = arrivals.copy()
    a["route_id"] = a["route_id"].astype(str)
    a["lat"] = _lateness(static, a)
    a = a.dropna(subset=["lat"]).sort_values(["trip_key", "arrival_ts"])
    t = a.groupby("trip_key").agg(route=("route_id", "first"), direction=("direction", "first"), first_ts=("arrival_ts", "min"), last_ts=("arrival_ts", "max"),
                                  first_stop=("stop_id", "first"), last_stop=("stop_id", "last"), first_lat=("lat", "first"), last_lat=("lat", "last"), n=("lat", "size")).reset_index()
    t = t[t["n"] >= 3]
    t["last_parent"] = t["last_stop"].map(static.parent_of)
    t["first_parent"] = t["first_stop"].map(static.parent_of)
    rows = []
    for (route, term), inbound in t.groupby(["route", "last_parent"]):
        outbound = t[(t["route"] == route) & (t["first_parent"] == term) & (t["direction"] != inbound["direction"].iloc[0])].sort_values("first_ts")
        if outbound.empty:
            continue
        out_ts = outbound["first_ts"].values
        used = np.zeros(len(outbound), dtype=bool)
        for r in inbound.sort_values("last_ts").itertuples(index=False):
            i = int(np.searchsorted(out_ts, r.last_ts + MIN_LAYOVER_SEC, side="left"))
            while i < len(out_ts) and used[i]:
                i += 1
            if i >= len(out_ts) or out_ts[i] - r.last_ts > MAX_LAYOVER_SEC:
                continue
            used[i] = True
            o = outbound.iloc[i]
            rows.append({"route": route, "terminal": term, "in_trip": r.trip_key, "out_trip": o["trip_key"], "in_last_ts": r.last_ts, "out_first_ts": float(o["first_ts"]),
                         "layover_sec": float(o["first_ts"] - r.last_ts), "in_last_lat": r.last_lat, "out_first_lat": float(o["first_lat"]), "in_n": r.n, "out_n": int(o["n"])})
    return pd.DataFrame(rows, columns=cols)


def terminal_recovery(arrivals: pd.DataFrame, static: StaticGTFS, min_pairs: int = 20) -> dict:
    out = {"n_pairs": 0, "by_route": [], "by_terminal": [], "overall": None}
    pairs = terminal_pairs(arrivals, static)
    out["n_pairs"] = int(len(pairs))
    if len(pairs) < min_pairs:
        return out

    def summ(p: pd.DataFrame) -> dict:
        late_in = p[p["in_last_lat"] >= 300]
        return {"n": int(len(p)), "median_layover_min": float(p["layover_sec"].median() / 60),
                "carry_slope": float(np.polyfit(p["in_last_lat"], p["out_first_lat"], 1)[0]) if p["in_last_lat"].std() > 0 else None,
                "share_late_in": float((p["in_last_lat"] >= 300).mean()),
                "share_late_out_given_late_in": float((late_in["out_first_lat"] >= 300).mean()) if len(late_in) >= 5 else None,
                "median_recovered_sec": float((late_in["in_last_lat"] - late_in["out_first_lat"]).median()) if len(late_in) >= 5 else None}
    out["overall"] = summ(pairs)
    for r, p in pairs.groupby("route"):
        if len(p) >= min_pairs:
            out["by_route"].append({"route": str(r), **summ(p)})
    for (r, term), p in pairs.groupby(["route", "terminal"]):
        if len(p) >= min_pairs:
            out["by_terminal"].append({"route": str(r), "terminal": term, "terminal_name": static.stop_name(term), **summ(p)})
    out["by_route"].sort(key=lambda x: -(x["share_late_out_given_late_in"] or 0))
    out["by_terminal"].sort(key=lambda x: -(x["share_late_out_given_late_in"] or 0))
    return out
