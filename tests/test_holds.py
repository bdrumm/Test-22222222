"""Network-wide hold log (dwell >= HOLD_SEC at any stop) and the alert-latency analysis."""
from __future__ import annotations

import numpy as np
import pandas as pd

from mta_delay_insights.analysis.holds import hold_summary, match_holds_to_alerts
from mta_delay_insights.collect.dwells import HOLD_SEC, DwellTracker
from pipeline import lib

T0 = int(pd.Timestamp("2026-09-21 08:00", tz="America/New_York").timestamp())


def _veh(status, stop, ts, trip="t1"):
    return pd.DataFrame([{"snapshot_ts": ts, "feed": "x", "trip_id": trip, "route_id": "6", "start_date": "20260921", "direction": "N",
                          "stop_id": stop, "current_stop_sequence": 1, "current_status": status, "vehicle_ts": ts}])


def test_tracker_keeps_holds_outside_the_stops_of_interest():
    dt = DwellTracker({"631N"})
    # a normal dwell at a stop we do not monitor: dropped
    dt.update(_veh("STOPPED_AT", "629N", T0), T0)
    assert dt.update(_veh("IN_TRANSIT_TO", "628N", T0 + 40), T0 + 40).empty
    # a hold (>= HOLD_SEC) at a stop we do not monitor: kept
    dt.update(_veh("STOPPED_AT", "625N", T0 + 100), T0 + 100)
    dt.update(_veh("STOPPED_AT", "625N", T0 + 100), T0 + 100 + HOLD_SEC + 30)
    out = dt.update(_veh("IN_TRANSIT_TO", "624N", T0 + 400), T0 + 400)
    assert len(out) == 1 and out.iloc[0]["stop_id"] == "625N" and out.iloc[0]["dwell_sec"] >= HOLD_SEC
    assert DwellTracker({"631N"}, hold_sec=None).update(_veh("STOPPED_AT", "625N", T0), T0).empty


def _holds(days=3):
    rows = []
    rng = np.random.default_rng(1)
    for d in range(days):
        base = T0 + d * 86400
        for i in range(12):
            # routine 3-minute holds at 625N through the day, plus two long ones at 633N in the morning peak
            rows.append({"trip_key": f"k|{d}-{i}", "route_id": "6", "direction": "N", "stop_id": "625N", "stopped_from_ts": base + i * 3600, "stopped_to_ts": base + i * 3600 + 180, "dwell_sec": 180 + rng.integers(0, 30), "polls": 6})
        for i in range(2):
            rows.append({"trip_key": f"h|{d}-{i}", "route_id": "6", "direction": "N", "stop_id": "633N", "stopped_from_ts": base + 600 + i * 900, "stopped_to_ts": base + 600 + i * 900 + 480, "dwell_sec": 480, "polls": 16})
        rows.append({"trip_key": f"x|{d}", "route_id": "4", "direction": "N", "stop_id": "631N", "stopped_from_ts": base + 5 * 3600, "stopped_to_ts": base + 5 * 3600 + 400, "dwell_sec": 400, "polls": 13})
    return pd.DataFrame(rows)


def _alerts(days=3):
    rows = []
    for d in range(days):
        base = T0 + d * 86400
        # an unplanned delay alert on the 6 posted 8 minutes after the first long hold each morning
        rows.append({"alert_id": f"a{d}", "alert_type": "Delays", "planned": False, "cause_category": "signal", "created_at": base + 600 + 480, "updated_at": base + 3000,
                     "active_start": base + 600 + 480, "active_end": base + 3600, "routes": ["6"], "stops": [], "header": "Northbound 6 trains are running with delays", "description": "", "last_seen_ts": base + 3600})
        # planned work on the 4: must not count
        rows.append({"alert_id": f"p{d}", "alert_type": "Planned Work", "planned": True, "cause_category": "planned", "created_at": base, "updated_at": base,
                     "active_start": base + 4 * 3600, "active_end": base + 8 * 3600, "routes": ["4"], "stops": [], "header": "Planned work", "description": "", "last_seen_ts": base})
    return pd.DataFrame(rows)


def test_hold_summary_and_alert_latency(static, tmp_path):
    holds, alerts = _holds(), _alerts()
    m = match_holds_to_alerts(holds[holds["dwell_sec"] >= 300], alerts)
    six = m[m["stop_id"] == "633N"]
    assert six["alert_latency_sec"].notna().all() and set(six["alert_latency_sec"].round()) == {480.0, -420.0}
    assert m[m["route_id"] == "4"]["alert_id"].isna().all(), "planned work is not an alert for a hold"
    origin = static.canonical_stop_sequence("6", "N")[0]
    terminal_wait = pd.DataFrame([{"trip_key": "t|0", "route_id": "6", "direction": "N", "stop_id": origin, "stopped_from_ts": T0 + 100, "stopped_to_ts": T0 + 1000, "dwell_sec": 900, "polls": 30}])
    s = hold_summary(pd.concat([holds, terminal_wait], ignore_index=True), alerts, static)
    assert s["n_terminal"] == 1 and all(x["stop_id"] != origin for x in s["by_stop"]), "waiting at the origin terminal is not a hold"
    assert s["n"] == len(holds) and s["days"] == 3 and s["per_day"] == len(holds) / 3
    assert s["by_stop"][0]["stop_id"] == "625N" and s["by_stop"][0]["name"] == static.stop_name("625N") and s["by_stop"][0]["n"] == 36
    assert {r["route"] for r in s["by_route"]} == {"6", "4"} and abs(sum(s["by_hour"]) - len(holds) / 3) < 1e-6
    lg = s["long"]
    assert lg["n"] == 9 and lg["share_with_alert"] == round(6 / 9, 3) and lg["share_alert_after"] == round(3 / 9, 3) and lg["median_latency_sec"] == 480
    assert s["longest"][0]["dwell_sec"] == 480 and len(s["longest"]) == 9
    assert hold_summary(pd.DataFrame(), alerts, static)["n"] == 0 and hold_summary(None, None, None)["n"] == 0
    assert lib.save_holds(tmp_path, holds) and len(lib.load_holds(tmp_path)) == len(holds)
