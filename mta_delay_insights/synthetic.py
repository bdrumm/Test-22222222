"""Synthetic scenarios: a mini Lexington-Avenue-style corridor with injected problems.

Purpose
-------
* Tests and demos run without network access.
* Each scenario injects a *known* cause (signal failure on a segment, peak dwell,
  merge conflicts, missing trips, late terminal departures, weather) so the
  attribution lenses can be validated against ground truth.
* ``to_rt_snapshots`` turns simulated arrivals back into GTFS-Realtime protobuf
  snapshots, so the collector is exercised on realistic feed bytes too.
"""
from __future__ import annotations

import bisect
import csv
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .sources.gtfs_realtime import encode_trip_updates
from .sources.gtfs_static import NY_TZ, StaticGTFS, service_midnight
from .storage.db import ARRIVAL_COLUMNS

# Corridor: parent id -> (name, lat, lon). Ordered south -> north.
CORRIDOR = [
    ("640", "Brooklyn Bridge-City Hall", 40.7132, -74.0041),
    ("639", "Canal St", 40.7187, -74.0002),
    ("638", "Spring St", 40.7223, -73.9972),
    ("637", "Bleecker St", 40.7259, -73.9945),
    ("636", "Astor Pl", 40.7300, -73.9911),
    ("635", "14 St-Union Sq", 40.7348, -73.9899),
    ("634", "23 St", 40.7397, -73.9865),
    ("633", "28 St", 40.7434, -73.9841),
    ("632", "33 St", 40.7461, -73.9822),
    ("631", "Grand Central-42 St", 40.7518, -73.9768),
    ("630", "51 St", 40.7571, -73.9720),
    ("629", "59 St", 40.7625, -73.9680),
    ("628", "68 St-Hunter College", 40.7681, -73.9638),
    ("627", "77 St", 40.7738, -73.9597),
]
EXPRESS_STOPS = ["640", "635", "631", "629", "627"]
LOCAL_RUN_SEC = 90
DWELL_SEC = 30


def _headway(route: str, hour: int, weekday: bool) -> int:
    peak = weekday and hour in (7, 8, 9, 16, 17, 18)
    if route == "6":
        return 240 if peak else (360 if 6 <= hour < 22 else 600)
    return 300 if peak else (480 if 6 <= hour < 22 else 720)


