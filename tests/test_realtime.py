import json
from datetime import datetime, timedelta

import pandas as pd

from mta_delay_insights import synthetic
from mta_delay_insights.collect.collector import Collector
from mta_delay_insights.realtime import build_live, fit_model, live_trains
from mta_delay_insights.realtime.propagation import PropagationModel, horizon_bucket
from mta_delay_insights.sources import gtfs_realtime as rt
from mta_delay_insights.sources.gtfs_static import NY_TZ, service_midnight
from mta_delay_insights.storage.db import Store
from pipeline import lib
from tests.conftest import START


def _setup(static, scenario="mixed", days=8):
    sc = synthetic.Scenario("t", START, START + timedelta(days=days), START + timedelta(days=2 * days),
                            issues=list(synthetic.SCENARIOS[scenario]), directions=("N",))
    sim = synthetic.simulate(static, sc)
    store = Store(":memory:")
    store.insert_arrivals(sim.arrivals)
    store.upsert_alerts(sim.alerts, seen_ts=0)
    targets = {"targets": [{"id": "gc-n", "station": "Grand Central", "direction": "N", "routes": ["6", "4"], "label": "GC"}], "upstream_stops": 6}
    _, feeds, resolved = lib.stops_and_feeds(static, targets)
    for t in resolved:
        rr = lib.resolve_target(static, t); t["upstream"], t["terminals"] = rr["upstream"], rr["terminals"]
    last = sc.end - timedelta(days=1)
    while last.weekday() >= 5:
        last -= timedelta(days=1)
    now = service_midnight(last).timestamp() + 8 * 3600 + 20 * 60
    return sim, store, resolved, now


def test_fit_model_learns_carry_and_alert_effect(static):
    sim, store, resolved, now = _setup(static)
    m = fit_model(store, static, resolved[0], now)
    assert m.n_arrivals > 1000 and m.n_days >= 10
    assert "6" in m.lateness_carry and "1" in m.lateness_carry["6"]
    slope, intercept, resid, n = m.carry("6", 1)
    assert 0.8 < slope < 1.3 and n > 100
    assert m.gap_p("6") > 0.6
    extra, n_alert = m.alert_extra("signal")
    assert extra > 120 and n_alert > 5
    # priors where there is no data
    assert m.carry("Z", 3)[0] == 1.0 and m.gap_p("Z") == 0.6
    assert m.alert_extra("weather")[1] > 0            # unknown cause falls back to the 'any alert' effect
    assert PropagationModel("x", "y", []).alert_extra("weather") == (0.0, 0)
    d = json.loads(json.dumps(m.to_dict()))
    assert PropagationModel.from_dict(d).carry("6", 1)[0] == slope
    assert horizon_bucket(0) == "0-300" and horizon_bucket(5000) == "3600-1000000000"


def test_live_forecast_flags_injected_delay(static):
    sim, store, resolved, now = _setup(static)
    model = fit_model(store, static, resolved[0], now)
    feed_key, _, data = synthetic.to_rt_snapshots(sim.arrivals, now, now, poll_interval=30)[0]
    trains = live_trains({feed_key: data}, static, now)
    assert trains and all(t.sched_matched for t in trains)
    victim = next(t for t in trains if t.route_id == "6" and (t.stops_until("631N") or 0) >= 2)
    msg = rt.parse_feed(data)
    for ent in msg.entity:
        if ent.HasField("trip_update") and ent.trip_update.trip.trip_id == victim.trip_id:
            for stu in ent.trip_update.stop_time_update:
                stu.arrival.time += 420; stu.departure.time += 420
    alerts = sim.alerts.copy()
    idx = alerts.index[~alerts["planned"]][-1]
    alerts.loc[idx, ["active_start", "active_end", "updated_at"]] = [now - 600, now + 1200, now]
    live = build_live({feed_key: msg.SerializeToString()}, alerts, static, resolved, {"gc-n": model}, now)
    assert live["trains_total"] == len(trains) and live["summary"]["good"] + live["summary"]["degraded"] + live["summary"]["disrupted"] == len(live["routes"])
    assert any("6" in a["routes"] for a in live["alerts"])
    st = live["stations"][0]
    kinds = {e["kind"] for e in st["effects"]}
    assert "late_inbound" in kinds and "alert" in kinds
    inbound = next(e for e in st["effects"] if e["kind"] == "late_inbound")
    assert "expected" in inbound["text"]
    arr = next(a for a in st["arrivals"] if a["trip_id"] == victim.trip_id)
    assert arr["now_lateness_sec"] >= 400 and arr["carry"]["lateness_sec"] > 300 and arr["model_eta_ts"] >= arr["feed_eta_ts"]
    assert arr["alert_extra_sec"] and arr["alert_extra_sec"] > 0
    six = next(r for r in live["routes"] if r["route_id"] == "6")
    assert six["status"] in ("degraded", "disrupted") and six["max_gap_sec"] > 0 and six["sched_headway_sec"] == 240
    json.dumps(live, default=str)


def test_build_live_without_model_uses_priors(static):
    sim, store, resolved, now = _setup(static, "none", days=2)
    feed_key, _, data = synthetic.to_rt_snapshots(sim.arrivals, now, now)[0]
    live = build_live({feed_key: data}, None, static, resolved, None, now)
    st = live["stations"][0]
    assert st["arrivals"] and st["model"]["n_arrivals"] == 0
    a = st["arrivals"][0]
    assert a["eta_lo_ts"] < a["model_eta_ts"] < a["eta_hi_ts"]


def test_collector_on_poll_exposes_latest_feeds(static):
    sim, store, resolved, now = _setup(static, "none", days=1)
    snaps = synthetic.to_rt_snapshots(sim.arrivals, now, now + 60, poll_interval=30)
    seen = []
    col = Collector(Store(":memory:"), ["1234567S"], None, poll_interval_sec=30, fetcher=lambda k: snaps.pop(0)[2])
    col.on_poll = lambda c, ts: seen.append((len(c.last_feed_bytes), ts))
    col.poll_once(now); col.poll_once(now + 30)
    assert seen == [(1, now), (1, now + 30)] and col.last_poll_ts == now + 30
