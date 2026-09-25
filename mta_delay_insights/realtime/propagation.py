"""Look-back propagation model: what happens downstream given what is happening now.

Fitted from the collected arrival history for one monitored platform (target):

* **ETA calibration** – how far the feed's predictions, made ``h`` minutes before
  arrival, ended up from the actual arrival (bias and p10/p90 spread by horizon).
  Feed ETAs assume scheduled run times from the train's current position, so
  systematic slippage (holds, merges, dwell) shows up here.
* **Lateness carry** – how lateness measured ``k`` stops upstream translates into
  lateness at the target (slope / intercept per ``k``), i.e. whether delays
  recover, persist or grow on the approach.
* **Gap persistence** – probability that a headway gap seen at the nearest
  upstream stop is still a gap at the target.
* **Alert effect** – extra lateness at the target while an unplanned alert of a
  given cause is active on the route.

All estimates are shrunk toward physically sensible priors (bias 0, slope 1,
persistence 0.6) so the model behaves with minutes of history and sharpens as
days accumulate. ``forecast_station`` applies the model to the live trains to
produce calibrated arrival windows, predicted headways/gaps and a narrative of
the downstream effects at the current time.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .. import config
from ..analysis import metrics as mt
from ..analysis.schedule_match import match_arrivals, scheduled_counts
from ..sources import alerts as alerts_src
from ..sources.gtfs_static import NY_TZ, StaticGTFS
from ..storage.db import Store

HORIZON_BUCKETS = [(0, 300), (300, 600), (600, 1200), (1200, 2400), (2400, 3600), (3600, 10 ** 9)]
PRIOR_N = 20.0


def horizon_bucket(h: float) -> str:
    for lo, hi in HORIZON_BUCKETS:
        if lo <= h < hi:
            return f"{lo}-{hi}"
    return f"{HORIZON_BUCKETS[-1][0]}-{HORIZON_BUCKETS[-1][1]}"


def prior_spread(h: float) -> tuple[float, float]:
    """Default p10/p90 of ETA error (seconds) at horizon h: widens with lead time."""
    return (-45.0 - 0.05 * h, 60.0 + 0.15 * h)


@dataclass
class PropagationModel:
    target_id: str
    stop_id: str
    routes: list[str]
    fitted_at: str = ""
    n_arrivals: int = 0
    n_days: int = 0
    eta_bias: dict = field(default_factory=dict)        # route -> bucket -> {bias, p10, p90, n}
    lateness_carry: dict = field(default_factory=dict)  # route -> k -> {slope, intercept, resid_std, n}
    gap_persistence: dict = field(default_factory=dict) # route -> {p, n}
    alert_effect: dict = field(default_factory=dict)    # cause -> {extra_sec, n}
    sched_headway_by_hour: dict = field(default_factory=dict)  # route -> hour -> sec

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PropagationModel":
        return cls(**{k: d.get(k, v) for k, v in asdict(cls("", "", [])).items()})

    # ---- lookups with priors -------------------------------------------- #
    def eta_adjustment(self, route: str, horizon_sec: float) -> tuple[float, float, float, int]:
        b = self.eta_bias.get(route, {}).get(horizon_bucket(max(0.0, horizon_sec)))
        p10, p90 = prior_spread(horizon_sec)
        if not b or b.get("n", 0) < 3:
            return 0.0, p10, p90, int(b["n"]) if b else 0
        n = float(b["n"]); w = n / (n + PRIOR_N)
        return (w * b["bias"], w * b["p10"] + (1 - w) * p10, w * b["p90"] + (1 - w) * p90, int(n))

    def carry(self, route: str, k: int) -> tuple[float, float, float, int]:
        """(slope, intercept_sec, resid_std_sec, n) for lateness k stops upstream -> target."""
        c = self.lateness_carry.get(route, {}).get(str(k)) or self.lateness_carry.get(route, {}).get(k)
        if not c:
            return 1.0, 0.0, 60.0 + 20.0 * k, 0
        n = float(c["n"]); w = n / (n + PRIOR_N)
        return (w * c["slope"] + (1 - w) * 1.0, w * c["intercept"], w * c["resid_std"] + (1 - w) * (60.0 + 20.0 * k), int(n))

    def gap_p(self, route: str) -> float:
        g = self.gap_persistence.get(route)
        if not g:
            return 0.6
        n = float(g["n"]); w = n / (n + PRIOR_N)
        return w * g["p"] + (1 - w) * 0.6

    def alert_extra(self, cause: str) -> tuple[float, int]:
        a = self.alert_effect.get(cause) or self.alert_effect.get("any")
        if not a:
            return 0.0, 0
        n = float(a["n"]); w = n / (n + PRIOR_N)
        return w * a["extra_sec"], int(n)


# --------------------------------------------------------------------------- #
# Fitting
# --------------------------------------------------------------------------- #
def fit_model(store: Store, static: StaticGTFS, target: dict, now: float | None = None,
              lookback_days: int = 21, defaults: config.AnalysisDefaults = config.DEFAULTS) -> PropagationModel:
    """``target``: resolved target dict with id, stop_id, routes, upstream {route: [stops nearest-first]}."""
    now = float(now or datetime.now(NY_TZ).timestamp())
    model = PropagationModel(target_id=target["id"], stop_id=target["stop_id"], routes=list(target["routes"]),
                             fitted_at=datetime.fromtimestamp(now, NY_TZ).isoformat())
    arr = store.arrivals(target["stop_id"], now - lookback_days * 86400, now, target["routes"])
    if arr.empty:
        return model
    matched = match_arrivals(arr, static, defaults.schedule_match_tolerance_sec)
    matched["route_id"] = matched["route_id"].astype(str)
    model.n_arrivals = int(len(matched))
    model.n_days = int(pd.to_datetime(matched["arrival_ts"], unit="s", utc=True).dt.tz_convert(NY_TZ).dt.date.nunique())

    # 1) ETA calibration from first-seen predictions.
    good = matched[(matched["n_predictions"] >= 2) & (matched["confidence"] >= 0.8) & matched["first_pred_ts"].notna()]
    for route, g in good.groupby("route_id"):
        h = (g["arrival_ts"] - g["first_seen_ts"]).astype(float)
        err = (g["arrival_ts"] - g["first_pred_ts"]).astype(float)
        buckets: dict = {}
        for bucket, e in err.groupby(h.map(horizon_bucket)):
            if len(e) >= 3:
                buckets[bucket] = {"bias": float(e.median()), "p10": float(e.quantile(0.1)), "p90": float(e.quantile(0.9)), "n": int(len(e))}
        if buckets:
            model.eta_bias[route] = buckets

    # 2) Lateness carry from upstream stops of the same trips.
    ups_by_route = target.get("upstream", {})
    ctx_stops = sorted({s for v in ups_by_route.values() for s in v})
    if ctx_stops:
        up = store.arrivals_for_trips(matched["trip_key"].unique())
        up = up[up["stop_id"].isin(ctx_stops)]
        if not up.empty:
            upm = match_arrivals(up, static, defaults.schedule_match_tolerance_sec)
            piv = upm.pivot_table(index="trip_key", columns="stop_id", values="lateness_sec", aggfunc="first")
            tgt_lat = matched.set_index("trip_key")["lateness_sec"]
            for route, ups in ups_by_route.items():
                carries: dict = {}
                rt_keys = matched[matched["route_id"] == str(route)]["trip_key"]
                for k, stop in enumerate(ups, start=1):
                    if stop not in piv.columns:
                        continue
                    x = piv.loc[piv.index.intersection(rt_keys), stop]
                    y = tgt_lat.reindex(x.index)
                    ok = x.notna() & y.notna()
                    if ok.sum() < 5:
                        continue
                    xv, yv = x[ok].values.astype(float), y[ok].values.astype(float)
                    slope, intercept = np.polyfit(xv, yv, 1) if np.std(xv) > 1e-6 else (1.0, float(np.mean(yv - xv)))
                    resid = yv - (slope * xv + intercept)
                    carries[str(k)] = {"slope": float(np.clip(slope, 0.0, 2.0)), "intercept": float(intercept),
                                       "resid_std": float(np.std(resid)), "n": int(ok.sum()), "stop_id": stop}
                if carries:
                    model.lateness_carry[str(route)] = carries
            # 3) Gap persistence: gap at nearest upstream -> gap at target (per route).
            dates = sorted(set(pd.to_datetime(matched["arrival_ts"], unit="s", utc=True).dt.tz_convert(NY_TZ).dt.date))
            tgt_flag = mt.flag_arrivals(matched, scheduled_counts(static, target["stop_id"], target["routes"], dates), defaults)
            for route, ups in ups_by_route.items():
                if not ups:
                    continue
                near = upm[(upm["stop_id"] == ups[0]) & (upm["route_id"].astype(str) == str(route))]
                if len(near) < 8:
                    continue
                near_flag = mt.flag_arrivals(near, None, defaults).set_index("trip_key")["is_gap"]
                t_route = tgt_flag[tgt_flag["route_id"] == str(route)].set_index("trip_key")["is_gap"]
                both = pd.concat([t_route.rename("t"), near_flag.rename("u")], axis=1).dropna()
                gaps_up = both[both["u"]]
                if len(gaps_up) >= 3:
                    model.gap_persistence[str(route)] = {"p": float(gaps_up["t"].mean()), "n": int(len(gaps_up))}

    # 4) Alert effect: lateness with vs without an unplanned delay alert active on the route.
    alerts = store.alerts(now - lookback_days * 86400, now)
    if not alerts.empty:
        kinds = [alerts_src.alert_kind(t, h) for t, h in zip(alerts["alert_type"], alerts["header"])]
        unplanned = alerts[[k == "delay" for k in kinds]]
        if not unplanned.empty:
            ts = matched["arrival_ts"].values
            routes = matched["route_id"].values
            cat = np.array([None] * len(matched), dtype=object)
            for a in unplanned.itertuples(index=False):
                if a.routes and not (set(a.routes) & set(map(str, target["routes"]))):
                    continue
                start = a.active_start if pd.notna(a.active_start) else -np.inf
                end = a.active_end if pd.notna(a.active_end) else ((a.updated_at if pd.notna(a.updated_at) else start) + 3 * 3600)
                m = (ts >= start) & (ts <= end)
                if a.routes:
                    m &= np.isin(routes, list(a.routes))
                cat[m & (cat == None)] = a.cause_category  # noqa: E711
            lat = matched["lateness_sec"].values.astype(float)
            base = lat[(cat == None) & np.isfinite(lat)]  # noqa: E711
            if len(base) >= 5:
                for c in set(x for x in cat if x is not None):
                    sel = lat[(cat == c) & np.isfinite(lat)]
                    if len(sel) >= 3:
                        model.alert_effect[str(c)] = {"extra_sec": float(np.median(sel) - np.median(base)), "n": int(len(sel))}
                allsel = lat[(cat != None) & np.isfinite(lat)]  # noqa: E711
                if len(allsel) >= 3:
                    model.alert_effect["any"] = {"extra_sec": float(np.median(allsel) - np.median(base)), "n": int(len(allsel))}

    # 5) Scheduled headway by hour (for gap thresholds at forecast time).
    sd = (datetime.fromtimestamp(now, NY_TZ) - timedelta(hours=3)).date()
    for route in target["routes"]:
        ev = static.scheduled_stop_events(target["stop_id"], sd, [route]).sort_values("arrival_ts")
        if ev.empty:
            continue
        ev["hour"] = (ev["arrival_sec"] // 3600) % 24
        ev["hw"] = ev["arrival_ts"].diff()
        model.sched_headway_by_hour[str(route)] = {str(int(h)): float(v) for h, v in ev.groupby("hour")["hw"].median().dropna().items()}
    return model


# --------------------------------------------------------------------------- #
# Forecast
# --------------------------------------------------------------------------- #
GAP_FLAG_HORIZON_SEC = 1800.0   # gaps are only called within 30 min: later trips may not be in the feed yet


def forecast_station(target: dict, trains: list, static: StaticGTFS, model: PropagationModel | None,
                     now: float, alerts_now: pd.DataFrame | None = None, horizon_sec: float = 3600.0,
                     defaults: config.AnalysisDefaults = config.DEFAULTS, learned=None) -> dict:
    """Next arrivals at the target with calibrated ETAs, predicted headways and downstream effects."""
    stop_id, routes = target["stop_id"], [str(r) for r in target["routes"]]
    model = model or PropagationModel(target["id"], stop_id, routes)
    hour = datetime.fromtimestamp(now, NY_TZ).hour
    ups_by_route = target.get("upstream", {})
    name = static.stop_name
    active_causes: dict[str, list[str]] = {}
    if alerts_now is not None and not alerts_now.empty:
        for a in alerts_now[alerts_now["kind"] == "delay"].itertuples(index=False):
            for r in (a.routes or []):
                if str(r) in routes:
                    active_causes.setdefault(str(r), []).append(str(a.cause_category))
    arrivals = []
    for t in trains:
        if t.route_id not in routes:
            continue
        eta = t.eta_at(stop_id)
        if eta is None or eta < now - 30 or eta > now + horizon_sec:
            continue
        h = eta - now
        bias, p10, p90, n_cal = model.eta_adjustment(t.route_id, h)
        extra, n_alert = 0.0, 0
        for c in active_causes.get(t.route_id, []):
            e, n = model.alert_extra(c)
            if e > extra:
                extra, n_alert = e, n
        model_eta = eta + bias + extra
        if t.position_lateness_sec is not None and t.lateness_sec is not None and t.position_lateness_sec > t.lateness_sec + 60:
            model_eta += t.position_lateness_sec - t.lateness_sec     # the position proves the feed optimistic
        eta_lo, eta_hi, source = eta + p10, eta + p90 + extra, "lookback"
        if learned is not None and t.started:
            try:
                lp = learned.predict(t, stop_id, feed_spread=(p90 - p10) if n_cal else 240.0)
            except Exception:
                lp = None
            if lp is not None:
                model_eta, eta_lo, eta_hi, source = lp["eta_ts"], lp["lo_ts"], lp["hi_ts"], "learned"
        sched = static.scheduled_arrival(t.trip_id, stop_id, t.service_date) if t.service_date else None
        k = t.stops_until(stop_id)
        carry = None
        if t.lateness_sec is not None and k is not None and k > 0:
            slope, intercept, resid, n_c = model.carry(t.route_id, k)
            carry = {"lateness_sec": float(slope * t.lateness_sec + intercept), "std_sec": float(resid), "n": n_c, "k": int(k)}
        arrivals.append({
            "trip_id": t.trip_id, "route_id": t.route_id, "feed_eta_ts": eta, "model_eta_ts": model_eta,
            "eta_lo_ts": eta_lo, "eta_hi_ts": eta_hi, "minutes_away": round(h / 60, 1), "model_source": source,
            "track_changed": bool(t.track_changed),
            "sched_method": getattr(t, "sched_method", None),
            "position": ({"status": t.pos_status, "stop_name": name(t.pos_stop_id) if t.pos_stop_id else None, "since_sec": t.since_update_sec, "at_origin": t.at_origin,
                          "holding": t.holding, "stalled": t.stalled, "corroboration": t.corroboration} if t.pos_status else None),
            "agreement": ("agree" if abs(model_eta - eta) < 45 else "model_later" if model_eta > eta else "model_earlier"),
            "sched_ts": sched, "feed_lateness_sec": (eta - sched) if sched else None,
            "model_lateness_sec": (model_eta - sched) if sched else None,
            "now_at_stop": t.next_stop_id, "now_at_stop_name": name(t.next_stop_id) if t.next_stop_id else None,
            "now_lateness_sec": t.lateness_sec if t.started else None, "started": t.started, "stops_away": k, "carry": carry if t.started else None,
            "calibration_n": n_cal, "alert_extra_sec": extra if extra else None,
        })
    arrivals.sort(key=lambda a: a["model_eta_ts"])
    # Predicted headways per route and combined.
    sched_hw = {}
    for r in routes:
        v = model.sched_headway_by_hour.get(r, {}).get(str(hour))
        if v is None:
            ev = static.scheduled_stop_events(stop_id, (datetime.fromtimestamp(now, NY_TZ) - timedelta(hours=3)).date(), [r]).sort_values("arrival_ts")
            win = ev[(ev["arrival_ts"] >= now - 2700) & (ev["arrival_ts"] <= now + 2700)]["arrival_ts"].diff().dropna()
            v = float(win.median()) if len(win) else None
        sched_hw[r] = v
    effects: list[dict] = []
    per_route: dict[str, dict] = {}
    for r in routes:
        lst = [a for a in arrivals if a["route_id"] == r]
        hws = []
        prev = now
        for a in lst:
            hw = a["model_eta_ts"] - prev
            a["headway_sec"] = hw if prev != now else None
            ratio = (hw / sched_hw[r]) if sched_hw.get(r) else None
            within = a["model_eta_ts"] <= now + GAP_FLAG_HORIZON_SEC
            a["gap"] = bool(ratio and ratio >= defaults.gap_ratio and prev != now and within)
            a["bunched"] = bool(ratio and ratio <= defaults.bunching_ratio and prev != now)
            if a["gap"]:
                effects.append({"kind": "gap", "route_id": r, "severity": "high" if ratio >= 2.5 else "medium",
                                "text": f"Gap forming on the {r}: {hw / 60:.0f} min before the train due {_hhmm(a['model_eta_ts'])} "
                                        f"(scheduled headway {sched_hw[r] / 60:.0f} min)" +
                                        (f"; that train is {a['now_lateness_sec'] / 60:.0f} min late at {a['now_at_stop_name']}" if a.get("now_lateness_sec") and a["now_lateness_sec"] >= 120 else "")})
            hws.append(hw)
            prev = a["model_eta_ts"]
        wait = (lst[0]["model_eta_ts"] - now) if lst else None
        per_route[r] = {"next_eta_ts": lst[0]["model_eta_ts"] if lst else None, "wait_sec": wait,
                        "sched_headway_sec": sched_hw.get(r), "n_upcoming": len(lst),
                        "max_headway_sec": max(hws[1:], default=None) if len(hws) > 1 else None,
                        "gap_now": bool(wait and sched_hw.get(r) and wait >= defaults.gap_ratio * sched_hw[r])}
        if per_route[r]["gap_now"]:
            effects.append({"kind": "wait", "route_id": r, "severity": "medium",
                            "text": f"No {r} train for {wait / 60:.0f} min (scheduled every {sched_hw[r] / 60:.0f}); next due {_hhmm(lst[0]['model_eta_ts'])}"})
    # Late inbound trains: expected lateness at the target from the carry model.
    for a in arrivals:
        if a.get("now_lateness_sec") is not None and a["now_lateness_sec"] >= 180 and a.get("carry"):
            c = a["carry"]
            effects.append({"kind": "late_inbound", "route_id": a["route_id"], "severity": "medium" if c["lateness_sec"] < 480 else "high",
                            "text": f"{a['route_id']} train {a['now_lateness_sec'] / 60:.0f} min late at {a['now_at_stop_name']} ({c['k']} stops away): "
                                    f"expected {c['lateness_sec'] / 60:.0f} min late here ({'history of ' + str(c['n']) + ' trips' if c['n'] else 'default carry-over'})"})
    for r, causes in active_causes.items():
        for c in dict.fromkeys(causes):
            extra, n = model.alert_extra(c)
            effects.append({"kind": "alert", "route_id": r, "severity": "medium",
                            "text": f"Unplanned '{c.replace('_', ' ')}' alert active on the {r}" +
                                    (f": historically +{extra / 60:.1f} min at this platform ({n} arrivals)" if n else "; no local history yet for its effect")})
    sev_rank = {"high": 0, "medium": 1, "low": 2}
    effects.sort(key=lambda e: sev_rank.get(e["severity"], 3))
    status = "disrupted" if any(e["severity"] == "high" for e in effects) else ("degraded" if effects else "normal")
    return {"id": target["id"], "label": target.get("label", target.get("station_name", "")), "stop_id": stop_id,
            "station_name": target.get("station_name"), "direction": target.get("direction"), "routes": routes,
            "status": status, "arrivals": arrivals[:12], "per_route": per_route, "effects": effects[:10],
            "model": {"fitted_at": model.fitted_at, "n_arrivals": model.n_arrivals, "n_days": model.n_days,
                      "calibrated_routes": sorted(model.eta_bias.keys()), "carry_routes": sorted(model.lateness_carry.keys())},
            "upstream_now": [{"route_id": t.route_id, "trip_id": t.trip_id, "at": name(t.next_stop_id) if t.next_stop_id else None,
                              "lateness_sec": t.lateness_sec, "stops_away": t.stops_until(stop_id)}
                             for t in trains if t.route_id in routes and t.stops_until(stop_id) not in (None, 0)][:12]}


def _hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts, NY_TZ).strftime("%H:%M")