def _fmt_time(sec: int) -> str:
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def build_mini_gtfs(out_dir: str | Path, start: date, end: date) -> Path:
    """Write a small but realistic GTFS feed for the corridor (routes 6 local, 4 express)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ymd = lambda d: d.strftime("%Y%m%d")
    with open(out / "agency.txt", "w", newline="") as f:
        csv.writer(f).writerows([["agency_id", "agency_name", "agency_url", "agency_timezone"],
                                 ["MTA NYCT", "MTA New York City Transit", "https://mta.info", "America/New_York"]])
    with open(out / "routes.txt", "w", newline="") as f:
        csv.writer(f).writerows([["route_id", "agency_id", "route_short_name", "route_long_name", "route_type", "route_color"],
                                 ["6", "MTA NYCT", "6", "Lexington Av Local", "1", "00933C"],
                                 ["4", "MTA NYCT", "4", "Lexington Av Express", "1", "00933C"]])
    with open(out / "stops.txt", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station"])
        for pid, name, lat, lon in CORRIDOR:
            w.writerow([pid, name, lat, lon, "1", ""])
            w.writerow([pid + "N", name, lat, lon, "", pid])
            w.writerow([pid + "S", name, lat, lon, "", pid])
    with open(out / "calendar.txt", "w", newline="") as f:
        csv.writer(f).writerows([["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "start_date", "end_date"],
                                 ["Weekday", 1, 1, 1, 1, 1, 0, 0, ymd(start), ymd(end)],
                                 ["Weekend", 0, 0, 0, 0, 0, 1, 1, ymd(start), ymd(end)]])
    with open(out / "calendar_dates.txt", "w", newline="") as f:
        csv.writer(f).writerow(["service_id", "date", "exception_type"])
    with open(out / "feed_info.txt", "w", newline="") as f:
        csv.writer(f).writerows([["feed_publisher_name", "feed_publisher_url", "feed_lang", "feed_start_date", "feed_end_date", "feed_version"],
                                 ["synthetic", "https://example.invalid", "EN", ymd(start), ymd(end), "synthetic-1"]])
    trips_rows = [["route_id", "trip_id", "service_id", "trip_headsign", "direction_id", "shape_id"]]
    st_rows = [["trip_id", "stop_id", "arrival_time", "departure_time", "stop_sequence"]]
    for service in ("Weekday", "Weekend"):
        weekday = service == "Weekday"
        for route in ("6", "4"):
            stops = [c[0] for c in CORRIDOR] if route == "6" else EXPRESS_STOPS
            for direction, d_id in (("N", 0), ("S", 1)):
                seq = stops if direction == "N" else list(reversed(stops))
                t = 5 * 3600
                while t < 25 * 3600:
                    hour = (t // 3600) % 24
                    origin_hundredths = int(round(t / 60 * 100))
                    trip_id = f"SYN-{service}-00_{origin_hundredths:06d}_{route}..{direction}01R"
                    trips_rows.append([route, trip_id, service, seq[-1], d_id, f"{route}..{direction}01R"])
                    cur = t
                    for i, pid in enumerate(seq):
                        if i > 0:
                            prev, this = seq[i - 1], pid
                            cur += _run_time(prev, this)
                        st_rows.append([trip_id, f"{pid}{direction}", _fmt_time(cur), _fmt_time(cur + DWELL_SEC), i + 1])
                        cur += DWELL_SEC
                    t += _headway(route, hour, weekday)
    with open(out / "trips.txt", "w", newline="") as f:
        csv.writer(f).writerows(trips_rows)
    with open(out / "stop_times.txt", "w", newline="") as f:
        csv.writer(f).writerows(st_rows)
    return out


def _run_time(a: str, b: str) -> int:
    ids = [c[0] for c in CORRIDOR]
    return abs(ids.index(a) - ids.index(b)) * LOCAL_RUN_SEC


# --------------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------------- #
@dataclass
class _Row:
    trip_id: str
    route_id: str
    direction: str
    arrival_sec: int
    arrival_ts: float
    stop_id: str


@dataclass
class Issue:
    kind: str                       # signal_segment | dwell_peak | merge_conflict | missing_trips | terminal_late | weather
    route: str = "6"
    direction: str = "N"
    hours: tuple[int, ...] = (7, 8, 9)
    segment: tuple[str, str] = ("633", "632")   # for signal_segment: delay applied arriving at segment[1]
    day_prob: float = 0.7
    magnitude_sec: float = 240.0
    fraction: float = 0.15          # missing_trips: share cancelled
    weekdays_only: bool = True


@dataclass
class Scenario:
    name: str
    start: date                     # baseline start
    window_start: date              # issue starts here
    end: date                       # exclusive
    issues: list[Issue] = field(default_factory=list)
    seed: int = 7
    target_stop: str = "631N"
    routes: tuple[str, ...] = ("6", "4")
    directions: tuple[str, ...] = ("N", "S")


SCENARIOS: dict[str, list[Issue]] = {
    "signal": [Issue("signal_segment", segment=("633", "632"), hours=(7, 8, 9), magnitude_sec=300)],
    "dwell": [Issue("dwell_peak", hours=(8, 9, 17, 18), magnitude_sec=45)],
    "merge": [Issue("merge_conflict", hours=(7, 8, 9, 16, 17, 18), magnitude_sec=150)],
    "missing": [Issue("missing_trips", hours=(7, 8, 9), fraction=0.25)],
    "terminal": [Issue("terminal_late", hours=(7, 8, 9), magnitude_sec=240)],
    "weather": [Issue("weather", hours=tuple(range(24)), magnitude_sec=150)],
    "mixed": [Issue("signal_segment", segment=("633", "632"), hours=(8, 9), magnitude_sec=300, day_prob=0.5),
              Issue("dwell_peak", hours=(8, 9), magnitude_sec=30),
              Issue("missing_trips", hours=(8,), fraction=0.12)],
    "none": [],
}


def make_scenario(name: str, days_baseline: int = 14, days_window: int = 14, start: date | None = None,
                  seed: int = 7, directions: tuple[str, ...] = ("N",)) -> Scenario:
    start = start or date(2026, 8, 24)  # a Monday
    ws = start + timedelta(days=days_baseline)
    return Scenario(name=name, start=start, window_start=ws, end=ws + timedelta(days=days_window),
                    issues=list(SCENARIOS[name]), seed=seed, directions=directions)


@dataclass
class SimResult:
    arrivals: pd.DataFrame
    alerts: pd.DataFrame
    ridership_profile: pd.DataFrame
    incidents: pd.DataFrame
    weather_daily: pd.DataFrame
    truth: dict


def simulate(static: StaticGTFS, sc: Scenario) -> SimResult:
    rng = np.random.default_rng(sc.seed)
    stop_ids_all = [f"{c[0]}{d}" for c in CORRIDOR for d in sc.directions]
    weather = _weather(rng, sc.start, sc.end)
    wx_by_date = weather.set_index("date")
    arrivals: list[dict] = []
    alerts: list[dict] = []
    d = sc.start
    while d < sc.end:
        weekday = d.weekday() < 5
        in_window = d >= sc.window_start
        midnight = service_midnight(d).timestamp()
        # Episodes (signal failures) for the day.
        episodes = []
        for iss in sc.issues:
            if iss.kind == "signal_segment" and in_window and (weekday or not iss.weekdays_only) and rng.random() < iss.day_prob:
                h = rng.choice(iss.hours)
                start_ts = midnight + h * 3600 + rng.uniform(0, 2400)
                dur = rng.uniform(1200, 2700)
                episodes.append((iss, start_ts, start_ts + dur))
                alerts.append({
                    "alert_id": f"lmm:alert:{int(start_ts)}", "alert_type": "Delays", "planned": False,
                    "cause_category": "signal", "created_at": start_ts + 180, "updated_at": start_ts + dur,
                    "active_start": start_ts + 180, "active_end": start_ts + dur + 300,
                    "routes": [iss.route], "stops": [],
                    "header": f"{'Northbound' if iss.direction == 'N' else 'Southbound'} {iss.route} trains are running with delays because of signal problems at {static.stop_name(iss.segment[0] + iss.direction)}.",
                    "description": "Expect delays.",
                })
        # Occasional unrelated planned work alert on weekends (noise for the lenses).
        if not weekday and rng.random() < 0.5:
            s0 = midnight + 0.5 * 3600
            alerts.append({"alert_id": f"lmm:planned:{int(s0)}", "alert_type": "Planned - Local to Express", "planned": True,
                           "cause_category": "planned_work", "created_at": s0 - 86400 * 3, "updated_at": s0,
                           "active_start": s0, "active_end": s0 + 4 * 3600, "routes": ["4"], "stops": [],
                           "header": "Planned work: 4 trains run express", "description": ""})
        events = static.scheduled_stop_events(stop_ids_all, d, list(sc.routes))
        events = events.sort_values(["trip_id", "stop_sequence"])
        # Express trips are simulated first so local trips can be checked against the
        # express train actually ahead of them at the merge stop.
        express_at: dict[str, list[float]] = {}
        by_trip: dict[str, list[dict]] = {}
        for rec in events[["trip_id", "route_id", "direction", "arrival_sec", "arrival_ts", "stop_id"]].to_dict("records"):
            by_trip.setdefault(rec["trip_id"], []).append(rec)
        groups = sorted(by_trip.items(), key=lambda kv: (kv[1][0]["route_id"] != "4", kv[0]))
        for trip_id, g in groups:
            route = g[0]["route_id"]
            direction = g[0]["direction"]
            hour0 = int(g[0]["arrival_sec"] // 3600) % 24
            # Missing trips
            cancelled = False
            for iss in sc.issues:
                if iss.kind == "missing_trips" and in_window and route == iss.route and direction == iss.direction \
                        and hour0 in iss.hours and (weekday or not iss.weekdays_only) and rng.random() < iss.fraction:
                    cancelled = True
            if cancelled:
                continue
            lateness = max(-30.0, rng.normal(30, 40))
            for iss in sc.issues:
                if iss.kind == "terminal_late" and in_window and route == iss.route and direction == iss.direction \
                        and hour0 in iss.hours and (weekday or not iss.weekdays_only):
                    lateness += max(0.0, rng.normal(iss.magnitude_sec, 60))
            extra_total = 0.0
            prev_stop = None
            n_stops = len(g)
            for row in g:
                row = _Row(**row)
                lateness += rng.normal(0, 8)
                extra = 0.0
                pid = row.stop_id[:-1]
                hour = int(((row.arrival_ts - midnight) // 3600) % 24)
                for iss in sc.issues:
                    if not in_window or (iss.weekdays_only and not weekday):
                        continue
                    if iss.direction != direction:
                        continue
                    if iss.kind == "signal_segment" and route == iss.route and prev_stop == iss.segment[0] and pid == iss.segment[1]:
                        for e_iss, e0, e1 in episodes:
                            if e_iss is iss and e0 <= row.arrival_ts + lateness <= e1:
                                extra += rng.uniform(0.6, 1.4) * iss.magnitude_sec
                    if iss.kind == "dwell_peak" and route == iss.route and hour in iss.hours and pid in ("633", "632", "631"):
                        extra += max(0.0, rng.normal(iss.magnitude_sec, 15))
                    if iss.kind == "weather":
                        w = wx_by_date.loc[d]
                        if w["precip_mm"] > 3:
                            extra += rng.exponential(iss.magnitude_sec * min(w["precip_mm"], 20) / 20) / max(1, n_stops / 4)
                    if iss.kind == "merge_conflict" and route == "6" and pid == "635" and hour in iss.hours:
                        # a 6 that would arrive within 150 s behind a 4 at the merge stop is held
                        lst = express_at.get(row.stop_id, [])
                        proj = row.arrival_ts + lateness
                        k = bisect.bisect_right(lst, proj) - 1
                        if k >= 0 and 0 <= proj - lst[k] <= 150:
                            extra += rng.uniform(0.8, 1.2) * iss.magnitude_sec
                lateness += extra
                extra_total += extra
                actual = row.arrival_ts + lateness
                if route == "4":
                    bisect.insort(express_at.setdefault(row.stop_id, []), actual)
                drift = 0.6 * extra_total + rng.normal(0, 15)
                arrivals.append({
                    "trip_key": f"{d.strftime('%Y%m%d')}|{_rt_id(trip_id)}", "trip_id": _rt_id(trip_id), "route_id": route,
                    "start_date": d.strftime("%Y%m%d"), "direction": direction, "stop_id": row.stop_id,
                    "arrival_ts": actual, "departure_ts": actual + DWELL_SEC, "source": "synthetic",
                    "first_seen_ts": actual - 900, "last_seen_ts": actual - 15, "first_pred_ts": actual - drift,
                    "n_predictions": 30, "pred_drift_sec": drift, "confidence": 1.0,
                })
                prev_stop = pid
        d += timedelta(days=1)
    arr = pd.DataFrame(arrivals, columns=ARRIVAL_COLUMNS)
    al = pd.DataFrame(alerts)
    truth = {"scenario": sc.name, "issues": [vars(i) for i in sc.issues], "window_start": sc.window_start.isoformat()}
    return SimResult(arr, al, ridership_profile(), incidents_table(sc), weather, truth)


def _rt_id(static_trip_id: str) -> str:
    return static_trip_id.split("_", 1)[1]


def _weather(rng: np.random.Generator, start: date, end: date) -> pd.DataFrame:
    rows = []
    d = start
    while d < end:
        rain = rng.random() < 0.3
        precip = float(rng.exponential(8)) if rain else 0.0
        rows.append({"date": d, "precip_mm": round(precip, 1), "snow_cm": 0.0, "temp_max_c": round(float(rng.normal(27, 4)), 1),
                     "temp_min_c": round(float(rng.normal(18, 3)), 1), "wind_max_kmh": round(float(rng.normal(20, 8)), 1),
                     "adverse_hours": int(precip >= 5) * int(rng.integers(1, 6))})
        d += timedelta(days=1)
    return pd.DataFrame(rows)


def ridership_profile() -> pd.DataFrame:
    """Entries per hour at a large Manhattan hub, weekday and weekend."""
    hours = np.arange(24)
    wk = 800 + 6500 * np.exp(-((hours - 8.5) ** 2) / 2.5) + 5500 * np.exp(-((hours - 17.5) ** 2) / 3.5)
    we = 400 + 1800 * np.exp(-((hours - 14) ** 2) / 12)
    rows = [{"day_type": "weekday", "hour": int(h), "riders_per_hour": float(v)} for h, v in zip(hours, wk)]
    rows += [{"day_type": "weekend", "hour": int(h), "riders_per_hour": float(v)} for h, v in zip(hours, we)]
    return pd.DataFrame(rows)


def incidents_table(sc: Scenario) -> pd.DataFrame:
    """A trains-delayed style table for several lines, with the scenario's cause over-indexed on the route."""
    cats = ["Signals", "Track", "Subway Car", "Persons on Trackbed/Police/Medical", "Planned ROW Work",
            "Infrastructure & Equipment", "Operating Environment", "Crew Availability", "Other"]
    base = np.array([0.18, 0.10, 0.12, 0.16, 0.14, 0.10, 0.08, 0.06, 0.06])
    kinds = {i.kind for i in sc.issues}
    rows = []
    months = sorted({pd.Timestamp(sc.start.year, sc.start.month, 1), pd.Timestamp(sc.end.year, sc.end.month, 1)})
    for m in months:
        for line in ("6", "4", "A", "L", "F", "N"):
            mix = base.copy()
            if line in sc.routes:
                if "signal_segment" in kinds:
                    mix[0] *= 2.2
                if "missing_trips" in kinds:
                    mix[7] *= 2.5
                if "terminal_late" in kinds:
                    mix[7] *= 1.8
                if "merge_conflict" in kinds or "dwell_peak" in kinds:
                    mix[6] *= 1.6
            mix = mix / mix.sum()
            total = 2500 if line in ("6", "4") else 1800
            for c, share in zip(cats, mix):
                rows.append({"month": m, "division": "A DIVISION", "line": line, "day_type": "1",
                             "reporting_category": c, "subcategory": c, "delays": float(round(total * share))})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Convert simulated arrivals into GTFS-RT snapshots for collector replay.
