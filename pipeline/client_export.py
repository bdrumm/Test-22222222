"""Export what the browser needs to compute a live board directly from the MTA feeds.

The GTFS-Realtime endpoint allows cross-origin requests, so the published site
can poll the feeds itself every 30 seconds. The browser has no static GTFS, so
the build ships a compact extract: today's scheduled arrivals per monitored
platform (for lateness), each line's stop sequence with scheduled running
times (for stalled-train detection), the feeds to poll, and the constants the
server uses, so both sides agree.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from mta_delay_insights import config
from mta_delay_insights.realtime.simulate import DEFAULT_HOLD_EXTRA_SEC, MIN_HEADWAY_SEC
from mta_delay_insights.realtime.status import HOLD_SEC, STALL_SLACK_SEC
from mta_delay_insights.sources.gtfs_static import NY_TZ, StaticGTFS, rt_trip_stem, rt_trip_suffix


def export_client_schedule(static: StaticGTFS, resolved: list[dict], journeys: list, out_data: Path, now: datetime,
                           feeds: list[str] | None = None, feed_urls: dict[str, str] | None = None, demo_now: float | None = None,
                           extra_routes: list[str] | None = None) -> dict:
    """``feed_urls`` overrides the feed URLs (the synthetic preview serves recorded feeds next to the site);
    ``demo_now`` pins the browser's clock to the snapshot time so a recorded feed stays readable;
    ``extra_routes`` adds lines (topology + today's schedule) beyond the ones the targets and journeys need,
    for the Line view's live overlay. Writes client_schedule.json (small, for the Live page) and
    client_lines.json (per-line schedules, loaded by the Line view only)."""
    sd = (now - timedelta(hours=3)).date()
    days = [sd, sd + timedelta(days=1)]
    targets = {}
    routes_needed: set[str] = set()
    for t in resolved:
        sched = []
        for d in days:
            ev = static.scheduled_stop_events(t["stop_id"], d, [str(r) for r in t["routes"]])
            for r in ev.itertuples(index=False):
                sched.append([rt_trip_suffix(r.trip_id), str(r.route_id), int(r.arrival_ts)])
        sched.sort(key=lambda x: x[2])
        targets[t["id"]] = {"stop_id": t["stop_id"], "station_name": t.get("station_name") or t.get("station"), "direction": t.get("direction"),
                            "routes": [str(r) for r in t["routes"]], "label": t.get("label"), "sched": sched}
        routes_needed |= {str(r) for r in t["routes"]}
    for spec in journeys or []:
        for leg in spec.legs:
            routes_needed |= {str(r) for r in leg.routes}
    lines, line_sched = {}, {}
    for r in sorted(routes_needed | {str(x) for x in (extra_routes or [])}):
        for d in ("N", "S"):
            seq = static.canonical_stop_sequence(r, d)
            if not seq:
                continue
            idx = {s: i for i, s in enumerate(seq)}
            ev = static.scheduled_stop_events(seq, sd, [r])
            run, sched = [], []
            if not ev.empty:
                tid = ev["trip_id"].value_counts().index[0]
                trip = ev[ev["trip_id"] == tid].sort_values("stop_sequence")
                at = dict(zip(trip["stop_id"], trip["arrival_ts"]))
                for a, b in zip(seq, seq[1:]):
                    run.append(int(at[b] - at[a]) if a in at and b in at and at[b] > at[a] else None)
                # every trip today: its scheduled time at the last canonical stop it serves (the browser derives
                # the time at any earlier stop from the canonical running times)
                for t_id, g in ev.sort_values("stop_sequence").groupby("trip_id", sort=False):
                    last = g.iloc[-1]
                    sched.append([rt_trip_stem(t_id), int(idx[last["stop_id"]]), int(last["arrival_ts"])])
            lines[f"{r}_{d}"] = {"stops": seq, "names": [static.stop_name(s) for s in seq], "run_sec": run}
            line_sched[f"{r}_{d}"] = sorted(sched, key=lambda x: x[2])
    all_routes = {x for v in config.SUBWAY_FEED_ROUTES.values() for x in v}
    target_feeds = sorted(feeds or {config.feed_for_route(r) for r in routes_needed if r in all_routes})
    feed_keys = sorted(set(target_feeds) | (set() if feed_urls else set(config.SUBWAY_FEED_ROUTES)))
    (out_data / "client_lines.json").write_text(json.dumps({"generated_at": now.isoformat(), "service_date": sd.isoformat(), "lines": line_sched}))
    out = {"generated_at": now.isoformat(), "service_date": sd.isoformat(), "targets": targets, "lines": lines,
           "feeds": {k: (feed_urls or {}).get(k) or config.rt_feed_url(k) for k in feed_keys}, "target_feeds": target_feeds,
           "route_feeds": {r: k for k, rs in config.SUBWAY_FEED_ROUTES.items() for r in rs},
           "alerts_url": config.rt_feed_url("subway_alerts_json"),
           "demo_now": demo_now,
           "constants": {"hold_sec": HOLD_SEC, "stall_slack_sec": STALL_SLACK_SEC, "past_slack_sec": 90, "gap_ratio": config.DEFAULTS.gap_ratio, "bunching_ratio": config.DEFAULTS.bunching_ratio,
                         "hold_extra_sec": DEFAULT_HOLD_EXTRA_SEC, "min_headway_sec": MIN_HEADWAY_SEC}}
    (out_data / "client_schedule.json").write_text(json.dumps(out))
    return out
