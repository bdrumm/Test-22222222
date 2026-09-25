from datetime import timedelta

import pandas as pd

from mta_delay_insights import synthetic
from mta_delay_insights.analysis import line_view
from mta_delay_insights.realtime import live_trains
from mta_delay_insights.realtime.incidents import developing_incidents
from mta_delay_insights.sources.gtfs_static import service_midnight
from mta_delay_insights.storage.db import Store
from tests.conftest import START


def _sim(static, scenario="signal", days=6):
    sc = synthetic.Scenario("t", START, START + timedelta(days=2), START + timedelta(days=days),
                            issues=list(synthetic.SCENARIOS[scenario]), directions=("N",))
    sim = synthetic.simulate(static, sc)
    store = Store(":memory:"); store.insert_arrivals(sim.arrivals)
    return sim, store, sc


def test_line_snapshot_and_deviation_grid(static):
    sim, store, sc = _sim(static)
    last = sc.end - timedelta(days=1)
    while last.weekday() >= 5:
        last -= timedelta(days=1)
    now = service_midnight(last).timestamp() + 8 * 3600 + 30 * 60
    feed_key, _, data = synthetic.to_rt_snapshots(sim.arrivals, now, now)[0]
    trains = live_trains({feed_key: data}, static, now)
    snap = line_view.line_snapshot(store.arrivals(), static, "6", "N", now, trains)
    assert len(snap["stops"]) == 14 and snap["actual"] and snap["live"] and snap["scheduled"]
    assert all(p[1] <= now + 60 for t in snap["actual"] for p in t["points"])
    assert all(len(t["points"]) >= 2 for t in snap["actual"])
    dev = line_view.deviation_grid(store.arrivals(), static, "6", "N")
    assert len(dev["grid"]) == 14 and dev["n_trips"] > 500
    # the signal failure between 28 St and 33 St in the morning peak shows as time lost arriving at 33 St
    i33 = [s["stop_id"] for s in dev["stops"]].index("632N")
    peak = [v for v in dev["grid"][i33][7:10] if v is not None]
    assert peak and max(peak) > 15
    assert dev["worst_stops"][0]["stop_id"] == "632N"


def test_developing_incident_detected_without_alert(static):
    sim, store, sc = _sim(static, days=10)
    assert not sim.alerts.empty
    # scan through the injected signal episodes: consecutive slow trains arriving at 33 St must be flagged
    found = None
    for ep in sim.alerts[~sim.alerts["planned"]].itertuples(index=False):
        t = float(ep.active_start)
        while t <= float(ep.active_end) + 600 and found is None:
            hit = [x for x in developing_incidents(store, static, t, None) if x["to_stop"] == "632N" and x["route_id"] == "6"]
            if hit:
                found = hit[0]
            t += 120
        if found:
            break
    assert found is not None and found["n_slow"] >= 2 and found["mean_loss_sec"] >= 120 and not found["alerted"] and "no alert" in found["text"]
    alerts_now = pd.DataFrame([{"kind": "delay", "routes": ["6"]}])
    inc2 = developing_incidents(store, static, found["last_seen_ts"] + 60, alerts_now)
    assert any(x["alerted"] for x in inc2 if x["to_stop"] == "632N")


def test_eta_trust_summary():
    samples = pd.DataFrame([{"trip_key": f"d|t{i}", "route_id": "6", "stop_id": "631N", "at_stop": "633N", "at_ts": 1000 + i, "stops_ahead": k, "eta_ts": 2000 + i + (10 * k if i % 2 else -5 * k)}
                            for i in range(60) for k in (1, 3)])
    arr = pd.DataFrame([{"trip_key": f"d|t{i}", "stop_id": "631N", "arrival_ts": 2000 + i} for i in range(60)])
    t = line_view.eta_trust(samples, arr)
    assert t["n"] == 120 and len(t["overall"]) == 2 and "6" in t["by_route"]
    k3 = [x for x in t["overall"] if x["stops_ahead"] == 3][0]
    assert 10 <= k3["median_abs_err_sec"] <= 30 and k3["n"] == 60


def test_event_study_curves(static):
    from mta_delay_insights.analysis.event_study import event_study
    sim, store, sc = _sim(static, days=16)
    es = event_study(sim.arrivals, sim.alerts, static)
    assert es["n_alerts"] >= 3
    o = es["overall"]
    assert len(o["mean_curve"]) == len(o["bins"]) and o["peak_excess_sec"] > 60
    peak_i = max(range(len(o["bins"])), key=lambda i: (o["mean_curve"][i] or 0))
    assert 0 <= o["bins"][peak_i] <= 40           # the injected 20-45 min episodes peak shortly after the alert
    assert o["recovery_min"] is not None and o["recovery_min"] > o["bins"][peak_i]
    assert es["by_cause"][0]["cause"] == "signal"


def test_scorecard_rows(static):
    from mta_delay_insights.analysis.scorecard import scorecard
    sim, store, sc = _sim(static, days=8)
    sc_ = scorecard(sim.arrivals, static)
    assert sc_["n_arrivals"] > 1000 and len(sc_["rows"]) == 2
    r6 = next(r for r in sc_["rows"] if r["route"] == "6")
    assert r6["direction"] == "N" and r6["n_trips"] > 100 and 0 <= r6["share_late_5min"] <= 1
    assert r6["headway_cv_peak"] is not None and r6["loss_per_trip_sec"] >= 0 and len(r6["hourly_mean_lateness"]) == 24
    assert r6["worst_segment_stop"] in ("33 St", "28 St", "Grand Central-42 St")