# --------------------------------------------------------------------------- #
def to_rt_snapshots(arrivals: pd.DataFrame, start_ts: float, end_ts: float, poll_interval: int = 30,
                    feed_key: str = "1234567S", seed: int = 3) -> list[tuple[str, float, bytes]]:
    rng = np.random.default_rng(seed)
    a = arrivals[(arrivals["arrival_ts"] >= start_ts - 3600) & (arrivals["arrival_ts"] <= end_ts + 3600)]
    by_trip = {k: g.sort_values("arrival_ts") for k, g in a.groupby("trip_key")}
    snaps = []
    t = start_ts
    while t <= end_ts:
        trips = []
        for key, g in by_trip.items():
            first, last = g["arrival_ts"].iloc[0], g["arrival_ts"].iloc[-1]
            if last < t or first > t + 1800:  # finished, or not yet in the feed
                continue
            fut = g[g["arrival_ts"] >= t]
            if fut.empty:
                continue
            stops = []
            prev_pred = -np.inf
            for r in fut.itertuples(index=False):
                horizon = r.arrival_ts - t
                pred = r.arrival_ts + rng.normal(0, max(5.0, horizon * 0.08))
                pred = max(pred, prev_pred + 45.0)          # predictions keep the stop order, like a real feed
                prev_pred = pred
                stops.append((r.stop_id, pred, pred + DWELL_SEC))
            # vehicle position from the simulated movements: dwelling at the last served stop or running to the next
            past = g[g["arrival_ts"] < t]
            vehicle = None
            if first <= t:
                if len(past) and t - float(past["arrival_ts"].iloc[-1]) <= DWELL_SEC:
                    vehicle = {"stop_id": past["stop_id"].iloc[-1], "ts": float(past["arrival_ts"].iloc[-1]), "status": "STOPPED_AT"}
                else:
                    dep = float(past["arrival_ts"].iloc[-1]) + DWELL_SEC if len(past) else first
                    vehicle = {"stop_id": fut["stop_id"].iloc[0], "ts": dep, "status": "IN_TRANSIT_TO"}
            trips.append({"trip_id": g.iloc[0]["trip_id"], "route_id": g.iloc[0]["route_id"],
                          "start_date": g.iloc[0]["start_date"], "stops": stops, "vehicle": vehicle})
        snaps.append((feed_key, float(t), encode_trip_updates(trips, t)))
        t += poll_interval
    return snaps


def alerts_json(alerts: pd.DataFrame) -> dict:
    """Render alerts as the MTA JSON feed shape (for tests of the alerts parser)."""
    ents = []
    for r in alerts.itertuples(index=False):
        ents.append({
            "id": r.alert_id,
            "alert": {
                "active_period": [{"start": int(r.active_start), "end": int(r.active_end)}],
                "informed_entity": [{"agency_id": "MTASBWY", "route_id": x} for x in r.routes],
                "header_text": {"translation": [{"text": r.header, "language": "en"}]},
                "description_text": {"translation": [{"text": r.description, "language": "en"}]},
                "transit_realtime.mercury_alert": {"created_at": int(r.created_at), "updated_at": int(r.updated_at),
                                                   "alert_type": r.alert_type},
            },
        })
    return {"header": {"gtfs_realtime_version": "2.0", "timestamp": int(datetime.now(NY_TZ).timestamp())}, "entity": ents}
