"""Holistic system status at the current time from the GTFS-Realtime feeds.

``build_live`` turns the raw feed bytes (one per line-group feed) plus the alerts
document into a JSON-able snapshot:

* ``routes``: per route and direction, trains in service, median lateness of the
  trains that could be matched to the schedule, the largest headway gap forming
  anywhere on the line (and where), bunching, active unplanned alerts, and a
  status label (good / degraded / disrupted).
* ``alerts``: active unplanned delay alerts and reduced-service notices.
* ``stations``: for each monitored platform, the next arrivals with feed ETAs,
  the look-back model's calibrated ETAs, predicted headways and gap warnings,
  and the "downstream effects" narrative (see :mod:`propagation`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from ..sources import alerts as alerts_src
from ..sources import gtfs_realtime as rt
from ..sources.gtfs_static import NY_TZ, StaticGTFS, direction_from_stop_id
from .propagation import PropagationModel, forecast_station

GAP_DEGRADED, GAP_DISRUPTED = 1.6, 2.5          # predicted headway / scheduled headway
LATE_DEGRADED_SEC, LATE_DISRUPTED_SEC = 240, 480
DISRUPTION_TYPES = ("delays", "suspended", "part suspended", "trains rerouted", "service change",
                    "stops skipped", "express to local", "local to express", "multiple changes", "slow speeds")


@dataclass
class LiveTrain:
    trip_id: str
    route_id: str
    direction: str | None
    start_date: str | None
    feed: str
    stops: list[tuple[str, float]]        # (stop_id, eta_ts) for stops not yet left, in order
    next_stop_id: str | None = None
    next_eta_ts: float | None = None
    lateness_sec: float | None = None      # feed ETA at next stop minus scheduled arrival (if matched)
    sched_matched: bool = False
    sched_trip_id: str | None = None        # static trip matched by the nearest-trip fallback (id not in the timetable)
    sched_method: str | None = None         # trip_id | nearest
    service_date: date | None = None
    started: bool = True                   # False for scheduled trips the feed lists before departure
    track_changed: float = 0.0             # 1.0 when the feed's actual track differs from the scheduled one
    train_id: str | None = None
    # vehicle position (second, independent signal): where the train physically is and for how long
    pos_status: str | None = None          # STOPPED_AT | IN_TRANSIT_TO | INCOMING_AT
    pos_stop_id: str | None = None
    pos_ts: float | None = None            # feed timestamp of the current position state
    since_update_sec: float | None = None  # time in the current state (dwell so far, or run so far)
    expected_run_sec: float | None = None  # scheduled run time into pos_stop_id (IN_TRANSIT_TO)
    holding: bool = False                  # stopped at a station much longer than a normal dwell (not at its origin terminal)
    stalled: bool = False                  # between stations much longer than the scheduled run
    at_origin: bool = False                # stopped at a terminal of the route: waiting to depart or relay is not a hold
    position_lateness_sec: float | None = None   # lateness implied by the position alone
    corroboration: str | None = None       # feed ETA vs position: agree | feed_optimistic | position_unknown

    @property
    def effective_lateness_sec(self) -> float | None:
        """Lateness the forecasts should use: the feed's, raised to what the position already proves."""
        vals = [v for v in (self.lateness_sec, self.position_lateness_sec) if v is not None]
        return max(vals) if vals else None

    def eta_at(self, stop_id: str) -> float | None:
        for s, t in self.stops:
            if s == stop_id:
                return t
        return None

    def stops_until(self, stop_id: str) -> int | None:
        for i, (s, _) in enumerate(self.stops):
            if s == stop_id:
                return i
        return None

    def as_dict(self, static: StaticGTFS | None = None) -> dict:
        name = (lambda s: static.stop_name(s) if static and s else s)
        return {"trip_id": self.trip_id, "route_id": self.route_id, "direction": self.direction, "feed": self.feed,
                "next_stop_id": self.next_stop_id, "next_stop_name": name(self.next_stop_id),
                "next_eta_ts": self.next_eta_ts, "lateness_sec": self.lateness_sec, "sched_matched": self.sched_matched, "sched_method": self.sched_method,
                "started": self.started, "stops_ahead": len(self.stops), "track_changed": bool(self.track_changed), "train_id": self.train_id,
                "position": {"status": self.pos_status, "stop_id": self.pos_stop_id, "stop_name": name(self.pos_stop_id), "since_sec": self.since_update_sec,
                             "expected_run_sec": self.expected_run_sec, "holding": self.holding, "stalled": self.stalled,
                             "position_lateness_sec": self.position_lateness_sec, "corroboration": self.corroboration} if self.pos_status else None,
                "effective_lateness_sec": self.effective_lateness_sec}


