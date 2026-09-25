"""Serve the learned arrival model for live trains.

For a live train and a target stop ``d`` the model needs the same state the
training rows were built from: the train's last observed stop ``u`` (from the
store's recent arrivals, falling back to the feed's next stop), its lateness
and momentum there, the traffic ahead at ``u``, the segment's recent excess,
the destination's recent lateness, and the feed's own ETA for ``d`` right now.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..models.arrival import ArrivalModel
from ..models.features import live_features
from ..sources.gtfs_static import StaticGTFS
from ..storage.db import Store
from .status import LiveTrain


@dataclass
class LearnedContext:
    model: ArrivalModel
    store: Store | None
    static: StaticGTFS
    now: float
    alerts_df: pd.DataFrame | None = None
    weather_daily: pd.DataFrame | None = None
    events_df: pd.DataFrame | None = None
    nws_df: pd.DataFrame | None = None
    climatology: dict | None = None
    _recent: pd.DataFrame | None = None

    def recent(self) -> pd.DataFrame:
        """Arrivals of the last two hours with scheduled arrivals and lateness attached (cached)."""
        if self._recent is None:
            if self.store is None:
                self._recent = pd.DataFrame(columns=["trip_key", "trip_id", "route_id", "stop_id", "arrival_ts", "sched_ts", "lateness_sec"])
            else:
                a = self.store.arrivals(None, self.now - 2 * 3600, self.now + 1)
                if a.empty:
                    self._recent = pd.DataFrame(columns=["trip_key", "trip_id", "route_id", "stop_id", "arrival_ts", "sched_ts", "lateness_sec"])
                else:
                    from ..analysis.schedule_match import service_date_of
                    sched = [self.static.scheduled_arrival(t, s, service_date_of(ts, sd)) for t, s, ts, sd in
                             zip(a["trip_id"], a["stop_id"], a["arrival_ts"], a.get("start_date", [None] * len(a)))]
                    a["sched_ts"] = sched
                    a["lateness_sec"] = a["arrival_ts"] - pd.to_numeric(a["sched_ts"], errors="coerce")
                    self._recent = a.sort_values("arrival_ts").reset_index(drop=True)
        return self._recent

    def state_for(self, train: LiveTrain, d: str) -> dict | None:
        """Feature row for predicting the train's arrival at stop ``d``; None when not predictable."""
        rec = self.recent()
        key = f"{train.start_date or ''}|{train.trip_id}"
        mine = rec[rec["trip_key"] == key] if not rec.empty else rec
        j = train.stops_until(d)
        if j is None:
            return None
        sched_d = self.static.scheduled_arrival(train.trip_id, d, train.service_date) if train.service_date else None
        if sched_d is None:
            return None
        feed_eta_d = train.eta_at(d)
        if not mine.empty and np.isfinite(mine["lateness_sec"].iloc[-1]) and self.now - float(mine["arrival_ts"].iloc[-1]) < 1200:
            last = mine.iloc[-1]
            u, t_u, lat_u, sched_u = str(last["stop_id"]), float(last["arrival_ts"]), float(last["lateness_sec"]), float(last["sched_ts"])
            # a train provably held since that arrival is later than its last observation says
            if train.position_lateness_sec is not None and train.position_lateness_sec > lat_u + 60:
                lat_u = float(train.position_lateness_sec)
            k = j + 1
            lat = mine["lateness_sec"].values
            mom1 = float(lat[-1] - lat[-2]) if len(lat) >= 2 and np.isfinite(lat[-2]) else None
            mom3 = float(lat[-1] - lat[-4]) if len(lat) >= 4 and np.isfinite(lat[-4]) else None
        else:
            if train.lateness_sec is None or not train.started or j == 0:
                return None
            u, t_u, lat_u = train.next_stop_id, float(train.next_eta_ts), float(train.effective_lateness_sec)
            sched_u = self.static.scheduled_arrival(train.trip_id, u, train.service_date)
            if sched_u is None:
                return None
            k, mom1, mom3 = j, None, None
        sched_run = sched_d - sched_u
        if sched_run <= 0:
            return None
        # traffic ahead at u
        gap = leader_lat = same = None
        at_u = rec[(rec["stop_id"] == u) & (rec["arrival_ts"] < t_u - 1) & (rec["trip_key"] != key)] if not rec.empty else rec
        if not at_u.empty:
            lead = at_u.iloc[-1]
            gap = float(t_u - lead["arrival_ts"])
            if gap <= 3600:
                leader_lat = float(lead["lateness_sec"]) if np.isfinite(lead["lateness_sec"]) else None
                same = float(str(lead["route_id"]) == str(train.route_id))
            else:
                gap = None
        # segment conditions: last 3 trips that reached d after passing u
        seg = None
        if not rec.empty:
            ru = rec[rec["stop_id"] == u][["trip_key", "lateness_sec"]].rename(columns={"lateness_sec": "lu"})
            rd = rec[(rec["stop_id"] == d) & (rec["arrival_ts"] <= self.now)][["trip_key", "lateness_sec", "arrival_ts"]].rename(columns={"lateness_sec": "ld"})
            jj = ru.merge(rd, on="trip_key").dropna().sort_values("arrival_ts")
            jj = jj[jj["trip_key"] != key]
            if len(jj):
                seg = float((jj["ld"] - jj["lu"]).tail(3).mean())
        dest = None
        if not rec.empty:
            rd = rec[(rec["stop_id"] == d) & (rec["arrival_ts"] >= self.now - 900) & (rec["arrival_ts"] <= self.now)]["lateness_sec"].dropna()
            if len(rd):
                dest = float(rd.mean())
        feed_excess = (feed_eta_d - (sched_d + lat_u)) if feed_eta_d is not None else None
        sched_hw = None
        row = live_features(train.route_id, train.direction, int(k), float(sched_run), lat_u, mom1, mom3, gap, leader_lat, same, sched_hw,
                            seg, dest, feed_excess, float(getattr(train, "track_changed", 0.0) or 0.0), self.now,
                            self.alerts_df, self.weather_daily, self.events_df, self.nws_df, self.climatology)
        row["_sched_d"] = sched_d; row["_lat_u"] = lat_u; row["_u"] = u
        return row

    def predict(self, train: LiveTrain, d: str, feed_spread: float | None = None) -> dict | None:
        """{'eta_ts', 'lo_ts', 'hi_ts', 'excess_sec', 'k', 'u'} for the train at d, or None.

        ``feed_spread`` is the feed ETA's own p10-p90 width at this horizon (from the look-back
        calibration) and enables blending when the model has no feed feature."""
        if not self.model.ready:
            return None
        row = self.state_for(train, d)
        if row is None:
            return None
        pred = self.model.predict(pd.DataFrame([row]))
        base = row["_sched_d"] + row["_lat_u"]
        p10, p50, p90 = float(pred["p10"].iloc[0]), float(pred["p50"].iloc[0]), float(pred["p90"].iloc[0])
        eta, lo, hi = base + p50, base + p10, base + p90
        feed_eta = train.eta_at(d)
        blended = False
        # If the model never saw the feed's own forecast as a feature, combine the two estimators by
        # inverse variance: the feed knows about holds and dispatch decisions the state cannot show.
        if feed_eta is not None and "feed_excess" not in self.model.features and feed_spread is not None:
            var_m = max(((p90 - p10) / 2.56) ** 2, 1.0)
            var_f = max((feed_spread / 2.56) ** 2, 1.0)
            w = var_f / (var_m + var_f)          # weight on the model
            eta = w * eta + (1 - w) * feed_eta
            lo, hi = eta + (p10 - p50) * w ** 0.5, eta + (p90 - p50) * w ** 0.5
            blended = True
        return {"eta_ts": eta, "lo_ts": lo, "hi_ts": hi, "excess_sec": eta - base, "k": row["k"], "u": row["_u"],
                "feed_eta_ts": feed_eta, "range_sec": hi - lo, "blended": blended}
