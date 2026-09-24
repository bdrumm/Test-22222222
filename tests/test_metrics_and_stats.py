import numpy as np
import pandas as pd

from mta_delay_insights.analysis import metrics as mt
from mta_delay_insights.analysis import significance as sg
from mta_delay_insights.analysis import trends as tr


def test_expected_wait():
    assert mt.expected_wait([300, 300, 300]) == 150
    # Bunching: same number of trains, same mean headway, longer expected wait.
    assert mt.expected_wait([100, 500, 100, 500]) > 150


def _arrivals(headways, start=1_757_400_000):
    ts = np.cumsum([0] + list(headways)) + start
    return pd.DataFrame({
        "trip_key": [f"d|t{i}" for i in range(len(ts))], "route_id": "6", "stop_id": "631N", "arrival_ts": ts,
        "sched_arrival_ts": ts - 20, "lateness_sec": 20.0, "sched_headway_sec": 300.0,
        "service_date": pd.Timestamp("2026-09-09").date(), "pred_drift_sec": 0.0, "confidence": 1.0,
    })


def test_flags_and_profile_regular_service():
    f = mt.flag_arrivals(_arrivals([300] * 11))
    assert not f["is_gap"].any() and not f["is_bunched"].any() and not f["problem"].any()
    prof = mt.hourly_profile(f)
    assert abs(prof["apt_sec"].sum()) < 1e-6


def test_flags_detect_gap_and_bunching():
    f = mt.flag_arrivals(_arrivals([300, 300, 600, 60, 300]))
    assert f["is_gap"].tolist()[3] and f["is_bunched"].tolist()[4]
    f2 = mt.flag_arrivals(_arrivals([300] * 4).assign(lateness_sec=[10, 400, 10, 10, 10]))
    assert f2["is_late"].tolist() == [False, True, False, False, False]


def test_compare_metric_detects_shift_and_ignores_noise():
    rng = np.random.default_rng(0)
    base = rng.normal(30, 10, 40)
    c = sg.compare_metric("lateness_sec", base + 60, base)
    assert c.direction == "worse" and c.significant and c.material and c.ci_lo > 0
    c2 = sg.compare_metric("lateness_sec", rng.normal(30, 10, 40), base)
    assert c2.direction == "flat"
    c3 = sg.compare_metric("lateness_sec", base + 5, base)   # significant but not material
    assert c3.direction == "flat" and not c3.material
    assert 0.9 < sg.cliffs_delta(base + 60, base) <= 1.0


def test_rider_impact_and_severity():
    hours = [8, 9]
    w = pd.DataFrame({"hour": hours, "apt_sec": [120, 60], "lateness_mean_sec": [90, 30], "n_actual": [20, 20]})
    b = pd.DataFrame({"hour": hours, "apt_sec": [0, 0], "lateness_mean_sec": [30, 30], "n_actual": [20, 20]})
    rp = pd.DataFrame({"hour": hours, "riders_per_hour": [6000, 3000]})
    imp = sg.rider_impact(w, b, rp, hours, route_share=0.5)
    # APT: (120/60*3000 + 60/60*1500) = 7500 ; ATT: 60/60*3000 = 3000
    assert abs(imp.apt_passenger_minutes_per_day - 7500) < 1e-6
    assert abs(imp.att_passenger_minutes_per_day - 3000) < 1e-6
    assert imp.ridership_source == "hourly_ridership"
    comp = sg.Comparison("apt_sec", 90, 0, 90, 60, 120, 0.001, 0.8, 14, 14, True, "worse")
    score, parts = sg.severity_score([comp], imp)
    assert 40 < score <= 100 and sg.severity_label(score) in {"high", "critical", "moderate"}
    assert sg.severity_score([], None) == (0.0, {})


def test_trend_and_changepoint():
    days = pd.date_range("2026-08-24", periods=20).date
    vals = [10] * 10 + [40] * 10
    prof = pd.DataFrame({"service_date": days, "hour": 8, "n_actual": 20, "apt_sec": vals})
    res = tr.trend_test(prof, "apt_sec")
    assert res.direction == "worsening" and res.changepoint_date == str(days[10])
    flat = pd.DataFrame({"service_date": days, "hour": 8, "n_actual": 20, "apt_sec": np.random.default_rng(1).normal(10, 1, 20)})
    assert tr.trend_test(flat, "apt_sec").changepoint_date is None
