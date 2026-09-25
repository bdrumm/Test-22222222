import pandas as pd

from mta_delay_insights.sources import alerts_archive as aa
from mta_delay_insights.sources import context_feeds as cf

ROWS = [
    {"alert_id": "534411", "event_id": "260975", "update_number": "0", "date": "2026-07-30T23:42:00.000", "agency": "NYCT Subway",
     "status_label": "delays", "affected": "E | F", "header": "Downtown E/F trains are delayed while we address a signal problem at 5 Av/53 St."},
    {"alert_id": "534413", "event_id": "260975", "update_number": "1", "date": "2026-07-30T23:52:00.000", "agency": "NYCT Subway",
     "status_label": "delays", "affected": "E | F", "header": "Downtown E/F trains are running with delays while we address a signal problem at 5 Av/53 St."},
    {"alert_id": "534416", "event_id": "260976", "update_number": "0", "date": "2026-07-30T08:15:00.000", "agency": "NYCT Subway",
     "status_label": "delays", "affected": "A | H", "header": "A/Rockaway Park Shuttle trains are delayed in both directions while the South Channel Bridge opens for marine traffic to pass."},
    {"alert_id": "534420", "event_id": "260980", "update_number": "0", "date": "2026-07-31T08:20:00.000", "agency": "NYCT Subway",
     "status_label": "planned-work", "affected": "F", "header": "Planned work: F trains skip 4 Av-9 St."},
]


def test_archive_normalise_events_and_climatology():
    arch = aa.normalize(pd.DataFrame(ROWS))
    assert len(arch) == 4 and arch.loc[0, "routes"] == ["E", "F"] and arch.loc[0, "cause_category"] == "signal"
    assert arch["planned"].tolist() == [False, False, False, True]
    ev = aa.events(arch)
    assert len(ev) == 3
    e = ev[ev["event_id"] == "260975"].iloc[0]
    assert e["n_updates"] == 2 and abs(e["duration_min"] - 10) < 1e-6 and e["routes"] == ["E", "F"]
    clim = aa.climatology(ev, weeks=4.0)
    assert clim["n_events"] == 2                       # planned work excluded
    routes = {r["route"]: r for r in clim["by_route"]}
    assert set(routes) == {"E", "F", "A", "H"} and routes["E"]["per_week"] == 0.25
    assert sum(clim["per_week_by_hour"]) == 0.5 and len(clim["grid_by_route"]["E"]) == 7
    assert clim["by_cause"][0]["cause"] in ("signal", "other", "unknown", "bridge")
    p = aa.disruption_probability(clim, "E", e["start_ts"])
    assert p == 0.25 and aa.disruption_probability(clim, "Q", e["start_ts"]) is None


def test_nws_features():
    a = pd.DataFrame([{"id": "x", "event": "Flood Watch", "severity": "Severe", "urgency": "Expected", "onset_ts": 100.0, "ends_ts": 200.0, "area": "Kings", "headline": "h"},
                      {"id": "y", "event": "Heat Advisory", "severity": "Moderate", "urgency": "Expected", "onset_ts": 150.0, "ends_ts": None, "area": "Bronx", "headline": "h"}])
    f = cf.nws_features(a, 160.0)
    assert f["nws_any"] == 1.0 and f["nws_severe"] == 1.0 and f["nws_kinds"] == ["Flood Watch", "Heat Advisory"]
    assert cf.nws_features(a, 250.0)["nws_kinds"] == ["Heat Advisory"] and cf.nws_features(a, 50.0)["nws_any"] == 0.0
    assert cf.nws_features(None, 1.0)["nws_any"] == 0.0
