"""Pull NY Open Data performance datasets, ridership for the target complexes, and weather.

    python -m pipeline.context --data-dir data-branch
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from mta_delay_insights.sources import open_data, weather

from . import lib


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(lib.DEFAULT_DATA_DIR))
    ap.add_argument("--targets", default=None)
    ap.add_argument("--gtfs", default="data/gtfs_subway.zip")
    ap.add_argument("--since", default=(date.today() - timedelta(days=800)).strftime("%Y-%m-01"))
    ap.add_argument("--days", type=int, default=60, help="days of hourly ridership and weather")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    data_dir = Path(args.data_dir)
    targets = lib.load_targets(args.targets)
    client = open_data.SocrataClient()
    record: dict = {"kind": "context", "ok": [], "failed": {}}

    def step(name, fn):
        try:
            df = fn()
            if df is None or df.empty:
                raise ValueError("empty result")
            lib.save_context(data_dir, name, df)
            record["ok"].append(f"{name}:{len(df)}")
            logging.info("%s rows=%d", name, len(df))
            return df
        except Exception as exc:  # keep going; one dataset failing must not block the rest
            record["failed"][name] = str(exc)[:600]
            logging.warning("%s failed: %s", name, exc)
            return None

    step("trains_delayed", lambda: client.trains_delayed(None, args.since))
    step("delay_incidents", lambda: client.delay_causing_incidents(None, args.since))
    step("major_incidents", lambda: client.major_incidents(None, args.since))
    step("customer_journey", lambda: client.customer_journey(None, args.since))
    stations = step("stations", client.stations)

    # Hourly ridership for the target station complexes (needs the stations table for complex ids).
    if stations is not None:
        static = lib.load_static(args.gtfs)
        _, _, resolved = lib.stops_and_feeds(static, targets)
        stations["gtfs_stop_id"] = stations["gtfs_stop_id"].astype(str)
        complex_ids = {}
        for t in resolved:
            hit = stations[stations["gtfs_stop_id"] == str(t["station_id"])]
            if not hit.empty:
                complex_ids[t["id"]] = str(hit.iloc[0]["complex_id"])
        record["complex_ids"] = complex_ids
        if complex_ids:
            end = date.today() + timedelta(days=1)
            start = end - timedelta(days=args.days)
            hourly = step("ridership_hourly", lambda: client.hourly_ridership(sorted(set(complex_ids.values())),
                                                                              start.isoformat(), end.isoformat()))
            if hourly is not None:
                profiles = []
                for tid, cid in complex_ids.items():
                    prof = open_data.ridership_profile(hourly[hourly["station_complex_id"].astype(str) == cid])
                    prof["target_id"] = tid
                    prof["station_complex_id"] = cid
                    profiles.append(prof)
                if profiles:
                    lib.save_context(data_dir, "ridership_profile", pd.concat(profiles, ignore_index=True))
    step("weather_daily", lambda: weather.daily_summary(weather.fetch_recent_hourly(args.days)))

    # External signals for the journey model: permitted events, venue events, news, holidays.
    from mta_delay_insights.sources import events as events_src
    frames = []
    for name, fn in (("nyc_events", lambda: events_src.fetch_nyc_permitted_events()),
                     ("ticketmaster", lambda: events_src.fetch_ticketmaster_events()),
                     ("news", lambda: events_src.fetch_news())):
        try:
            df = fn()
            if df is not None and not df.empty:
                frames.append(df)
                record["ok"].append(f"{name}:{len(df)}")
            else:
                record.setdefault("skipped", []).append(name)
        except Exception as exc:
            record["failed"][name] = str(exc)[:300]
            logging.warning("%s failed: %s", name, exc)
    frames.append(events_src.holiday_events(date.today() - timedelta(days=60), date.today() + timedelta(days=60)))
    ev = pd.concat(frames, ignore_index=True)
    ev["routes"] = ev["routes"].map(lambda rs: json.dumps(list(rs) if isinstance(rs, (list, tuple)) else []))
    old = lib.load_context(data_dir, "events")
    if old is not None and not old.empty:
        ev = pd.concat([old, ev], ignore_index=True).drop_duplicates(["source", "title", "ts_start"], keep="last")
        ev = ev[ev["ts_start"] >= (pd.Timestamp.now().timestamp() - 120 * 86400)]
    lib.save_context(data_dir, "events", ev)
    record["ok"].append(f"events:{len(ev)}")
    lib.append_run(data_dir, record)
    logging.info("done: %s", record)
    return 0


if __name__ == "__main__":
    sys.exit(main())
