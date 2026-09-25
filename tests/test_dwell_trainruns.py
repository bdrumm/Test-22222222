import numpy as np
import pandas as pd

from mta_delay_insights.analysis.dwell import dwell_profile, dwell_ridership_elasticity
from mta_delay_insights.analysis.train_runs import terminal_recovery


def test_dwell_profile_and_elasticity(static):
    rng = np.random.default_rng(0)
    rows = []
    base = int(pd.Timestamp("2026-09-21 00:00", tz="America/New_York").timestamp())   # a Monday, local midnight
    for day in range(3):
        for h in range(24):
            for i in range(6):
                ts = base + day * 86400 + h * 3600 + i * 500
                crowd = 40 + 30 * (h in (8, 9, 17, 18))       # crowded peaks dwell longer at 631N
                rows.append({"trip_key": f"d|{day}-{h}-{i}", "route_id": "6", "direction": "N", "stop_id": "631N", "stopped_from_ts": ts, "stopped_to_ts": ts + crowd, "dwell_sec": crowd + rng.normal(0, 3), "polls": 2})
                rows.append({"trip_key": f"d|{day}-{h}-{i}", "route_id": "6", "direction": "N", "stop_id": "633N", "stopped_from_ts": ts + 200, "stopped_to_ts": ts + 230, "dwell_sec": 30 + rng.normal(0, 3), "polls": 1})
    prof = dwell_profile(pd.DataFrame(rows), static)
    assert prof["n"] == len(rows) and len(prof["stops"]) == 2 and prof["stops"][0]["stop_id"] == "631N"
    s = prof["stops"][0]
    assert s["peak_median_sec"] > s["offpeak_median_sec"] + 15 and len(s["by_hour"]) == 24
    rp = pd.DataFrame([{"target_id": "gc", "hour": h, "riders_per_hour": 1000 + 3000 * (h in (8, 9, 17, 18))} for h in range(24)])
    el = dwell_ridership_elasticity(prof, rp, {"631N": "gc"})
    assert len(el) == 1 and el[0]["correlation"] > 0.8 and el[0]["reading"].startswith("crowding")


def test_terminal_recovery_pairs_trips_at_terminals(static):
    from datetime import timedelta
    from mta_delay_insights import synthetic
    from mta_delay_insights.analysis.train_runs import terminal_pairs
    from tests.conftest import START
    sc = synthetic.Scenario("t", START, START + timedelta(days=2), START + timedelta(days=4), issues=list(synthetic.SCENARIOS["terminal"]), directions=("N", "S"))
    sim = synthetic.simulate(static, sc)
    pairs = terminal_pairs(sim.arrivals, static)
    assert len(pairs) > 50
    assert (pairs["layover_sec"] > 60).all() and (pairs["layover_sec"] <= 40 * 60).all()
    assert set(pairs["terminal"]) <= {"627", "640"} and pairs["out_trip"].is_unique
    rec = terminal_recovery(sim.arrivals, static, min_pairs=20)
    assert rec["n_pairs"] == len(pairs) and rec["overall"]["n"] == len(pairs) and 0 <= rec["overall"]["share_late_in"] <= 1
    assert rec["by_terminal"] and all(t["terminal_name"] for t in rec["by_terminal"])
