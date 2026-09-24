"""GTFS-Realtime (protobuf) fetching and normalisation for MTA feeds.

The MTA feeds carry the NYCT extension (train id, assigned track). The base
protobuf parser skips unknown extensions, so the frames built here rely only on
standard fields plus the MTA trip-id / stop-id conventions for direction.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Iterable

import pandas as pd
import requests
from google.transit import gtfs_realtime_pb2 as rt

from .. import config
from .gtfs_static import direction_from_stop_id, direction_from_trip_id

TRIP_UPDATE_COLUMNS = [
    "snapshot_ts", "feed_ts", "feed", "trip_id", "route_id", "start_date", "direction",
    "stop_id", "stop_sequence", "arrival_ts", "departure_ts", "schedule_relationship",
]
VEHICLE_COLUMNS = [
    "snapshot_ts", "feed", "trip_id", "route_id", "start_date", "direction", "stop_id",
    "current_stop_sequence", "current_status", "vehicle_ts",
]


def fetch_feed_bytes(url: str, api_key: str | None = None, timeout: int | None = None) -> bytes:
    headers = {}
    key = api_key or config.api_key()
    if key:
        headers["x-api-key"] = key
    resp = requests.get(url, headers=headers, timeout=timeout or config.DEFAULTS.request_timeout_sec)
    resp.raise_for_status()
    return resp.content


def parse_feed(data: bytes) -> rt.FeedMessage:
    msg = rt.FeedMessage()
    msg.ParseFromString(data)
    return msg


def fetch_feed(feed_key: str, api_key: str | None = None) -> rt.FeedMessage:
    return parse_feed(fetch_feed_bytes(config.rt_feed_url(feed_key), api_key))


def _direction(trip_id: str, stop_id: str | None) -> str | None:
    return direction_from_stop_id(stop_id or "") or direction_from_trip_id(trip_id)


def trip_updates_frame(feed: rt.FeedMessage, feed_name: str = "", snapshot_ts: float | None = None) -> pd.DataFrame:
    """One row per (trip, stop) prediction in the feed."""
    snap = float(snapshot_ts if snapshot_ts is not None else time.time())
    feed_ts = float(feed.header.timestamp) if feed.header.HasField("timestamp") else snap
    rows = []
    for ent in feed.entity:
        if not ent.HasField("trip_update"):
            continue
        tu = ent.trip_update
        trip = tu.trip
        rel = rt.TripDescriptor.ScheduleRelationship.Name(trip.schedule_relationship) if trip.HasField("schedule_relationship") else "SCHEDULED"
        for stu in tu.stop_time_update:
            arr = float(stu.arrival.time) if stu.HasField("arrival") and stu.arrival.time else None
            dep = float(stu.departure.time) if stu.HasField("departure") and stu.departure.time else None
            rows.append({
                "snapshot_ts": snap,
                "feed_ts": feed_ts,
                "feed": feed_name,
                "trip_id": trip.trip_id,
                "route_id": trip.route_id,
                "start_date": trip.start_date or None,
                "direction": _direction(trip.trip_id, stu.stop_id),
                "stop_id": stu.stop_id,
                "stop_sequence": int(stu.stop_sequence) if stu.HasField("stop_sequence") else None,
                "arrival_ts": arr if arr is not None else dep,
                "departure_ts": dep if dep is not None else arr,
                "schedule_relationship": rel,
            })
    return pd.DataFrame(rows, columns=TRIP_UPDATE_COLUMNS)


def vehicle_positions_frame(feed: rt.FeedMessage, feed_name: str = "", snapshot_ts: float | None = None) -> pd.DataFrame:
    snap = float(snapshot_ts if snapshot_ts is not None else time.time())
    rows = []
    for ent in feed.entity:
        if not ent.HasField("vehicle"):
            continue
        v = ent.vehicle
        rows.append({
            "snapshot_ts": snap,
            "feed": feed_name,
            "trip_id": v.trip.trip_id,
            "route_id": v.trip.route_id,
            "start_date": v.trip.start_date or None,
            "direction": _direction(v.trip.trip_id, v.stop_id),
            "stop_id": v.stop_id or None,
            "current_stop_sequence": int(v.current_stop_sequence) if v.HasField("current_stop_sequence") else None,
            "current_status": rt.VehiclePosition.VehicleStopStatus.Name(v.current_status) if v.HasField("current_status") else None,
            "vehicle_ts": float(v.timestamp) if v.HasField("timestamp") else None,
        })
    return pd.DataFrame(rows, columns=VEHICLE_COLUMNS)


def alerts_frame_from_pb(feed: rt.FeedMessage) -> pd.DataFrame:
    """Minimal alert extraction from a protobuf alerts feed (no Mercury fields)."""
    rows = []
    for ent in feed.entity:
        if not ent.HasField("alert"):
            continue
        a = ent.alert
        header = a.header_text.translation[0].text if a.header_text.translation else ""
        desc = a.description_text.translation[0].text if a.description_text.translation else ""
        routes = sorted({ie.route_id for ie in a.informed_entity if ie.route_id})
        stops = sorted({ie.stop_id for ie in a.informed_entity if ie.stop_id})
        periods = list(a.active_period) or [None]
        for p in periods:
            rows.append({
                "alert_id": ent.id,
                "alert_type": None,
                "active_start": float(p.start) if p is not None and p.HasField("start") else None,
                "active_end": float(p.end) if p is not None and p.HasField("end") else None,
                "routes": routes, "stops": stops, "header": header, "description": desc,
                "cause": rt.Alert.Cause.Name(a.cause) if a.HasField("cause") else None,
                "effect": rt.Alert.Effect.Name(a.effect) if a.HasField("effect") else None,
            })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Encoding helpers: build feeds from records. Used by tests and the synthetic
# scenario generator so the collector is exercised on real protobuf bytes.
# --------------------------------------------------------------------------- #
def encode_trip_updates(trips: Iterable[dict], feed_ts: float) -> bytes:
    """``trips`` items: {trip_id, route_id, start_date, stops: [(stop_id, arrival_ts, departure_ts), ...]}."""
    msg = rt.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.incrementality = rt.FeedHeader.FULL_DATASET
    msg.header.timestamp = int(feed_ts)
    for t in trips:
        ent = msg.entity.add()
        ent.id = t["trip_id"]
        tu = ent.trip_update
        tu.trip.trip_id = t["trip_id"]
        tu.trip.route_id = t["route_id"]
        if t.get("start_date"):
            tu.trip.start_date = t["start_date"]
        for i, (stop_id, arr, dep) in enumerate(t["stops"]):
            stu = tu.stop_time_update.add()
            stu.stop_id = stop_id
            if t.get("with_sequence"):
                stu.stop_sequence = i + 1
            if arr is not None:
                stu.arrival.time = int(arr)
            if dep is not None:
                stu.departure.time = int(dep)
        if t.get("vehicle"):
            vent = msg.entity.add()
            vent.id = "v:" + t["trip_id"]
            v = vent.vehicle
            v.trip.trip_id = t["trip_id"]
            v.trip.route_id = t["route_id"]
            v.stop_id = t["vehicle"].get("stop_id", "")
            v.timestamp = int(t["vehicle"].get("ts", feed_ts))
            status = t["vehicle"].get("status", "IN_TRANSIT_TO")
            v.current_status = rt.VehiclePosition.VehicleStopStatus.Value(status)
    return msg.SerializeToString()


def snapshot_timestamp_iso(ts: float) -> str:
    return datetime.utcfromtimestamp(ts).isoformat() + "Z"
