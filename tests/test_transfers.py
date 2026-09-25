import json
from datetime import timedelta

from mta_delay_insights import synthetic
from mta_delay_insights.analysis import transfers as T
from mta_delay_insights.realtime.journey import resolve_journeys
from mta_delay_insights.sources.gtfs_static import service_midnight
from mta_delay_insights.storage.db import Store
from tests.conftest import START
from tests.test_journey import CFG


def _run(static, scenario, days=8):
    sc = synthetic.Scenario("t", START, START + timedelta(days=days), START + timedelta(days=2 * days),
                            issues=list(synthetic.SCENARIOS[scenario]), directions=("N",))
    sim = synthetic.simulate(static, sc)
    store = Store(":memory:"); store.insert_arrivals(sim.arrivals)
    specs = resolve_journeys(static, CFG)
    start, end = service_midnight(sc.start).timestamp(), service_midnight(sc.end).timestamp()
    return store, specs, start, end


def test_transfers_from_journeys_dedupes(static):
    specs = resolve_journeys(static, CFG)
    trs = T.transfers_from_journeys(specs + specs)
    assert len(trs) == 1
    tr = trs[0]
    assert tr.from_stop == "631N" and tr.to_stop == "631N" and tr.from_routes == ["6"] and tr.to_routes == ["4"]
    assert tr.walk_sec == 60 and tr.journey_ids == ["usq-59", "usq-59"] and tr.station_name.startswith("Grand Central")


def test_connections_and_late_feeder_cost(static):
    store, specs, start, end = _run(static, "merge")
    tr = T.transfers_from_journeys(specs)[0]
    tbl = T.connection_table(store, static, tr, start, end, coverage=[(start, end)])
    assert len(tbl) > 1000
    assert (tbl["wait_sec"] >= 0).all() and tbl["sched_wait_sec"].notna().mean() > 0.95
    assert (tbl["conn_ts"] >= tbl["ready_ts"]).all()
    s = T.transfer_summary(tbl)
    assert s["ok"] and 120 < s["wait_median_sec"] < 600 and 0 < s["missed_rate"] < 0.3
    eff = s["feeder_lateness_effect"]
    # a held 6 (just behind a 4) has just missed that 4: longer connection waits, far more missed connections
    assert eff["significant"] and eff["excess_wait_diff_sec"] > 30
    assert eff["missed_rate_late"] > 3 * eff["missed_rate_on_time"]
    assert len(s["by_hour"]) == 24 and s["by_hour"][8]["n"] > 50


def test_shared_track_interaction_detected(static):
    store, specs, start, end = _run(static, "merge")
    leg = specs[0].legs[0]
    x = T.cross_line_interaction(store, static, "6", "4", "N", leg.stops, start, end)
    assert x and x["significant"]
    assert x["best"]["stop_id"] == "635N" and x["best"]["extra_sec"] > 60 and x["best"]["p_value"] < 0.01
    # the express is not slowed by the local
    y = T.cross_line_interaction(store, static, "4", "6", "N", leg.stops, start, end)
    assert y is None or not y["significant"]
    # and nothing in the null scenario
    store0, specs0, s0, e0 = _run(static, "none")
    z = T.cross_line_interaction(store0, static, "6", "4", "N", leg.stops, s0, e0)
    assert z is None or not z["significant"]


def test_comovement_only_when_lines_share_a_cause(static):
    store, specs, start, end = _run(static, "weather")
    c = T.cross_line_comovement(store, static, "631N", ["6"], "631N", ["4"], start, end)
    assert c["ok"] and c["spearman"] > 0.1 and c["joint_lift"] > 1.5
    store, specs, start, end = _run(static, "signal")
    c = T.cross_line_comovement(store, static, "631N", ["6"], "631N", ["4"], start, end)
    assert c["ok"] and abs(c["spearman"]) < 0.15


def test_analyze_routes_end_to_end(static):
    store, specs, start, end = _run(static, "signal")
    out = T.analyze_routes(store, static, specs, start, end, coverage=[(start, end)])
    json.dumps(out, default=str)
    r = out["routes"][0]
    assert r["status"] == "ok" and r["transfers"] and out["transfers"][0]["summary"]["ok"]
    d = r["decomposition"]
    comps = {c["kind"] + str(c["leg"]): c for c in d["components"]}
    assert set(comps) == {"wait0", "ride0", "transfer0", "ride1"}
    # the signal failure hits the 6 between 28 St and 33 St in the morning peak: leg-1 ride excess is largest then
    ride6 = comps["ride0"]["by_hour"]
    assert ride6[8] is not None and ride6[8] > max(v for h, v in enumerate(ride6) if v is not None and h in (12, 13, 14, 15))
    assert d["total_by_hour"][8] is not None and len(d["total_by_hour"]) == 24
    assert any(f["kind"] == "decomposition" for f in r["findings"])
    assert all(f["severity"] in ("high", "medium", "low", "info") for f in r["findings"])
    store0, specs0, s0, e0 = _run(static, "none")
    out0 = T.analyze_routes(store0, static, specs0, s0, e0, coverage=[(s0, e0)])
    assert not any(f["severity"] == "high" for f in out0["routes"][0]["findings"])