def _service_date(start_date: str | None, now: float) -> date:
    if isinstance(start_date, str) and len(start_date) == 8 and start_date.isdigit():
        return date(int(start_date[:4]), int(start_date[4:6]), int(start_date[6:]))
    return (datetime.fromtimestamp(now, NY_TZ) - timedelta(hours=3)).date()


from ..collect.dwells import HOLD_SEC   # stopped this long at a station = holding (a normal dwell is 30-60 s)
STALL_SLACK_SEC = 120.0  # in transit this much longer than the scheduled run = stalled


def _sched_at(static: StaticGTFS, train: "LiveTrain", stop_id: str) -> float | None:
    """Scheduled arrival of this train at a stop, through its matched static trip when the id itself is not in the timetable."""
    if train.sched_trip_id:
        return static.static_trip_arrival(train.sched_trip_id, stop_id, train.service_date)
    return static.scheduled_arrival(train.trip_id, stop_id, train.service_date)


def _fuse_position(train: "LiveTrain", veh: dict, static: StaticGTFS | None, now: float) -> None:
    """Attach the vehicle position and derive holding / stalled flags and position-implied lateness."""
    status, stop = veh.get("current_status"), veh.get("stop_id")
    ts = veh.get("vehicle_ts")
    ts = float(ts) if ts is not None and not pd.isna(ts) else None
    # NYCT also publishes a vehicle for trips that have not started (no status, timestamp = scheduled departure)
    if not isinstance(stop, str) or (ts is not None and ts > now + 60):
        return
    if not isinstance(status, str):
        status = "IN_TRANSIT_TO"          # the GTFS-Realtime default when current_status is absent
    train.pos_status, train.pos_stop_id, train.pos_ts = status, stop, ts
    if train.pos_ts is not None:
        train.since_update_sec = max(0.0, now - train.pos_ts)
    if static is None or not train.pos_stop_id or train.service_date is None:
        return
    sched_here = _sched_at(static, train, train.pos_stop_id)
    try:
        seq = static.canonical_stop_sequence(train.route_id, train.direction or "N")
    except Exception:
        seq = []
    train.at_origin = bool(seq) and train.pos_stop_id in (seq[0], seq[-1])   # either terminal: waiting or relaying
    if train.pos_status == "STOPPED_AT":
        if train.since_update_sec is not None:
            train.holding = train.since_update_sec >= HOLD_SEC and not train.at_origin
        if sched_here is not None:
            train.position_lateness_sec = float(now - sched_here)          # still here: at least this late
    elif train.pos_status in ("IN_TRANSIT_TO", "INCOMING_AT"):
        i = seq.index(train.pos_stop_id) if train.pos_stop_id in seq else -1
        prev = seq[i - 1] if i > 0 else None
        if prev is not None and sched_here is not None:
            sched_prev = _sched_at(static, train, prev)
            if sched_prev is not None:
                train.expected_run_sec = max(30.0, float(sched_here - sched_prev))
        if train.since_update_sec is not None and train.expected_run_sec is not None:
            train.stalled = train.since_update_sec > train.expected_run_sec + STALL_SLACK_SEC
        if sched_here is not None:
            remaining = 0.0 if train.expected_run_sec is None else max(0.0, train.expected_run_sec - (train.since_update_sec or 0.0))
            train.position_lateness_sec = float(now + remaining - sched_here)
    # corroborate the feed's own lateness with the position's lower bound
    if train.lateness_sec is not None and train.position_lateness_sec is not None:
        gap = train.position_lateness_sec - train.lateness_sec
        train.corroboration = "feed_optimistic" if gap > 60 else "agree"
    elif train.position_lateness_sec is None:
        train.corroboration = "position_unknown"


