from mta_delay_insights.realtime import build_live, live_trains
from mta_delay_insights.realtime.simulate import simulate_line, simulate_routes, station_scenarios
from tests.test_positions import _snapshot


def test_baseline_projection_respects_headways(static):
    sim, now, feeds, _ = _snapshot(static)
    trains = live_trains(feeds, static, now)
    res = simulate_line(trains, static, "6", "N", now)
    assert res["trains"] and res["stops"] and res["scenario"] == "baseline"
    # projected times are monotone along each train and never closer than the minimum headway at a stop
    for t in res["trains"]:
        ts = [p[1] for p in t["points"]]
        assert ts == sorted(ts)
    by_stop = {}
    for t in res["trains"]:
        for i, ts in t["points"]:
            by_stop.setdefault(i, []).append(ts)
    for i, ts in by_stop.items():
        ts = sorted(ts)
        assert all(b - a >= 89 for a, b in zip(ts, ts[1:])), f"headway violated at stop {i}"
    assert res["worst_gap"] is None or res["worst_gap"]["gap_sec"] > 0


def test_hold_scenario_cascades_to_followers(static):
    sim, now, feeds, held = _snapshot(static, hold_sec=400)
    trains = live_trains(feeds, static, now)
    sims = simulate_routes(trains, static, [("6", "N"), ("4", "N")], now, hold_extra_sec=600)
    six = next(e for e in sims if e["route"] == "6")
    assert six["disturbed"] and set(six["scenarios"]) == {"baseline", "hold_persists", "clears_now"}
    base, hold = six["scenarios"]["baseline"], six["scenarios"]["hold_persists"]
    held_base = next(t for t in base["trains"] if t["trip_id"] == held)
    held_hold = next(t for t in hold["trains"] if t["trip_id"] == held)
    # the held train itself is 10 min later everywhere under the persisting hold
    b = dict(held_base["points"]); h = dict(held_hold["points"])
    assert all(h[i] - b[i] >= 590 for i in b if i in h)
    # and at least one follower is pushed back by the headway constraint
    assert hold["n_knock_on"] >= 1 and hold["knock_on_total_sec"] > 0
    sc = station_scenarios(sims, "631N", now)
    assert sc and sc["disturbed"] and sc["headline"]
    six_sc = next(r for r in sc["routes"] if r["route"] == "6")
    assert six_sc["hold_effect"]["hold_extra_sec"] == 600 and six_sc["baseline"]
    live = build_live(feeds, None, static, [{"id": "gc", "stop_id": "631N", "routes": ["6", "4"], "direction": "N", "label": "GC", "station_name": "GC", "upstream": {}}], None, now)
    assert live["positions"]["holding"] >= 1 and live["simulation"] and live["stations"][0]["scenarios"]["disturbed"]


def test_hold_model_scales_the_scenarios_to_the_time_already_held(static):
    import numpy as np
    import pandas as pd
    from mta_delay_insights.realtime.client_model import fit_hold_survival, remaining_hold
    sim, now, feeds, held = _snapshot(static, hold_sec=400)
    trains = live_trains(feeds, static, now)
    dw = pd.DataFrame({"trip_key": [f"t{i}" for i in range(300)], "route_id": "6", "direction": "N", "stop_id": "633N", "stopped_from_ts": 1.0, "stopped_to_ts": 2.0,
                       "dwell_sec": np.r_[np.full(150, 220.0), np.full(100, 500.0), np.full(50, 1400.0)], "polls": 3})
    hm = fit_hold_survival(dw, static)
    fixed = simulate_routes(trains, static, [("6", "N")], now, hold_extra_sec=600)
    modelled = simulate_routes(trains, static, [("6", "N")], now, hold_extra_sec=600, hold_model=hm)
    six_f, six_m = fixed[0]["scenarios"], modelled[0]["scenarios"]
    rem = remaining_hold(hm, next(t for t in trains if t.trip_id == held).since_update_sec)
    for sc, key in (("baseline", "expected"), ("hold_persists", "p90")):
        t_m = next(t for t in six_m[sc]["trains"] if t["trip_id"] == held)
        assert t_m["hold_extra_sec"] == round(rem[key]) and six_m[sc]["hold_model"]
    assert next(t for t in six_m["clears_now"]["trains"] if t["trip_id"] == held)["hold_extra_sec"] == 0
    # the fixed scenario adds nothing on the baseline and 600 s when the hold persists; the modelled one adds what
    # holds this long usually still take, and its persisting case is the 90th percentile of that
    assert next(t for t in six_f["baseline"]["trains"] if t["trip_id"] == held)["hold_extra_sec"] == 0
    assert six_f["hold_persists"]["hold_extra_sec"] == 600 and six_m["hold_persists"]["hold_extra_sec"] == round(rem["p90"], 1) or six_m["hold_persists"]["hold_extra_sec"] == rem["p90"]
    b, h = dict(next(t for t in six_m["baseline"]["trains"] if t["trip_id"] == held)["points"]), dict(next(t for t in six_m["hold_persists"]["trains"] if t["trip_id"] == held)["points"])
    assert all(h[i] - b[i] >= rem["p90"] - rem["expected"] - 1 for i in b if i in h)
    sc = station_scenarios(modelled, "631N", now)
    assert sc and sc["headline"] and "90th percentile" in sc["headline"]
    live = build_live(feeds, None, static, [{"id": "gc", "stop_id": "631N", "routes": ["6"], "direction": "N", "label": "GC", "station_name": "GC", "upstream": {}}], None, now, hold_model=hm)
    assert live["simulation"][0]["scenarios"]["hold_persists"]["hold_model"] is True
