"""Historical MTA service alerts (data.ny.gov 7kct-peq7, since April 2020) and the
disruption climatology derived from them.

Each row is one update of an alert; ``event_id`` groups the updates of a single
disruption, so first/last update give its start and duration. ``affected``
lists the routes (``"E | F"``), ``status_label`` the kind (delays, some-delays,
part-suspended, reroute, stops-skipped, slow-speeds, planned-work, ...), and the
header text carries the cause, which the live-alert classifier already knows
how to tag.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .alerts import classify_cause
from .gtfs_static import NY_TZ
from .open_data import SocrataClient

DATASET = "7kct-peq7"
UNPLANNED = {"delays", "some-delays", "cancellations", "part-suspended", "delays-and-cancellations", "slow-speeds",
             "expect-delays", "reroute | delays", "express-to-local | delays", "trains-rerouted", "reroute", "stops-skipped",
             "stations-skipped", "suspended", "local-to-express", "express-to-local"}
PLANNED = {"planned-work", "weekday-service", "weekend-service", "no-scheduled-service", "service-change", "boarding-change", "station-notice"}


def fetch_archive(since: str, client: SocrataClient | None = None, agency: str = "NYCT Subway", limit: int = 200_000) -> pd.DataFrame:
    client = client or SocrataClient()
    where = f"agency='{agency}' AND date>='{since}'"
    df = client.fetch(DATASET, where=where, order="date ASC", max_rows=limit)
    return normalize(df)


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["alert_id", "event_id", "update_number", "ts", "status_label", "routes", "header", "cause_category", "planned"]
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    d = df.copy()
    d["ts"] = pd.to_datetime(d["date"]).dt.tz_localize(NY_TZ, ambiguous="NaT", nonexistent="shift_forward").map(lambda x: x.timestamp() if pd.notna(x) else np.nan)
    d["routes"] = d["affected"].fillna("").map(lambda s: [r.strip() for r in str(s).split("|") if r.strip()])
    d["update_number"] = pd.to_numeric(d.get("update_number"), errors="coerce").fillna(0).astype(int)
    d["status_label"] = d["status_label"].astype(str)
    d["cause_category"] = d["header"].map(lambda h: classify_cause(str(h)) if isinstance(h, str) else "unknown")
    d["planned"] = d["status_label"].isin(PLANNED)
    return d[cols].dropna(subset=["ts"]).reset_index(drop=True)


def events(archive: pd.DataFrame) -> pd.DataFrame:
    """One row per disruption event: start, end (last update), duration, routes, kind, cause."""
    if archive.empty:
        return pd.DataFrame(columns=["event_id", "start_ts", "end_ts", "duration_min", "n_updates", "routes", "status_label", "cause_category", "planned", "header"])
    a = archive.sort_values(["event_id", "ts"])
    g = a.groupby("event_id")
    ev = pd.DataFrame({
        "start_ts": g["ts"].min(), "end_ts": g["ts"].max(), "n_updates": g.size(),
        "routes": g["routes"].first(), "status_label": g["status_label"].first(), "cause_category": g["cause_category"].first(),
        "planned": g["planned"].first(), "header": g["header"].first(),
    }).reset_index()
    ev["duration_min"] = (ev["end_ts"] - ev["start_ts"]) / 60.0
    return ev


def climatology(ev: pd.DataFrame, weeks: float | None = None) -> dict:
    """Base rates of unplanned disruptions by route, hour and weekday, durations by cause."""
    un = ev[~ev["planned"] & ev["status_label"].isin(UNPLANNED)].copy() if not ev.empty else ev
    if un.empty:
        return {"n_events": 0}
    local = pd.to_datetime(un["start_ts"], unit="s", utc=True).dt.tz_convert(NY_TZ)
    un["hour"] = local.dt.hour; un["dow"] = local.dt.weekday
    span_weeks = weeks or max(1.0, (un["start_ts"].max() - un["start_ts"].min()) / (7 * 86400))
    ex = un.explode("routes").dropna(subset=["routes"])
    ex = ex[ex["routes"].astype(str).str.len() <= 3]
    by_route = ex.groupby("routes").agg(n=("event_id", "count"), median_duration_min=("duration_min", "median"), p90_duration_min=("duration_min", lambda s: float(np.quantile(s, 0.9))))
    by_route["per_week"] = by_route["n"] / span_weeks
    by_route = by_route.sort_values("per_week", ascending=False)
    hw = ex.groupby(["routes", "dow", "hour"]).size().rename("n").reset_index()
    hw["per_week"] = hw["n"] / span_weeks
    grid = {}
    for r, g in hw.groupby("routes"):
        m = np.zeros((7, 24))
        for row in g.itertuples(index=False):
            m[int(row.dow), int(row.hour)] = row.per_week
        grid[str(r)] = m.round(3).tolist()
    by_cause = un.groupby("cause_category").agg(n=("event_id", "count"), median_duration_min=("duration_min", "median"),
                                                p90_duration_min=("duration_min", lambda s: float(np.quantile(s, 0.9)))).sort_values("n", ascending=False)
    by_hour_all = un.groupby("hour").size().reindex(range(24), fill_value=0) / span_weeks
    by_dow_all = un.groupby("dow").size().reindex(range(7), fill_value=0) / span_weeks
    return {"n_events": int(len(un)), "weeks": float(span_weeks), "first_ts": float(un["start_ts"].min()), "last_ts": float(un["start_ts"].max()),
            "by_route": [{"route": str(r), "n": int(x.n), "per_week": float(x.per_week), "median_duration_min": float(x.median_duration_min),
                          "p90_duration_min": float(x.p90_duration_min)} for r, x in by_route.iterrows()],
            "by_cause": [{"cause": str(c), "n": int(x.n), "share": float(x.n / len(un)), "median_duration_min": float(x.median_duration_min),
                          "p90_duration_min": float(x.p90_duration_min)} for c, x in by_cause.iterrows()],
            "per_week_by_hour": [float(v) for v in by_hour_all.values], "per_week_by_dow": [float(v) for v in by_dow_all.values],
            "grid_by_route": grid,
            "kinds": un["status_label"].value_counts().head(12).to_dict()}


def disruption_probability(clim: dict, route: str, ts: float, window_hours: float = 1.0) -> float | None:
    """Expected number of new unplanned disruptions on ``route`` in the hour of ``ts`` (from the climatology grid)."""
    grid = clim.get("grid_by_route", {}).get(str(route))
    if not grid:
        return None
    local = datetime.fromtimestamp(ts, NY_TZ)
    return float(grid[local.weekday()][local.hour]) * window_hours
