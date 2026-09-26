"""Run the analyses and assemble the static site (HTML/JS + JSON data) for GitHub Pages.

    python -m pipeline.build_site --data-dir data-branch --out _site
    python -m pipeline.build_site --synthetic --out _site      # offline preview with injected causes
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from mta_delay_insights import __version__
from mta_delay_insights.analysis.engine import AnalysisRequest, analyze_station
from mta_delay_insights.analysis.line_insights import line_insights
from mta_delay_insights.sources.alerts import alert_kind
from mta_delay_insights.realtime import build_live, fit_model
from mta_delay_insights.realtime.journey import JourneyModel, fit_journey, resolve_journeys
from mta_delay_insights.analysis.transfers import analyze_routes
from mta_delay_insights.models import ArrivalModel, build_training_rows, train_arrival_model
from mta_delay_insights.analysis import line_view
from mta_delay_insights.sources.gtfs_static import NY_TZ, StaticGTFS
from mta_delay_insights.sources.registry import as_records
from mta_delay_insights.storage.db import Store

from . import lib

MIN_BASELINE_HOURS = 36


ROUTE_WINDOW_DAYS = 14
NETWORK_TRAIN_DAYS = 10


def _windows(arrivals: pd.DataFrame, now: datetime) -> tuple[datetime, datetime, datetime, datetime, dict]:
    """Split the available coverage into baseline and window (window = most recent half, >= 1 day)."""
    first = datetime.fromtimestamp(float(arrivals["arrival_ts"].min()), NY_TZ)
    span_h = (now - first).total_seconds() / 3600
    if span_h < MIN_BASELINE_HOURS:
        return first, now, first, first, {"span_hours": round(span_h, 1), "mode": "window_only"}
    half = timedelta(hours=max(24.0, span_h / 2))
    ws = now - half
    return ws, now, first, ws, {"span_hours": round(span_h, 1), "mode": "split"}


def coverage_from_runs(runs: list[dict]) -> list[tuple[float, float]]:
    """Polling intervals recorded by collect runs (per feed first/last poll)."""
    out = []
    for r in runs:
        if r.get("kind") != "collect":
            continue
        for f in r.get("per_feed", []) or []:
            try:
                a, b = float(f["first_ts"]), float(f["last_ts"])
            except (KeyError, TypeError, ValueError):
                continue
            if b > a:
                out.append((a, b))
    return out


def analyze_targets(store: Store, static: StaticGTFS, targets: dict, ridership: pd.DataFrame | None,
                    incidents: pd.DataFrame | None, weather_daily: pd.DataFrame | None, now: datetime,
                    coverage: list[tuple[float, float]] | None = None) -> list[dict]:
    reports = []
    for t in targets["targets"]:
        entry = {"id": t["id"], "label": t.get("label", t["station"]), "station": t["station"],
                 "direction": t["direction"], "routes": t["routes"], "status": "ok"}
        try:
            req0 = AnalysisRequest(station=t["station"], direction=t["direction"], routes=list(t["routes"]))
            from mta_delay_insights.analysis.engine import resolve_target
            target = resolve_target(static, req0)
            arr = store.arrivals(target["stop_id"], None, now.timestamp(), target["routes"])
            if arr.empty:
                raise ValueError("no arrivals collected yet for this platform")
            ws, we, bs, be, cov = _windows(arr, now)
            rp = None
            if ridership is not None and not ridership.empty and "target_id" in ridership:
                rp = ridership[ridership["target_id"] == t["id"]]
                rp = rp if not rp.empty else None
            req = AnalysisRequest(station=t["station"], direction=t["direction"], routes=list(t["routes"]),
                                  window_start=ws, window_end=we, baseline_start=bs, baseline_end=be,
                                  route_share_of_entries=targets.get("route_share_of_entries", 0.5))
            report = analyze_station(store, static, req, ridership_profile=rp, incidents=incidents, weather_daily=weather_daily,
                                     coverage_intervals=coverage)
            d = report.to_dict()
            d.update({"id": t["id"], "label": entry["label"], "coverage_windows": cov, "status": "ok",
                      "arrival_count": int(len(arr)), "first_arrival": datetime.fromtimestamp(float(arr["arrival_ts"].min()), NY_TZ).isoformat(),
                      "last_arrival": datetime.fromtimestamp(float(arr["arrival_ts"].max()), NY_TZ).isoformat()})
            entry.update({"severity": d["severity"], "verdict": d["verdict"], "focus_hours": d["focus_hours"],
                          "top_cause": d["ranked_causes"][0] if d["ranked_causes"] else None,
                          "top_location": d["ranked_locations"][0] if d["ranked_locations"] else None,
                          "coverage": d["coverage"], "coverage_windows": cov, "arrival_count": int(len(arr)),
                          "impact": d["impact"], "stop_id": target["stop_id"], "station_name": target["station_name"]})
            reports.append((entry, d))
        except Exception as exc:
            logging.warning("target %s: %s", t["id"], exc)
            entry.update({"status": "insufficient_data", "message": str(exc)[:300]})
            reports.append((entry, {"id": t["id"], "label": entry["label"], "status": "insufficient_data", "message": str(exc)[:300]}))
    return reports


def current_alerts(alerts: pd.DataFrame, now_ts: float, lookback_h: float = 24) -> list[dict]:
    if alerts is None or alerts.empty:
        return []
    a = alerts.copy()
    a["end_ts"] = a["active_end"].fillna(a["updated_at"].fillna(a["active_start"]) + 3 * 3600)
    a = a[a["end_ts"] >= now_ts - lookback_h * 3600]
    a["_kind"] = [alert_kind(t, hd) for t, hd in zip(a["alert_type"], a["header"])]
    a["_active"] = (a["active_start"].fillna(0) <= now_ts) & (a["end_ts"] >= now_ts)
    # Current unplanned delays first, then active planned changes, then everything else by recency.
    a = a.sort_values(["_active", "_kind", "active_start"], ascending=[False, True, False],
                      key=lambda c: c.map({"delay": 0, "planned": 1, "notice": 2}) if c.name == "_kind" else c)
    out = []
    for r in a.itertuples(index=False):
        start = _n(r.active_start) or 0.0
        out.append({"alert_id": r.alert_id, "alert_type": r.alert_type, "planned": bool(r.planned),
                    "kind": alert_kind(r.alert_type, r.header),
                    "cause_category": r.cause_category, "active_start": _n(r.active_start), "active_end": _n(r.active_end),
                    "updated_at": _n(r.updated_at), "routes": list(r.routes), "header": r.header,
                    "active_now": bool(start <= now_ts <= float(r.end_ts))})
    return out[:400]


def _n(v):
    try:
        f = float(v)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


def fit_models(store: Store, static: StaticGTFS, targets: dict, now: datetime) -> tuple[dict, list[dict]]:
    """Fit the look-back propagation model per target; returns ({id: model}, resolved targets)."""
    models, resolved = {}, []
    for t in targets["targets"]:
        try:
            rr = lib.resolve_target(static, {**t, "upstream_stops": targets.get("upstream_stops", 6)})
            r = {**t, **{k: rr[k] for k in ("station_id", "station_name", "stop_id", "routes")}, "upstream": rr["upstream"], "terminals": rr["terminals"]}
            resolved.append(r)
            models[t["id"]] = fit_model(store, static, r, now.timestamp())
        except Exception as exc:
            logging.warning("model fit %s failed: %s", t.get("id"), exc)
    return models, resolved


def fit_journeys(store: Store, static: StaticGTFS, targets: dict, alerts: pd.DataFrame, context: dict,
                 now: datetime, data_dir: Path | None) -> tuple[list, dict]:
    """Fit the journey-time model per configured journey; export the training table to the data dir."""
    specs, models, tables = [], {}, []
    try:
        specs = resolve_journeys(static, targets)
    except Exception as exc:
        logging.warning("journeys not resolved: %s", exc)
        return [], {}
    events = context.get("events")
    for spec in specs:
        try:
            m, table = fit_journey(store, static, spec, alerts, context.get("weather_daily"), events, now.timestamp())
            models[spec.id] = m
            if not table.empty:
                tables.append(table)
        except Exception as exc:
            logging.warning("journey %s fit failed: %s", spec.id, exc)
    if tables and data_dir is not None:
        try:
            lib.save_context(data_dir, "journey_training", pd.concat(tables, ignore_index=True))
        except Exception as exc:
            logging.warning("training export failed: %s", exc)
    return specs, models


TRAIN_ROWS_CAP = 1_500_000
MAX_TRAIN_ARRIVALS = 700_000


def _cap_arrivals(arr: pd.DataFrame, cap: int, seed: int = 1) -> pd.DataFrame:
    """Keep whole trips (so momentum, leaders and segment state stay consistent), sampling when over the cap."""
    if len(arr) <= cap:
        return arr
    keys = arr["trip_key"].unique()
    rng = np.random.default_rng(seed)
    share = cap / len(arr)
    keep = set(rng.choice(keys, size=int(len(keys) * share), replace=False))
    return arr[arr["trip_key"].isin(keep)]
from mta_delay_insights.realtime.client_export import LINE_VIEW_ROUTES  # noqa: E402


def build_line_views(store: Store, static: StaticGTFS, context: dict, live: dict | None, out_data: Path, now: datetime) -> list[dict]:
    """Per route/direction: Marey snapshot (last 2 h actual + live + schedule) and the deviation grid (last days)."""
    net = context.get("_network_arrivals")
    core = store.arrivals()
    arr = pd.concat([core, net], ignore_index=True).drop_duplicates(["trip_key", "stop_id"]) if net is not None and not net.empty else core
    if arr.empty:
        return []
    (out_data / "lines").mkdir(parents=True, exist_ok=True)
    live_trains = []
    if live is not None and context.get("_feed_bytes"):
        try:
            from mta_delay_insights.realtime.status import live_trains as _lt
            live_trains = _lt(context["_feed_bytes"], static, now.timestamp())
        except Exception as exc:
            logging.warning("live trains for line view failed: %s", exc)
    ts = now.timestamp()
    recent = arr[arr["arrival_ts"] >= ts - 3 * 3600]
    index = []
    present = set(arr["route_id"].astype(str).unique())
    for route in [r for r in LINE_VIEW_ROUTES if r in present]:
        for direction in ("N", "S"):
            try:
                snap = line_view.line_snapshot(recent, static, route, direction, ts, live_trains)
                grid = line_view.deviation_grid(arr, static, route, direction)
                if not snap["stops"] or (not snap["actual"] and not snap["live"] and not grid.get("n_trips")):
                    continue
                (out_data / "lines" / f"{route}_{direction}.json").write_text(json.dumps({"snapshot": snap, "deviation": grid, "generated_at": now.isoformat()}, default=str))
                index.append({"route": route, "direction": direction, "n_actual": len(snap["actual"]), "n_live": len(snap["live"]), "n_trips": grid.get("n_trips", 0),
                              "worst": grid.get("worst_stops", [])[:1]})
            except Exception as exc:
                logging.warning("line view %s%s failed: %s", route, direction, exc)
    return index


def train_learned(store: Store, static: StaticGTFS, alerts: pd.DataFrame, context: dict, extra_arrivals: pd.DataFrame | None,
                  eta_samples: pd.DataFrame | None, out_models: Path) -> ArrivalModel:
    """Build training rows from the core store plus network-wide arrivals, fit, evaluate, publish model + card."""
    frames = [store.arrivals()]
    if extra_arrivals is not None and not extra_arrivals.empty:
        frames.append(extra_arrivals)
    arr = pd.concat(frames, ignore_index=True).drop_duplicates(["trip_key", "stop_id"])
    n_all = len(arr)
    arr = _cap_arrivals(arr, MAX_TRAIN_ARRIVALS)
    logging.info("training arrivals: %d of %d", len(arr), n_all)
    rows = build_training_rows(arr, static, alerts, context.get("weather_daily"), context.get("events"), eta_samples,
                               nws_df=context.get("nws_alerts"), climatology=context.get("_climatology"))
    if len(rows) > TRAIN_ROWS_CAP:
        rows = rows.sample(TRAIN_ROWS_CAP, random_state=1).sort_values("t")
    model = train_arrival_model(rows)
    model.card["n_rows"] = int(len(rows)); model.card["n_arrivals"] = int(len(arr))
    try:
        model.save(out_models / "arrival.joblib")
    except Exception as exc:
        logging.warning("model save failed: %s", exc)
    (out_models / "arrival.card.json").write_text(json.dumps(model.card, default=str, indent=1))
    logging.info("learned model: %s", {k: model.card.get(k) for k in ("status", "n_train", "n_test")})
    return model


def live_snapshot(static: StaticGTFS, resolved: list[dict], models: dict, alerts: pd.DataFrame, now: datetime,
                  feed_bytes: dict[str, bytes] | None, journeys: list | None = None, journey_models: dict | None = None,
                  context: dict | None = None, learned: ArrivalModel | None = None, store: Store | None = None) -> dict | None:
    if not feed_bytes:
        return None
    try:
        ctx = context or {}
        return build_live(feed_bytes, alerts, static, resolved, models, now.timestamp(), source="build-snapshot",
                          journeys=journeys, journey_models=journey_models, weather_daily=ctx.get("weather_daily"), events_df=ctx.get("events"),
                          learned=learned, store=store, nws_df=ctx.get("nws_alerts"), climatology=ctx.get("_climatology"))
    except Exception as exc:
        logging.warning("live snapshot failed: %s", exc)
        return None


def build(data_dir: Path, site_src: Path, out: Path, static: StaticGTFS, targets: dict, store: Store,
          alerts: pd.DataFrame, context: dict[str, pd.DataFrame | None], runs: list[dict], now: datetime,
          mode: str, feed_bytes: dict[str, bytes] | None = None) -> dict:
    out_data = out / "data"
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(site_src, out)
    (out_data / "reports").mkdir(parents=True, exist_ok=True)
    (out_data / "models").mkdir(parents=True, exist_ok=True)
    models, resolved = fit_models(store, static, targets, now)
    for tid, m in models.items():
        (out_data / "models" / f"{tid}.json").write_text(json.dumps(m.to_dict(), default=str))
    try:
        from mta_delay_insights.sources import alerts_archive as _aa
        arch0 = context.get("alerts_archive")
        context["_climatology"] = _aa.climatology(_aa.events(arch0)) if arch0 is not None and not arch0.empty else None
    except Exception as exc:
        logging.warning("climatology (for features) failed: %s", exc); context["_climatology"] = None
    specs, jmodels = fit_journeys(store, static, targets, alerts, context, now, data_dir if mode == "live" else None)
    for jid, m in jmodels.items():
        (out_data / "models" / f"journey_{jid}.json").write_text(json.dumps(m.to_dict(), default=str))
    try:
        learned = train_learned(store, static, alerts, context, context.get("_network_arrivals"), context.get("_eta_samples"), out_data / "models")
    except Exception as exc:
        logging.warning("learned model failed: %s", exc)
        learned = ArrivalModel()
        (out_data / "models" / "arrival.card.json").write_text(json.dumps({"status": "error", "error": str(exc)[:300]}))
    hz = context.get("_holds")
    if hz is not None and not hz.empty:
        # the snapshot's "holds in the last hour" reads the store; the build's store has no dwells of its own
        recent = hz[hz["stopped_from_ts"] >= now.timestamp() - 2 * 3600]
        if not recent.empty:
            store.insert_dwells(recent)
    live = live_snapshot(static, resolved, models, alerts, now, feed_bytes, specs, jmodels, context, learned, store)
    try:
        from mta_delay_insights.realtime.client_export import export_client_schedule
        _, feeds_all, _ = lib.stops_and_feeds(static, targets)
        feed_urls, demo_now = None, None
        if mode == "synthetic" and feed_bytes:
            # self-contained preview: the browser-side live mode replays the recorded snapshot at its own time
            (out_data / "feeds").mkdir(exist_ok=True)
            for k, data in feed_bytes.items():
                (out_data / "feeds" / f"{k}.pb").write_bytes(data)
            feed_urls, demo_now, feeds_all = {k: f"feeds/{k}.pb" for k in feed_bytes}, now.timestamp(), sorted(feed_bytes)
        export_client_schedule(static, resolved, specs, out_data, now, feeds_all, feed_urls=feed_urls, demo_now=demo_now, extra_routes=LINE_VIEW_ROUTES)
        from mta_delay_insights.realtime.client_export import export_client_geometry
        cs_keys = list(json.loads((out_data / "client_schedule.json").read_text()).get("lines", {}).keys())
        export_client_geometry(static, cs_keys, out_data, now)
    except Exception as exc:
        logging.warning("client schedule export failed: %s", exc)
    routes_out = {"routes": [], "transfers": []}
    if specs:
        try:
            arr_min = store.arrivals()["arrival_ts"].min() if not store.arrivals().empty else now.timestamp()
            r_start = max(float(arr_min), now.timestamp() - ROUTE_WINDOW_DAYS * 86400)
            routes_out = analyze_routes(store, static, specs, r_start, now.timestamp(),
                                        coverage=coverage_from_runs(runs) if mode == "live" else None)
        except Exception as exc:
            logging.warning("route analysis failed: %s", exc)
            routes_out = {"routes": [], "transfers": [], "error": str(exc)[:200]}
    (out_data / "routes.json").write_text(json.dumps(routes_out, default=str))
    clim = dict(context.get("_climatology") or {"n_events": 0})
    clim["generated_at"] = now.isoformat()
    (out_data / "climatology.json").write_text(json.dumps(clim, default=str))
    lines_index = build_line_views(store, static, context, live, out_data, now)
    trust = {"n": 0}
    try:
        es = context.get("_eta_samples")
        if es is not None and not es.empty:
            allarr = pd.concat([store.arrivals(), context.get("_network_arrivals", pd.DataFrame())], ignore_index=True)
            trust = line_view.eta_trust(es, allarr)
    except Exception as exc:
        logging.warning("eta trust failed: %s", exc)
    (out_data / "eta_trust.json").write_text(json.dumps(trust, default=str))
    try:
        from mta_delay_insights.analysis.event_study import event_study
        es_arr = pd.concat([store.arrivals(), context.get("_network_arrivals", pd.DataFrame())], ignore_index=True).drop_duplicates(["trip_key", "stop_id"])
        es = event_study(es_arr, alerts, static)
    except Exception as exc:
        logging.warning("event study failed: %s", exc)
        es = {"n_alerts": 0, "error": str(exc)[:200]}
    (out_data / "event_study.json").write_text(json.dumps(es, default=str))
    try:
        from mta_delay_insights.analysis.scorecard import scorecard
        sc_arr = pd.concat([store.arrivals(), context.get("_network_arrivals", pd.DataFrame())], ignore_index=True).drop_duplicates(["trip_key", "stop_id"])
        sc = scorecard(sc_arr, static)
        sc["generated_at"] = now.isoformat()
    except Exception as exc:
        logging.warning("scorecard failed: %s", exc)
        sc = {"rows": [], "error": str(exc)[:200]}
    (out_data / "scorecard.json").write_text(json.dumps(sc, default=str))
    try:
        from mta_delay_insights.analysis.dwell import dwell_profile, dwell_ridership_elasticity
        dw = dwell_profile(context.get("_dwells"), static)
        stop_to_target = {t["stop_id"]: t["id"] for t in resolved}
        dw["elasticity"] = dwell_ridership_elasticity(dw, context.get("ridership_profile"), stop_to_target)
        dw["generated_at"] = now.isoformat()
    except Exception as exc:
        logging.warning("dwell analysis failed: %s", exc)
        dw = {"n": 0, "stops": [], "error": str(exc)[:200]}
    (out_data / "dwell.json").write_text(json.dumps(dw, default=str))
    try:
        from mta_delay_insights.analysis.holds import hold_summary
        hs = hold_summary(context.get("_holds"), alerts, static)
    except Exception as exc:
        logging.warning("hold analysis failed: %s", exc); hs = {"n": 0, "error": str(exc)[:200]}
    (out_data / "holds.json").write_text(json.dumps(hs, default=str))
    # the client prediction engine: tables the browser and the phone apply to the live feeds
    try:
        from mta_delay_insights.realtime.client_model import fit_client_model
        cm_arr = pd.concat([store.arrivals(), context.get("_network_arrivals", pd.DataFrame())], ignore_index=True).drop_duplicates(["trip_key", "stop_id"])
        client_model = fit_client_model(context.get("_eta_samples"), cm_arr, context.get("_holds"), static, now.isoformat())
    except Exception as exc:
        logging.warning("client model failed: %s", exc)
        from mta_delay_insights.realtime.client_model import fit_client_model
        client_model = fit_client_model(None, None, None, None, now.isoformat()) | {"error": str(exc)[:200]}
    (out_data / "client_model.json").write_text(json.dumps(client_model, default=str))
    try:
        from mta_delay_insights.analysis.segments import segment_profile
        sp = segment_profile(context.get("_segment_runs"), static)
    except Exception as exc:
        logging.warning("segment analysis failed: %s", exc); sp = {"n": 0, "error": str(exc)[:200]}
    (out_data / "segments.json").write_text(json.dumps(sp, default=str))
    try:
        from mta_delay_insights.analysis.train_runs import terminal_recovery
        tr_arr = pd.concat([store.arrivals(), context.get("_network_arrivals", pd.DataFrame())], ignore_index=True).drop_duplicates(["trip_key", "stop_id"])
        trr = terminal_recovery(tr_arr, static)
        trr["generated_at"] = now.isoformat()
    except Exception as exc:
        logging.warning("terminal recovery failed: %s", exc)
        trr = {"n_pairs": 0, "error": str(exc)[:200]}
    (out_data / "train_runs.json").write_text(json.dumps(trr, default=str))
    if live is not None:
        (out_data / "live.json").write_text(json.dumps(live, default=str))
    coverage = coverage_from_runs(runs) if mode == "live" else []
    for day in (context.get("_backfill_manifest") or {}).get("days", []):
        try:
            d0 = datetime.fromisoformat(day).replace(tzinfo=NY_TZ)
            coverage.append((d0.timestamp(), d0.timestamp() + 86400))
        except ValueError:
            continue
    reports = analyze_targets(store, static, targets, context.get("ridership_profile"), context.get("trains_delayed"),
                              context.get("weather_daily"), now, coverage)
    for entry, d in reports:
        (out_data / "reports" / f"{entry['id']}.json").write_text(json.dumps(d, default=str))
    try:
        lines = line_insights(context.get("trains_delayed"), context.get("customer_journey"), context.get("major_incidents"))
    except Exception as exc:  # a schema surprise in one Open Data table must not block publishing
        logging.warning("line insights failed: %s", exc)
        lines = {"months": [], "lines": {}, "system": {}, "categories": [], "error": str(exc)[:300]}
    (out_data / "lines.json").write_text(json.dumps(lines, default=str))
    (out_data / "alerts.json").write_text(json.dumps({"generated_at": now.isoformat(), "alerts": current_alerts(alerts, now.timestamp())}, default=str))
    # Collection status: arrivals per day and run log.
    arr_all = store.arrivals()
    per_day = {}
    if not arr_all.empty:
        per_day = arr_all["arrival_ts"].map(lib.local_date).value_counts().sort_index().to_dict()
    net = context.get("_network_arrivals")
    es_samples = context.get("_eta_samples")
    fe = context.get("_forecast_eval")
    try:
        from mta_delay_insights.realtime.evaluate import summarize_forecast_eval
        (out_data / "forecast_eval.json").write_text(json.dumps(summarize_forecast_eval(fe), default=str))
    except Exception as exc:
        logging.warning("forecast evaluation summary failed: %s", exc)
    datasets = {"core_arrivals": int(len(arr_all)),
                "forecast_eval": int(len(fe)) if fe is not None else 0,
                "network_arrivals": int(len(net)) if net is not None else 0,
                "network_days": int(pd.to_datetime(net["arrival_ts"], unit="s").dt.date.nunique()) if net is not None and not net.empty else 0,
                "network_sources": (net["source"].value_counts().to_dict() if net is not None and not net.empty and "source" in net else {}),
                "eta_samples": int(len(es_samples)) if es_samples is not None else 0,
                "dwells": int(len(context.get("_dwells"))) if context.get("_dwells") is not None else 0,
                "holds": int(len(context.get("_holds"))) if context.get("_holds") is not None else 0,
                "client_model": {"eta_samples": client_model["eta_calibration"].get("n", 0), "routes_calibrated": len(client_model["eta_calibration"].get("by_route", {})),
                                 "holds": client_model["hold_survival"].get("n_holds", 0), "carry_pairs": client_model["lateness_carry"].get("n", 0),
                                 "routes_carry": len(client_model["lateness_carry"].get("by_route", {}))},
                "segment_runs": int(len(context.get("_segment_runs"))) if context.get("_segment_runs") is not None else 0,
                "alerts_archive_rows": int(len(context.get("alerts_archive"))) if context.get("alerts_archive") is not None else 0,
                "events_rows": int(len(context.get("events"))) if context.get("events") is not None else 0,
                "backfill_days": (context.get("_backfill_manifest") or {}).get("n_days", 0)}
    status = {"generated_at": now.isoformat(), "mode": mode, "version": __version__,
              "arrivals_total": int(len(arr_all)), "arrivals_per_day": per_day,
              "days_with_data": len(per_day), "runs": runs[-60:],
              "context": {k: (int(len(v)) if v is not None and hasattr(v, "__len__") else 0) for k, v in context.items() if not k.startswith("_")},
              "datasets": datasets, "gtfs": static.summary()}
    (out_data / "status.json").write_text(json.dumps(status, default=str))
    index = {"generated_at": now.isoformat(), "mode": mode, "targets": [e for e, _ in reports], "live": live is not None,
             "lines_view": lines_index, "eta_trust_n": trust.get("n", 0),
             "learned_model": {k: learned.card.get(k) for k in ("status", "n_train", "n_test", "trained_at", "n_rows")} | (
                 {"mae_model": (learned.card.get("evaluation") or {}).get("mae_model"), "mae_schedule": (learned.card.get("evaluation") or {}).get("mae_schedule"),
                  "mae_feed": (learned.card.get("evaluation") or {}).get("mae_feed"), "coverage": (learned.card.get("evaluation") or {}).get("coverage_p10_p90")}),
             "models": {tid: {"n_arrivals": m.n_arrivals, "n_days": m.n_days} for tid, m in models.items()},
             "journeys": [{"id": sp.id, "label": sp.label, "legs": [l.as_dict() for l in sp.legs],
                           "n_samples": jmodels[sp.id].n_samples if sp.id in jmodels else 0} for sp in specs],
             "climatology": {"n_events": clim.get("n_events", 0), "weeks": clim.get("weeks"),
                             "top_routes": [(x["route"], round(x["per_week"], 2)) for x in clim.get("by_route", [])[:5]]},
             "routes": [{"id": r["id"], "label": r["label"], "status": r["status"], "n_findings": len(r["findings"]),
                         "top": next((f["text"] for f in r["findings"] if f["severity"] in ("high", "medium")), None),
                         "dominant": r["decomposition"].get("dominant")} for r in routes_out.get("routes", [])],
             "sources": as_records(), "lines_available": sorted(lines.get("lines", {}).keys()),
             "alerts_active": sum(1 for a in json.loads((out_data / "alerts.json").read_text())["alerts"] if a["active_now"]),
             "status": {k: status[k] for k in ("arrivals_total", "days_with_data")}}
    (out_data / "index.json").write_text(json.dumps(index, default=str))
    try:
        from .digest import build_digest
        build_digest(out_data, now)
    except Exception as exc:
        logging.warning("digest failed: %s", exc)
    (out / ".nojekyll").write_text("")
    return index


def build_from_data(args) -> dict:
    data_dir = Path(args.data_dir)
    targets = lib.load_targets(args.targets)
    static = lib.load_static(args.gtfs)
    store = Store(":memory:")
    stops_of_interest, _, _ = lib.stops_and_feeds(static, targets)
    network = lib.load_network_arrivals(data_dir, days=NETWORK_TRAIN_DAYS)
    backfill_days: list[str] = []
    mpath = data_dir / "arrivals_all" / "backfill_manifest.json"
    if mpath.exists():
        try:
            mf = json.loads(mpath.read_text()); backfill_days = [d for d, v in mf.get("days", {}).items() if v.get("rows")]
        except Exception:
            pass
    # Network-wide history at the stops of interest feeds the station reports, journeys and transfers
    # straight away (weeks of baseline from the backfill); our own rows replace archive rows on conflict.
    if network is not None and not network.empty:
        extra = network[network["stop_id"].isin(stops_of_interest)]
        if not extra.empty:
            store.insert_arrivals(extra)
            logging.info("network history at stops of interest: %d rows", len(extra))
    arrivals = lib.load_arrivals(data_dir)
    store.insert_arrivals(arrivals)
    alerts = lib.load_alerts(data_dir)
    if not alerts.empty:
        store.upsert_alerts(alerts, seen_ts=time.time())
    context = {k: lib.load_context(data_dir, k) for k in
               ("trains_delayed", "delay_incidents", "major_incidents", "customer_journey", "ridership_profile", "weather_daily")}
    context["events"] = lib.load_events(data_dir)
    context["alerts_archive"] = lib.load_alerts_archive(data_dir)
    context["nws_alerts"] = lib.load_context(data_dir, "nws_alerts")
    context["_network_arrivals"] = network
    context["_eta_samples"] = lib.load_eta_samples(data_dir, days=45)
    context["_dwells"] = lib.load_dwells(data_dir, days=45)
    context["_holds"] = lib.load_holds(data_dir, days=30)
    context["_segment_runs"] = lib.load_segment_runs(data_dir, days=14)
    context["_forecast_eval"] = lib.load_forecast_eval(data_dir, days=14)
    context["_backfill_manifest"] = {"n_days": len(backfill_days), "days": backfill_days}
    feed_bytes = {}
    context["_feed_bytes"] = feed_bytes
    if not args.no_feeds:
        from mta_delay_insights import config
        from mta_delay_insights.sources import alerts as alerts_src
        from mta_delay_insights.sources import gtfs_realtime as rt
        _, feeds, _ = lib.stops_and_feeds(static, targets)
        for key in feeds:
            try:
                feed_bytes[key] = rt.fetch_feed_bytes(config.rt_feed_url(key))
            except Exception as exc:
                logging.warning("feed %s unavailable at build time: %s", key, exc)
        try:
            fresh = alerts_src.alerts_frame(alerts_src.fetch_alerts_json())
            if not fresh.empty:
                alerts = pd.concat([alerts, fresh], ignore_index=True).drop_duplicates(["alert_id", "active_start"], keep="last")
        except Exception as exc:
            logging.warning("alerts unavailable at build time: %s", exc)
    return build(data_dir, Path(args.site_src), Path(args.out), static, targets, store, alerts, context,
                 lib.load_runs(data_dir), datetime.now(NY_TZ), "live", feed_bytes)


def build_synthetic(args) -> dict:
    """Offline preview: the mini corridor with an injected signal failure + missing trips."""
    from mta_delay_insights import synthetic
    sc = synthetic.make_scenario("mixed", 10, 10)
    tmp = Path(tempfile.mkdtemp(prefix="site_syn_"))
    static = StaticGTFS.load(synthetic.build_mini_gtfs(tmp / "gtfs", sc.start - timedelta(days=1), sc.end + timedelta(days=1)))
    sim = synthetic.simulate(static, sc)
    store = Store(":memory:")
    store.insert_arrivals(sim.arrivals)
    store.upsert_alerts(sim.alerts, seen_ts=0)
    rp = sim.ridership_profile.copy(); rp["target_id"] = "grand-central-n"
    inc = sim.incidents.copy()
    # Fabricate a 24-month history so the lines view has something to show.
    frames = []
    for i in range(24):
        m = pd.Timestamp(sc.start.year, sc.start.month, 1) - pd.DateOffset(months=23 - i)
        f = inc[inc["month"] == inc["month"].min()].copy()
        f["month"] = m
        f["delays"] = (f["delays"] * (0.8 + 0.4 * ((i * 7) % 10) / 10)).round()
        frames.append(f)
    td = pd.concat(frames, ignore_index=True)
    cj = pd.DataFrame([{"month": m, "line": l, "period": "peak", "additional_platform_time": 1.0 + 0.5 * ((i + k) % 5) / 5,
                        "additional_train_time": 0.6 + 0.3 * ((i * 3 + k) % 4) / 4, "customer_journey_time_performance": 0.85 - 0.03 * ((i + 2 * k) % 4) / 4}
                       for i, m in enumerate(sorted(td["month"].unique())) for k, l in enumerate(["6", "4", "A", "L", "F", "N"])])
    targets = {"targets": [{"id": "grand-central-n", "station": "Grand Central", "direction": "N", "routes": ["6", "4"],
                            "label": "Grand Central-42 St, uptown 6/4 (synthetic)"}],
               "journeys": [{"id": "union-sq-to-59st", "label": "14 St-Union Sq → 59 St (6 local, transfer to 4 at Grand Central)",
                             "legs": [{"from": {"station": "14 St-Union Sq", "direction": "N", "routes": ["6"]}, "to": {"station": "Grand Central", "direction": "N", "routes": ["6"]}},
                                      {"transfer_min": 1, "from": {"station": "Grand Central", "direction": "N", "routes": ["4"]}, "to": {"station": "59 St", "direction": "N", "routes": ["4"]}}]},
                            {"id": "bleecker-to-59st-direct", "label": "Bleecker St → 59 St (6 local, no transfer)",
                             "legs": [{"from": {"station": "Bleecker St", "direction": "N", "routes": ["6"]}, "to": {"station": "59 St", "direction": "N", "routes": ["6"]}}]},
                            {"id": "bleecker-to-59st-via-4", "label": "Bleecker St → 59 St (6 to Grand Central, then 4 express)",
                             "legs": [{"from": {"station": "Bleecker St", "direction": "N", "routes": ["6"]}, "to": {"station": "Grand Central", "direction": "N", "routes": ["6"]}},
                                      {"transfer_min": 1, "from": {"station": "Grand Central", "direction": "N", "routes": ["4"]}, "to": {"station": "59 St", "direction": "N", "routes": ["4"]}}]}],
               "upstream_stops": 6, "route_share_of_entries": 0.5}
    now = datetime.combine(sc.end, datetime.min.time(), NY_TZ)
    from mta_delay_insights.sources import events as events_src
    ev = events_src.holiday_events(sc.start, sc.end)
    context = {"trains_delayed": td, "customer_journey": cj, "major_incidents": None, "ridership_profile": rp,
               "weather_daily": sim.weather_daily, "delay_incidents": None, "events": ev}
    runs = [{"ts": now.timestamp() - 3600 * i, "iso": (now - timedelta(hours=i)).isoformat(), "kind": "collect",
             "polls": 100, "arrivals": 900, "errors": 0} for i in range(5)]
    # Live snapshot: a weekday morning of the last simulated day, with one train held 6 minutes.
    last = sc.end - timedelta(days=1)
    while last.weekday() >= 5:
        last -= timedelta(days=1)
    snap_now = datetime.combine(last, datetime.min.time(), NY_TZ) + timedelta(hours=8, minutes=20)
    feed_key, _, data = synthetic.to_rt_snapshots(sim.arrivals, snap_now.timestamp(), snap_now.timestamp(), poll_interval=30)[0]
    from mta_delay_insights.sources import gtfs_realtime as rt
    msg = rt.parse_feed(data)
    held = None
    for ent in msg.entity:
        tu = ent.trip_update if ent.HasField("trip_update") else None
        if tu and tu.trip.route_id == "6" and any(x.stop_id == "631N" for x in tu.stop_time_update) and tu.stop_time_update[0].stop_id != "631N":
            held = held or tu.trip.trip_id
            if tu.trip.trip_id == held:
                for x in tu.stop_time_update:
                    x.arrival.time += 360; x.departure.time += 360
    for ent in msg.entity:
        if held and ent.HasField("vehicle") and ent.vehicle.trip.trip_id == held:
            # its reported position: stopped at the next stop for 7 minutes (the feed's ETAs above are still optimistic)
            held_tu = next(e.trip_update for e in msg.entity if e.HasField("trip_update") and e.trip_update.trip.trip_id == held)
            from google.transit import gtfs_realtime_pb2 as _pb
            ent.vehicle.current_status = _pb.VehiclePosition.VehicleStopStatus.Value("STOPPED_AT")
            ent.vehicle.stop_id = held_tu.stop_time_update[0].stop_id
            ent.vehicle.timestamp = int(snap_now.timestamp() - 420)
    context["_feed_bytes"] = {feed_key: data}
    alerts_live = sim.alerts.copy()
    unplanned_idx = alerts_live.index[~alerts_live["planned"]] if not alerts_live.empty else []
    if len(unplanned_idx):
        alerts_live.loc[unplanned_idx[-1], ["active_start", "active_end", "updated_at"]] = [snap_now.timestamp() - 600, snap_now.timestamp() + 1200, snap_now.timestamp()]
    return build(tmp, Path(args.site_src), Path(args.out), static, targets, store, alerts_live, context, runs, snap_now, "synthetic",
                 {feed_key: msg.SerializeToString()})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(lib.DEFAULT_DATA_DIR))
    ap.add_argument("--targets", default=None)
    ap.add_argument("--gtfs", default="data/gtfs_subway.zip")
    ap.add_argument("--site-src", default=str(lib.ROOT / "site"))
    ap.add_argument("--out", default=str(lib.ROOT / "_site"))
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--no-feeds", action="store_true", help="skip fetching the realtime feeds for the live snapshot")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    index = build_synthetic(args) if args.synthetic else build_from_data(args)
    print(json.dumps({"targets": [(t["id"], t["status"]) for t in index["targets"]], "out": args.out}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
