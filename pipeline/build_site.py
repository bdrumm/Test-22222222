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

import pandas as pd

from mta_delay_insights import __version__
from mta_delay_insights.analysis.engine import AnalysisRequest, analyze_station
from mta_delay_insights.analysis.line_insights import line_insights
from mta_delay_insights.sources.gtfs_static import NY_TZ, StaticGTFS
from mta_delay_insights.sources.registry import as_records
from mta_delay_insights.storage.db import Store

from . import lib

MIN_BASELINE_HOURS = 36


def _windows(arrivals: pd.DataFrame, now: datetime) -> tuple[datetime, datetime, datetime, datetime, dict]:
    """Split the available coverage into baseline and window (window = most recent half, >= 1 day)."""
    first = datetime.fromtimestamp(float(arrivals["arrival_ts"].min()), NY_TZ)
    span_h = (now - first).total_seconds() / 3600
    if span_h < MIN_BASELINE_HOURS:
        return first, now, first, first, {"span_hours": round(span_h, 1), "mode": "window_only"}
    half = timedelta(hours=max(24.0, span_h / 2))
    ws = now - half
    return ws, now, first, ws, {"span_hours": round(span_h, 1), "mode": "split"}


def analyze_targets(store: Store, static: StaticGTFS, targets: dict, ridership: pd.DataFrame | None,
                    incidents: pd.DataFrame | None, weather_daily: pd.DataFrame | None, now: datetime) -> list[dict]:
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
            report = analyze_station(store, static, req, ridership_profile=rp, incidents=incidents, weather_daily=weather_daily)
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
    a = a[a["end_ts"] >= now_ts - lookback_h * 3600].sort_values("active_start", ascending=False)
    out = []
    for r in a.itertuples(index=False):
        start = _n(r.active_start) or 0.0
        out.append({"alert_id": r.alert_id, "alert_type": r.alert_type, "planned": bool(r.planned),
                    "cause_category": r.cause_category, "active_start": _n(r.active_start), "active_end": _n(r.active_end),
                    "updated_at": _n(r.updated_at), "routes": list(r.routes), "header": r.header,
                    "active_now": bool(start <= now_ts <= float(r.end_ts))})
    return out[:300]


def _n(v):
    try:
        f = float(v)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


def build(data_dir: Path, site_src: Path, out: Path, static: StaticGTFS, targets: dict, store: Store,
          alerts: pd.DataFrame, context: dict[str, pd.DataFrame | None], runs: list[dict], now: datetime,
          mode: str) -> dict:
    out_data = out / "data"
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(site_src, out)
    (out_data / "reports").mkdir(parents=True, exist_ok=True)
    reports = analyze_targets(store, static, targets, context.get("ridership_profile"), context.get("trains_delayed"),
                              context.get("weather_daily"), now)
    for entry, d in reports:
        (out_data / "reports" / f"{entry['id']}.json").write_text(json.dumps(d, default=str))
    lines = line_insights(context.get("trains_delayed"), context.get("customer_journey"), context.get("major_incidents"))
    (out_data / "lines.json").write_text(json.dumps(lines, default=str))
    (out_data / "alerts.json").write_text(json.dumps({"generated_at": now.isoformat(), "alerts": current_alerts(alerts, now.timestamp())}, default=str))
    # Collection status: arrivals per day and run log.
    arr_all = store.arrivals()
    per_day = {}
    if not arr_all.empty:
        per_day = arr_all["arrival_ts"].map(lib.local_date).value_counts().sort_index().to_dict()
    status = {"generated_at": now.isoformat(), "mode": mode, "version": __version__,
              "arrivals_total": int(len(arr_all)), "arrivals_per_day": per_day,
              "days_with_data": len(per_day), "runs": runs[-60:],
              "context": {k: (int(len(v)) if v is not None else 0) for k, v in context.items()},
              "gtfs": static.summary()}
    (out_data / "status.json").write_text(json.dumps(status, default=str))
    index = {"generated_at": now.isoformat(), "mode": mode, "targets": [e for e, _ in reports],
             "sources": as_records(), "lines_available": sorted(lines.get("lines", {}).keys()),
             "alerts_active": sum(1 for a in json.loads((out_data / "alerts.json").read_text())["alerts"] if a["active_now"]),
             "status": {k: status[k] for k in ("arrivals_total", "days_with_data")}}
    (out_data / "index.json").write_text(json.dumps(index, default=str))
    (out / ".nojekyll").write_text("")
    return index


def build_live(args) -> dict:
    data_dir = Path(args.data_dir)
    targets = lib.load_targets(args.targets)
    static = lib.load_static(args.gtfs)
    store = Store(":memory:")
    arrivals = lib.load_arrivals(data_dir)
    store.insert_arrivals(arrivals)
    alerts = lib.load_alerts(data_dir)
    if not alerts.empty:
        store.upsert_alerts(alerts, seen_ts=time.time())
    context = {k: lib.load_context(data_dir, k) for k in
               ("trains_delayed", "delay_incidents", "major_incidents", "customer_journey", "ridership_profile", "weather_daily")}
    return build(data_dir, Path(args.site_src), Path(args.out), static, targets, store, alerts, context,
                 lib.load_runs(data_dir), datetime.now(NY_TZ), "live")


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
               "upstream_stops": 6, "route_share_of_entries": 0.5}
    now = datetime.combine(sc.end, datetime.min.time(), NY_TZ)
    context = {"trains_delayed": td, "customer_journey": cj, "major_incidents": None, "ridership_profile": rp,
               "weather_daily": sim.weather_daily, "delay_incidents": None}
    runs = [{"ts": now.timestamp() - 3600 * i, "iso": (now - timedelta(hours=i)).isoformat(), "kind": "collect",
             "polls": 100, "arrivals": 900, "errors": 0} for i in range(5)]
    return build(tmp, Path(args.site_src), Path(args.out), static, targets, store, sim.alerts, context, runs, now, "synthetic")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(lib.DEFAULT_DATA_DIR))
    ap.add_argument("--targets", default=None)
    ap.add_argument("--gtfs", default="data/gtfs_subway.zip")
    ap.add_argument("--site-src", default=str(lib.ROOT / "site"))
    ap.add_argument("--out", default=str(lib.ROOT / "_site"))
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    index = build_synthetic(args) if args.synthetic else build_live(args)
    print(json.dumps({"targets": [(t["id"], t["status"]) for t in index["targets"]], "out": args.out}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
