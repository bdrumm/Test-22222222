"""Vectorised context features (time bands, hourly weather, alert / event timelines), day-pattern profiles and
the time-band lateness-carry tables agree with their straightforward definitions."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from mta_delay_insights.models import context as C
from mta_delay_insights.models.arrival import Profiles
from mta_delay_insights.realtime import client_model as cm
from mta_delay_insights.sources.gtfs_static import NY_TZ


def _ts(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=NY_TZ).timestamp()


def test_time_features_bands_and_day_types():
    ts = np.array([_ts(2026, 9, 21, 8, 30), _ts(2026, 9, 21, 23), _ts(2026, 9, 26, 12), _ts(2026, 9, 27, 3), _ts(2026, 9, 7, 9)])  # Mon, Mon, Sat, Sun, Labor Day
    f = C.time_features(ts)
    assert f["band"].tolist() == [1, 4, 5, 6, 5] and f["daytype"].tolist() == [0, 0, 1, 2, 2]
    assert f["peak"].tolist() == [1, 0, 0, 0, 0] and f["holiday"].tolist() == [0, 0, 0, 0, 1] and f["weekend"].tolist() == [0, 0, 1, 1, 0]
    assert abs(f["hour"].iloc[0] - 8.5) < 1e-9 and f["dow"].tolist() == [0, 0, 5, 6, 0]
    # the clients' band rule (no holidays) matches the model's for ordinary days
    for t, b in zip(ts[:4], f["band"].tolist()[:4]):
        assert cm.band_at(t) == C.BANDS[int(b)]


def test_weather_hourly_lookup_and_rolling_rain():
    hours = pd.date_range("2026-09-26 00:00", periods=6, freq="h")
    df = pd.DataFrame({"ts": hours, "temp_c": [10, 11, 12, 13, 14, 15], "precip_mm": [0, 2.0, 1.0, 0, 0, 0], "rain_mm": 0, "snow_cm": 0,
                       "wind_kmh": 5, "weather_code": [0, 61, 61, 3, 3, 95]})
    w = C.WeatherHourly.from_frame(df)
    ts = np.array([_ts(2026, 9, 26, 2, 30), _ts(2026, 9, 26, 4, 59), _ts(2026, 9, 26, 9), _ts(2026, 9, 25, 23)])
    x = w.at(ts)
    assert x["temp_c"].tolist()[:2] == [12.0, 14.0] and x["precip_hr_mm"].iloc[0] == 1.0 and x["precip_3h_mm"].iloc[0] == 3.0
    assert x["wcode_group"].tolist()[:2] == [4.0, 1.0]
    assert np.isnan(x["temp_c"].iloc[2]) and np.isnan(x["temp_c"].iloc[3])        # beyond the data: unknown, not zero


def test_alert_timeline_matches_naive_loop_and_marks_uncovered_time():
    t0 = _ts(2026, 9, 26, 6)
    alerts = pd.DataFrame([
        {"alert_id": "a", "alert_type": "Delays", "header": "signal problems", "routes": ["6"], "active_start": t0, "active_end": t0 + 1800, "updated_at": t0, "last_seen_ts": t0 + 1800, "cause_category": "signal"},
        {"alert_id": "b", "alert_type": "Delays", "header": "sick passenger", "routes": ["6", "4"], "active_start": t0 + 600, "active_end": t0 + 3600, "updated_at": t0 + 600, "last_seen_ts": t0 + 3600, "cause_category": "medical"},
        {"alert_id": "c", "alert_type": "Planned - Part Suspended", "header": "planned work", "routes": ["F"], "active_start": t0 - 86400, "active_end": t0 + 86400, "updated_at": t0, "last_seen_ts": t0 + 3600, "cause_category": "planned"},
        {"alert_id": "d", "alert_type": "Delays", "header": "no routes listed", "routes": [], "active_start": t0 + 7000, "active_end": None, "updated_at": t0 + 7000, "last_seen_ts": t0 + 7200, "cause_category": None},
    ])
    tl = C.AlertTimeline.from_live(alerts)
    assert tl.coverage[0] == t0 + 1800 - 600 and tl.coverage[1] == t0 + 7200     # first poll - grace .. last poll
    ts = t0 + np.arange(-1200, 9000, 60.0)
    rng = np.random.default_rng(1)
    routes = rng.choice(["6", "4", "F", "L"], len(ts))
    f = tl.features(ts, routes)
    # naive reference
    for i, (t, r) in enumerate(zip(ts, routes)):
        on = [a for a in alerts.itertuples(index=False) if (a.active_start <= t <= (a.active_end if pd.notna(a.active_end) else a.last_seen_ts + 1200)) and (not a.routes or r in a.routes)]
        delay = [a for a in on if not a.alert_type.startswith("Planned")]
        if t < tl.coverage[0] or t > tl.coverage[1]:    # outside the observed span: unknown, not "no alert"
            assert np.isnan(f["alert_active"].iloc[i])
            continue
        assert f["alert_active"].iloc[i] == float(bool(delay)) and f["alert_n"].iloc[i] == len(delay)
        assert f["planned_active"].iloc[i] == float(any(a.alert_type.startswith("Planned") for a in on))
        if delay:
            newest = max(delay, key=lambda a: a.active_start)
            assert abs(f["alert_age_min"].iloc[i] - (t - newest.active_start) / 60) < 1e-6
            assert f["cause_code"].iloc[i] == C.cause_code(newest.cause_category)
        else:
            assert np.isnan(f["alert_age_min"].iloc[i]) and f["cause_code"].iloc[i] == 0
    assert f["net_alert_n"].max() == 2.0


def test_event_timeline_weights_by_kind_and_route():
    t0 = _ts(2026, 9, 26, 19)
    ev = pd.DataFrame([
        {"ts_start": t0, "ts_end": t0 + 3 * 3600, "kind": "venue_event", "weight": 0.9, "routes": ["7"], "title": "x"},
        {"ts_start": t0, "ts_end": t0 + 3600, "kind": "street_event", "weight": 0.5, "routes": [], "title": "y"},
        {"ts_start": t0 - 86400, "ts_end": t0 + 86400, "kind": "holiday", "weight": 1.0, "routes": [], "title": "z"},
        {"ts_start": t0, "ts_end": t0 + 3600, "kind": "news", "weight": 0.6, "routes": "[]", "title": "w"},
    ])
    tl = C.EventTimeline.from_frame(ev)
    f = tl.features(np.array([t0 + 600, t0 + 600, t0 + 5 * 3600]), np.array(["7", "A", "7"]))
    assert f["venue_event_w"].tolist() == [0.9, 0.0, 0.9] and f["street_event_w"].tolist() == [0.5, 0.5, 0.0]
    assert f["news_w"].tolist() == [0.6, 0.6, 0.0] and f["holiday_event"].tolist() == [1.0, 1.0, 1.0]


def test_profiles_shrink_toward_route_level_and_apply():
    rng = np.random.default_rng(0)
    n = 4000
    rows = pd.DataFrame({"trip_key": [f"t{i // 4}" for i in range(n)], "u": rng.choice(["a", "b"], n), "d": rng.choice(["c", "d"], n),
                         "route_id": "6", "direction": "N", "k": 2, "daytype": rng.choice([0, 1], n), "hour": rng.choice([8.0, 13.0], n),
                         "lateness_u": rng.normal(40, 10, n)})
    rows["delta_sec"] = np.where((rows["daytype"] == 0) & (rows["hour"] == 8.0), 90.0, 10.0) + rng.normal(0, 5, n)
    p = Profiles().fit(rows)
    assert p.ready and p.n_rows == n
    probe = pd.DataFrame({"u": ["a", "a", "zz"], "d": ["c", "c", "zz"], "route_id": "6", "direction": "N", "k": 2, "daytype": [0, 1, 0], "hour": [8.2, 8.0, 8.0], "lateness_u": 0.0})
    f = p.apply(probe)
    assert abs(f["prof_seg_excess"].iloc[0] - 90) < 8 and abs(f["prof_seg_excess"].iloc[1] - 10) < 8
    assert abs(f["prof_seg_excess"].iloc[2] - 90) < 8              # unseen segment: the route / k / day type / hour fallback
    assert abs(f["prof_route_lateness"].iloc[0] - 40) < 5


def test_carry_lookup_falls_back_route_band_route_band_all():
    table = lambda v: {"slope": [v], "intercept": [0.0], "resid_std": [30.0], "n": [10]}
    model = {"lateness_carry": {"all": table(1.0), "by_route": {"6": table(0.8)}, "by_band": {"am_peak": table(0.9)},
                                "by_route_band": {"6": {"am_peak": table(0.7)}}}}
    assert cm.carry_at(model, "6", 1, "am_peak")["slope"] == 0.7
    assert cm.carry_at(model, "6", 1, "midday")["slope"] == 0.8
    assert cm.carry_at(model, "L", 1, "am_peak")["slope"] == 0.9
    assert cm.carry_at(model, "L", 1, "midday")["slope"] == 1.0
    assert cm.carry_at(model, "6", 1)["slope"] == 0.8 and cm.carry_at(model, "6", 2, "am_peak") is None
    assert cm.band_of_hour(8, 0) == 1 and cm.band_of_hour(12, 5) == 5 and cm.band_of_hour(2, 6) == 6 and cm.band_of_hour(22, 2) == 4
