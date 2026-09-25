import json
from datetime import date, timedelta

import pandas as pd

from mta_delay_insights import synthetic
from mta_delay_insights.realtime import build_live, live_trains
from mta_delay_insights.realtime.journey import JourneyModel, fit_journey, plan_journey, resolve_journeys
from mta_delay_insights.sources import events as events_src
from mta_delay_insights.sources.gtfs_static import service_midnight
from mta_delay_insights.storage.db import Store
from tests.conftest import START

CFG = {"journeys": [
    {"id": "usq-59", "label": "Union Sq to 59 St via 6 then 4",
     "legs": [{"from": {"station": "14 St-Union Sq", "direction": "N", "routes": ["6"]}, "to": {"station": "Grand Central", "direction": "N", "routes": ["6"]}},
              {"transfer_min": 1, "from": {"station": "Grand Central", "direction": "N", "routes": ["4"]}, "to": {"station": "59 St", "direction": "N", "routes": ["4"]}}]},
    {"id": "direct", "label": "Bleecker to 59 St on the 6",
     "legs": [{"from": {"station": "Bleecker St", "direction": "N", "routes": ["6"]}, "to": {"station": "59 St", "direction": "N", "routes": ["6"]}}]},
]}


def _setup(static, scenario="signal", days=8):
    sc = synthetic.Scenario("t", START, START + timedelta(days=days), START + timedelta(days=2 * days),
                            issues=list(synthetic.SCENARIOS[scenario]), directions=("N",))
    sim = synthetic.simulate(static, sc)
    store = Store(":memory:"); store.insert_arrivals(sim.arrivals); store.upsert_alerts(sim.alerts, seen_ts=0)
    last = sc.end - timedelta(days=1)
    while last.weekday() >= 5:
        last -= timedelta(days=1)
    now = service_midnight(last).timestamp() + 8 * 3600 + 20 * 60
    return sim, store, now


def test_resolve_journeys(static):
    specs = resolve_journeys(static, CFG)
    assert [s.id for s in specs] == ["usq-59", "direct"]
    leg = specs[0].legs[0]
    assert leg.from_stop == "635N" and leg.to_stop == "631N" and leg.routes == ["6"]
    assert leg.stops[0] == "635N" and leg.stops[-1] == "631N" and len(leg.stops) == 5
    assert specs[0].legs[1].transfer_min == 1 and specs[0].legs[1].from_stop == "631N" and specs[0].legs[1].to_stop == "629N"
    assert specs[1].legs[0].stops[-1] == "629N"


def test_fit_journey_learns_from_history(static):
    sim, store, now = _setup(static)
    spec = resolve_journeys(static, CFG)[0]
    events = events_src.holiday_events(START - timedelta(days=30), START + timedelta(days=30))
    model, table = fit_journey(store, static, spec, sim.alerts, sim.weather_daily, events, now)
    assert model.n_samples > 500 and not table.empty
    assert set(["journey_id", "leg", "ride_sec", "sched_ride_sec", "excess_sec", "alert_active", "holiday"]).issubset(table.columns)
    leg0 = model.legs[0]
    assert leg0.n > 200 and "6" in leg0.sched_ride and leg0.sched_ride["6"].get("8")
    assert leg0.wait["6"]["8"]["expected"] > 60
    # the signal scenario makes rides longer while alerts are active: the coefficient is positive
    assert leg0.coef["alert_active"] > 30
    lo, hi = leg0.spread("6", "peak")
    assert lo < 0 < hi
    d = json.loads(json.dumps(model.to_dict()))
    assert JourneyModel.from_dict(d).legs[0].coef["alert_active"] == leg0.coef["alert_active"]


def test_plan_journey_enumerates_catchable_trains(static):
    sim, store, now = _setup(static)
    specs = resolve_journeys(static, CFG)
    model, _ = fit_journey(store, static, specs[0], sim.alerts, sim.weather_daily, None, now)
    feed_key, _, data = synthetic.to_rt_snapshots(sim.arrivals, now, now, poll_interval=30)[0]
    trains = live_trains({feed_key: data}, static, now)
    p = plan_journey(specs[0], model, trains, static, now, sim.alerts, sim.weather_daily, None)
    assert p["options"] and p["best"]
    best = p["best"]
    assert best["depart_ts"] >= now - 30 and best["arrive_ts"] > best["depart_ts"]
    assert len(best["legs"]) == 2 and best["legs"][1]["transfer_sec"] == 60
    assert best["legs"][0]["ride_sec"] > 60 and best["legs"][0]["sched_ride_sec"] > 0
    assert best["total_lo_sec"] <= best["total_sec"] <= best["total_hi_sec"]
    assert all(o["arrive_ts"] >= p["options"][0]["arrive_ts"] for o in p["options"])
    assert p["stringline"][0]["stops"][0]["stop_id"] == "635N" and p["stringline"][0]["trains"]
    # without a model the planner still works on priors
    p2 = plan_journey(specs[1], None, trains, static, now)
    assert p2["options"] and p2["best"]["legs"][0]["ride_source"] == "feed"
    live = build_live({feed_key: data}, sim.alerts, static, [], None, now, journeys=specs, journey_models={specs[0].id: model})
    assert [j["id"] for j in live["journeys"]] == ["usq-59", "direct"]
    json.dumps(live, default=str)


def test_event_features_and_holidays():
    rss = "<rss><channel><item><title>F train delays after signal problems</title><pubDate>Thu, 25 Sep 2026 08:00:00 -0400</pubDate></item></channel></rss>"
    news = pd.DataFrame(events_src.parse_rss(rss))
    assert news.iloc[0]["routes"] == ["F"] and news.iloc[0]["weight"] == 0.6
    ts = float(news.iloc[0]["ts_start"]) + 600
    f = events_src.event_features(news, ts, ["F"])
    assert f["news_w"] == 0.6 and f["events_any"] == 0.6
    assert events_src.event_features(news, ts, ["L"])["news_w"] == 0.0
    hol = events_src.us_federal_holidays(2026)
    assert date(2026, 11, 26) in hol and date(2026, 7, 3) in hol   # Thanksgiving; July 4 observed on Friday
    ev = events_src.normalize_nyc_events(pd.DataFrame([{"event_name": "Marathon", "start_date_time": "2026-11-01T08:00:00",
                                                        "end_date_time": "2026-11-01T16:00:00", "event_type": "Race", "event_borough": "Manhattan"}]))
    assert ev.iloc[0]["weight"] == 0.8 and ev.iloc[0]["kind"] == "race"
