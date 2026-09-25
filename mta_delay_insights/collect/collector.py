"""Polling collector: fetch feeds, derive arrivals, persist, optionally archive raw bytes."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from .. import config
from ..sources import alerts as alerts_src
from ..sources import gtfs_realtime as rt
from ..storage.db import Store
from .arrivals import ArrivalTracker
from .dwells import DwellTracker

log = logging.getLogger(__name__)

Fetcher = Callable[[str], bytes]  # feed_key -> protobuf bytes


class Collector:
    def __init__(self, store: Store, feeds: Iterable[str], stops_of_interest: Iterable[str] | None = None,
                 store_predictions: bool = False, poll_interval_sec: float = 30.0,
                 fetcher: Fetcher | None = None, alerts_fetcher: Callable[[], dict] | None = None,
                 alerts_every_n_polls: int = 2, raw_dir: str | Path | None = None,
                 sample_stops: Iterable[str] | None = None, track_dwells: bool = True):
        self.store = store
        self.feeds = list(feeds)
        self.stops_of_interest = set(stops_of_interest) if stops_of_interest else None
        self.sample_stops = set(sample_stops) if sample_stops is not None else self.stops_of_interest
        self.track_dwells = track_dwells
        self.store_predictions = store_predictions
        self.poll_interval_sec = poll_interval_sec
        self.fetcher = fetcher or (lambda key: rt.fetch_feed_bytes(config.rt_feed_url(key)))
        self.alerts_fetcher = alerts_fetcher
        self.alerts_every_n_polls = alerts_every_n_polls
        self.raw_dir = Path(raw_dir) if raw_dir else None
        self.trackers = {f: ArrivalTracker(self.stops_of_interest, poll_interval_sec, sample_stops=self.sample_stops) for f in self.feeds}
        self.dwell_trackers = {f: DwellTracker(self.stops_of_interest) for f in self.feeds}
        self.polls = 0
        self.last_feed_bytes: dict[str, bytes] = {}
        self.last_alerts: dict | None = None
        self.last_poll_ts: float | None = None
        self.on_poll: Callable[["Collector", float], None] | None = None

    def ingest(self, feed_key: str, data: bytes, snapshot_ts: float) -> pd.DataFrame:
        """Process one raw feed snapshot (live or replayed). Returns emitted arrivals."""
        msg = rt.parse_feed(data)
        tu = rt.trip_updates_frame(msg, feed_key, snapshot_ts)
        vp = rt.vehicle_positions_frame(msg, feed_key, snapshot_ts)
        tracker = self.trackers[feed_key]
        arrivals = tracker.update(tu, snapshot_ts)
        if self.store_predictions:
            self.store.insert_predictions(tu if not self.stops_of_interest else tu[tu["stop_id"].isin(self.stops_of_interest)])
        self.store.insert_arrivals(arrivals)
        self.store.insert_eta_samples(tracker.take_samples())
        if self.track_dwells:
            dt = self.dwell_trackers.setdefault(feed_key, DwellTracker(self.stops_of_interest))
            self.store.insert_dwells(dt.update(vp, snapshot_ts))
        feed_ts = float(msg.header.timestamp) if msg.header.HasField("timestamp") else None
        self.store.insert_snapshot(feed_key, snapshot_ts, feed_ts, int(tu["trip_id"].nunique()), len(vp), len(arrivals))
        if self.raw_dir:
            d = self.raw_dir / feed_key
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{int(snapshot_ts)}.pb").write_bytes(data)
        return arrivals

    def ingest_alerts(self, payload: dict, snapshot_ts: float) -> int:
        df = alerts_src.alerts_frame(payload)
        return self.store.upsert_alerts(df, seen_ts=snapshot_ts)

    def poll_once(self, now: float | None = None) -> dict:
        now = now or time.time()
        summary = {"ts": now, "arrivals": 0, "errors": 0}
        for key in self.feeds:
            try:
                data = self.fetcher(key)
                self.last_feed_bytes[key] = data
                arrivals = self.ingest(key, data, now)
                summary["arrivals"] += len(arrivals)
            except Exception as exc:  # network hiccups must not kill the loop
                summary["errors"] += 1
                log.warning("feed %s failed: %s", key, exc)
        if self.alerts_fetcher and self.polls % self.alerts_every_n_polls == 0:
            try:
                payload = self.alerts_fetcher()
                self.last_alerts = payload
                summary["alerts"] = self.ingest_alerts(payload, now)
            except Exception as exc:
                summary["errors"] += 1
                log.warning("alerts failed: %s", exc)
        self.polls += 1
        self.last_poll_ts = now
        if self.on_poll:
            try:
                self.on_poll(self, now)
            except Exception as exc:
                log.warning("on_poll hook failed: %s", exc)
        return summary

    def run(self, duration_sec: float | None = None, max_polls: int | None = None) -> list[dict]:
        start = time.time()
        out = []
        while True:
            t0 = time.time()
            out.append(self.poll_once(t0))
            if max_polls and len(out) >= max_polls:
                break
            if duration_sec and time.time() - start >= duration_sec:
                break
            time.sleep(max(0.0, self.poll_interval_sec - (time.time() - t0)))
        return out

    def flush(self, now: float | None = None) -> int:
        """Persist arrivals still pending in the trackers (call at the end of a bounded run)."""
        now = now or time.time()
        n = 0
        for tracker in self.trackers.values():
            n += self.store.insert_arrivals(tracker.flush(now))
        return n

    def replay(self, snapshots: Iterable[tuple[str, float, bytes]]) -> int:
        """Replay recorded (feed_key, ts, bytes) snapshots in order. Returns arrivals emitted."""
        n = 0
        for key, ts, data in sorted(snapshots, key=lambda s: s[1]):
            if key not in self.trackers:
                self.trackers[key] = ArrivalTracker(self.stops_of_interest, self.poll_interval_sec, sample_stops=self.sample_stops)
            n += len(self.ingest(key, data, ts))
        return n

    @staticmethod
    def load_raw_dir(raw_dir: str | Path) -> list[tuple[str, float, bytes]]:
        out = []
        for feed_dir in Path(raw_dir).iterdir():
            if not feed_dir.is_dir():
                continue
            for f in feed_dir.glob("*.pb"):
                out.append((feed_dir.name, float(f.stem), f.read_bytes()))
        return sorted(out, key=lambda s: s[1])
