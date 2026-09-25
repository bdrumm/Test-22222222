"""Poll the MTA realtime feeds for a bounded time and persist observed arrivals + alerts.

    python -m pipeline.collect --minutes 50 --data-dir data-branch
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

from mta_delay_insights.collect.collector import Collector
from mta_delay_insights.sources import alerts as alerts_src
from mta_delay_insights.storage.db import Store

from . import lib


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(lib.DEFAULT_DATA_DIR))
    ap.add_argument("--targets", default=None)
    ap.add_argument("--gtfs", default="data/gtfs_subway.zip")
    ap.add_argument("--minutes", type=float, default=50)
    ap.add_argument("--interval", type=float, default=30)
    ap.add_argument("--live-every", type=float, default=0, help="seconds between live snapshots (0 = off)")
    ap.add_argument("--live-out", default="live.json", help="where to write the live snapshot")
    ap.add_argument("--models-dir", default="gh-pages-branch/data/models", help="fitted propagation models (from the published site)")
    ap.add_argument("--live-publish", default="", help="script to run after each live snapshot (e.g. pipeline/live_publish.sh)")
    ap.add_argument("--all-stops", action="store_true", help="track every stop of the polled feeds (also targets.json collect.all_stops)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)

    data_dir = Path(args.data_dir)
    targets = lib.load_targets(args.targets)
    static = lib.load_static(args.gtfs)
    stops, feeds, resolved = lib.stops_and_feeds(static, targets)
    logging.info("targets=%s feeds=%s stops=%d", [t["id"] for t in resolved], feeds, len(stops))

    store = Store(":memory:")
    copts = lib.collect_options(targets)
    all_stops = bool(copts.get("all_stops")) or args.all_stops
    if all_stops:
        # every feed's stops are tracked; ETA samples and dwell rows stay limited to the stops of interest
        feeds = sorted(set(feeds) | set(copts.get("extra_feeds", [])))
    logging.info("collection mode: %s", "all stops" if all_stops else f"{len(stops)} stops of interest")
    col = Collector(store, feeds, None if all_stops else stops, poll_interval_sec=args.interval,
                    alerts_fetcher=lambda: alerts_src.fetch_alerts_json(), alerts_every_n_polls=4,
                    sample_stops=stops)
    if args.live_every > 0:
        from mta_delay_insights.realtime import build_live
        from mta_delay_insights.realtime.propagation import PropagationModel
        from mta_delay_insights.realtime.journey import JourneyModel, resolve_journeys
        models = {}
        try:
            journeys = resolve_journeys(static, targets)
        except Exception as exc:
            logging.warning("journeys not resolved: %s", exc); journeys = []
        jmodels = {}
        for j in journeys:
            jf = Path(args.models_dir) / f"journey_{j.id}.json"
            if jf.exists():
                try:
                    jmodels[j.id] = JourneyModel.from_dict(json.loads(jf.read_text()))
                except Exception as exc:
                    logging.warning("journey model %s unreadable: %s", jf, exc)
        weather_daily = lib.load_context(data_dir, "weather_daily")
        events_df = lib.load_events(data_dir)
        nws_df = lib.load_context(data_dir, "nws_alerts")
        clim_path = Path(args.models_dir).parent / "climatology.json"
        climatology = None
        if clim_path.exists():
            try:
                climatology = json.loads(clim_path.read_text())
            except Exception:
                climatology = None
        learned = None
        lf = Path(args.models_dir) / "arrival.joblib"
        if lf.exists():
            try:
                from mta_delay_insights.models import ArrivalModel
                learned = ArrivalModel.load(lf)
                logging.info("learned model loaded (n_train=%s)", learned.n_train)
            except Exception as exc:
                logging.warning("learned model unreadable: %s", exc)
        for t in resolved:
            rr = lib.resolve_target(static, t); t["upstream"], t["terminals"] = rr["upstream"], rr["terminals"]
            mf = Path(args.models_dir) / f"{t['id']}.json"
            if mf.exists():
                try:
                    models[t["id"]] = PropagationModel.from_dict(json.loads(mf.read_text()))
                except Exception as exc:
                    logging.warning("model %s unreadable: %s", mf, exc)
        state = {"last": 0.0, "n": 0, "proj": []}

        def on_poll(c: Collector, now: float) -> None:
            if now - state["last"] < args.live_every or not c.last_feed_bytes:
                return
            alerts_df = alerts_src.alerts_frame(c.last_alerts) if c.last_alerts else c.store.alerts()
            live = build_live(dict(c.last_feed_bytes), alerts_df, static, resolved, models, now, source="pipeline-snapshot",
                              journeys=journeys, journey_models=jmodels, weather_daily=weather_daily, events_df=events_df,
                              learned=learned, store=store, nws_df=nws_df, climatology=climatology)
            Path(args.live_out).write_text(json.dumps(live, default=str))
            try:
                from mta_delay_insights.realtime.evaluate import projections_from_live
                state["proj"].extend(projections_from_live(live))
            except Exception as exc:
                logging.warning("projection record failed: %s", exc)
            state["last"], state["n"] = now, state["n"] + 1
            logging.info("live snapshot %d: %d trains, %s", state["n"], live["trains_total"], live["summary"])
            if args.live_publish:
                subprocess.run([args.live_publish, args.live_out], check=False)
        col.on_poll = on_poll
    t0 = time.time()
    summaries = col.run(duration_sec=args.minutes * 60)
    flushed = col.flush(time.time())
    arrivals = store.arrivals()
    alerts = store.alerts()
    if all_stops:
        core = arrivals[arrivals["stop_id"].isin(stops)]
        written_all = lib.save_network_arrivals(data_dir, arrivals)
        lib.prune_daily(data_dir, "arrivals_all", int(copts.get("network_retention_days", 21)))
        logging.info("network arrivals: %d rows in %d day files", len(arrivals), len(written_all))
        arrivals = core
    written = lib.save_arrivals(data_dir, arrivals)
    n_samples = lib.save_eta_samples(data_dir, store.eta_samples())
    n_dwells = lib.save_dwells(data_dir, store.dwells(stops))
    from mta_delay_insights.collect.dwells import HOLD_SEC
    all_dwells = store.dwells()
    n_holds = lib.save_holds(data_dir, all_dwells[all_dwells["dwell_sec"] >= HOLD_SEC]) if not all_dwells.empty else {}
    n_runs = lib.save_segment_runs(data_dir, col.take_segment_runs())
    logging.info("eta samples: %s, dwells: %s, holds: %s, segment runs: %s", n_samples, n_dwells, n_holds, n_runs)
    n_alerts = lib.save_alerts(data_dir, alerts, time.time())
    n_eval = 0
    if args.live_every > 0 and state["proj"]:
        try:
            import pandas as pd
            from mta_delay_insights.realtime.evaluate import evaluate_projections
            ev = evaluate_projections(pd.DataFrame(state["proj"]), arrivals)
            n_eval = int(len(ev))
            lib.save_forecast_eval(data_dir, ev)
            logging.info("forecast evaluation: %d of %d projections matched to an observed arrival", n_eval, len(state["proj"]))
        except Exception as exc:
            logging.warning("forecast evaluation failed: %s", exc)
    stats = store.snapshot_stats()
    record = {
        "kind": "collect", "feeds": feeds, "polls": len(summaries), "errors": int(sum(s["errors"] for s in summaries)),
        "arrivals": int(len(arrivals)), "flushed": int(flushed), "alerts": int(n_alerts), "forecast_eval": n_eval,
        "duration_sec": round(time.time() - t0), "written": written,
        "per_feed": stats.to_dict(orient="records") if not stats.empty else [],
    }
    lib.append_run(data_dir, record)
    logging.info("done: %s", record)
    return 0


if __name__ == "__main__":
    sys.exit(main())
