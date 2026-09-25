"""Live projections are recorded per snapshot and scored against the arrivals observed afterwards."""
from __future__ import annotations

import pandas as pd

from mta_delay_insights.realtime import build_live
from mta_delay_insights.realtime.evaluate import evaluate_projections, projections_from_live, summarize_forecast_eval
from pipeline import lib
from tests.test_positions import _snapshot


def test_projections_are_scored_against_observed_arrivals(static, tmp_path):
    sim, now, feeds, held = _snapshot(static, hold_sec=400)
    targets = {"targets": [{"id": "gc", "station": "Grand Central", "direction": "N", "routes": ["6", "4"]}], "upstream_stops": 3}
    _, _, resolved = lib.stops_and_feeds(static, targets)
    for t in resolved:
        rr = lib.resolve_target(static, t); t["upstream"], t["terminals"] = rr["upstream"], rr["terminals"]
    live = build_live(feeds, None, static, resolved, {}, now)
    proj = projections_from_live(live)
    assert proj and all(p["stop_id"] == "631N" for p in proj)
    assert any(p["sim_eta_ts"] is not None for p in proj), "simulation projections should be attached"
    assert any(p["holding"] for p in proj) and any(p["hold_eta_ts"] is not None for p in proj)
    # "what happened": the synthetic truth at the platform
    truth = sim.arrivals[(sim.arrivals["stop_id"] == "631N") & (sim.arrivals["arrival_ts"] > now - 600)]
    ev = evaluate_projections(pd.DataFrame(proj), truth)
    assert len(ev) >= len(proj) * 0.6
    assert ev["feed_err_sec"].abs().median() < 900 and ev["sim_err_sec"].dropna().abs().median() < 900
    # the held train's feed ETA was made optimistic: its feed error must be negative (arrived after the promise)
    row = ev[ev["trip_id"] == held]
    assert not row.empty and row["feed_err_sec"].iloc[0] < 0
    summary = summarize_forecast_eval(ev)
    assert summary["n"] == len(ev) and summary["by_horizon"] and summary["overall"]["feed"]["n"] == len(ev)
    assert {c["corroboration"] for c in summary["corroboration"]} & {"agree", "feed_optimistic", "position_unknown", "no_position"}
    assert summary["held"]["n"] >= 1
    # round trip through the data branch
    lib.save_forecast_eval(tmp_path, ev)
    back = lib.load_forecast_eval(tmp_path)
    assert len(back) == len(ev) and set(back.columns) >= {"made_ts", "trip_id", "feed_err_sec", "sim_err_sec"}
    assert summarize_forecast_eval(pd.DataFrame())["n"] == 0
