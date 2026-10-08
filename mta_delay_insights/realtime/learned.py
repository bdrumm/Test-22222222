"""Serve the learned arrival model for live trains.

For a live train and a target stop ``d`` the model needs the same state the
training rows were built from: the train's last observed stop ``u`` (from the
store's recent arrivals, falling back to the feed's next stop), its lateness
and momentum there, the traffic ahead at ``u``, the line and the network right
now, the segment's recent excess, the destination's recent lateness, and the
feed's own ETA for ``d`` right now.

The work is organised so a snapshot with hundreds of trains stays cheap: the
recent arrivals are indexed by trip and by stop once, the line and network
state is computed once, each train's own state once (cached), and all of a
train's stops are predicted in one batch (``predict_many``) with every
(train, stop) result cached for the other callers in the same snapshot — the
forward simulation asks for the same ETAs under each hold scenario.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..models.arrival import ArrivalModel
from ..models.context import FeatureContext, cached_context
from ..models.features import batch_live_features
from ..sources.gtfs_static import StaticGTFS
from ..storage.db import Store
from .status import LiveTrain

_EMPTY = pd.DataFrame(columns=["trip_key", "trip_id", "route_id", "direction", "stop_id", "arrival_ts", "sched_ts", "lateness_sec"])


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
    weather_hourly: pd.DataFrame | None = None
    _recent: pd.DataFrame | None = None
    _fctx: FeatureContext | None = None
    _seq: dict = field(default_factory=dict)
    _by_trip: dict = field(default_factory=dict)
    _by_stop: dict = field(default_factory=dict)
    _route_state: dict = field(default_factory=dict)
    _net_recent: float | None = None
    _train_state: dict = field(default_factory=dict)
    _pred: dict = field(default_factory=dict)

    # ------------------------------------------------------------------ shared, once per snapshot
    def line_index(self, route: str, direction: str | None) -> dict[str, int]:
        """stop_id -> index on the route's canonical stop sequence (cached)."""
        key = (str(route), str(direction or "N"))
        if key not in self._seq:
            try:
                self._seq[key] = {sid: i for i, sid in enumerate(self.static.canonical_stop_sequence(*key))}
            except Exception:
                self._seq[key] = {}
        return self._seq[key]

    @property
    def fctx(self) -> FeatureContext:
        """Alert / event / weather timelines built once per snapshot (cached across trains and stops)."""
        if self._fctx is None:
            self._fctx = cached_context(self.alerts_df, self.weather_daily, self.events_df, self.nws_df, self.climatology, self.weather_hourly)
        return self._fctx

    def recent(self) -> pd.DataFrame:
        """Arrivals of the last two hours with scheduled arrivals and lateness attached, indexed by trip and by stop."""
        if self._recent is None:
            rec = _EMPTY
            if self.store is not None:
                a = self.store.arrivals(None, self.now - 2 * 3600, self.now + 1)
                if not a.empty:
                    from ..analysis.schedule_match import service_date_of
                    sched = [self.static.scheduled_arrival(t, s, service_date_of(ts, sd)) for t, s, ts, sd in
                             zip(a["trip_id"], a["stop_id"], a["arrival_ts"], a.get("start_date", [None] * len(a)))]
                    a["sched_ts"] = sched
                    a["lateness_sec"] = a["arrival_ts"] - pd.to_numeric(a["sched_ts"], errors="coerce")
                    a["route_id"] = a["route_id"].astype(str)
                    rec = a.sort_values("arrival_ts").reset_index(drop=True)
            self._recent = rec
            if not rec.empty:
                self._by_trip = {k: g for k, g in rec.groupby("trip_key", sort=False)}
                self._by_stop = {k: g for k, g in rec.groupby("stop_id", sort=False)}
                win = rec[(rec["arrival_ts"] >= self.now - 1800) & (rec["arrival_ts"] < self.now)]
                if "direction" in win.columns:
                    grp = win.groupby(["route_id", "direction"], sort=False)["lateness_sec"]
                    self._route_state = {k: (float(v.mean()) if v.notna().any() else None, float(len(v))) for k, v in grp}
                lat_n = win[win["arrival_ts"] >= self.now - 900]["lateness_sec"].dropna()
                self._net_recent = float(lat_n.mean()) if len(lat_n) else None
        return self._recent

    # ------------------------------------------------------------------ per train
    def train_state(self, train: LiveTrain) -> dict | None:
        """Where the train is and how it got there: the same for every stop ahead (cached per train)."""
        key = f"{train.start_date or ''}|{train.trip_id}"
        if key in self._train_state:
            return self._train_state[key]
        self.recent()
        st = self._train_state_uncached(train, key)
        self._train_state[key] = st
        return st

    def _train_state_uncached(self, train: LiveTrain, key: str) -> dict | None:
        mine = self._by_trip.get(key, _EMPTY)
        if not mine.empty and np.isfinite(mine["lateness_sec"].iloc[-1]) and self.now - float(mine["arrival_ts"].iloc[-1]) < 1200:
            last = mine.iloc[-1]
            u, t_u, lat_u, sched_u = str(last["stop_id"]), float(last["arrival_ts"]), float(last["lateness_sec"]), float(last["sched_ts"])
            # a train provably held since that arrival is later than its last observation says
            if train.position_lateness_sec is not None and train.position_lateness_sec > lat_u + 60:
                lat_u = float(train.position_lateness_sec)
            k_off = 1
            lat = mine["lateness_sec"].values
            mom1 = float(lat[-1] - lat[-2]) if len(lat) >= 2 and np.isfinite(lat[-2]) else None
            mom3 = float(lat[-1] - lat[-4]) if len(lat) >= 4 and np.isfinite(lat[-4]) else None
        else:
            if train.lateness_sec is None or not train.started or not train.next_stop_id:
                return None
            u, t_u, lat_u = train.next_stop_id, float(train.next_eta_ts), float(train.effective_lateness_sec)
            sched_u = self.static.scheduled_arrival(train.trip_id, u, train.service_date) if train.service_date else None
            if sched_u is None:
                return None
            k_off, mom1, mom3 = 0, None, None
        # traffic ahead at u: the previous arrival there, its lateness and its momentum
        gap = leader_lat = same = leader_mom = None
        at_u = self._by_stop.get(u)
        if at_u is not None:
            prior = at_u[(at_u["arrival_ts"] < t_u - 1) & (at_u["trip_key"] != key)]
            if not prior.empty:
                lead = prior.iloc[-1]
                gap = float(t_u - lead["arrival_ts"])
                if gap <= 3600:
                    leader_lat = float(lead["lateness_sec"]) if np.isfinite(lead["lateness_sec"]) else None
                    same = float(str(lead["route_id"]) == str(train.route_id))
                    lrows = self._by_trip.get(lead["trip_key"])
                    if lrows is not None:
                        lv = lrows[lrows["arrival_ts"] <= lead["arrival_ts"]]["lateness_sec"].values
                        if len(lv) >= 2 and np.isfinite(lv[-1]) and np.isfinite(lv[-2]):
                            leader_mom = float(lv[-1] - lv[-2])
                else:
                    gap = None
        rs = self._route_state.get((str(train.route_id), train.direction))
        sq = self.line_index(train.route_id, train.direction)
        pos_u = sq.get(u)
        return {"key": key, "u": u, "t_u": t_u, "lat_u": lat_u, "sched_u": float(sched_u), "k_off": k_off, "mom1": mom1, "mom3": mom3,
                "gap": gap, "leader_lat": leader_lat, "same": same, "leader_mom": leader_mom,
                "route_recent": rs[0] if rs else None, "n_route": rs[1] if rs else None, "net_recent": self._net_recent,
                "pos_u": None if pos_u is None else float(pos_u), "stops_to_end": float(len(sq) - 1 - pos_u) if pos_u is not None else None, "seq": sq}

    def _stop_row(self, train: LiveTrain, ts: dict, d: str) -> dict | None:
        """The feature row's stop-specific part for target ``d`` (None when d is not ahead or unscheduled)."""
        j = train.stops_until(d)
        if j is None or (ts["k_off"] == 0 and j == 0):
            return None
        sched_d = self.static.scheduled_arrival(train.trip_id, d, train.service_date) if train.service_date else None
        if sched_d is None:
            return None
        sched_run = sched_d - ts["sched_u"]
        if sched_run <= 0:
            return None
        k = j + ts["k_off"]
        u, key = ts["u"], ts["key"]
        # segment conditions: last 3 trips that reached d after passing u, the last one and how long ago
        seg = seg_last = seg_stale = dest = None
        rd = self._by_stop.get(d)
        if rd is not None:
            ru = self._by_stop.get(u)
            if ru is not None:
                lu = dict(zip(ru["trip_key"].values, ru["lateness_sec"].values))
                rdd = rd[(rd["arrival_ts"] <= self.now) & (rd["trip_key"] != key)]
                pairs = [(float(ld), float(lu[tk]), float(at)) for tk, ld, at in zip(rdd["trip_key"].values, rdd["lateness_sec"].values, rdd["arrival_ts"].values)
                         if tk in lu and np.isfinite(ld) and np.isfinite(lu[tk])]
                if pairs:
                    seg_stale = float(min(self.now - pairs[-1][2], 7200.0))
                    if seg_stale <= 3600:
                        seg = float(np.mean([ld - lu_ for ld, lu_, _ in pairs[-3:]]))
                        seg_last = pairs[-1][0] - pairs[-1][1]
            recent_d = rd[(rd["arrival_ts"] >= self.now - 900) & (rd["arrival_ts"] <= self.now)]["lateness_sec"].dropna()
            if len(recent_d):
                dest = float(recent_d.mean())
        feed_eta_d = train.eta_at(d)
        feed_excess = (feed_eta_d - (sched_d + ts["lat_u"])) if feed_eta_d is not None else None
        sq = ts["seq"]
        pos_d = sq.get(d)
        d_is_last = float(pos_d >= len(sq) - 1) if (pos_d is not None and sq) else None
        nan = float("nan")

        def f(v):
            return nan if v is None else float(v)
        return {"route_id": str(train.route_id), "direction": train.direction, "u": u, "d": d, "t": float(self.now),
                "k": int(k), "sched_run_sec": float(sched_run), "lateness_u": ts["lat_u"], "mom1": f(ts["mom1"]), "mom3": f(ts["mom3"]),
                "gap_ahead_sec": f(ts["gap"]), "leader_lateness": f(ts["leader_lat"]), "leader_same_route": f(ts["same"]), "leader_mom1": f(ts["leader_mom"]),
                "sched_headway_sec": nan, "seg_recent_excess": f(seg), "seg_last_excess": f(seg_last), "seg_staleness": f(seg_stale),
                "dest_recent_lateness": f(dest), "feed_excess": f(feed_excess), "track_changed": float(getattr(train, "track_changed", 0.0) or 0.0),
                "route_recent_lateness": f(ts["route_recent"]), "net_recent_lateness": f(ts["net_recent"]), "route_arrivals_30": f(ts["n_route"]),
                "pos_u": f(ts["pos_u"]), "stops_to_end": f(ts["stops_to_end"]), "d_is_last": f(d_is_last),
                "_sched_d": float(sched_d), "_feed_eta": feed_eta_d}

    # ------------------------------------------------------------------ prediction
    def predict_many(self, train: LiveTrain, stop_ids: list[str], feed_spread: float | None = None) -> dict[str, dict]:
        """{stop_id: {'eta_ts', 'lo_ts', 'hi_ts', 'excess_sec', 'k', 'u', ...}} for every predictable stop, in one model call.

        ``feed_spread`` is the feed ETA's own p10-p90 width at this horizon and enables blending when the
        model has no feed feature."""
        if not self.model.ready or not stop_ids:
            return {}
        ts = self.train_state(train)
        if ts is None:
            return {}
        out: dict[str, dict] = {}
        todo, rows = [], []
        for d in stop_ids:
            ck = (ts["key"], d)
            if ck in self._pred:
                if self._pred[ck] is not None:
                    out[d] = self._pred[ck]
                continue
            row = self._stop_row(train, ts, d)
            if row is None:
                self._pred[ck] = None
                continue
            todo.append(d); rows.append(row)
        if rows:
            X = batch_live_features(rows, self.fctx)
            pred = self.model.predict(X)
            blend = "feed_excess" not in self.model.features and feed_spread is not None
            for i, (d, row) in enumerate(zip(todo, rows)):
                base = row["_sched_d"] + row["lateness_u"]
                p10, p50, p90 = float(pred["p10"].iloc[i]), float(pred["p50"].iloc[i]), float(pred["p90"].iloc[i])
                eta, lo, hi = base + p50, base + p10, base + p90
                feed_eta = row["_feed_eta"]
                blended = False
                if feed_eta is not None and blend:
                    # the feed knows about holds and dispatch decisions the state cannot show: inverse-variance blend
                    var_m = max(((p90 - p10) / 2.56) ** 2, 1.0)
                    var_f = max((feed_spread / 2.56) ** 2, 1.0)
                    w = var_f / (var_m + var_f)
                    eta = w * eta + (1 - w) * feed_eta
                    lo, hi = eta + (p10 - p50) * w ** 0.5, eta + (p90 - p50) * w ** 0.5
                    blended = True
                res = {"eta_ts": eta, "lo_ts": lo, "hi_ts": hi, "excess_sec": eta - base, "k": row["k"], "u": row["u"],
                       "feed_eta_ts": feed_eta, "range_sec": hi - lo, "blended": blended}
                self._pred[(ts["key"], d)] = res
                out[d] = res
        return out

    def state_for(self, train: LiveTrain, d: str) -> dict | None:
        """Feature row for predicting the train's arrival at stop ``d``; None when not predictable."""
        ts = self.train_state(train)
        if ts is None:
            return None
        row = self._stop_row(train, ts, d)
        if row is None:
            return None
        row["_lat_u"] = row["lateness_u"]; row["_u"] = row["u"]
        return row

    def predict(self, train: LiveTrain, d: str, feed_spread: float | None = None) -> dict | None:
        """One stop; see ``predict_many``."""
        return self.predict_many(train, [d], feed_spread).get(d)
