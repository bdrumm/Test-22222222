"""Journey-time model and live trip planner.

A *journey* is a chain of legs (ride on a set of routes from one platform to
another; a transfer walk between legs). For each leg the model learns, from the
collected arrival history, how actual ride time relates to the schedule and to
conditions at departure:

    excess_ride_sec = f(lateness at origin, unplanned alert on the route,
                        holiday / weekend / peak, venue and street events,
                        news mentions, precipitation, heat) + residual

The mean is a ridge regression (shrunk to zero with little data), residual
spread comes from empirical quantiles per route and period, and typical waits
come from observed headways. The live planner enumerates the trains a rider
can actually catch (from the realtime snapshot), estimates each leg's ride from
the feed and the model, chains legs through transfers, and returns options with
arrival windows plus the data for a time-distance ("stringline") chart.

The training table is exported so richer models can be trained elsewhere.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .. import config
from ..analysis.schedule_match import match_arrivals
from ..sources import alerts as alerts_src
from ..sources import events as events_src
from ..sources.gtfs_static import NY_TZ, StaticGTFS
from ..storage.db import Store

PEAK_HOURS = {7, 8, 9, 16, 17, 18, 19}
FEATURES = ["lateness_at_from_min", "alert_active", "holiday", "weekend", "peak", "venue_event_w",
            "street_event_w", "news_w", "precip_mm", "heat"]
RIDGE_LAMBDA = 25.0
PRIOR_N = 15.0


# --------------------------------------------------------------------------- #
# Specs
# --------------------------------------------------------------------------- #
@dataclass
class LegSpec:
    from_stop: str
    to_stop: str
    routes: list[str]
    from_name: str = ""
    to_name: str = ""
    transfer_min: float = 0.0            # walk before boarding this leg (0 for the first leg)
    stops: list[str] = field(default_factory=list)   # ordered stop ids from from_stop to to_stop

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class JourneySpec:
    id: str
    label: str
    legs: list[LegSpec]

    def as_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "legs": [l.as_dict() for l in self.legs]}


def _platform(static: StaticGTFS, station: str, direction: str, routes: list[str]) -> tuple[str, str]:
    st = static.find_stations(station)
    if st.empty:
        raise ValueError(f"no station matches {station!r}")
    for r in st.itertuples(index=False):
        try:
            pid = static.platform_for(r.stop_id, direction)
        except KeyError:
            continue
        if set(routes) & set(static.routes_serving(pid)):
            return pid, r.stop_name
    raise ValueError(f"no {direction} platform at {station!r} serving {routes}")


def leg_stops(static: StaticGTFS, from_stop: str, to_stop: str, routes: list[str], direction: str) -> list[str]:
    for r in routes:
        seq = static.canonical_stop_sequence(r, direction)
        if from_stop in seq and to_stop in seq:
            i, j = seq.index(from_stop), seq.index(to_stop)
            if i < j:
                return seq[i:j + 1]
    return [from_stop, to_stop]


def resolve_journeys(static: StaticGTFS, cfg: dict) -> list[JourneySpec]:
    """``cfg['journeys']`` items: {id, label, legs: [{from: {station, direction, routes}, to: {...}, transfer_min?}]}."""
    out = []
    for j in cfg.get("journeys", []):
        legs = []
        for lg in j["legs"]:
            routes = [str(r) for r in lg["from"].get("routes", lg.get("routes", []))]
            direction = lg["from"].get("direction", "N")
            f_id, f_name = _platform(static, lg["from"]["station"], direction, routes)
            t_id, t_name = _platform(static, lg["to"]["station"], lg["to"].get("direction", direction), routes)
            legs.append(LegSpec(f_id, t_id, routes, f_name, t_name, float(lg.get("transfer_min", 0.0)),
                                leg_stops(static, f_id, t_id, routes, direction)))
        out.append(JourneySpec(j["id"], j.get("label", j["id"]), legs))
    return out


# --------------------------------------------------------------------------- #
# Training data
# --------------------------------------------------------------------------- #
def _sched_ride(static: StaticGTFS, trip_id: str, from_stop: str, to_stop: str, service_date) -> float | None:
    a = static.scheduled_arrival(trip_id, from_stop, service_date)
    b = static.scheduled_arrival(trip_id, to_stop, service_date)
    return (b - a) if (a is not None and b is not None) else None


def build_leg_training(store: Store, static: StaticGTFS, leg: LegSpec, alerts_df: pd.DataFrame | None,
                       weather_daily: pd.DataFrame | None, events_df: pd.DataFrame | None, now: float,
                       lookback_days: int = 30) -> pd.DataFrame:
    """One row per trip observed at both ends of the leg, with the model features."""
    cols = ["trip_key", "route_id", "depart_ts", "arrive_ts", "ride_sec", "sched_ride_sec", "excess_sec", "hour", "dow"] + FEATURES
    a = store.arrivals(leg.from_stop, now - lookback_days * 86400, now, leg.routes)
    b = store.arrivals(leg.to_stop, now - lookback_days * 86400 - 7200, now, leg.routes)
    if a.empty or b.empty:
        return pd.DataFrame(columns=cols)
    am = match_arrivals(a, static)
    j = am.merge(b[["trip_key", "arrival_ts"]].rename(columns={"arrival_ts": "arrive_ts"}), on="trip_key", how="inner")
    j = j[(j["arrive_ts"] > j["arrival_ts"]) & (j["arrive_ts"] - j["arrival_ts"] < 4 * 3600)]
    if j.empty:
        return pd.DataFrame(columns=cols)
    j["ride_sec"] = j["arrive_ts"] - j["arrival_ts"]
    j["sched_ride_sec"] = [_sched_ride(static, t, leg.from_stop, leg.to_stop, sd) for t, sd in zip(j["trip_id"], j["service_date"])]
    j = j.dropna(subset=["sched_ride_sec"])
    j = j[j["sched_ride_sec"] > 0]
    if j.empty:
        return pd.DataFrame(columns=cols)
    j["excess_sec"] = j["ride_sec"] - j["sched_ride_sec"]
    local = pd.to_datetime(j["arrival_ts"], unit="s", utc=True).dt.tz_convert(NY_TZ)
    j["hour"] = local.dt.hour
    j["dow"] = local.dt.dayofweek
    feats = [features_at(t, str(r), lat, alerts_df, weather_daily, events_df)
             for t, r, lat in zip(j["arrival_ts"], j["route_id"], j["lateness_sec"])]
    fdf = pd.DataFrame(feats, index=j.index)
    j = pd.concat([j, fdf], axis=1)
    j = j.rename(columns={"arrival_ts": "depart_ts"})
    j["route_id"] = j["route_id"].astype(str)
    return j[cols].reset_index(drop=True)


def features_at(ts: float, route: str, lateness_sec: float | None, alerts_df: pd.DataFrame | None,
                weather_daily: pd.DataFrame | None, events_df: pd.DataFrame | None) -> dict:
    local = datetime.fromtimestamp(ts, NY_TZ)
    f = {"lateness_at_from_min": float(np.clip((lateness_sec or 0.0) / 60.0, -5, 30)) if lateness_sec is not None and np.isfinite(lateness_sec) else 0.0,
         "alert_active": 0.0, "holiday": 0.0, "weekend": float(local.weekday() >= 5), "peak": float(local.hour in PEAK_HOURS and local.weekday() < 5),
         "venue_event_w": 0.0, "street_event_w": 0.0, "news_w": 0.0, "precip_mm": 0.0, "heat": 0.0}
    if alerts_df is not None and not alerts_df.empty:
        a = alerts_df
        kinds = [alerts_src.alert_kind(t, h) for t, h in zip(a["alert_type"], a["header"])]
        start = a["active_start"].fillna(-np.inf)
        end = a["active_end"].fillna(a["updated_at"].fillna(a["active_start"]) + 3 * 3600)
        on = (start <= ts) & (end >= ts) & np.array([k == "delay" for k in kinds]) & a["routes"].map(lambda rs: (not rs) or route in [str(x) for x in rs])
        f["alert_active"] = float(on.any())
    if weather_daily is not None and not weather_daily.empty:
        day = local.date().isoformat()
        w = weather_daily[weather_daily["date"].astype(str) == day]
        if not w.empty:
            f["precip_mm"] = float(min(float(w.iloc[0].get("precip_mm", 0) or 0), 30.0))
            f["heat"] = float((w.iloc[0].get("temp_max_c", 0) or 0) >= 32)
    if events_df is not None and not events_df.empty:
        ef = events_src.event_features(events_df, ts, [route])
        f.update({k: float(ef[k]) for k in ("holiday", "venue_event_w", "street_event_w", "news_w")})
    return f


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #
@dataclass
class LegModel:
    from_stop: str
    to_stop: str
    routes: list[str]
    n: int = 0
    coef: dict = field(default_factory=dict)          # feature -> seconds per unit (ridge)
    intercept: float = 0.0
    resid_q: dict = field(default_factory=dict)       # route -> period -> {p10, p50, p90, n}
    sched_ride: dict = field(default_factory=dict)    # route -> hour -> scheduled ride seconds
    wait: dict = field(default_factory=dict)          # route -> hour -> {expected, p90, n}

    def excess(self, feats: dict) -> float:
        n = float(self.n); w = n / (n + PRIOR_N)
        return w * (self.intercept + sum(self.coef.get(k, 0.0) * float(feats.get(k, 0.0)) for k in FEATURES))

    def spread(self, route: str, period: str) -> tuple[float, float]:
        q = self.resid_q.get(route, {}).get(period) or self.resid_q.get(route, {}).get("all")
        if not q or q.get("n", 0) < 5:
            return -60.0, 120.0
        n = float(q["n"]); w = n / (n + PRIOR_N)
        return w * q["p10"] + (1 - w) * -60.0, w * q["p90"] + (1 - w) * 120.0


@dataclass
class JourneyModel:
    journey_id: str
    legs: list[LegModel]
    fitted_at: str = ""
    n_samples: int = 0
    features: list[str] = field(default_factory=lambda: list(FEATURES))

    def to_dict(self) -> dict:
        return {"journey_id": self.journey_id, "fitted_at": self.fitted_at, "n_samples": self.n_samples,
                "features": self.features, "legs": [asdict(l) for l in self.legs]}

    @classmethod
    def from_dict(cls, d: dict) -> "JourneyModel":
        return cls(d["journey_id"], [LegModel(**l) for l in d.get("legs", [])], d.get("fitted_at", ""), d.get("n_samples", 0),
                   d.get("features", list(FEATURES)))


def _period(hour: int, dow: int) -> str:
    if dow >= 5:
        return "weekend"
    return "peak" if hour in PEAK_HOURS else "offpeak"


def fit_leg(train: pd.DataFrame, leg: LegSpec, static: StaticGTFS, store: Store, now: float) -> LegModel:
    m = LegModel(leg.from_stop, leg.to_stop, list(leg.routes))
    if not train.empty:
        y = train["excess_sec"].values.astype(float)
        y = np.clip(y, -600, 1800)
        X = train[FEATURES].values.astype(float)
        Xc = np.column_stack([np.ones(len(X)), X])
        lam = np.eye(Xc.shape[1]) * RIDGE_LAMBDA
        lam[0, 0] = 0.0
        beta = np.linalg.solve(Xc.T @ Xc + lam, Xc.T @ y)
        m.intercept = float(beta[0])
        m.coef = {k: float(b) for k, b in zip(FEATURES, beta[1:])}
        m.n = int(len(y))
        resid = y - Xc @ beta
        per = [ _period(int(h), int(d)) for h, d in zip(train["hour"], train["dow"])]
        df = pd.DataFrame({"route": train["route_id"].astype(str), "period": per, "r": resid})
        for route, g in df.groupby("route"):
            m.resid_q[route] = {"all": {"p10": float(np.quantile(g["r"], 0.1)), "p50": float(np.median(g["r"])),
                                        "p90": float(np.quantile(g["r"], 0.9)), "n": int(len(g))}}
            for p, gg in g.groupby("period"):
                if len(gg) >= 5:
                    m.resid_q[route][p] = {"p10": float(np.quantile(gg["r"], 0.1)), "p50": float(np.median(gg["r"])),
                                           "p90": float(np.quantile(gg["r"], 0.9)), "n": int(len(gg))}
    # Scheduled ride time by route and hour (from today's schedule) and typical waits from observed headways.
    sd = (datetime.fromtimestamp(now, NY_TZ) - timedelta(hours=3)).date()
    for r in leg.routes:
        ev = static.scheduled_stop_events(leg.from_stop, sd, [r]).sort_values("arrival_ts")
        if ev.empty:
            continue
        rides, hws = {}, {}
        ev["hour"] = (ev["arrival_sec"] // 3600) % 24
        ev["hw"] = ev["arrival_ts"].diff()
        for h, g in ev.groupby("hour"):
            vals = [_sched_ride(static, t, leg.from_stop, leg.to_stop, sd) for t in g["trip_id"].head(3)]
            vals = [v for v in vals if v]
            if vals:
                rides[str(int(h))] = float(np.median(vals))
            hw = g["hw"].dropna()
            if len(hw):
                hws[str(int(h))] = float(hw.median())
        m.sched_ride[r] = rides
        obs = store.arrivals(leg.from_stop, now - 30 * 86400, now, [r]).sort_values("arrival_ts")
        wait_by_hour = {}
        if len(obs) > 10:
            obs["hour"] = pd.to_datetime(obs["arrival_ts"], unit="s", utc=True).dt.tz_convert(NY_TZ).dt.hour
            obs["hw"] = obs["arrival_ts"].diff()
            for h, g in obs.groupby("hour"):
                hw = g["hw"].dropna()
                hw = hw[(hw > 30) & (hw < 3600)]
                if len(hw) >= 5:
                    wait_by_hour[str(int(h))] = {"expected": float((hw ** 2).sum() / (2 * hw.sum())), "p90": float(np.quantile(hw, 0.9)), "n": int(len(hw))}
        for h, s in hws.items():
            wait_by_hour.setdefault(h, {"expected": s / 2.0, "p90": s, "n": 0})
        m.wait[r] = wait_by_hour
    return m


def fit_journey(store: Store, static: StaticGTFS, spec: JourneySpec, alerts_df, weather_daily, events_df,
                now: float | None = None, lookback_days: int = 30) -> tuple[JourneyModel, pd.DataFrame]:
    """Returns the fitted model and the concatenated training table (for export)."""
    now = float(now or datetime.now(NY_TZ).timestamp())
    legs, tables = [], []
    for i, leg in enumerate(spec.legs):
        tr = build_leg_training(store, static, leg, alerts_df, weather_daily, events_df, now, lookback_days)
        tr.insert(0, "leg", i)
        tr.insert(0, "journey_id", spec.id)
        tables.append(tr)
        legs.append(fit_leg(tr, leg, static, store, now))
    table = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()
    model = JourneyModel(spec.id, legs, datetime.fromtimestamp(now, NY_TZ).isoformat(), int(len(table)))
    return model, table


# --------------------------------------------------------------------------- #
# Live planning
# --------------------------------------------------------------------------- #
def _hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts, NY_TZ).strftime("%H:%M")


def _leg_candidates(leg: LegSpec, trains: list, now: float, earliest_board: float, horizon: float) -> list:
    out = []
    for t in trains:
        if t.route_id not in leg.routes:
            continue
        eta_from = t.eta_at(leg.from_stop)
        if eta_from is None or eta_from < earliest_board or eta_from > now + horizon:
            continue
        i = t.stops_until(leg.from_stop)
        eta_to = t.eta_at(leg.to_stop)
        j = t.stops_until(leg.to_stop)
        if eta_to is not None and j is not None and i is not None and j <= i:
            eta_to = None
        out.append((t, eta_from, eta_to))
    out.sort(key=lambda x: x[1])
    return out


def plan_journey(spec: JourneySpec, model: JourneyModel | None, trains: list, static: StaticGTFS, now: float,
                 alerts_df: pd.DataFrame | None = None, weather_daily: pd.DataFrame | None = None,
                 events_df: pd.DataFrame | None = None, horizon_sec: float = 3600.0, max_options: int = 6) -> dict:
    """Enumerate catchable itineraries now and estimate each one's arrival window."""
    model = model or JourneyModel(spec.id, [LegModel(l.from_stop, l.to_stop, list(l.routes)) for l in spec.legs])
    local = datetime.fromtimestamp(now, NY_TZ)
    hour, period = local.hour, _period(local.hour, local.weekday())
    options = []
    first = spec.legs[0]
    for t0, dep0, arr0 in _leg_candidates(first, trains, now, now - 30, horizon_sec)[:max_options + 2]:
        legs_out, ok = [], True
        t_cur, board_ts = t0, dep0
        lo_total, hi_total = 0.0, 0.0
        for li, leg in enumerate(spec.legs):
            lm = model.legs[li] if li < len(model.legs) else LegModel(leg.from_stop, leg.to_stop, list(leg.routes))
            if li > 0:
                earliest = board_ts + leg.transfer_min * 60
                cands = _leg_candidates(leg, trains, now, earliest, horizon_sec + 1800)
                if cands:
                    t_cur, dep, arr = cands[0]
                    wait = dep - earliest
                    src = "feed"
                else:
                    w = lm.wait.get(t_cur.route_id if t_cur.route_id in lm.wait else (leg.routes[0]), {}).get(str(hour))
                    wait = (w or {}).get("expected", 300.0)
                    dep, arr, src = earliest + wait, None, "typical"
                    t_cur = None
                transfer = leg.transfer_min * 60
            else:
                dep, arr, src, wait, transfer = dep0, arr0, "feed", dep0 - now, 0.0
            route = t_cur.route_id if t_cur else leg.routes[0]
            feats = features_at(dep, route, (t_cur.lateness_sec if t_cur and t_cur.started else 0.0), alerts_df, weather_daily, events_df)
            sched = lm.sched_ride.get(route, {}).get(str(hour)) or lm.sched_ride.get(route, {}).get(str((hour + 1) % 24)) or _fallback_sched(static, leg, route, dep)
            excess = lm.excess(feats)
            ride_model = (sched + excess) if sched else None
            ride_feed = (arr - dep) if arr else None
            if ride_feed and ride_model:
                ride = 0.5 * (ride_feed + ride_model)
            else:
                ride = ride_feed or ride_model or 600.0
            p10, p90 = lm.spread(route, period)
            legs_out.append({"leg": li, "route_id": route, "trip_id": t_cur.trip_id if t_cur else None, "from": leg.from_stop, "to": leg.to_stop,
                             "from_name": leg.from_name, "to_name": leg.to_name, "board_ts": dep, "arrive_ts": dep + ride,
                             "wait_sec": wait, "transfer_sec": transfer, "ride_sec": ride, "ride_feed_sec": ride_feed, "ride_model_sec": ride_model,
                             "sched_ride_sec": sched, "excess_pred_sec": excess, "ride_source": src, "ride_lo_sec": ride + p10, "ride_hi_sec": ride + p90,
                             "train_lateness_sec": (t_cur.lateness_sec if t_cur and t_cur.started else None),
                             "train_now_at": static.stop_name(t_cur.next_stop_id) if t_cur and t_cur.next_stop_id else None,
                             "features": feats})
            lo_total += p10; hi_total += p90
            board_ts = dep + ride
        total = board_ts - now
        options.append({"depart_ts": dep0, "arrive_ts": board_ts, "total_sec": total, "total_lo_sec": total + lo_total, "total_hi_sec": total + hi_total,
                        "wait_sec": dep0 - now, "legs": legs_out, "routes": [l["route_id"] for l in legs_out],
                        "summary": f"Leave in {max(0, (dep0 - now)) / 60:.0f} min on the {legs_out[0]['route_id']}: arrive {_hhmm(board_ts)} "
                                   f"({total / 60:.0f} min, range {(total + lo_total) / 60:.0f}-{(total + hi_total) / 60:.0f})"})
    options.sort(key=lambda o: o["arrive_ts"])
    best = options[0] if options else None
    # Typical (schedule + history) figure for comparison.
    typical = 0.0
    for li, leg in enumerate(spec.legs):
        lm = model.legs[li]
        r = leg.routes[0]
        w = lm.wait.get(r, {}).get(str(hour), {}).get("expected", 300.0)
        s = lm.sched_ride.get(r, {}).get(str(hour)) or 600.0
        typical += w + s + leg.transfer_min * 60
    return {"id": spec.id, "label": spec.label, "legs": [l.as_dict() for l in spec.legs], "options": options[:max_options],
            "best": best, "typical_total_sec": typical, "model": {"fitted_at": model.fitted_at, "n_samples": model.n_samples,
            "coef": {li: lm.coef for li, lm in enumerate(model.legs)}},
            "stringline": stringline_data(spec, trains, static, now, horizon_sec)}


