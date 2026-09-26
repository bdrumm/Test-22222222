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

from .. import config
from ..sources.gtfs_static import NY_TZ, StaticGTFS, rt_trip_stem, rt_trip_suffix
from .simulate import DEFAULT_HOLD_EXTRA_SEC, MIN_HEADWAY_SEC
from .status import HOLD_SEC, STALL_SLACK_SEC

# lines the Line view offers (and the browser overlay needs); every subway route with a line-group feed
LINE_VIEW_ROUTES = ["1", "2", "3", "4", "5", "6", "7", "A", "C", "E", "B", "D", "F", "M", "G", "J", "Z", "L", "N", "Q", "R", "W", "SI"]


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
            try:
                dist = [None if x is None else round(x) for x in static.segment_lengths(r, d)]
            except Exception:
                dist = []
            lines[f"{r}_{d}"] = {"stops": seq, "names": [static.stop_name(s) for s in seq], "run_sec": run, "dist_m": dist}
            line_sched[f"{r}_{d}"] = sorted(sched, key=lambda x: x[2])
    transfers = station_transfers(static, lines)
    all_routes = {x for v in config.SUBWAY_FEED_ROUTES.values() for x in v}
    target_feeds = sorted(feeds or {config.feed_for_route(r) for r in routes_needed if r in all_routes})
    feed_keys = sorted(set(target_feeds) | (set() if feed_urls else set(config.SUBWAY_FEED_ROUTES)))
    (out_data / "client_lines.json").write_text(json.dumps({"generated_at": now.isoformat(), "service_date": sd.isoformat(), "lines": line_sched}))
    out = {"generated_at": now.isoformat(), "service_date": sd.isoformat(), "targets": targets, "lines": lines,
           "feeds": {k: (feed_urls or {}).get(k) or config.rt_feed_url(k) for k in feed_keys}, "target_feeds": target_feeds,
           "route_feeds": {r: k for k, rs in config.SUBWAY_FEED_ROUTES.items() for r in rs},
           "transfers": transfers,
           "journeys": [{"id": j.id, "label": j.label, "legs": [{"from_stop": l.from_stop, "to_stop": l.to_stop, "routes": [str(r) for r in l.routes],
                                                                  "from_name": l.from_name, "to_name": l.to_name, "transfer_min": l.transfer_min} for l in j.legs]}
                        for j in (journeys or [])],
           "alerts_url": config.rt_feed_url("subway_alerts_json"),
           "demo_now": demo_now,
           "constants": {"hold_sec": HOLD_SEC, "stall_slack_sec": STALL_SLACK_SEC, "past_slack_sec": 90, "gap_ratio": config.DEFAULTS.gap_ratio, "bunching_ratio": config.DEFAULTS.bunching_ratio,
                         "hold_extra_sec": DEFAULT_HOLD_EXTRA_SEC, "min_headway_sec": MIN_HEADWAY_SEC}}
    (out_data / "client_schedule.json").write_text(json.dumps(out))
    return out


def station_transfers(static: StaticGTFS, lines: dict, default_sec: int = 120) -> dict[str, list[dict]]:
    """For every stop of every exported line: the other lines' stops in the same station complex.

    A complex is the set of parent stations joined by GTFS transfers.txt (plus the stop's own parent); the
    minimum transfer time comes from transfers.txt when the pair is listed. The browser uses this to offer
    destinations reachable with one change and to plan the connection."""
    parent = {}
    for key, ln in lines.items():
        for sid in ln["stops"]:
            parent[sid] = static.parent_of(sid)
    # union-find over parents
    root: dict[str, str] = {}
    def find(x):
        root.setdefault(x, x)
        while root[x] != x:
            root[x] = root[root[x]]; x = root[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            root[ra] = rb
    min_sec: dict[tuple[str, str], int] = {}
    tr = getattr(static, "transfers", None)
    if tr is not None and not tr.empty and {"from_stop_id", "to_stop_id"} <= set(tr.columns):
        for r in tr.itertuples(index=False):
            a, b = str(r.from_stop_id), str(r.to_stop_id)
            if a in root or b in root or a in parent.values() or b in parent.values():
                union(a, b)
            mt = getattr(r, "min_transfer_time", None)
            try:
                if mt is not None and mt == mt:
                    min_sec[(a, b)] = int(float(mt)); min_sec.setdefault((b, a), int(float(mt)))
            except (TypeError, ValueError):
                pass
    by_complex: dict[str, list[tuple[str, str]]] = {}
    for key, ln in lines.items():
        for sid in ln["stops"]:
            by_complex.setdefault(find(parent[sid]), []).append((key, sid))
    out: dict[str, list[dict]] = {}
    for key, ln in lines.items():
        route = key.split("_")[0]
        for sid in ln["stops"]:
            opts = []
            for key2, sid2 in by_complex.get(find(parent[sid]), []):
                if key2 == key or key2.split("_")[0] == route:
                    continue
                p1, p2 = parent[sid], parent[sid2]
                sec = min_sec.get((p1, p2), min_sec.get((p1, p1), default_sec) if p1 == p2 else default_sec)
                opts.append({"line": key2, "stop": sid2, "min_sec": int(sec)})
            if opts:
                out[sid] = sorted(opts, key=lambda o: o["line"])
    return out


def export_client_geometry(static, keys: list[str], out_data: Path, now=None) -> dict:
    """Stop coordinates and the simplified track per exported line ("F_N"), for map views; a separate file
    because it is only needed on demand."""
    lines = {}
    for k in keys:
        route, _, direction = k.rpartition("_")
        try:
            seq = static.canonical_stop_sequence(route, direction)
        except Exception:
            seq = []
        if not seq:
            continue
        try:
            shape = static.pattern_shape(route, direction)
        except Exception:
            shape = []
        lines[k] = {"coords": static.stop_coords(seq), "shape": shape}
    out = {"generated_at": now.isoformat() if now is not None else None, "lines": lines}
    (out_data / "client_geometry.json").write_text(json.dumps(out, separators=(",", ":")))
    return out
