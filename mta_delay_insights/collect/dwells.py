"""Dwell-time estimates from vehicle positions.

The NYCT vehicle feed reports ``STOPPED_AT`` a stop while the train is in the
station. Polling every ~30 s gives a lower bound on each dwell (first poll seen
stopped to last poll seen stopped); averaged over many trains per stop and
hour it is a usable crowding / holding signal, and the transition times are a
second, independent estimate of arrival and departure.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..storage.db import DWELL_COLUMNS

HOLD_SEC = 150.0        # stopped this long at a station = holding (a normal dwell is 30-60 s)


@dataclass
class _Stop:
    route_id: str | None
    direction: str | None
    stop_id: str
    first_ts: float
    last_ts: float
    polls: int = 1


@dataclass
class DwellTracker:
    """Dwells at the stops of interest, plus every *hold* (dwell >= ``hold_sec``) anywhere on the network."""

    stops_of_interest: set[str] | None = None
    hold_sec: float | None = HOLD_SEC
    state: dict[str, _Stop] = field(default_factory=dict)

    @staticmethod
    def trip_key(trip_id: str, start_date: str | None) -> str:
        return f"{start_date or ''}|{trip_id}"

    def update(self, vehicles: pd.DataFrame, snapshot_ts: float) -> pd.DataFrame:
        out = []
        seen = set()
        if vehicles is not None and not vehicles.empty:
            for r in vehicles.itertuples(index=False):
                key = self.trip_key(r.trip_id, None if pd.isna(r.start_date) else r.start_date)
                seen.add(key)
                stopped = (r.current_status == "STOPPED_AT") and isinstance(r.stop_id, str) and r.stop_id
                ts = float(r.vehicle_ts) if r.vehicle_ts is not None and not pd.isna(r.vehicle_ts) else snapshot_ts
                ts = min(ts, snapshot_ts)
                cur = self.state.get(key)
                if stopped:
                    if cur is not None and cur.stop_id == r.stop_id:
                        cur.last_ts = max(cur.last_ts, snapshot_ts)
                        cur.polls += 1
                        continue
                    if cur is not None:
                        out.append(self._emit(key, cur))
                    self.state[key] = _Stop(r.route_id, r.direction, r.stop_id, ts, snapshot_ts)
                elif cur is not None:
                    out.append(self._emit(key, cur))
                    del self.state[key]
        for key in list(self.state):
            if key not in seen:
                out.append(self._emit(key, self.state.pop(key)))
        rows = [d for d in out if self.stops_of_interest is None or d["stop_id"] in self.stops_of_interest
                or (self.hold_sec is not None and d["dwell_sec"] >= self.hold_sec)]
        return pd.DataFrame(rows, columns=DWELL_COLUMNS)

    @staticmethod
    def _emit(key: str, s: _Stop) -> dict:
        return {"trip_key": key, "route_id": s.route_id, "direction": s.direction, "stop_id": s.stop_id,
                "stopped_from_ts": s.first_ts, "stopped_to_ts": s.last_ts, "dwell_sec": max(0.0, s.last_ts - s.first_ts), "polls": s.polls}


SEGMENT_RUN_COLUMNS = ["trip_key", "route_id", "direction", "from_stop", "to_stop", "depart_ts", "arrive_ts", "run_sec"]


@dataclass
class _Obs:
    status: str
    stop_id: str
    ts: float
    last_stopped: str | None = None


@dataclass
class SegmentTracker:
    """Realized inter-station running times from vehicle state transitions.

    The NYCT feed has no GPS or speed, but a vehicle's timestamp is the moment it entered its current state:
    ``IN_TRANSIT_TO X`` stamped at departure from the previous stop, ``STOPPED_AT X`` stamped at arrival. Seeing
    the transit state and then the stop gives the segment's run time at feed precision, not poll precision."""

    state: dict[str, _Obs] = field(default_factory=dict)

    @staticmethod
    def trip_key(trip_id: str, start_date: str | None) -> str:
        return f"{start_date or ''}|{trip_id}"

    def update(self, vehicles: pd.DataFrame, snapshot_ts: float) -> pd.DataFrame:
        rows = []
        if vehicles is not None and not vehicles.empty:
            for r in vehicles.itertuples(index=False):
                stop = r.stop_id if isinstance(r.stop_id, str) else None
                ts = float(r.vehicle_ts) if r.vehicle_ts is not None and not pd.isna(r.vehicle_ts) else None
                if not stop or ts is None or ts > snapshot_ts + 60:
                    continue
                status = r.current_status if isinstance(r.current_status, str) else "IN_TRANSIT_TO"
                key = self.trip_key(r.trip_id, None if pd.isna(r.start_date) else r.start_date)
                prev = self.state.get(key)
                if prev is not None and status == "STOPPED_AT" and prev.status in ("IN_TRANSIT_TO", "INCOMING_AT") \
                        and prev.stop_id == stop and prev.last_stopped and prev.last_stopped != stop and ts > prev.ts:
                    rows.append({"trip_key": key, "route_id": r.route_id, "direction": r.direction, "from_stop": prev.last_stopped, "to_stop": stop,
                                 "depart_ts": prev.ts, "arrive_ts": ts, "run_sec": ts - prev.ts})
                last_stopped = prev.last_stopped if prev is not None else None
                if status == "STOPPED_AT":
                    last_stopped = stop
                elif prev is not None and prev.status == "STOPPED_AT" and prev.stop_id != stop:
                    last_stopped = prev.stop_id
                self.state[key] = _Obs(status, stop, ts, last_stopped)
        return pd.DataFrame(rows, columns=SEGMENT_RUN_COLUMNS)