def _fallback_sched(static: StaticGTFS, leg: LegSpec, route: str, ts: float) -> float | None:
    sd = (datetime.fromtimestamp(ts, NY_TZ) - timedelta(hours=3)).date()
    ev = static.scheduled_stop_events(leg.from_stop, sd, [route])
    for t in ev["trip_id"].head(5):
        v = _sched_ride(static, t, leg.from_stop, leg.to_stop, sd)
        if v:
            return v
    return None


def stringline_data(spec: JourneySpec, trains: list, static: StaticGTFS, now: float, horizon_sec: float) -> list[dict]:
    """Per leg: ordered stops and each live train's (stop index, ETA) points for a time-distance chart."""
    out = []
    for li, leg in enumerate(spec.legs):
        idx = {s: i for i, s in enumerate(leg.stops)}
        lines = []
        for t in trains:
            if t.route_id not in leg.routes:
                continue
            pts = [(idx[s], eta) for s, eta in t.stops if s in idx and eta <= now + horizon_sec + 900]
            if len(pts) >= 2:
                lines.append({"trip_id": t.trip_id, "route_id": t.route_id, "points": pts, "lateness_sec": t.lateness_sec if t.started else None})
        out.append({"leg": li, "stops": [{"stop_id": s, "name": static.stop_name(s)} for s in leg.stops], "trains": lines})
    return out
