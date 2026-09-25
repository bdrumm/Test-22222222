"""Backfill network-wide arrival history from subwaydata.nyc into the data branch.

    python -m pipeline.backfill --data-dir data-branch --days 21

Day files already present in arrivals_all/ are skipped, so the hourly run only
fetches the newest missing day (about 1.4 MB). A manifest records what came from
the archive so provenance stays clear.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from mta_delay_insights.sources import subwaydata
from mta_delay_insights.sources.gtfs_static import NY_TZ

from . import lib


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(lib.DEFAULT_DATA_DIR))
    ap.add_argument("--days", type=int, default=21)
    ap.add_argument("--max-fetch", type=int, default=8, help="days to fetch per run (bandwidth courtesy)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    data_dir = Path(args.data_dir)
    mpath = data_dir / "arrivals_all" / "backfill_manifest.json"
    manifest = json.loads(mpath.read_text()) if mpath.exists() else {"days": {}}
    have = set(manifest["days"].keys())
    days = subwaydata.missing_days(have, args.days)[: args.max_fetch]
    logging.info("backfill: %d days to fetch (%s)", len(days), [d.isoformat() for d in days])
    record = {"kind": "backfill", "iso": datetime.now(NY_TZ).isoformat(), "fetched": [], "failed": {}}
    for d in days:
        res = subwaydata.backfill_days([d])[d]
        if res.empty:
            record["failed"][d.isoformat()] = res.attrs.get("error", "empty")
            manifest["days"][d.isoformat()] = {"rows": 0, "error": res.attrs.get("error", "empty")}
            continue
        written = lib.save_network_arrivals(data_dir, res)
        manifest["days"][d.isoformat()] = {"rows": int(len(res)), "written": written}
        record["fetched"].append({"day": d.isoformat(), "rows": int(len(res))})
        logging.info("%s: %d arrivals", d, len(res))
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(manifest, indent=1))
    lib.prune_daily(data_dir, "arrivals_all", int(lib.collect_options(lib.load_targets(None)).get("network_retention_days", 21)))
    lib.append_run(data_dir, record)
    return 0


if __name__ == "__main__":
    sys.exit(main())
