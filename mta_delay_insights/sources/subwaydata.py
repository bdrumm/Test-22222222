"""Backfill arrival history from Subway Data NYC (subwaydata.nyc).

A community archive built from the same GTFS-Realtime feeds since April 2021:
one ``.tar.xz`` per day (~1.4 MB) with ``trips.csv`` (trip_uid, trip_id, route_id,
direction_id, start_time, vehicle_id = NYCT train id, ...) and ``stop_times.csv``
(trip_uid, stop_id, track, arrival_time, departure_time, last_observed,
marked_past). Rows map directly onto this project's arrivals table, which
turns a few days of our own collection into months of training history.
"""
from __future__ import annotations

import io
import tarfile
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import requests

from .. import config
from ..storage.db import ARRIVAL_COLUMNS
from .gtfs_static import NY_TZ, direction_from_stop_id

URL = "https://subwaydata.nyc/data/subwaydatanyc_{day}_csv.tar.xz"
SOURCE = "subwaydata"


def fetch_day(day: date, timeout: int | None = None) -> bytes:
    resp = requests.get(URL.format(day=day.isoformat()), timeout=timeout or 120, headers={"User-Agent": "mta-delay-insights/0.1"})
    resp.raise_for_status()
    return resp.content


def parse_archive(data: bytes) -> tuple[pd.DataFrame, pd.DataFrame]:
    tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:xz")
    trips = stops = None
    for m in tf.getmembers():
        if not m.isfile():
            continue
        raw = tf.extractfile(m).read()
        if m.name.endswith("_trips.csv"):
            trips = pd.read_csv(io.BytesIO(raw), dtype={"trip_id": str, "route_id": str, "vehicle_id": str})
        elif m.name.endswith("_stop_times.csv"):
            stops = pd.read_csv(io.BytesIO(raw), dtype={"stop_id": str, "track": str})
    if trips is None or stops is None:
        raise ValueError("archive lacks trips.csv / stop_times.csv")
    return trips, stops


def _origin_sec(trip_id: str) -> float:
    head = str(trip_id).split("_", 1)[0]
    return int(head) * 0.6 if head.isdigit() else float("nan")


def to_arrivals(trips: pd.DataFrame, stops: pd.DataFrame, service_date: date | None = None) -> pd.DataFrame:
    """Normalise to ARRIVAL_COLUMNS. trip_key = YYYYMMDD|trip_id like the feed's start_date.

    The archive's ``start_time`` is the service date at 00:00 *UTC* plus the trip's origin time
    (the hundredths-of-a-minute prefix of the trip id), so the service date is recovered by
    removing the origin time and reading the UTC date; the day file's own date is preferred.
    """
    t = trips[["trip_uid", "trip_id", "route_id", "start_time", "vehicle_id", "num_updates"]].copy()
    if service_date is not None:
        svc = pd.Series(service_date.strftime("%Y%m%d"), index=t.index)
    else:
        origin = t["trip_id"].map(_origin_sec).fillna(0.0)
        svc = pd.to_datetime(t["start_time"] - origin, unit="s", utc=True).dt.strftime("%Y%m%d")
    t["start_date"] = svc
    t["trip_key"] = t["start_date"] + "|" + t["trip_id"].astype(str)
    s = stops.merge(t, on="trip_uid", how="inner")
    arr = s["arrival_time"].where(s["arrival_time"].notna(), s["departure_time"])
    dep = s["departure_time"].where(s["departure_time"].notna(), s["arrival_time"])
    s = s[arr.notna()].copy()
    arr = arr[s.index]; dep = dep[s.index]
    out = pd.DataFrame({
        "trip_key": s["trip_key"], "trip_id": s["trip_id"].astype(str), "route_id": s["route_id"].astype(str),
        "start_date": s["start_date"], "direction": s["stop_id"].map(direction_from_stop_id), "stop_id": s["stop_id"],
        "arrival_ts": arr.astype(float), "departure_ts": dep.astype(float), "source": SOURCE,
        "first_seen_ts": np.nan, "last_seen_ts": s["last_observed"].astype(float), "first_pred_ts": np.nan,
        "n_predictions": s.get("num_updates", pd.Series(np.nan, index=s.index)), "pred_drift_sec": np.nan,
        "confidence": np.where(s["marked_past"].notna(), 0.95, 0.75),
        "train_id": s["vehicle_id"], "sched_track": None, "actual_track": s["track"],
    })
    return out.reindex(columns=ARRIVAL_COLUMNS).reset_index(drop=True)


def backfill_days(days: list[date], fetch=fetch_day) -> dict[date, pd.DataFrame]:
    out = {}
    for d in days:
        try:
            trips, stops = parse_archive(fetch(d))
            out[d] = to_arrivals(trips, stops, service_date=d)
        except Exception as exc:
            out[d] = pd.DataFrame(columns=ARRIVAL_COLUMNS)
            out[d].attrs["error"] = str(exc)[:200]
    return out


def missing_days(existing: set[str], days_back: int, today: date | None = None) -> list[date]:
    """Days in the window whose network files are absent; yesterday is the newest day published."""
    today = today or datetime.now(NY_TZ).date()
    want = [today - timedelta(days=i) for i in range(1, days_back + 1)]
    return [d for d in want if d.isoformat() not in existing]