def live_trains(feed_bytes: dict[str, bytes], static: StaticGTFS | None, now: float,
                past_slack_sec: float = 90.0) -> list[LiveTrain]:
    """Parse all feeds into LiveTrain objects with lateness at the next stop when the schedule matches,
    fused with the vehicle positions (holding / stalled / position-implied lateness)."""
    trains: list[LiveTrain] = []
    for feed_key, data in feed_bytes.items():
        try:
            msg = rt.parse_feed(data)
        except Exception:
            continue
        tu = rt.trip_updates_frame(msg, feed_key, now)
        if tu.empty:
            continue
        vp = rt.vehicle_positions_frame(msg, feed_key, now)
        positions = {}
        if not vp.empty:
            for r in vp.to_dict("records"):
                positions[(r["trip_id"], None if pd.isna(r.get("start_date")) else r.get("start_date"))] = r
        tu = tu.dropna(subset=["arrival_ts"])
        for (trip_id, start_date), g in tu.groupby(["trip_id", "start_date"], dropna=False, sort=False):
            start_date = None if pd.isna(start_date) else start_date
            g = g.sort_values("arrival_ts")
            stops = [(s, float(t)) for s, t in zip(g["stop_id"], g["arrival_ts"]) if t >= now - past_slack_sec]
            if not stops:
                continue
            direction = direction_from_stop_id(stops[0][0]) or (g["direction"].iloc[0] if "direction" in g else None)
            train = LiveTrain(trip_id=trip_id, route_id=str(g["route_id"].iloc[0]), direction=direction,
                              start_date=start_date, feed=feed_key, stops=stops, next_stop_id=stops[0][0],
                              next_eta_ts=stops[0][1], service_date=_service_date(start_date, now))
            if "actual_track" in g.columns:
                tr = g[["sched_track", "actual_track"]].dropna()
                train.track_changed = float(bool(len(tr) and (tr["sched_track"] != tr["actual_track"]).any()))
                tid = g["train_id"].dropna()
                train.train_id = str(tid.iloc[0]) if len(tid) else None
            if static is not None:
                sched = static.scheduled_arrival(trip_id, train.next_stop_id, train.service_date)
                if sched is not None:
                    train.sched_method = "trip_id"
                else:
                    # id not in the timetable (supplement schedule, reroute): nearest scheduled trip of the route
                    hit = static.nearest_scheduled_trip(train.next_stop_id, train.route_id, train.service_date, train.next_eta_ts)
                    if hit is not None:
                        train.sched_trip_id, sched = hit
                        train.sched_method = "nearest"
                if sched is not None:
                    train.lateness_sec = float(train.next_eta_ts - sched)
                    train.sched_matched = True
                    # A trip that still lists every scheduled stop and is not due for a while has not left
                    # its terminal yet: the feed shows its timetable, not a position.
                    tid = static.match_trip(trip_id, train.service_date)
                    n_sched = len(static._trip_stop_index().get(tid, {})) if tid else 0
                    if n_sched and len(stops) >= n_sched and train.next_eta_ts > now + 60:
                        train.started = False
            veh = positions.get((trip_id, start_date)) or positions.get((trip_id, None))
            if veh is not None and train.started:
                try:
                    _fuse_position(train, veh, static, now)
                except Exception:
                    pass
            trains.append(train)
    return trains


