"""Poll the MTA realtime feeds for a bounded time and persist observed arrivals + alerts.

    python -m pipeline.collect --minutes 50 --data-dir data-branch
"""
from __future__ import annotations

import argparse
import logging
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
