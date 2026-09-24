import json
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from mta_delay_insights import synthetic
from mta_delay_insights.analysis.line_insights import line_insights
from pipeline import build_site, lib


def test_arrivals_and_alerts_round_trip(static, tmp_path):
    day = date(2026, 9, 9)
    sc = synthetic.Scenario("t", day, day, day + timedelta(days=2), issues=list(synthetic.SCENARIOS["signal"]), directions=("N",))
    sim = synthetic.simulate(static, sc)
    first = sim.arrivals.iloc[: len(sim.arrivals) // 2]
    lib.save_arrivals(tmp_path, first)
    # second save overlaps the first: dedupe on (trip_key, stop_id)
    written = lib.save_arrivals(tmp_path, sim.arrivals)
    assert set(written) >= {"2026-09-09", "2026-09-10"}
    loaded = lib.load_arrivals(tmp_path)
    assert len(loaded) == len(sim.arrivals.drop_duplicates(["trip_key", "stop_id"]))
    assert lib.load_arrivals(tmp_path, since="2026-09-10")["arrival_ts"].min() > loaded["arrival_ts"].min()
    n = lib.save_alerts(tmp_path, sim.alerts, seen_ts=sim.alerts["active_start"].max())
    n2 = lib.save_alerts(tmp_path, sim.alerts, seen_ts=sim.alerts["active_start"].max())
    assert n == n2 == len(sim.alerts)
    al = lib.load_alerts(tmp_path)
    assert len(al) == len(sim.alerts) and al["planned"].dtype == bool and isinstance(al.iloc[0]["routes"], list)
    lib.append_run(tmp_path, {"kind": "collect", "polls": 3})
    assert lib.load_runs(tmp_path)[0]["polls"] == 3
    lib.save_context(tmp_path, "weather_daily", sim.weather_daily)
    assert len(lib.load_context(tmp_path, "weather_daily")) == len(sim.weather_daily)
    assert lib.load_context(tmp_path, "missing") is None


def test_targets_resolve_and_feeds(static):
    targets = {"targets": [{"id": "gc", "station": "Grand Central", "direction": "N", "routes": ["6", "4"]}], "upstream_stops": 3}
    stops, feeds, resolved = lib.stops_and_feeds(static, targets)
    assert "631N" in stops and "632N" in stops and "640N" in stops
    assert feeds == ["1234567S"] and resolved[0]["stop_id"] == "631N"


def test_line_insights_shape():
    sc = synthetic.make_scenario("signal", 7, 7)
    inc = synthetic.incidents_table(sc)
    out = line_insights(inc, None, None, months=12)
    assert out["months"] and "6" in out["lines"] and out["system"]["ranking"]
    six = out["lines"]["6"]
    assert six["category_mix_3m"][0]["category"] == "Signals"
    assert len(six["monthly_total"]) == len(out["months"])
    assert line_insights(None) == {"months": [], "lines": {}, "system": {}, "categories": []}


def test_build_site_synthetic(tmp_path):
    class Args:
        site_src = str(lib.ROOT / "site"); out = str(tmp_path / "_site")
    index = build_site.build_synthetic(Args())
    out = tmp_path / "_site"
    for f in ("index.html", "app.js", "charts.js", "styles.css", ".nojekyll", "data/index.json", "data/lines.json",
              "data/alerts.json", "data/status.json", "data/reports/grand-central-n.json"):
        assert (out / f).exists(), f
    assert index["targets"][0]["status"] == "ok"
    rep = json.loads((out / "data/reports/grand-central-n.json").read_text())
    assert rep["status"] == "ok" and rep["daily_series"] and rep["bucket_grid"] and rep["hour_table"]
    assert rep["ranked_causes"][0]["cause"] in {"signal", "missing_service", "segment_restriction"}
    alerts = json.loads((out / "data/alerts.json").read_text())["alerts"]
    assert all("active_now" in a for a in alerts)


def test_current_alerts_active_flag():
    now = time.time()
    df = pd.DataFrame([
        {"alert_id": "a", "alert_type": "Delays", "planned": False, "cause_category": "signal", "created_at": now - 600, "updated_at": now - 300,
         "active_start": now - 600, "active_end": None, "routes": ["6"], "stops": [], "header": "x", "description": ""},
        {"alert_id": "b", "alert_type": "Delays", "planned": False, "cause_category": "track", "created_at": now - 190000, "updated_at": now - 180000,
         "active_start": now - 190000, "active_end": now - 180000, "routes": ["6"], "stops": [], "header": "y", "description": ""},
        {"alert_id": "c", "alert_type": "Delays", "planned": False, "cause_category": "track", "created_at": now - 20000, "updated_at": now - 20000,
         "active_start": now - 20000, "active_end": now - 10000, "routes": ["L"], "stops": [], "header": "z", "description": ""},
    ])
    out = {a["alert_id"]: a["active_now"] for a in build_site.current_alerts(df, now)}
    assert out == {"a": True, "c": False}   # b ended more than 24 h ago and is dropped