def _sched_headway(static: StaticGTFS, route: str, direction: str, ref_stop: str, now: float,
                   cache: dict) -> float | None:
    """Median scheduled headway of a route at a reference stop in the current hour."""
    sd = (datetime.fromtimestamp(now, NY_TZ) - timedelta(hours=3)).date()
    key = (route, direction, ref_stop, sd)
    if key not in cache:
        ev = static.scheduled_stop_events(ref_stop, sd, [route])
        ev = ev.sort_values("arrival_ts")
        cache[key] = ev
    ev = cache[key]
    if ev.empty:
        return None
    win = ev[(ev["arrival_ts"] >= now - 2700) & (ev["arrival_ts"] <= now + 2700)]
    hw = win["arrival_ts"].diff().dropna()
    if hw.empty:
        hw = ev["arrival_ts"].diff().dropna()
    return float(hw.median()) if len(hw) else None


def active_alerts(alerts_df: pd.DataFrame | None, now: float) -> pd.DataFrame:
    if alerts_df is None or alerts_df.empty:
        return pd.DataFrame(columns=["alert_id", "alert_type", "planned", "cause_category", "routes", "header", "kind", "active_start", "active_end"])
    a = alerts_df.copy()
    a["kind"] = [alerts_src.alert_kind(t, h) for t, h in zip(a["alert_type"], a["header"])]
    start_ok = a["active_start"].isna() | (a["active_start"] <= now)
    end = a["active_end"].fillna(a["updated_at"].fillna(a["active_start"]) + 3 * 3600)
    return a[start_ok & (end >= now)].reset_index(drop=True)


def route_status(trains: list[LiveTrain], alerts_now: pd.DataFrame, static: StaticGTFS, now: float,
                 horizon_sec: float = 1200.0) -> list[dict]:
    """Per route/direction summary with the largest predicted gap on the line.

    Gaps are measured only between ETAs within ``horizon_sec`` (default 20 min): the
    feed publishes upcoming trips only shortly before departure, so headways further
    out would be artificially long.
    """
    cache: dict = {}
    out = []
    by_rd: dict[tuple[str, str], list[LiveTrain]] = {}
    for t in trains:
        if t.direction:
            by_rd.setdefault((t.route_id, t.direction), []).append(t)
    for (route, direction), ts in sorted(by_rd.items()):
        # ETAs per stop across trains -> headways; the largest is the gap forming on the line.
        etas: dict[str, list[float]] = {}
        for t in ts:
            for s, eta in t.stops:
                if eta <= now + horizon_sec:
                    etas.setdefault(s, []).append(eta)
        best_gap, gap_stop, gap_when, n_hw, n_bunch = 0.0, None, None, 0, 0
        ref_stop = max(etas, key=lambda s: len(etas[s])) if etas else None
        sched_hw = _sched_headway(static, route, direction, ref_stop, now, cache) if ref_stop else None
        for s, lst in etas.items():
            if len(lst) < 2:
                continue
            lst = sorted(lst)
            for a, b in zip(lst, lst[1:]):
                n_hw += 1
                if sched_hw and (b - a) <= 0.5 * sched_hw:
                    n_bunch += 1
                if b - a > best_gap:
                    best_gap, gap_stop, gap_when = b - a, s, b
        started = [t for t in ts if t.started]
        lat = [t.effective_lateness_sec for t in started if t.effective_lateness_sec is not None]
        med_late = float(np.median(lat)) if lat else None
        p90_late = float(np.quantile(lat, 0.9)) if lat else None
        n_holding = sum(1 for t in started if t.holding)
        n_stalled = sum(1 for t in started if t.stalled)
        n_pos = sum(1 for t in started if t.pos_status)
        n_optimistic = sum(1 for t in started if t.corroboration == "feed_optimistic")
        route_alerts = alerts_now[alerts_now["routes"].map(lambda rs: route in rs)] if not alerts_now.empty else alerts_now
        unplanned = route_alerts[(route_alerts["kind"] == "delay")] if not route_alerts.empty else route_alerts
        disruptive = [h for t, h in zip(unplanned.get("alert_type", []), unplanned.get("header", []))
                      if str(t).lower().startswith(DISRUPTION_TYPES)]
        gap_ratio = (best_gap / sched_hw) if (sched_hw and best_gap) else None
        if disruptive or (gap_ratio and gap_ratio >= GAP_DISRUPTED) or (med_late is not None and med_late >= LATE_DISRUPTED_SEC) or n_stalled >= 2:
            status = "disrupted"
        elif len(unplanned) or (gap_ratio and gap_ratio >= GAP_DEGRADED) or (med_late is not None and med_late >= LATE_DEGRADED_SEC) or n_stalled or n_holding >= 2:
            status = "degraded"
        else:
            status = "good"
        out.append({
            "route_id": route, "direction": direction, "trains": len(started), "scheduled_not_started": len(ts) - len(started),
            "matched": sum(1 for t in started if t.sched_matched),
            "median_lateness_sec": med_late, "p90_lateness_sec": p90_late,
            "positions": {"n": n_pos, "holding": n_holding, "stalled": n_stalled, "feed_optimistic": n_optimistic,
                          "stalled_trains": [{"trip_id": t.trip_id, "train_id": t.train_id, "toward": static.stop_name(t.pos_stop_id), "since_sec": t.since_update_sec, "expected_run_sec": t.expected_run_sec} for t in started if t.stalled][:5],
                          "holding_trains": [{"trip_id": t.trip_id, "train_id": t.train_id, "at": static.stop_name(t.pos_stop_id), "since_sec": t.since_update_sec} for t in started if t.holding][:5]},
            "sched_headway_sec": sched_hw, "max_gap_sec": best_gap or None,
            "max_gap_ratio": gap_ratio, "max_gap_stop": gap_stop, "max_gap_stop_name": static.stop_name(gap_stop) if gap_stop else None,
            "max_gap_at_ts": gap_when, "bunching_share": (n_bunch / n_hw) if n_hw else None,
            "unplanned_alerts": int(len(unplanned)), "alert_headers": [str(h)[:160] for h in unplanned.get("header", [])][:3],
            "status": status,
        })
    return out


