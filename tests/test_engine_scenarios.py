"""End-to-end: inject a known cause, check the engine names it."""
from datetime import date, timedelta

import pytest

from mta_delay_insights import synthetic
from mta_delay_insights.analysis.engine import AnalysisRequest, analyze_station
from mta_delay_insights.storage.db import Store
from tests.conftest import START, dt

WINDOW = START + timedelta(days=10)
END = WINDOW + timedelta(days=10)


def run(static, issues, hours=None, seed=7):
    sc = synthetic.Scenario("t", START, WINDOW, END, issues=issues, seed=seed, directions=("N",))
    sim = synthetic.simulate(static, sc)
    store = Store(":memory:")
    store.insert_arrivals(sim.arrivals)
    if not sim.alerts.empty:
        store.upsert_alerts(sim.alerts, seen_ts=0)
    store.put_frame("ridership_profile", sim.ridership_profile)
    store.put_frame("trains_delayed", sim.incidents)
    store.put_frame("weather_daily", sim.weather_daily)
    req = AnalysisRequest("Grand Central", "N", ["6", "4"], dt(WINDOW), dt(END), dt(START), dt(WINDOW), hours=hours)
    return analyze_station(store, static, req)


def causes(report, n=3):
    return [c["cause"] for c in report.ranked_causes[:n]]


def test_null_scenario_reports_no_issue(static):
    r = run(static, [])
    assert r.severity_score < 10
    assert r.verdict.startswith("No significant")
    assert r.focus_mode == "none"
    assert not any(c.direction == "worse" for c in r.comparisons)


def test_signal_failure_is_attributed_to_signal_alerts_and_segment(static):
    r = run(static, [synthetic.Issue("signal_segment", segment=("633", "632"), hours=(8,), magnitude_sec=360, day_prob=1.0)])
    assert 8 in r.focus_hours and r.focus_mode == "detected"
    assert causes(r, 1) == ["signal"]
    assert r.ranked_locations[0]["cause"] == "upstream_propagation"
    seg = [e for e in r.evidence if e.lens == "run_time_pattern"]
    assert seg and "28 St -> 33 St" in seg[0].summary and seg[0].cause == "segment_restriction"
    assert any("signal" in rec.action.lower() or "signal" in rec.tags for rec in r.recommendations)
    assert r.impact.passenger_hours_per_day > 0
    md = r.to_markdown(); js = r.to_json()
    assert "## Why (ranked causes)" in md and '"ranked_causes"' in js


def test_missing_trips_are_attributed_to_missing_service(static):
    r = run(static, [synthetic.Issue("missing_trips", hours=(8, 9), fraction=0.3)])
    assert set(r.focus_hours) & {8, 9}
    assert causes(r, 1) == ["missing_service"]
    sd = next(c for c in r.comparisons if c.metric == "service_delivered")
    assert sd.direction == "worse"


def test_late_terminal_departures(static):
    r = run(static, [synthetic.Issue("terminal_late", hours=(8, 9), magnitude_sec=300)])
    assert causes(r, 1) == ["terminal_dispatch"]
    assert r.ranked_locations[0]["cause"] == "upstream_propagation"


def test_merge_conflicts(static):
    r = run(static, [synthetic.Issue("merge_conflict", hours=(7, 8, 9, 16, 17, 18), magnitude_sec=150)])
    assert "interlining_merge" in causes(r, 2)
    ev = next(e for e in r.evidence if e.lens == "merge")
    assert ev.details["merge_stop"] == "635N" and ev.details["extra_sec"] > 60


def test_dwell_pattern(static):
    r = run(static, [synthetic.Issue("dwell_peak", hours=(8, 9, 17, 18), magnitude_sec=60)])
    assert "dwell_time" in causes(r, 2)
    assert any(c.metric == "lateness_sec" and c.direction == "worse" for c in r.comparisons)


def test_requested_hours_and_no_baseline_paths(static):
    r = run(static, [synthetic.Issue("signal_segment", hours=(8,), magnitude_sec=360, day_prob=1.0)], hours=[8])
    assert r.focus_mode == "requested" and r.focus_hours == [8]
    sc = synthetic.Scenario("t", WINDOW, WINDOW, END, issues=[], directions=("N",))
    sim = synthetic.simulate(static, sc)
    store = Store(":memory:"); store.insert_arrivals(sim.arrivals)
    req = AnalysisRequest("Grand Central", "N", ["6"], dt(WINDOW), dt(END), dt(START), dt(WINDOW))
    r2 = analyze_station(store, static, req)
    assert r2.comparisons == [] and any("no baseline" in c for c in r2.caveats)
    with pytest.raises(ValueError):
        analyze_station(store, static, AnalysisRequest("Nowhere", "N"))
