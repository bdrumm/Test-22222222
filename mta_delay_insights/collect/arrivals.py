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

from ..storage.db import ARRIVAL_COLUMNS, ETA_SAMPLE_COLUMNS

SAMPLE_STOPS_AHEAD = (1, 2, 3, 5, 8, 12)   # ETA snapshots kept when the train is this many stops away


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
    train_id: str | None = None
    sched_track: str | None = None
    actual_track: str | None = None


@dataclass
class ArrivalTracker:
    """Stateful tracker; call :meth:`update` with each snapshot's trip-update frame."""

    stops_of_interest: set[str] | None = None
    poll_interval_sec: float = 30.0
    vanish_slack_sec: float = 120.0
    sample_stops: set[str] | None = None      # stops for which ETA samples are kept (default: stops of interest)
    state: dict[str, dict[str, TrackedStop]] = field(default_factory=dict)
    trip_last_seen: dict[str, float] = field(default_factory=dict)
    trip_order: dict[str, list[str]] = field(default_factory=dict)   # remaining stop order at the last poll
    samples: list[dict] = field(default_factory=list)

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
                order_now = list(grp["stop_id"])
                current = set(order_now)
                prev_order = self.trip_order.get(key, [])
                # Stops we tracked that are no longer predicted -> served.
                served = [sid for sid in prev_order if sid not in current] or [sid for sid in tracked if sid not in current]
                for stop_id in served:
                    ts = tracked.pop(stop_id, None)
                    if ts is not None:
                        emitted.append(self._emit(ts, snapshot_ts, source="rt_dropoff"))
                        self._sample(key, stop_id, ts, prev_order, tracked)
                    else:
                        self._sample(key, stop_id, None, prev_order, tracked, snapshot_ts)
                self.trip_order[key] = order_now
                if self.stops_of_interest:
                    grp = grp[grp["stop_id"].isin(self.stops_of_interest)]
                has_ext = "train_id" in grp.columns
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
                            last_dep_ts=None if pd.isna(row.departure_ts) else float(row.departure_ts),
                            train_id=(row.train_id if has_ext and isinstance(row.train_id, str) else None),
                            sched_track=(row.sched_track if has_ext and isinstance(row.sched_track, str) else None),
                            actual_track=(row.actual_track if has_ext and isinstance(row.actual_track, str) else None))
                    else:
                        t.last_seen_ts = snapshot_ts
                        t.last_pred_ts = float(row.arrival_ts)
                        t.last_dep_ts = None if pd.isna(row.departure_ts) else float(row.departure_ts)
                        t.n_predictions += 1
                        if has_ext:
                            if isinstance(row.actual_track, str):
                                t.actual_track = row.actual_track
                            if isinstance(row.sched_track, str) and not t.sched_track:
                                t.sched_track = row.sched_track
                            if isinstance(row.train_id, str) and not t.train_id:
                                t.train_id = row.train_id
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
            self.trip_order.pop(key, None)
        return pd.DataFrame(emitted, columns=ARRIVAL_COLUMNS)

    def _sample(self, key: str, at_stop: str, served: TrackedStop | None, prev_order: list[str],
                tracked: dict[str, TrackedStop], now: float | None = None) -> None:
        """When the train serves ``at_stop``, record the ETA it was showing for the stops ahead."""
        if at_stop not in prev_order:
            return
        i = prev_order.index(at_stop)
        at_ts = served.last_pred_ts if served is not None else float(now)
        route = served.route_id if served is not None else next((t.route_id for t in tracked.values()), None)
        keep = self.sample_stops if self.sample_stops is not None else self.stops_of_interest
        for k in SAMPLE_STOPS_AHEAD:
            j = i + k
            if j >= len(prev_order):
                break
            sid = prev_order[j]
            if keep is not None and sid not in keep:
                continue
            t = tracked.get(sid)
            if t is None:
                continue
            self.samples.append({"trip_key": key, "route_id": route, "stop_id": sid, "at_stop": at_stop, "at_ts": at_ts,
                                 "stops_ahead": k, "eta_ts": t.last_pred_ts})

    def take_samples(self) -> pd.DataFrame:
        out = pd.DataFrame(self.samples, columns=ETA_SAMPLE_COLUMNS)
        self.samples = []
        return out

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
            "train_id": ts.train_id, "sched_track": ts.sched_track, "actual_track": ts.actual_track,
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