def recent_holds(store, static: StaticGTFS | None, now: float, window_sec: float = 3600.0) -> dict | None:
    """Holds (dwell >= HOLD_SEC) observed anywhere in the last hour, excluding origin terminals where waiting is by design."""
    if store is None:
        return None
    try:
        dw = store.dwells(None, now - window_sec, now)
    except Exception:
        return None
    if dw is None or dw.empty:
        return {"n": 0, "window_sec": window_sec, "by_route": {}, "top_stops": [], "minutes": 0.0}
    hh = dw[dw["dwell_sec"] >= HOLD_SEC].copy()
    if static is not None and not hh.empty:
        origins = set()
        for r in hh["route_id"].dropna().unique():
            for d in ("N", "S"):
                try:
                    seq = static.canonical_stop_sequence(str(r), d)
                    if seq:
                        origins.add(seq[0]); origins.add(seq[-1])
                except Exception:
                    pass
        hh = hh[~hh["stop_id"].isin(origins)]
    name = static.stop_name if static is not None else (lambda s: s)
    top = []
    for sid, g in hh.groupby("stop_id"):
        top.append({"stop_id": sid, "name": name(sid), "n": int(len(g)), "max_sec": float(g["dwell_sec"].max()), "routes": sorted({str(x) for x in g["route_id"].dropna()})})
    top.sort(key=lambda x: (-x["n"], -x["max_sec"]))
    return {"n": int(len(hh)), "window_sec": window_sec, "minutes": round(float(hh["dwell_sec"].sum()) / 60, 1),
            "by_route": {str(k): int(v) for k, v in hh.groupby("route_id").size().sort_values(ascending=False).head(8).items()}, "top_stops": top[:6]}


