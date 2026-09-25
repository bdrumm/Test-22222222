"""The browser-side decoder/board (site/rt-client.js) must agree with the Python parser and position rules."""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from mta_delay_insights.sources import gtfs_realtime as rt
from mta_delay_insights.sources.gtfs_static import NY_TZ
from mta_delay_insights.realtime.status import live_trains
from pipeline import lib
from pipeline.client_export import export_client_schedule
from tests.test_positions import _snapshot

ROOT = Path(__file__).resolve().parents[1]
HARNESS = """
import { parseFeed, computeBoard, tripSuffix, lineBoard, planJourneys, journeyFeeds, trainProgress, segmentTrips, stationIndex, enumeratePaths, reachableStations, pathTrips, schedHeadwayAt } from "%s";
import { readFileSync } from "node:fs";
const schedule = JSON.parse(readFileSync(process.argv[2], "utf8"));
const lines = JSON.parse(readFileSync(process.argv[2].replace("client_schedule", "client_lines"), "utf8")).lines;
const feeds = {}; for (const [key, path] of Object.entries(JSON.parse(process.argv[3]))) feeds[key] = parseFeed(new Uint8Array(readFileSync(path)));
const now = Number(process.argv[4]);
const board = computeBoard(schedule, feeds, now);
board.line = lineBoard(schedule, lines["6_N"] || [], feeds, "6", "N", now);
board.plan = planJourneys(schedule, feeds, now); board.journey_feeds = journeyFeeds(schedule);
const L6 = schedule.lines["6_N"]; const fi = L6.stops.indexOf("635N"), ti = L6.stops.indexOf("631N");
// a second poll 150 s later in which one in-transit train has arrived: its segment run is timed from the feed timestamps
{ const fd = feeds[Object.keys(feeds)[0]]; const moving = fd.vehicles.find(v => v.status === "IN_TRANSIT_TO" && v.trip.route_id === "6" && L6.stops.indexOf(v.stop_id) > 0);
  if (moving) { const t0 = moving.timestamp; moving.status = "STOPPED_AT"; moving.timestamp = t0 + 150; const lb2 = lineBoard(schedule, lines["6_N"] || [], feeds, "6", "N", now + 150);
    const tr = lb2.trains.find(t => t.trip_id === moving.trip.trip_id); board.speed = { trip: moving.trip.trip_id, run_sec: t0 + 150 - t0, last_run: tr && tr.last_run, first_poll_segment: (board.line.trains.find(t => t.trip_id === moving.trip.trip_id) || {}).segment }; moving.status = "IN_TRANSIT_TO"; moving.timestamp = t0; } }
board.travel = { trips: segmentTrips(board.line, L6, fi, ti, now), progress0: board.line.trains.map(t => trainProgress(t, 0, L6)), progress60: board.line.trains.map(t => trainProgress(t, 60, L6)) };
const index = stationIndex(schedule); const usq = index.stationOf("635N"), s59 = index.stationOf("629N"), gc = index.stationOf("631N");
const paths = enumeratePaths(schedule, index, usq, s59, 8);
const boards = {}; for (const k of Object.keys(schedule.lines)) { const [r, d] = k.split("_"); boards[k] = lineBoard(schedule, lines[k] || [], feeds, r, d, now); }
board.paths = { n_stations: index.stations.size, usq, s59, gc, reach: Object.fromEntries(reachableStations(schedule, index, usq)), paths: paths.map(p => ({ id: p.id, label: p.label, sched_sec: p.sched_sec, legs: p.legs.map(l => ({ keys: l.keys, routes: l.routes, from: l.from, to: l.to, sched_ride_sec: l.sched_ride_sec })), transfer: p.transfer, its: pathTrips(boards, schedule, p, now, 3) })),
  hw6: schedHeadwayAt(schedule, lines, ["6_N"], "635N", now), hw46: schedHeadwayAt(schedule, lines, ["4_N", "6_N"], "631N", now) };
const parsed = Object.fromEntries(Object.entries(feeds).map(([k, f]) => [k, { timestamp: f.timestamp, trips: f.trips.length, vehicles: f.vehicles.length,
  sample: f.trips[0] && { trip_id: f.trips[0].trip.trip_id, route: f.trips[0].trip.route_id, n_stops: f.trips[0].stops.length, first: f.trips[0].stops[0] } }]));
console.log(JSON.stringify({ board, parsed, suffix: tripSuffix("AFA25GEN-1038-Sunday-00_000600_1..S03R") }));
"""


