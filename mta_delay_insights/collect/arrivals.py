"""Derive *observed* arrivals from successive GTFS-Realtime trip updates.

The MTA feed only publishes predictions for stops a train has not yet left.
When a stop disappears from a trip's stop list between two snapshots, the train
has served it; the last prediction we saw is the best estimate of the actual
arrival (it converges to the real time as the train approaches). This is the
same approach used by public arrival-history projects for the NYC subway.

For each tracked (trip, stop) pair we also keep how many predictions were seen
and how far the ETA drifted, which the analysis uses as a "prediction
volatility" signal (a symptom of holds and dispatch changes).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..storage.db import ARRIVAL_COLUMNS


@dataclass
class TrackedStop:
    trip_key: str
    trip_id: str
    route_id: str
    start_date: str | None
    direction: str | None
    stop_id: str
    first_seen_ts: float
    last_seen_ts: float
    first_pred_ts: float
    last_pred_ts: float
    last_dep_ts: float | None
    n_predictions: int = 1


@dataclass
class ArrivalTracker:
    """Stateful tracker; call :meth:`update` with each snapshot's trip-update frame."""

    stops_of_interest: set[str] | None = None
    poll_interval_sec: float = 30.0
    vanish_slack_sec: float = 120.0
    state: dict[str, dict[str, TrackedStop]] = field(default_factory=dict)
    trip_last_seen: dict[str, float] = field(default_factory=dict)

    @staticmethod
    def trip_key(trip_id: str, start_date: str | None) -> str:
        return f"{start_date or ''}|{trip_id}"

    def update(self, predictions: pd.DataFrame, snapshot_ts: float) -> pd.DataFrame:
        """Ingest one snapshot; return a DataFrame of arrivals observed since the last one."""
        emitted: list[dict] = []
        seen_trips: set[str] = set()
        if not predictions.empty:
            preds = predictions
            if self.stops_of_interest:
                preds = preds[preds["stop_id"].isin(self.stops_of_interest)]
            for (trip_id, start_date), grp in predictions.groupby(["trip_id", "start_date"], dropna=False, sort=False):
                start_date = None if pd.isna(start_date) else start_date
                key = self.trip_key(trip_id, start_date)
                seen_trips.add(key)
                self.trip_last_seen[key] = snapshot_ts
                tracked = self.state.setdefault(key, {})
                current = set(grp["stop_id"])
                # Stops we tracked that are no longer predicted -> served.
                for stop_id in list(tracked):
                    if stop_id not in current:
                        ts = tracked.pop(stop_id)
                        emitted.append(self._emit(ts, snapshot_ts, source="rt_dropoff"))
                if self.stops_of_interest:
                    grp = grp[grp["stop_id"].isin(self.stops_of_interest)]
                for row in grp.itertuples(index=False):
                    if row.arrival_ts is None or pd.isna(row.arrival_ts):
                        continue
                    t = tracked.get(row.stop_id)
                    if t is None:
                        tracked[row.stop_id] = TrackedStop(
                            trip_key=key, trip_id=trip_id, route_id=row.route_id, start_date=start_date,
                            direction=row.direction, stop_id=row.stop_id, first_seen_ts=snapshot_ts,
                            last_seen_ts=snapshot_ts, first_pred_ts=float(row.arrival_ts),
                            last_pred_ts=float(row.arrival_ts),
                            last_dep_ts=None if pd.isna(row.departure_ts) else float(row.departure_ts))
                    else:
                        t.last_seen_ts = snapshot_ts
                        t.last_pred_ts = float(row.arrival_ts)
                        t.last_dep_ts = None if pd.isna(row.departure_ts) else float(row.departure_ts)
                        t.n_predictions += 1
        # Trips that vanished: their remaining stops were probably served (trip ended)
        # if their predicted time has passed; otherwise treat as cancelled.
        for key in list(self.state):
            if key in seen_trips:
                continue
            last_seen = self.trip_last_seen.get(key, snapshot_ts)
            if snapshot_ts - last_seen < self.poll_interval_sec * 2:
                continue  # allow one missed poll before deciding
            for stop_id, ts in self.state[key].items():
                if ts.last_pred_ts <= snapshot_ts + self.vanish_slack_sec:
                    emitted.append(self._emit(ts, snapshot_ts, source="trip_vanished"))
            del self.state[key]
            self.trip_last_seen.pop(key, None)
        return pd.DataFrame(emitted, columns=ARRIVAL_COLUMNS)

    def _emit(self, ts: TrackedStop, snapshot_ts: float, source: str) -> dict:
        arrival = min(ts.last_pred_ts, snapshot_ts)
        staleness = snapshot_ts - ts.last_seen_ts
        # High confidence when the stop dropped off within ~2 polls of its last prediction
        # and the prediction itself was not far in the future (i.e. converged).
        conf = 1.0
        if source == "trip_vanished":
            conf = 0.6
        if staleness > 2 * self.poll_interval_sec:
            conf -= 0.2
        if ts.n_predictions < 2:
            conf -= 0.2
        if ts.last_pred_ts - ts.last_seen_ts > 4 * self.poll_interval_sec:
            conf -= 0.3
        return {
            "trip_key": ts.trip_key, "trip_id": ts.trip_id, "route_id": ts.route_id,
            "start_date": ts.start_date, "direction": ts.direction, "stop_id": ts.stop_id,
            "arrival_ts": arrival, "departure_ts": ts.last_dep_ts, "source": source,
            "first_seen_ts": ts.first_seen_ts, "last_seen_ts": ts.last_seen_ts,
            "first_pred_ts": ts.first_pred_ts, "n_predictions": ts.n_predictions,
            "pred_drift_sec": ts.last_pred_ts - ts.first_pred_ts, "confidence": max(0.0, round(conf, 2)),
        }

    def pending(self) -> int:
        return sum(len(v) for v in self.state.values())

    def flush(self, now: float) -> pd.DataFrame:
        """Emit tracked stops whose predicted time has passed (end of a collection run).

        Chunked collection (e.g. hourly CI jobs) would otherwise lose the arrivals of
        the last few minutes. Emitted rows carry ``source='flush'`` and reduced confidence.
        """
        emitted = []
        for key in list(self.state):
            for stop_id, ts in list(self.state[key].items()):
                if ts.last_pred_ts <= now:
                    row = self._emit(ts, now, source="flush")
                    row["confidence"] = max(0.0, min(row["confidence"], 0.7))
                    emitted.append(row)
                    del self.state[key][stop_id]
            if not self.state[key]:
                del self.state[key]
        return pd.DataFrame(emitted, columns=ARRIVAL_COLUMNS)
