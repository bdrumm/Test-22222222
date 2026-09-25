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
    service_date: date | None = None
    started: bool = True                   # False for scheduled trips the feed lists before departure

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
                "next_eta_ts": self.next_eta_ts, "lateness_sec": self.lateness_sec, "sched_matched": self.sched_matched,
                "started": self.started, "stops_ahead": len(self.stops)}


def _service_date(start_date: str | None, now: float) -> date:
    if isinstance(start_date, str) and len(start_date) == 8 and start_date.isdigit():
        return date(int(start_date[:4]), int(start_date[4:6]), int(start_date[6:]))
    return (datetime.fromtimestamp(now, NY_TZ) - timedelta(hours=3)).date()


def live_trains(feed_bytes: dict[str, bytes], static: StaticGTFS | None, now: float,
                past_slack_sec: float = 90.0) -> list[LiveTrain]:
    """Parse all feeds into LiveTrain objects with lateness at the next stop when the schedule matches."""
    trains: list[LiveTrain] = []
    for feed_key, data in feed_bytes.items():
        try:
            msg = rt.parse_feed(data)
        except Exception:
            continue
        tu = rt.trip_updates_frame(msg, feed_key, now)
        if tu.empty:
            continue
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
            if static is not None:
                sched = static.scheduled_arrival(trip_id, train.next_stop_id, train.service_date)
                if sched is not None:
                    train.lateness_sec = float(train.next_eta_ts - sched)
                    train.sched_matched = True
                    # A trip that still lists every scheduled stop and is not due for a while has not left
                    # its terminal yet: the feed shows its timetable, not a position.
                    tid = static.match_trip(trip_id, train.service_date)
                    n_sched = len(static._trip_stop_index().get(tid, {})) if tid else 0
                    if n_sched and len(stops) >= n_sched and train.next_eta_ts > now + 60:
                        train.started = False
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
        lat = [t.lateness_sec for t in started if t.lateness_sec is not None]
        med_late = float(np.median(lat)) if lat else None
        p90_late = float(np.quantile(lat, 0.9)) if lat else None
        route_alerts = alerts_now[alerts_now["routes"].map(lambda rs: route in rs)] if not alerts_now.empty else alerts_now
        unplanned = route_alerts[(route_alerts["kind"] == "delay")] if not route_alerts.empty else route_alerts
        disruptive = [h for t, h in zip(unplanned.get("alert_type", []), unplanned.get("header", []))
                      if str(t).lower().startswith(DISRUPTION_TYPES)]
        gap_ratio = (best_gap / sched_hw) if (sched_hw and best_gap) else None
        if disruptive or (gap_ratio and gap_ratio >= GAP_DISRUPTED) or (med_late is not None and med_late >= LATE_DISRUPTED_SEC):
            status = "disrupted"
        elif len(unplanned) or (gap_ratio and gap_ratio >= GAP_DEGRADED) or (med_late is not None and med_late >= LATE_DEGRADED_SEC):
            status = "degraded"
        else:
            status = "good"
        out.append({
            "route_id": route, "direction": direction, "trains": len(started), "scheduled_not_started": len(ts) - len(started),
            "matched": sum(1 for t in started if t.sched_matched),
            "median_lateness_sec": med_late, "p90_lateness_sec": p90_late,
            "sched_headway_sec": sched_hw, "max_gap_sec": best_gap or None,
            "max_gap_ratio": gap_ratio, "max_gap_stop": gap_stop, "max_gap_stop_name": static.stop_name(gap_stop) if gap_stop else None,
            "max_gap_at_ts": gap_when, "bunching_share": (n_bunch / n_hw) if n_hw else None,
            "unplanned_alerts": int(len(unplanned)), "alert_headers": [str(h)[:160] for h in unplanned.get("header", [])][:3],
            "status": status,
        })
    return out


def build_live(feed_bytes: dict[str, bytes], alerts_df: pd.DataFrame | None, static: StaticGTFS,
               targets: list[dict], models: dict[str, PropagationModel] | None, now: float | None = None,
               source: str = "live", journeys: list | None = None, journey_models: dict | None = None,
               weather_daily: pd.DataFrame | None = None, events_df: pd.DataFrame | None = None) -> dict:
    """Assemble the full live snapshot. ``targets`` are resolved target dicts (see pipeline.lib.resolve_target);
    ``journeys`` are JourneySpec objects with optional fitted ``journey_models``."""
    now = float(now or datetime.now(NY_TZ).timestamp())
    trains = live_trains(feed_bytes, static, now)
    alerts_now = active_alerts(alerts_df, now)
    routes = route_status(trains, alerts_now, static, now)
    stations = []
    for t in targets:
        model = (models or {}).get(t["id"])
        stations.append(forecast_station(t, trains, static, model, now, alerts_now))
    plans = []
    if journeys:
        from .journey import plan_journey
        for spec in journeys:
            try:
                plans.append(plan_journey(spec, (journey_models or {}).get(spec.id), trains, static, now, alerts_df, weather_daily, events_df))
            except Exception as exc:  # planning must never break the snapshot
                plans.append({"id": spec.id, "label": spec.label, "error": str(exc)[:200], "options": [], "legs": [l.as_dict() for l in spec.legs]})
    unplanned = alerts_now[alerts_now["kind"] == "delay"] if not alerts_now.empty else alerts_now
    alerts_out = [{"alert_id": r.alert_id, "alert_type": r.alert_type, "cause_category": r.cause_category,
                   "routes": list(r.routes), "header": str(r.header)[:240], "active_start": _f(r.active_start)}
                  for r in unplanned.itertuples(index=False)][:60]
    n_by_status = {"good": 0, "degraded": 0, "disrupted": 0}
    for r in routes:
        n_by_status[r["status"]] += 1
    started = [t for t in trains if t.started]
    return {
        "generated_at": datetime.fromtimestamp(now, NY_TZ).isoformat(), "generated_ts": now, "source": source,
        "feeds": sorted(feed_bytes.keys()), "trains_total": len(started), "trains_scheduled_not_started": len(trains) - len(started),
        "trains_matched": sum(1 for t in started if t.sched_matched),
        "summary": n_by_status, "routes": routes, "alerts": alerts_out, "stations": stations, "journeys": plans,
    }


def _f(v):
    try:
        f = float(v)
        return None if np.isnan(f) else f
    except (TypeError, ValueError):
        return None
