"""Static GTFS loader with the schedule and topology helpers the analysis needs.

Beyond plain table access this module answers:

* which platform (``stop_id``) serves a station in a direction,
* which trips are *scheduled* to arrive at a stop on a service date,
* what the scheduled headways are,
* which stops are upstream of a stop on a route (for propagation analysis),
* where other routes merge onto the same track (for interlining conflicts).
"""
from __future__ import annotations

import re

import io
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from .. import config

NY_TZ = ZoneInfo("America/New_York")

REQUIRED_FILES = ["stops.txt", "routes.txt", "trips.txt", "stop_times.txt"]
OPTIONAL_FILES = ["calendar.txt", "calendar_dates.txt", "transfers.txt", "feed_info.txt", "shapes.txt"]


def gtfs_time_to_seconds(value: str) -> int:
    """Convert ``HH:MM:SS`` (hours may exceed 24) to seconds since service midnight."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return -1
    h, m, s = str(value).strip().split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def service_midnight(service_date: date) -> datetime:
    """Local midnight of a service date (GTFS 'noon minus 12h' is equivalent for NYC)."""
    return datetime(service_date.year, service_date.month, service_date.day, tzinfo=NY_TZ)


def direction_from_stop_id(stop_id: str) -> str | None:
    if stop_id and stop_id[-1] in ("N", "S"):
        return stop_id[-1]
    return None


def direction_from_trip_id(trip_id: str) -> str | None:
    """MTA trip ids end in ``..N03R`` / ``..S03R``; the letter after ``..`` is the direction."""
    if ".." in trip_id:
        tail = trip_id.rsplit("..", 1)[1]
        if tail and tail[0] in ("N", "S"):
            return tail[0]
    return None


def rt_trip_suffix(trip_id: str) -> str:
    """The realtime feed uses ``<origin>_<route>..<dir><path>``; static ids carry a
    service prefix before that. Return the comparable suffix for either form."""
    parts = trip_id.split("_")
    if len(parts) >= 3:
        return "_".join(parts[-2:])
    return trip_id


def haversine_m(a: tuple, b: tuple) -> float:
    """Great-circle distance in metres between (lat, lon) pairs."""
    try:
        la1, lo1, la2, lo2 = map(float, (a[0], a[1], b[0], b[1]))
    except (TypeError, ValueError):
        return float("nan")
    if any(v != v for v in (la1, lo1, la2, lo2)):
        return float("nan")
    p1, p2 = np.radians(la1), np.radians(la2); dphi = p2 - p1; dl = np.radians(lo2 - lo1)
    h = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return float(2 * 6_371_000.0 * np.arcsin(np.sqrt(h)))


_STEM_RE = re.compile(r"^(\d+_[^.]+\.\.?[NS])")


def rt_trip_stem(trip_id: str) -> str:
    """The suffix without its path code (``020300_L..N01R`` -> ``020300_L..N``). Some feeds (the L, some
    G and 7 trips) publish realtime ids without the path code, so matching falls back to the stem."""
    suffix = rt_trip_suffix(trip_id)
    m = _STEM_RE.match(suffix)
    return m.group(1) if m else suffix


def origin_time_seconds(trip_id: str) -> int | None:
    """MTA trip ids start with the origin departure time in hundredths of a minute."""
    try:
        prefix = rt_trip_suffix(trip_id).split("_")[0]
        return int(round(int(prefix) * 60 / 100))
    except (ValueError, IndexError):
        return None


@dataclass
class StaticGTFS:
    stops: pd.DataFrame
    routes: pd.DataFrame
    trips: pd.DataFrame
    stop_times: pd.DataFrame
    calendar: pd.DataFrame
    calendar_dates: pd.DataFrame
    transfers: pd.DataFrame | None = None
    shapes: pd.DataFrame | None = None
    feed_info: pd.DataFrame | None = None

    # ------------------------------------------------------------------ #
    # Loading
    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, source: str | Path | bytes) -> "StaticGTFS":
        """Load from a directory, a zip path, or raw zip bytes."""
        tables: dict[str, pd.DataFrame] = {}
        if isinstance(source, (bytes, bytearray)):
            zf = zipfile.ZipFile(io.BytesIO(source))
            tables = {n: pd.read_csv(zf.open(n), dtype=str) for n in zf.namelist()
                      if n in REQUIRED_FILES + OPTIONAL_FILES}
        else:
            p = Path(source)
            if p.is_dir():
                for name in REQUIRED_FILES + OPTIONAL_FILES:
                    f = p / name
                    if f.exists():
                        tables[name] = pd.read_csv(f, dtype=str)
            else:
                zf = zipfile.ZipFile(p)
                tables = {n: pd.read_csv(zf.open(n), dtype=str) for n in zf.namelist()
                          if n in REQUIRED_FILES + OPTIONAL_FILES}
        missing = [n for n in REQUIRED_FILES if n not in tables]
        if missing:
            raise ValueError(f"GTFS source is missing {missing}")
        return cls._from_tables(tables)

    @classmethod
    def download(cls, key_or_url: str = "subway", dest: str | Path | None = None,
                 timeout: int = 120) -> "StaticGTFS":
        url = config.STATIC_GTFS_URLS.get(key_or_url, key_or_url)
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        if dest:
            Path(dest).write_bytes(resp.content)
        return cls.load(resp.content)

    @classmethod
    def _from_tables(cls, t: dict[str, pd.DataFrame]) -> "StaticGTFS":
        stops = t["stops"] if "stops" in t else t["stops.txt"]
        routes = t["routes.txt"]
        trips = t["trips.txt"]
        st = t["stop_times.txt"].copy()
        st["arrival_sec"] = st["arrival_time"].map(gtfs_time_to_seconds).astype("int64")
        st["departure_sec"] = st["departure_time"].map(gtfs_time_to_seconds).astype("int64")
        st["stop_sequence"] = st["stop_sequence"].astype(int)
        stops = stops.copy()
        for col in ("stop_lat", "stop_lon"):
            if col in stops:
                stops[col] = pd.to_numeric(stops[col], errors="coerce")
        if "parent_station" not in stops:
            stops["parent_station"] = None
        if "location_type" not in stops:
            stops["location_type"] = None
        trips = trips.copy()
        if "direction_id" in trips:
            trips["direction_id"] = pd.to_numeric(trips["direction_id"], errors="coerce")
        cal = t.get("calendar.txt", pd.DataFrame(columns=[
            "service_id", "monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday", "start_date", "end_date"]))
        cald = t.get("calendar_dates.txt", pd.DataFrame(columns=["service_id", "date", "exception_type"]))
        obj = cls(stops=stops, routes=routes, trips=trips, stop_times=st, calendar=cal,
                  calendar_dates=cald, transfers=t.get("transfers.txt"), feed_info=t.get("feed_info.txt"), shapes=t.get("shapes.txt"))
        obj._build_indexes()
        return obj

    def _build_indexes(self) -> None:
        self._trip_meta = self.trips.set_index("trip_id")[["route_id", "service_id", "direction_id"]]
        self._st_by_stop = {sid: df for sid, df in self.stop_times.groupby("stop_id", sort=False)}
        self._stop_names = self.stops.set_index("stop_id")["stop_name"].to_dict()
        self._parent = self.stops.set_index("stop_id")["parent_station"].to_dict()
        # realtime trip-id suffix -> [(static trip_id, service_id)] for O(1) matching
        self._by_suffix: dict[str, list[tuple[str, str]]] = {}
        self._by_stem: dict[str, list[tuple[str, str]]] = {}
        for tid, sid in zip(self.trips["trip_id"], self.trips["service_id"]):
            self._by_suffix.setdefault(rt_trip_suffix(tid), []).append((tid, sid))
            self._by_stem.setdefault(rt_trip_stem(tid), []).append((tid, sid))
        self._st_by_trip: dict[str, dict[str, int]] | None = None
        self._service_cache: dict[date, set[str]] = {}
        self._nearest_cache: dict[tuple, tuple[list[str], np.ndarray]] = {}
        self._seg_cache: dict[tuple, list] = {}
        self._run_cache: dict[tuple, list] = {}

    def _trip_stop_index(self) -> dict[str, dict[str, int]]:
        if self._st_by_trip is None:
            idx: dict[str, dict[str, int]] = {}
            for tid, sid, sec in zip(self.stop_times["trip_id"], self.stop_times["stop_id"], self.stop_times["arrival_sec"]):
                idx.setdefault(tid, {})[sid] = int(sec)
            self._st_by_trip = idx
        return self._st_by_trip

    def scheduled_arrival(self, rt_trip_id: str, stop_id: str, service_date: date) -> float | None:
        """Scheduled arrival (epoch seconds) of a realtime trip at a stop, or None if unmatched."""
        tid = self.match_trip(rt_trip_id, service_date)
        if tid is None:
            return None
        sec = self._trip_stop_index().get(tid, {}).get(stop_id)
        if sec is None:
            return None
        return service_midnight(service_date).timestamp() + sec

    # ------------------------------------------------------------------ #
    # Stations
    # ------------------------------------------------------------------ #
    def static_trip_arrival(self, static_trip_id: str, stop_id: str, service_date: date) -> float | None:
        """Scheduled arrival (epoch seconds) of a *static* trip id at a stop on a service date."""
        sec = self._trip_stop_index().get(static_trip_id, {}).get(stop_id)
        if sec is None:
            return None
        return service_midnight(service_date).timestamp() + float(sec)

    def nearest_scheduled_trip(self, stop_id: str, route_id: str, service_date: date, ts: float,
                               tol_sec: float = 900.0) -> tuple[str, float] | None:
        """(static trip id, scheduled arrival) of the same route nearest to ``ts`` at the stop, within ``tol_sec``.

        The fallback for realtime trips whose id is not in the timetable (supplement schedules, reroutes)."""
        key = (stop_id, service_date, str(route_id))
        ev = self._nearest_cache.get(key)
        if ev is None:
            df = self.scheduled_stop_events(stop_id, service_date, [str(route_id)])
            ev = (list(df["trip_id"]), df["arrival_ts"].to_numpy(dtype=float))
            self._nearest_cache[key] = ev
        tids, arr = ev
        if len(arr) == 0:
            return None
        i = int(np.argmin(np.abs(arr - ts)))
        if abs(arr[i] - ts) > tol_sec:
            return None
        return tids[i], float(arr[i])

    def stop_name(self, stop_id: str) -> str:
        return self._stop_names.get(stop_id, stop_id)

    def parent_of(self, stop_id: str) -> str:
        p = self._parent.get(stop_id)
        if isinstance(p, str) and p:
            return p
        return stop_id

    def find_stations(self, query: str) -> pd.DataFrame:
        """Parent stations whose name contains ``query`` (case-insensitive) or whose id equals it."""
        parents = self.stops[(self.stops["location_type"].astype(str) == "1") |
                             (self.stops["parent_station"].isna()) |
                             (self.stops["parent_station"].astype(str) == "")]
        q = query.lower()
        mask = parents["stop_name"].str.lower().str.contains(q, regex=False) | (parents["stop_id"] == query)
        return parents[mask][["stop_id", "stop_name", "stop_lat", "stop_lon"]].reset_index(drop=True)

    def station_platforms(self, parent_id: str) -> list[str]:
        kids = self.stops[self.stops["parent_station"] == parent_id]["stop_id"].tolist()
        return kids or [parent_id]

    def platform_for(self, parent_id: str, direction: str) -> str:
        cand = f"{parent_id}{direction.upper()}"
        if cand in self._stop_names:
            return cand
        for k in self.station_platforms(parent_id):
            if direction_from_stop_id(k) == direction.upper():
                return k
        raise KeyError(f"no {direction} platform for station {parent_id}")

    def routes_serving(self, stop_id: str) -> list[str]:
        st = self._st_by_stop.get(stop_id)
        if st is None:
            return []
        routes = self._trip_meta.reindex(st["trip_id"])["route_id"].dropna().unique()
        return sorted(routes.tolist())

    # ------------------------------------------------------------------ #
    # Calendar
    # ------------------------------------------------------------------ #
    def active_services(self, service_date: date) -> set[str]:
        ymd = service_date.strftime("%Y%m%d")
        dow = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][service_date.weekday()]
        active: set[str] = set()
        if len(self.calendar):
            cal = self.calendar
            mask = (cal[dow].astype(str) == "1") & (cal["start_date"] <= ymd) & (cal["end_date"] >= ymd)
            active |= set(cal[mask]["service_id"])
        if len(self.calendar_dates):
            cd = self.calendar_dates[self.calendar_dates["date"] == ymd]
            active |= set(cd[cd["exception_type"].astype(str) == "1"]["service_id"])
            active -= set(cd[cd["exception_type"].astype(str) == "2"]["service_id"])
        return active

    # ------------------------------------------------------------------ #
    # Schedule
    # ------------------------------------------------------------------ #
    def scheduled_stop_events(self, stop_ids: list[str] | str, service_date: date,
                              route_ids: list[str] | None = None) -> pd.DataFrame:
        """All scheduled arrivals at the stop(s) on a service date.

        Columns: trip_id, route_id, direction_id, direction, service_id, stop_id,
        stop_sequence, arrival_sec, arrival_ts (epoch seconds), service_date.
        """
        if isinstance(stop_ids, str):
            stop_ids = [stop_ids]
        active = self.active_services(service_date)
        frames = []
        for sid in stop_ids:
            st = self._st_by_stop.get(sid)
            if st is None:
                continue
            frames.append(st)
        if not frames:
            return pd.DataFrame(columns=["trip_id", "route_id", "direction_id", "direction", "service_id",
                                         "stop_id", "stop_sequence", "arrival_sec", "arrival_ts", "service_date"])
        st = pd.concat(frames)
        st = st.join(self._trip_meta, on="trip_id")
        st = st[st["service_id"].isin(active)]
        if route_ids:
            st = st[st["route_id"].isin(route_ids)]
        midnight = service_midnight(service_date).timestamp()
        out = st[["trip_id", "route_id", "direction_id", "service_id", "stop_id", "stop_sequence", "arrival_sec"]].copy()
        out["arrival_ts"] = midnight + out["arrival_sec"].astype(float)
        out["direction"] = out["stop_id"].map(direction_from_stop_id)
        out["service_date"] = service_date
        return out.sort_values("arrival_ts").reset_index(drop=True)

    def scheduled_headways(self, stop_id: str, service_date: date,
                           route_ids: list[str] | None = None) -> pd.DataFrame:
        """Scheduled headways (seconds) at a stop, per arrival, for the given routes combined."""
        ev = self.scheduled_stop_events(stop_id, service_date, route_ids)
        ev["headway_sec"] = ev["arrival_ts"].diff()
        return ev

    def match_trip(self, rt_trip_id: str, service_date: date) -> str | None:
        """Best static trip id for a realtime trip id (suffix match among active services)."""
        suffix = rt_trip_suffix(rt_trip_id)
        active = self._service_cache.get(service_date)
        if active is None:
            active = self.active_services(service_date)
            self._service_cache[service_date] = active
        for tid, sid in self._by_suffix.get(suffix, []):
            if sid in active:
                return tid
        for tid, sid in self._by_stem.get(rt_trip_stem(rt_trip_id), []):   # realtime id without a path code
            if sid in active:
                return tid
        return None

    # ------------------------------------------------------------------ #
    # Topology
    # ------------------------------------------------------------------ #
    def canonical_stop_sequence(self, route_id: str, direction: str) -> list[str]:
        """Ordered stop ids of the most common trip pattern for a route/direction."""
        trips = self.trips[self.trips["route_id"] == route_id]
        trips = trips[trips["trip_id"].map(direction_from_trip_id) == direction.upper()]
        if trips.empty:
            return []
        st = self.stop_times[self.stop_times["trip_id"].isin(trips["trip_id"])]
        patterns = st.sort_values(["trip_id", "stop_sequence"]).groupby("trip_id")["stop_id"].agg(tuple)
        if patterns.empty:
            return []
        # Prefer the longest of the most common patterns.
        counts = patterns.value_counts()
        top = counts[counts == counts.max()].index
        best = max(top, key=len)
        return list(best)

    def _pattern_trips(self, route_id: str, direction: str, seq: list[str], limit: int = 60) -> list[str]:
        """Trip ids of the route/direction whose stop pattern is exactly the canonical sequence."""
        trips = self.trips[self.trips["route_id"] == route_id]
        trips = trips[trips["trip_id"].map(direction_from_trip_id) == direction.upper()]
        idx = self._trip_stop_index()
        want = tuple(seq)
        out = []
        for tid in trips["trip_id"]:
            st = idx.get(tid)
            if st and len(st) == len(want) and tuple(sorted(st, key=st.get)) == want:
                out.append(tid)
                if len(out) >= limit:
                    break
        return out

    def canonical_run_sec(self, route_id: str, direction: str) -> list[float | None]:
        """Median scheduled running time between consecutive canonical stops (len = stops - 1)."""
        key = (route_id, direction.upper())
        if key in self._run_cache:
            return self._run_cache[key]
        seq = self.canonical_stop_sequence(route_id, direction)
        out: list[float | None] = []
        if len(seq) >= 2:
            idx = self._trip_stop_index()
            tids = self._pattern_trips(route_id, direction, seq)
            for a, b in zip(seq, seq[1:]):
                vals = [idx[t][b] - idx[t][a] for t in tids if idx[t][b] > idx[t][a]]
                out.append(float(np.median(vals)) if vals else None)
        self._run_cache[key] = out
        return out

    def segment_lengths(self, route_id: str, direction: str) -> list[float | None]:
        """Track distance in metres between consecutive canonical stops, from shapes.txt when the feed has it
        (stops projected onto the trip's shape), else the great-circle distance between the stops."""
        key = (route_id, direction.upper())
        if key in self._seg_cache:
            return self._seg_cache[key]
        seq = self.canonical_stop_sequence(route_id, direction)
        coords = self.stops.set_index("stop_id")[["stop_lat", "stop_lon"]] if "stop_lat" in self.stops else None
        out: list[float | None] = []
        if len(seq) >= 2 and coords is not None:
            pts = [tuple(coords.loc[s]) if s in coords.index else (np.nan, np.nan) for s in seq]
            gc = [haversine_m(pts[i], pts[i + 1]) for i in range(len(seq) - 1)]
            along = self._along_shape(route_id, direction, seq, pts)
            for i in range(len(seq) - 1):
                d = None
                if along is not None and along[i] is not None and along[i + 1] is not None and along[i + 1] > along[i]:
                    d = along[i + 1] - along[i]
                    if gc[i] and d < gc[i] * 0.9:     # a projection glitch: the track cannot be shorter than the crow flies
                        d = None
                out.append(float(d) if d is not None else (float(gc[i]) if gc[i] == gc[i] else None))
        self._seg_cache[key] = out
        return out

    def _along_shape(self, route_id: str, direction: str, seq: list[str], pts: list) -> list | None:
        if self.shapes is None or self.shapes.empty or "shape_id" not in self.trips.columns:
            return None
        tids = self._pattern_trips(route_id, direction, seq, limit=20)
        if not tids:
            return None
        sid = self.trips[self.trips["trip_id"].isin(tids)]["shape_id"].dropna().mode()
        if sid.empty:
            return None
        sh = self.shapes[self.shapes["shape_id"] == sid.iloc[0]].copy()
        if sh.empty:
            return None
        sh["shape_pt_sequence"] = pd.to_numeric(sh["shape_pt_sequence"], errors="coerce")
        sh = sh.sort_values("shape_pt_sequence")
        lat = pd.to_numeric(sh["shape_pt_lat"], errors="coerce").to_numpy(); lon = pd.to_numeric(sh["shape_pt_lon"], errors="coerce").to_numpy()
        if len(lat) < 2:
            return None
        # local planar metres around the shape's centroid
        lat0 = float(np.nanmean(lat)); kx = 111_320.0 * np.cos(np.radians(lat0)); ky = 110_540.0
        x = (lon - float(np.nanmean(lon))) * kx; y = (lat - lat0) * ky
        seg = np.hypot(np.diff(x), np.diff(y)); cum = np.concatenate([[0.0], np.cumsum(seg)])
        out = []
        for plat, plon in pts:
            if plat != plat:
                out.append(None); continue
            px = (plon - float(np.nanmean(lon))) * kx; py = (plat - lat0) * ky
            ax, ay, bx, by = x[:-1], y[:-1], x[1:], y[1:]
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy
            t = np.where(L2 > 0, ((px - ax) * dx + (py - ay) * dy) / np.where(L2 > 0, L2, 1), 0.0)
            t = np.clip(t, 0.0, 1.0)
            qx, qy = ax + t * dx, ay + t * dy
            d2 = (px - qx) ** 2 + (py - qy) ** 2
            i = int(np.argmin(d2))
            if d2[i] > 300.0 ** 2:      # more than 300 m from the shape: not this track
                out.append(None); continue
            out.append(float(cum[i] + t[i] * seg[i]))
        return out

    def upstream_stops(self, route_id: str, direction: str, stop_id: str, n: int = 6) -> list[str]:
        """Up to ``n`` stops immediately preceding ``stop_id`` on the canonical pattern
        (nearest first). Falls back to any trip pattern that contains the stop."""
        seq = self.canonical_stop_sequence(route_id, direction)
        if stop_id not in seq:
            trips = self.trips[self.trips["route_id"] == route_id]["trip_id"]
            st = self._st_by_stop.get(stop_id)
            if st is None:
                return []
            cand = st[st["trip_id"].isin(trips)]
            if cand.empty:
                return []
            tid = cand.iloc[0]["trip_id"]
            seq = self.stop_times[self.stop_times["trip_id"] == tid].sort_values("stop_sequence")["stop_id"].tolist()
            if stop_id not in seq:
                return []
        i = seq.index(stop_id)
        return list(reversed(seq[max(0, i - n):i]))

    def merge_routes_upstream(self, route_id: str, direction: str, stop_id: str, n: int = 6) -> dict[str, list[str]]:
        """Other routes that share the upstream stops (i.e. interline on the approach).

        Returns {other_route_id: [shared_stop_ids]} for the ``n`` stops before ``stop_id``.
        """
        ups = self.upstream_stops(route_id, direction, stop_id, n) + [stop_id]
        shared: dict[str, list[str]] = {}
        for s in ups:
            for r in self.routes_serving(s):
                if r != route_id:
                    shared.setdefault(r, []).append(s)
        return shared

    def terminal_stop(self, route_id: str, direction: str) -> str | None:
        seq = self.canonical_stop_sequence(route_id, direction)
        return seq[0] if seq else None

    def summary(self) -> dict:
        info = {}
        if self.feed_info is not None and len(self.feed_info):
            row = self.feed_info.iloc[0].to_dict()
            info = {k: row.get(k) for k in ("feed_version", "feed_start_date", "feed_end_date")}
        return {
            "routes": int(self.routes["route_id"].nunique()),
            "stations": int((self.stops["location_type"].astype(str) == "1").sum()),
            "trips": int(len(self.trips)),
            "stop_times": int(len(self.stop_times)),
            **info,
        }
