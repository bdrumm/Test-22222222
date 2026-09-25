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