def _scrub_nan(obj):
    """NaN is not valid JSON: browsers reject the whole snapshot. Replace it with null everywhere."""
    if isinstance(obj, float):
        return None if obj != obj else obj
    if isinstance(obj, dict):
        return {k: _scrub_nan(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub_nan(v) for v in obj]
    return obj


def build_live(feed_bytes: dict[str, bytes], alerts_df: pd.DataFrame | None, static: StaticGTFS,
               targets: list[dict], models: dict[str, PropagationModel] | None, now: float | None = None,
               source: str = "live", journeys: list | None = None, journey_models: dict | None = None,
               weather_daily: pd.DataFrame | None = None, events_df: pd.DataFrame | None = None,
               learned=None, store=None, nws_df=None, climatology: dict | None = None, hold_model: dict | None = None) -> dict:
    """Assemble the full live snapshot. ``targets`` are resolved target dicts (see pipeline.lib.resolve_target);
    ``journeys`` are JourneySpec objects with optional fitted ``journey_models``."""
    now = float(now or datetime.now(NY_TZ).timestamp())
    trains = live_trains(feed_bytes, static, now)
    alerts_now = active_alerts(alerts_df, now)
    routes = route_status(trains, alerts_now, static, now)
    lctx = None
    if learned is not None and getattr(learned, "ready", False):
        from .learned import LearnedContext
        lctx = LearnedContext(learned, store, static, now, alerts_df, weather_daily, events_df, nws_df, climatology)
    stations = []
    for t in targets:
        model = (models or {}).get(t["id"])
        stations.append(forecast_station(t, trains, static, model, now, alerts_now, learned=lctx))
    # forward simulation for the lines that matter to the monitored platforms and journeys
    sims = []
    try:
        from .simulate import simulate_routes, station_scenarios
        pairs = sorted({(str(r), t.get("direction") or "N") for t in targets for r in t.get("routes", [])})
        for spec in journeys or []:
            for leg in spec.legs:
                d = leg.from_stop[-1] if leg.from_stop and leg.from_stop[-1] in "NS" else "N"
                pairs += [(str(r), d) for r in leg.routes]
        pairs = sorted(set(pairs))
        sims = simulate_routes(trains, static, pairs, now, lctx, hold_model=hold_model)
        for st in stations:
            st["scenarios"] = station_scenarios(sims, st["stop_id"], now)
    except Exception as exc:  # the simulation is an add-on; never break the snapshot
        sims = [{"error": str(exc)[:200]}]
    incidents = []
    if store is not None:
        try:
            from .incidents import developing_incidents
            incidents = developing_incidents(store, static, now, alerts_now)
        except Exception as exc:  # never break the snapshot
            incidents = [{"error": str(exc)[:200]}]
    alerted_routes = set()
    if alerts_now is not None and not alerts_now.empty and "kind" in alerts_now:
        for rs in alerts_now[alerts_now["kind"] == "delay"]["routes"]:
            alerted_routes |= {str(x) for x in (rs or [])}
    for t in trains:
        if t.started and (t.stalled or (t.holding and (t.since_update_sec or 0) >= 2 * HOLD_SEC)):
            where = (f"between the previous stop and {static.stop_name(t.pos_stop_id)}" if t.stalled else f"at {static.stop_name(t.pos_stop_id)}")
            incidents.append({"kind": "stalled" if t.stalled else "holding", "route_id": t.route_id, "direction": t.direction, "trip_id": t.trip_id, "train_id": t.train_id,
                              "to_stop": t.pos_stop_id, "to_name": static.stop_name(t.pos_stop_id), "n_trains": 1, "n_slow": 1,
                              "mean_loss_sec": float(t.since_update_sec or 0) - float(t.expected_run_sec or 0), "first_seen_ts": t.pos_ts, "last_seen_ts": now,
                              "alerted": t.route_id in alerted_routes,
                              "text": f"{t.route_id} {'northbound' if t.direction == 'N' else 'southbound'} train {t.train_id or t.trip_id} has been {where} for "
                                      f"{(t.since_update_sec or 0) / 60:.0f} min" + (f" (scheduled run {t.expected_run_sec / 60:.0f} min)" if t.stalled and t.expected_run_sec else "")
                                      + ("" if t.route_id in alerted_routes else " (no alert posted yet)")})
    incidents = [x for x in incidents if "error" not in x]
    incidents.sort(key=lambda x: (x.get("alerted", False), -(x.get("mean_loss_sec") or 0)))
    plans = []
    comparisons = []
    leave_by_out = []
    if journeys:
        from .journey import plan_journey, compare_alternatives, leave_by
        for spec in journeys:
            try:
                plans.append(plan_journey(spec, (journey_models or {}).get(spec.id), trains, static, now, alerts_df, weather_daily, events_df, learned=lctx))
            except Exception as exc:  # planning must never break the snapshot
                plans.append({"id": spec.id, "label": spec.label, "error": str(exc)[:200], "options": [], "legs": [l.as_dict() for l in spec.legs]})
        try:
            comparisons = compare_alternatives(plans, journeys)
        except Exception as exc:
            comparisons = [{"error": str(exc)[:200]}]
        # leave-by budgets for the next few round hours, per journey (typical + conservative), for the planner's calculator
        for spec in journeys:
            try:
                jm = (journey_models or {}).get(spec.id)
                hours = []
                for k in range(1, 7):
                    target = (int(now // 3600) + k) * 3600
                    lb = leave_by(spec, jm, target, now)
                    hours.append({"arrive_by_ts": target, "leave_by_ts": lb["leave_by_ts"], "typical_total_sec": lb["typical_total_sec"], "conservative_total_sec": lb["conservative_total_sec"]})
                leave_by_out.append({"id": spec.id, "hours": hours})
            except Exception:
                continue
    unplanned = alerts_now[alerts_now["kind"] == "delay"] if not alerts_now.empty else alerts_now
    alerts_out = [{"alert_id": r.alert_id, "alert_type": r.alert_type, "cause_category": r.cause_category,
                   "routes": list(r.routes), "header": str(r.header)[:240], "active_start": _f(r.active_start)}
                  for r in unplanned.itertuples(index=False)][:60]
    n_by_status = {"good": 0, "degraded": 0, "disrupted": 0}
    for r in routes:
        n_by_status[r["status"]] += 1
    started = [t for t in trains if t.started]
    return _scrub_nan({
        "generated_at": datetime.fromtimestamp(now, NY_TZ).isoformat(), "generated_ts": now, "source": source,
        "feeds": sorted(feed_bytes.keys()), "trains_total": len(started), "trains_scheduled_not_started": len(trains) - len(started),
        "trains_matched": sum(1 for t in started if t.sched_matched),
        "summary": n_by_status, "routes": routes, "alerts": alerts_out, "stations": stations, "journeys": plans,
        "learned_model": ({"ready": True, "n_train": getattr(learned, "n_train", 0),
                           "mae_model": (learned.card.get("evaluation") or {}).get("mae_model"),
                           "mae_feed": (learned.card.get("evaluation") or {}).get("mae_feed"),
                           "trained_at": learned.card.get("trained_at")} if lctx is not None else {"ready": False}),
        "track_changes": [t.as_dict(static) for t in trains if t.track_changed and t.started][:40],
        "incidents_developing": incidents,
        "route_choice": comparisons, "leave_by": leave_by_out,
        "simulation": [x for x in sims if "error" not in x],
        "simulation_error": next((x["error"] for x in sims if "error" in x), None),
        "holds_last_hour": recent_holds(store, static, now),
        "positions": {"n_with_position": sum(1 for t in trains if t.pos_status and t.started),
                      "holding": sum(1 for t in trains if t.holding and t.started), "stalled": sum(1 for t in trains if t.stalled and t.started),
                      "feed_optimistic": sum(1 for t in trains if t.corroboration == "feed_optimistic" and t.started)},
    })


def _f(v):
    try:
        f = float(v)
        return None if np.isnan(f) else f
    except (TypeError, ValueError):
        return None
