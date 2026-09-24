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
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)

    data_dir = Path(args.data_dir)
    targets = lib.load_targets(args.targets)
    static = lib.load_static(args.gtfs)
    stops, feeds, resolved = lib.stops_and_feeds(static, targets)
    logging.info("targets=%s feeds=%s stops=%d", [t["id"] for t in resolved], feeds, len(stops))

    store = Store(":memory:")
    col = Collector(store, feeds, stops, poll_interval_sec=args.interval,
                    alerts_fetcher=lambda: alerts_src.fetch_alerts_json(), alerts_every_n_polls=4)
    if args.live_every > 0:
        from mta_delay_insights.realtime import build_live
        from mta_delay_insights.realtime.propagation import PropagationModel
        models = {}
        for t in resolved:
            rr = lib.resolve_target(static, t); t["upstream"], t["terminals"] = rr["upstream"], rr["terminals"]
            mf = Path(args.models_dir) / f"{t['id']}.json"
            if mf.exists():
                try:
                    models[t["id"]] = PropagationModel.from_dict(json.loads(mf.read_text()))
                except Exception as exc:
                    logging.warning("model %s unreadable: %s", mf, exc)
        state = {"last": 0.0, "n": 0}

        def on_poll(c: Collector, now: float) -> None:
            if now - state["last"] < args.live_every or not c.last_feed_bytes:
                return
            alerts_df = alerts_src.alerts_frame(c.last_alerts) if c.last_alerts else c.store.alerts()
            live = build_live(dict(c.last_feed_bytes), alerts_df, static, resolved, models, now, source="pipeline-snapshot")
            Path(args.live_out).write_text(json.dumps(live, default=str))
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
    written = lib.save_arrivals(data_dir, arrivals)
    n_alerts = lib.save_alerts(data_dir, alerts, time.time())
    stats = store.snapshot_stats()
    record = {
        "kind": "collect", "feeds": feeds, "polls": len(summaries), "errors": int(sum(s["errors"] for s in summaries)),
        "arrivals": int(len(arrivals)), "flushed": int(flushed), "alerts": int(n_alerts),
        "duration_sec": round(time.time() - t0), "written": written,
        "per_feed": stats.to_dict(orient="records") if not stats.empty else [],
    }
    lib.append_run(data_dir, record)
    logging.info("done: %s", record)
    return 0


if __name__ == "__main__":
    sys.exit(main())
