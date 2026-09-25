"""End-to-end build from a data directory laid out like the data branch, including backfilled network history."""
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from mta_delay_insights import synthetic
from mta_delay_insights.sources.gtfs_static import service_midnight
from pipeline import build_site, lib
from tests.conftest import START


def test_build_from_data_with_backfill(static, mini_gtfs_dir, tmp_path):
    sc = synthetic.Scenario("t", START, START + timedelta(days=3), START + timedelta(days=9),
                            issues=list(synthetic.SCENARIOS["mixed"]), directions=("N", "S"))
    sim = synthetic.simulate(static, sc)
    data_dir = tmp_path / "data-branch"; data_dir.mkdir()
    targets = {"targets": [{"id": "grand-central-n", "station": "Grand Central", "direction": "N", "routes": ["6", "4"], "label": "GC"}],
               "journeys": [{"id": "usq-59", "label": "Union Sq to 59 St", "legs": [
                   {"from": {"station": "14 St-Union Sq", "direction": "N", "routes": ["6"]}, "to": {"station": "Grand Central", "direction": "N", "routes": ["6"]}},
                   {"transfer_min": 1, "from": {"station": "Grand Central", "direction": "N", "routes": ["4"]}, "to": {"station": "59 St", "direction": "N", "routes": ["4"]}}]}],
               "collect": {"all_stops": True}, "upstream_stops": 6, "route_share_of_entries": 0.5}
    (tmp_path / "targets.json").write_text(json.dumps(targets))
    stops, _, _ = lib.stops_and_feeds(static, targets)
    arr = sim.arrivals
    # "backfilled" network days = the first 6 days at every stop; own collection = last 3 days at the stops of interest
    cut = service_midnight(START + timedelta(days=6)).timestamp()
    net = arr[arr["arrival_ts"] < cut].copy(); net["source"] = "subwaydata"; net["train_id"] = "06 0000  BBR/PEL"
    lib.save_network_arrivals(data_dir, net)
    days = sorted({d.isoformat() for d in pd.to_datetime(net["arrival_ts"], unit="s", utc=True).dt.tz_convert("America/New_York").dt.date})
    (data_dir / "arrivals_all" / "backfill_manifest.json").write_text(json.dumps({"days": {d: {"rows": 1} for d in days}}))
    own = arr[(arr["arrival_ts"] >= cut) & arr["stop_id"].isin(stops)]
    lib.save_arrivals(data_dir, own)
    lib.save_alerts(data_dir, sim.alerts, time.time())
    lib.save_context(data_dir, "weather_daily", sim.weather_daily)
    lib.append_run(data_dir, {"kind": "collect", "iso": "x", "polls": 10, "arrivals": 10, "errors": 0, "per_feed": [{"feed": "1234567S", "first_ts": cut, "last_ts": cut + 3000}]})
    _dd, _tg, _g = str(data_dir), str(tmp_path / "targets.json"), str(mini_gtfs_dir)

    class Args:
        data_dir = _dd; targets = _tg; gtfs = _g
        site_src = str(lib.ROOT / "site"); out = str(tmp_path / "site"); no_feeds = True
    index = build_site.build_from_data(Args)
    out = tmp_path / "site" / "data"
    assert (out / "index.json").exists() and index["targets"]
    st = json.loads((out / "status.json").read_text())
    assert st["datasets"]["network_arrivals"] > 10000 and st["datasets"]["backfill_days"] == len(days)
    # the backfilled days count as full coverage and feed the station report (baseline exists)
    rep = json.loads((out / "reports" / "grand-central-n.json").read_text())
    assert rep["arrival_count"] > 1000
    routes = json.loads((out / "routes.json").read_text())
    assert routes["routes"] and routes["routes"][0]["status"] in ("ok", "collecting")   # the route window is relative to wall-clock time
    sc = json.loads((out / "scorecard.json").read_text()); assert len(sc["rows"]) >= 2
    lines = json.loads((out / "index.json").read_text())["lines_view"]; assert any(l["route"] == "6" for l in lines)
    card = json.loads((out / "models" / "arrival.card.json").read_text()); assert card["status"] in ("ok", "insufficient_data")
    tr = json.loads((out / "train_runs.json").read_text()); assert tr["n_pairs"] > 20
    assert (out / "digest.md").exists() and (out / "climatology.json").exists()
    cs = json.loads((out / "client_schedule.json").read_text())
    assert cs["targets"]["grand-central-n"]["stop_id"] == "631N" and cs["constants"]["hold_sec"] > 0 and cs["feeds"]


def test_client_schedule_export(static, tmp_path):
    from pipeline.client_export import export_client_schedule
    from mta_delay_insights.sources.gtfs_static import NY_TZ
    targets = {"targets": [{"id": "gc", "station": "Grand Central", "direction": "N", "routes": ["6", "4"]}], "upstream_stops": 3}
    _, feeds, resolved = lib.stops_and_feeds(static, targets)
    now = datetime.combine(START + timedelta(days=2), datetime.min.time(), tzinfo=NY_TZ) + timedelta(hours=9)
    cs = export_client_schedule(static, resolved, [], tmp_path, now, feeds)
    tgt = cs["targets"]["gc"]
    assert tgt["stop_id"] == "631N" and len(tgt["sched"]) > 50 and all(len(x) == 3 for x in tgt["sched"])
    assert tgt["sched"] == sorted(tgt["sched"], key=lambda x: x[2]) and {x[1] for x in tgt["sched"]} == {"6", "4"}
    ln = cs["lines"]["6_N"]
    assert ln["stops"] and len(ln["run_sec"]) == len(ln["stops"]) - 1 and all(r is None or r > 0 for r in ln["run_sec"])
    assert len(ln["dist_m"]) == len(ln["stops"]) - 1 and all(d and 200 < d < 3000 for d in ln["dist_m"]), "segment lengths from the stop coordinates"
    assert (tmp_path / "client_schedule.json").exists()
    cl = json.loads((tmp_path / "client_lines.json").read_text())
    ls = cl["lines"]["6_N"]
    assert len(ls) > 50 and all(len(x) == 3 and 0 <= x[1] < len(ln["stops"]) for x in ls) and ls == sorted(ls, key=lambda x: x[2])
    assert cs["route_feeds"]["6"] == "1234567S" and cs["target_feeds"]
    # transfers: the 6 and the 4 share Grand Central (same stop id in the mini corridor)
    tx = cs["transfers"]
    assert {o["line"] for o in tx["631N"]} >= {"4_N"} and all(o["stop"] == "631N" and o["min_sec"] > 0 for o in tx["631N"] if o["line"] == "4_N")
    assert all(o["line"].split("_")[0] != "6" for o in tx.get("631N", [])), "never a transfer onto the same route"
