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
import { parseFeed, computeBoard, tripSuffix, lineBoard } from "%s";
import { readFileSync } from "node:fs";
const schedule = JSON.parse(readFileSync(process.argv[2], "utf8"));
const lines = JSON.parse(readFileSync(process.argv[2].replace("client_schedule", "client_lines"), "utf8")).lines;
const feeds = {}; for (const [key, path] of Object.entries(JSON.parse(process.argv[3]))) feeds[key] = parseFeed(new Uint8Array(readFileSync(path)));
const now = Number(process.argv[4]);
const board = computeBoard(schedule, feeds, now);
board.line = lineBoard(schedule, lines["6_N"] || [], feeds, "6", "N", now);
const parsed = Object.fromEntries(Object.entries(feeds).map(([k, f]) => [k, { timestamp: f.timestamp, trips: f.trips.length, vehicles: f.vehicles.length,
  sample: f.trips[0] && { trip_id: f.trips[0].trip.trip_id, route: f.trips[0].trip.route_id, n_stops: f.trips[0].stops.length, first: f.trips[0].stops[0] } }]));
console.log(JSON.stringify({ board, parsed, suffix: tripSuffix("AFA25GEN-1038-Sunday-00_000600_1..S03R") }));
"""


def _run_board(static, tmp_path, hold_sec=0.0):
    sim, now, feeds, held = _snapshot(static, hold_sec=hold_sec)
    targets = {"targets": [{"id": "gc", "station": "Grand Central", "direction": "N", "routes": ["6", "4"]}], "upstream_stops": 3}
    _, feed_keys, resolved = lib.stops_and_feeds(static, targets)
    export_client_schedule(static, resolved, [], tmp_path, datetime.fromtimestamp(now, NY_TZ), list(feeds))
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
