"""Command line interface: ``mta-insights <command>``."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from . import config, synthetic
from .analysis.engine import AnalysisRequest, analyze_station
from .collect.collector import Collector
from .sources import alerts as alerts_src
from .sources import open_data, weather
from .sources.gtfs_static import NY_TZ, StaticGTFS
from .sources.registry import format_table
from .storage.db import Store


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=NY_TZ)


def _load_static(path: str | None) -> StaticGTFS:
    if path and Path(path).exists():
        return StaticGTFS.load(path)
    print(f"static GTFS not found at {path!r}; downloading subway feed...", file=sys.stderr)
    dest = path or "data/gtfs_subway.zip"
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    return StaticGTFS.download("subway", dest)


# --------------------------------------------------------------------------- #
def cmd_sources(args):
    print(format_table())


def cmd_static(args):
    g = _load_static(args.gtfs)
    if args.action == "summary":
        print(json.dumps(g.summary(), indent=2))
    elif args.action == "stations":
        print(g.find_stations(args.query or "").to_string(index=False))
    elif args.action == "platform":
        sid = g.platform_for(args.station, args.direction)
        print(f"platform {sid}: routes {g.routes_serving(sid)}")
        for r in g.routes_serving(sid):
            print(f"  {r}: upstream {g.upstream_stops(r, args.direction, sid, 6)} terminal {g.terminal_stop(r, args.direction)}")


def _stops_for_collection(g: StaticGTFS, args) -> set[str] | None:
    if args.all_stops:
        return None
    stops: set[str] = set()
    if args.stops:
        stops |= set(args.stops.split(","))
    if args.station:
        sid = g.platform_for(g.find_stations(args.station).iloc[0]["stop_id"], args.direction)
        stops.add(sid)
        routes = args.routes.split(",") if args.routes else g.routes_serving(sid)
        for r in routes:
            stops |= set(g.upstream_stops(r, args.direction, sid, args.upstream))
            t = g.terminal_stop(r, args.direction)
            if t:
                stops.add(t)
    return stops or None


def cmd_collect(args):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    g = _load_static(args.gtfs)
    stops = _stops_for_collection(g, args)
    feeds = args.feeds.split(",") if args.feeds else sorted({config.feed_for_route(r) for r in (args.routes or "").split(",") if r} or ["1234567S"])
    store = Store(args.db)
    col = Collector(store, feeds, stops, store_predictions=args.store_predictions, poll_interval_sec=args.interval,
                    alerts_fetcher=(lambda: alerts_src.fetch_alerts_json()) if args.alerts else None, raw_dir=args.raw_dir)
    print(f"collecting feeds {feeds} every {args.interval}s; stops of interest: {len(stops) if stops else 'all'}", file=sys.stderr)
    summaries = col.run(duration_sec=args.duration, max_polls=args.max_polls)
    print(json.dumps({"polls": len(summaries), "arrivals": sum(s["arrivals"] for s in summaries),
                      "errors": sum(s["errors"] for s in summaries)}, indent=2))


def cmd_replay(args):
    store = Store(args.db)
    snaps = Collector.load_raw_dir(args.raw_dir)
    feeds = sorted({s[0] for s in snaps})
    col = Collector(store, feeds, None, poll_interval_sec=args.interval)
    n = col.replay(snaps)
    print(json.dumps({"snapshots": len(snaps), "arrivals": n}, indent=2))


def cmd_context(args):
    store = Store(args.db)
    client = open_data.SocrataClient()
    routes = args.routes.split(",") if args.routes else None
    what = set(args.what.split(","))
    if {"all", "incidents"} & what:
        td = client.trains_delayed(routes, args.since)
        store.put_frame("trains_delayed", td)
        inc = client.delay_causing_incidents(routes, args.since)
        store.put_frame("delay_incidents", inc)
        print(f"trains_delayed rows={len(td)} incidents rows={len(inc)}")
    if {"all", "journey"} & what:
        cj = client.customer_journey(routes, args.since)
        store.put_frame("customer_journey", cj)
        print(f"customer_journey rows={len(cj)}")
    if {"all", "ridership"} & what and args.complex_id:
        h = client.hourly_ridership([args.complex_id], args.start, args.end)
        store.put_frame("ridership_hourly", h)
        store.put_frame("ridership_profile", open_data.ridership_profile(h))
        print(f"ridership rows={len(h)}")
    if {"all", "weather"} & what:
        w = weather.fetch_hourly(args.start, args.end)
        store.put_frame("weather_hourly", w)
        store.put_frame("weather_daily", weather.daily_summary(w))
        print(f"weather hours={len(w)}")


def cmd_analyze(args):
    g = _load_static(args.gtfs)
    store = Store(args.db)
    req = AnalysisRequest(station=args.station, direction=args.direction,
                          routes=args.routes.split(",") if args.routes else None,
                          window_start=_parse_dt(args.window_start), window_end=_parse_dt(args.window_end),
                          baseline_start=_parse_dt(args.baseline_start), baseline_end=_parse_dt(args.baseline_end),
                          hours=[int(h) for h in args.hours.split(",")] if args.hours else None,
                          route_share_of_entries=args.route_share)
    report = analyze_station(store, g, req)
    _emit(report, args.out, args.json)


def _emit(report, out_md: str | None, out_json: str | None):
    md = report.to_markdown()
    if out_md:
        Path(out_md).parent.mkdir(parents=True, exist_ok=True)
        Path(out_md).write_text(md)
        print(f"wrote {out_md}", file=sys.stderr)
    if out_json:
        Path(out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(out_json).write_text(report.to_json())
        print(f"wrote {out_json}", file=sys.stderr)
    if not out_md:
        print(md)


def _resolved_targets(g, args):
    """Resolve --station/--direction/--routes or a targets.json into resolved target dicts."""
    from pipeline import lib
    if args.targets:
        return lib.stops_and_feeds(g, lib.load_targets(args.targets))
    t = {"id": args.station.lower().replace(" ", "-") + "-" + args.direction.lower(), "station": args.station,
         "direction": args.direction, "routes": args.routes.split(",") if args.routes else None}
    r = lib.resolve_target(g, {**t, "routes": t["routes"] or g.routes_serving(g.platform_for(g.find_stations(args.station).iloc[0]["stop_id"], args.direction))})
    resolved = [{**t, **{k: r[k] for k in ("station_id", "station_name", "stop_id", "routes")}, "upstream": r["upstream"], "terminals": r["terminals"]}]
    feeds = sorted({config.feed_for_route(x) for x in r["routes"]})
    return set(), feeds, resolved


def cmd_live(args):
    """One-shot realtime snapshot: holistic status + downstream forecasts for the targets."""
    from mta_delay_insights.realtime import build_live, fit_model
    from mta_delay_insights.sources import gtfs_realtime as rt
    g = _load_static(args.gtfs)
    _, feeds, resolved = _resolved_targets(g, args)
    for t in resolved:
        if "upstream" not in t:
            from pipeline import lib
            rr = lib.resolve_target(g, t); t["upstream"], t["terminals"] = rr["upstream"], rr["terminals"]
    models = {}
    if args.db and Path(args.db).exists():
        store = Store(args.db)
        for t in resolved:
            models[t["id"]] = fit_model(store, g, t)
    feed_bytes = {k: rt.fetch_feed_bytes(config.rt_feed_url(k)) for k in feeds}
    alerts_df = alerts_src.alerts_frame(alerts_src.fetch_alerts_json())
    live = build_live(feed_bytes, alerts_df, g, resolved, models)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(live, default=str, indent=1))
        print(f"wrote {args.json}", file=sys.stderr)
    print(_live_text(live))


def _live_text(live: dict) -> str:
    L = [f"Live status {live['generated_at']} - {live['trains_total']} trains ({live['trains_matched']} matched to schedule); "
         f"routes good/degraded/disrupted: {live['summary']['good']}/{live['summary']['degraded']}/{live['summary']['disrupted']}"]
    for r in live["routes"]:
        gap = f", largest gap {r['max_gap_sec'] / 60:.0f} min at {r['max_gap_stop_name']}" if r.get("max_gap_sec") else ""
        late = f", median lateness {r['median_lateness_sec'] / 60:.1f} min" if r.get("median_lateness_sec") is not None else ""
        L.append(f"  {r['route_id']}{r['direction']}: {r['status']:9s} {r['trains']} trains{late}{gap}"
                 + (f", {r['unplanned_alerts']} alert(s)" if r["unplanned_alerts"] else ""))
    for s in live["stations"]:
        L.append(f"\n{s['label']} [{s['status']}]")
        for a in s["arrivals"][:6]:
            lat = f" ({a['model_lateness_sec'] / 60:+.0f} min vs schedule)" if a.get("model_lateness_sec") is not None else ""
            L.append(f"  {a['route_id']} in {a['minutes_away']:.0f} min (model {datetime.fromtimestamp(a['model_eta_ts'], NY_TZ):%H:%M}){lat}"
                     + (" GAP" if a.get("gap") else ""))
        for e in s["effects"]:
            L.append(f"  ! {e['text']}")
    return "\n".join(L)


def cmd_serve(args):
    from mta_delay_insights.realtime.server import serve
    from pipeline import lib
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from mta_delay_insights.realtime.journey import resolve_journeys
    g = _load_static(args.gtfs)
    targets = lib.load_targets(args.targets)
    stops, feeds, resolved = lib.stops_and_feeds(g, targets)
    copts = lib.collect_options(targets)
    if args.all_feeds or copts.get("all_stops"):
        feeds = sorted(set(feeds) | set(copts.get("extra_feeds", [])) | set(config.SUBWAY_FEED_ROUTES))
    for t in resolved:
        rr = lib.resolve_target(g, t); t["upstream"], t["terminals"] = rr["upstream"], rr["terminals"]
    store = None
    if args.db:
        Path(args.db).parent.mkdir(parents=True, exist_ok=True)
        store = Store(args.db)
    try:
        journeys = resolve_journeys(g, targets)
    except Exception as exc:
        logging.warning("journeys not resolved: %s", exc); journeys = []
    learned_path = Path(args.db).with_name("arrival.joblib") if args.db else None
    serve(args.site, g, resolved, feeds, store, journeys, port=args.port, interval=args.interval,
          collect=not args.no_collect, sample_stops=stops, learned_path=learned_path)


def cmd_demo(args):
    sc = synthetic.make_scenario(args.scenario, args.days, args.days)
    tmp = Path(args.work_dir or tempfile.mkdtemp(prefix="mta_demo_"))
    gtfs_dir = synthetic.build_mini_gtfs(tmp / "gtfs", sc.start - timedelta(days=1), sc.end + timedelta(days=1))
    g = StaticGTFS.load(gtfs_dir)
    sim = synthetic.simulate(g, sc)
    store = Store(tmp / "demo.sqlite") if args.work_dir else Store(":memory:")
    store.insert_arrivals(sim.arrivals)
    store.upsert_alerts(sim.alerts, seen_ts=0)
    store.put_frame("ridership_profile", sim.ridership_profile)
    store.put_frame("trains_delayed", sim.incidents)
    store.put_frame("weather_daily", sim.weather_daily)
    req = AnalysisRequest(station="Grand Central", direction="N", routes=list(sc.routes),
                          window_start=datetime.combine(sc.window_start, datetime.min.time(), NY_TZ),
                          window_end=datetime.combine(sc.end, datetime.min.time(), NY_TZ),
                          baseline_start=datetime.combine(sc.start, datetime.min.time(), NY_TZ),
                          baseline_end=datetime.combine(sc.window_start, datetime.min.time(), NY_TZ),
                          hours=[int(h) for h in args.hours.split(",")] if args.hours else None)
    report = analyze_station(store, g, req)
    print(f"scenario={sc.name} truth={json.dumps(sim.truth['issues'])}", file=sys.stderr)
    _emit(report, args.out, args.json)


# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mta-insights", description="Diagnose train arrival problems at a station.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sources", help="list all data feeds the framework uses").set_defaults(func=cmd_sources)

    s = sub.add_parser("static", help="inspect the static GTFS schedule")
    s.add_argument("action", choices=["summary", "stations", "platform"])
    s.add_argument("--gtfs", default="data/gtfs_subway.zip")
    s.add_argument("--query")
    s.add_argument("--station")
    s.add_argument("--direction", default="N")
    s.set_defaults(func=cmd_static)

    c = sub.add_parser("collect", help="poll GTFS-RT feeds and store observed arrivals")
    c.add_argument("--db", default="data/mta.sqlite")
    c.add_argument("--gtfs", default="data/gtfs_subway.zip")
    c.add_argument("--feeds", help="comma list of feed keys (default: feeds for --routes, else 1234567S)")
    c.add_argument("--station", help="station name; stops of interest = platform + upstream + terminal")
    c.add_argument("--direction", default="N")
    c.add_argument("--routes", help="comma list of routes")
    c.add_argument("--upstream", type=int, default=6)
    c.add_argument("--stops", help="explicit comma list of stop ids")
    c.add_argument("--all-stops", action="store_true")
    c.add_argument("--interval", type=float, default=30)
    c.add_argument("--duration", type=float, help="seconds to run")
    c.add_argument("--max-polls", type=int)
    c.add_argument("--alerts", action="store_true", help="also poll the alerts feed")
    c.add_argument("--store-predictions", action="store_true")
    c.add_argument("--raw-dir", help="archive raw protobuf snapshots here")
    c.set_defaults(func=cmd_collect)

    r = sub.add_parser("replay", help="rebuild arrivals from archived raw snapshots")
    r.add_argument("--raw-dir", required=True)
    r.add_argument("--db", default="data/mta.sqlite")
    r.add_argument("--interval", type=float, default=30)
    r.set_defaults(func=cmd_replay)

    x = sub.add_parser("context", help="pull Open Data / weather context into the store")
    x.add_argument("--db", default="data/mta.sqlite")
    x.add_argument("--what", default="all", help="all|incidents|journey|ridership|weather (comma list)")
    x.add_argument("--routes")
    x.add_argument("--since", help="YYYY-MM-DD for monthly datasets")
    x.add_argument("--complex-id", help="station complex id for hourly ridership")
    x.add_argument("--start", default=(datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d"))
    x.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    x.set_defaults(func=cmd_context)

    a = sub.add_parser("analyze", help="analyse arrivals at a station for a set of routes")
    a.add_argument("--db", default="data/mta.sqlite")
    a.add_argument("--gtfs", default="data/gtfs_subway.zip")
    a.add_argument("--station", required=True)
    a.add_argument("--direction", default="N")
    a.add_argument("--routes")
    a.add_argument("--window-start")
    a.add_argument("--window-end")
    a.add_argument("--baseline-start")
    a.add_argument("--baseline-end")
    a.add_argument("--hours", help="comma list of local hours to focus on")
    a.add_argument("--route-share", type=float, default=0.5, help="share of station entries boarding the analysed routes/direction")
    a.add_argument("--out", help="markdown report path")
    a.add_argument("--json", help="json report path")
    a.set_defaults(func=cmd_analyze)

    lv = sub.add_parser("live", help="holistic realtime status + downstream forecasts for the targets (one shot)")
    lv.add_argument("--gtfs", default="data/gtfs_subway.zip")
    lv.add_argument("--db", default="data/mta.sqlite", help="arrival history used to fit the look-back model")
    lv.add_argument("--targets", help="targets.json (default: --station/--direction/--routes)")
    lv.add_argument("--station")
    lv.add_argument("--direction", default="N")
    lv.add_argument("--routes")
    lv.add_argument("--json", help="write the snapshot here")
    lv.set_defaults(func=cmd_live)

    sv = sub.add_parser("serve", help="serve the site locally with live.json refreshed from the feeds")
    sv.add_argument("--site", default="_site")
    sv.add_argument("--gtfs", default="data/gtfs_subway.zip")
    sv.add_argument("--db", default="data/mta.sqlite")
    sv.add_argument("--targets", default=None)
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--interval", type=float, default=30)
    sv.add_argument("--all-feeds", action="store_true", help="poll every subway feed (default when targets.json sets collect.all_stops)")
    sv.add_argument("--no-collect", action="store_true", help="serve only; do not persist arrivals into --db")
    sv.set_defaults(func=cmd_serve)

    d = sub.add_parser("demo", help="run a synthetic scenario end to end")
    d.add_argument("--scenario", default="signal", choices=sorted(synthetic.SCENARIOS))
    d.add_argument("--days", type=int, default=14)
    d.add_argument("--hours")
    d.add_argument("--work-dir")
    d.add_argument("--out")
    d.add_argument("--json")
    d.set_defaults(func=cmd_demo)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