def _run_board(static, tmp_path, hold_sec=0.0):
    sim, now, feeds, held = _snapshot(static, hold_sec=hold_sec)
    targets = {"targets": [{"id": "gc", "station": "Grand Central", "direction": "N", "routes": ["6", "4"]}], "upstream_stops": 3,
               "journeys": [{"id": "usq-59", "label": "Union Sq to 59 St", "legs": [
                   {"from": {"station": "14 St-Union Sq", "direction": "N", "routes": ["6"]}, "to": {"station": "Grand Central", "direction": "N", "routes": ["6"]}},
                   {"transfer_min": 1, "from": {"station": "Grand Central", "direction": "N", "routes": ["4"]}, "to": {"station": "59 St", "direction": "N", "routes": ["4"]}}]}]}
    _, feed_keys, resolved = lib.stops_and_feeds(static, targets)
    from mta_delay_insights.realtime.journey import resolve_journeys
    export_client_schedule(static, resolved, resolve_journeys(static, targets), tmp_path, datetime.fromtimestamp(now, NY_TZ), list(feeds))
    paths = {}
    for k, data in feeds.items():
        (tmp_path / f"{k}.pb").write_bytes(data); paths[k] = str(tmp_path / f"{k}.pb")
    mod = tmp_path / "rt-client.mjs"; shutil.copy(ROOT / "site" / "rt-client.js", mod)
    (tmp_path / "harness.mjs").write_text(HARNESS % mod.as_uri())
    out = subprocess.run(["node", str(tmp_path / "harness.mjs"), str(tmp_path / "client_schedule.json"), json.dumps(paths), str(now)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout), now, feeds, held


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_decoder_matches_python_parser(static, tmp_path):
    res, now, feeds, _ = _run_board(static, tmp_path)
    key, data = next(iter(feeds.items()))
    msg = rt.parse_feed(data)
    tu = rt.trip_updates_frame(msg, key, now); vp = rt.vehicle_positions_frame(msg, key, now)
    p = res["parsed"][key]
    assert p["timestamp"] == msg.header.timestamp
    assert p["trips"] == tu["trip_id"].nunique() and p["vehicles"] == len(vp)
    first = tu[tu["trip_id"] == p["sample"]["trip_id"]].sort_values("arrival_ts")
    assert p["sample"]["n_stops"] == len(first) and p["sample"]["first"]["arrival"] == int(first["arrival_ts"].iloc[0])
    assert res["suffix"] == "000600_1..S03R"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_board_flags_the_held_train_like_the_server(static, tmp_path):
    res, now, feeds, held = _run_board(static, tmp_path, hold_sec=400)
    board = res["board"]
    tgt = board["targets"][0]
    assert tgt["stop_id"] == "631N" and tgt["arrivals"] and tgt["disturbed"]
    assert all(a["sched_ts"] is not None for a in tgt["arrivals"]), "every synthetic trip should match today's schedule"
    row = next(a for a in tgt["arrivals"] if a["trip_id"] == held)
    assert row["position"]["holding"] and row["position"]["since_sec"] >= 400 and row["corroboration"] == "feed_optimistic"
    assert row["hold_eta_ts"] >= row["eta_ts"] + 600
    # same verdicts as the Python side for that train
    server = {t.trip_id: t for t in live_trains(feeds, static, now)}[held]
    assert server.holding and server.corroboration == "feed_optimistic"
    assert abs(row["position"]["position_lateness_sec"] - server.position_lateness_sec) < 120
    assert board["summary"]["holding"] >= 1 and board["summary"]["trips"] > 5
    hw = tgt["per_route"]["6"]["sched_headway_sec"]; assert hw and 120 <= hw <= 1200


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_line_board_places_every_train(static, tmp_path):
    res, now, feeds, held = _run_board(static, tmp_path, hold_sec=400)
    lb = res["board"]["line"]
    assert lb and lb["route"] == "6" and len(lb["stops"]) >= 10
    trains = lb["trains"]
    assert len(trains) >= 5 and all(t["points"] for t in trains)
    assert trains == sorted(trains, key=lambda t: (-t["next_idx"], t["eta_ts"]))     # furthest along first
    with_pos = [t for t in trains if t["position"]]
    assert len(with_pos) >= len(trains) * 0.8 and all(t["position"]["stop_idx"] is not None for t in with_pos)
    assert sum(1 for t in trains if t["lateness_sec"] is not None) >= len(trains) * 0.8, "line schedule should give lateness"
    h = next(t for t in trains if t["trip_id"] == held)
    assert h["position"]["holding"] and h["corroboration"] == "feed_optimistic" and lb["n_holding"] >= 1


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_planner_chains_legs_from_the_feed(static, tmp_path):
    res, now, feeds, held = _run_board(static, tmp_path, hold_sec=400)
    plan = res["board"]["plan"]
    assert res["board"]["journey_feeds"] == ["1234567S"]
    j = plan["journeys"][0]
    assert j["id"] == "usq-59" and j["options"] and j["best"]
    b = j["best"]
    assert len(b["legs"]) == 2 and b["legs"][0]["route"] == "6" and b["legs"][1]["route"] == "4"
    l1, l2 = b["legs"]
    assert now <= l1["board_ts"] < l1["arrive_ts"] and l1["arrive_ts"] + 60 <= l2["board_ts"] < l2["arrive_ts"] == b["arrive_ts"]
    assert b["total_sec"] == b["arrive_ts"] - now and 300 < b["total_sec"] < 3600
    assert j["options"] == sorted(j["options"], key=lambda o: o["arrive_ts"])
    if any(l["trip_id"] == held for o in j["options"] for l in o["legs"]):
        assert any(l["holding"] for o in j["options"] for l in o["legs"] if l["trip_id"] == held)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_alert_parser_matches_python(tmp_path):
    from mta_delay_insights.sources import alerts as alerts_src
    sample = ROOT / "tests" / "fixtures" / "subway_alerts_sample.json"
    doc = json.loads(sample.read_text())
    df = alerts_src.alerts_frame(doc)
    now = float(df["active_start"].dropna().median()) if df["active_start"].notna().any() else 0.0
    active = alerts_src.alerts_active_at(df, now)
    py_kinds = {}
    for r in active.itertuples(index=False):
        py_kinds[alerts_src.alert_kind(r.alert_type, r.header)] = py_kinds.get(alerts_src.alert_kind(r.alert_type, r.header), 0) + 1
    mod = tmp_path / "rt-client.mjs"; shutil.copy(ROOT / "site" / "rt-client.js", mod)
    (tmp_path / "h.mjs").write_text(f"""
import {{ parseAlerts }} from "{mod.as_uri()}";
import {{ readFileSync }} from "node:fs";
const out = parseAlerts(JSON.parse(readFileSync({json.dumps(str(sample))}, "utf8")), {now});
const kinds = {{}}; for (const a of out) kinds[a.kind] = (kinds[a.kind] || 0) + 1;
console.log(JSON.stringify({{ n: out.length, kinds, first: out[0] }}));
""")
    res = json.loads(subprocess.run(["node", str(tmp_path / "h.mjs")], capture_output=True, text=True, check=True).stdout)
    assert res["n"] == len(active) and res["kinds"] == py_kinds
    assert res["first"]["kind"] == "delay" or not py_kinds.get("delay")
    assert res["first"]["routes"] and res["first"]["header"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_travel_helpers_dead_reckon_and_pick_trips(static, tmp_path):
    res, now, feeds, held = _run_board(static, tmp_path, hold_sec=400)
    tv = res["board"]["travel"]; trains = res["board"]["line"]["trains"]
    trips = tv["trips"]
    assert trips and trips == sorted(trips, key=lambda t: t["board_ts"])
    for t in trips:
        assert t["board_ts"] >= now - 60 and t["arrive_ts"] > t["board_ts"] and t["ride_sec"] == t["arrive_ts"] - t["board_ts"]
        assert t["sched_ride_sec"] and t["ride_vs_sched_sec"] == t["ride_sec"] - t["sched_ride_sec"] and t["stops_to_origin"] >= 1
    p0, p60 = tv["progress0"], tv["progress60"]
    assert len(p0) == len(trains) == len(p60)
    for t, a, b in zip(trains, p0, p60):
        pos = t["position"]
        if pos and pos["status"] == "STOPPED_AT":
            assert a["idx"] == pos["stop_idx"] == b["idx"] and a["state"] in ("stopped", "holding", "terminal")
        elif pos:
            assert pos["stop_idx"] - 1 <= a["idx"] < pos["stop_idx"] and b["idx"] >= a["idx"] and a["state"] in ("moving", "stalled")
        else:
            assert a["state"] == "unknown"
    h = next(t for t in trains if t["trip_id"] == held); ph = next(p for t, p in zip(trains, p0) if t["trip_id"] == held)
    assert ph["state"] == "holding" and ph["since"] >= 400


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_station_graph_paths_and_itineraries(static, tmp_path):
    res, now, feeds, held = _run_board(static, tmp_path, hold_sec=400)
    P = res["board"]["paths"]
    assert P["n_stations"] >= 10 and P["usq"] != P["s59"]
    assert P["reach"][P["s59"]]["how"] == "direct" and P["reach"][P["gc"]]["how"] == "direct" and "6" in P["reach"][P["s59"]]["direct"]
    assert all(isinstance(e["via"], list) for e in P["reach"].values())
    paths = P["paths"]; assert paths and paths == sorted(paths, key=lambda p: p["sched_sec"])
    direct = [p for p in paths if p["transfer"] is None]
    assert direct and set(direct[0]["legs"][0]["routes"]) >= {"4", "6"}, "the 4 and the 6 serve the same stops: merged into one direct option"
    assert all(p["transfer"] is None or p["transfer"]["walk_sec"] > 0 for p in paths)
    assert not any(p["transfer"] and p["legs"][0]["routes"][0] in direct[0]["legs"][0]["routes"] for p in paths), "no change off a line that goes there directly"
    its = direct[0]["its"]; assert its and len(its[0]["legs"]) == 1 and its[0]["board_ts"] >= now - 60 and its[0]["arrive_ts"] > its[0]["board_ts"]
    assert its[0]["sched_ride_sec"] == direct[0]["sched_sec"] and its == sorted(its, key=lambda x: x["arrive_ts"])
    assert P["hw6"] and 120 <= P["hw6"] <= 1200 and P["hw46"] and P["hw46"] < P["hw6"], "two routes at a stop: shorter combined headway"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_speed_from_state_transitions(static, tmp_path):
    res, now, feeds, held = _run_board(static, tmp_path)
    sp = res["board"].get("speed")
    assert sp, "the synthetic snapshot should have a train in transit"
    seg = sp["first_poll_segment"]
    assert seg and seg["dist_m"] > 200 and seg["sched_run_sec"] and 10 < seg["sched_speed_kmh"] < 80 and seg["covered_m"] <= seg["dist_m"]
    lr = sp["last_run"]
    assert lr and lr["run_sec"] == 150 and lr["dist_m"] and abs(lr["speed_kmh"] - lr["dist_m"] / 150 * 3.6) < 1e-6 and lr["sched_speed_kmh"]
