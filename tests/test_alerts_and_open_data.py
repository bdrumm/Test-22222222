import json
from pathlib import Path

import pandas as pd

from mta_delay_insights.sources import alerts as al
from mta_delay_insights.sources import open_data as od
from mta_delay_insights.sources import weather as wx

FIX = Path(__file__).parent / "fixtures"


def test_classify_cause():
    assert al.classify_cause("Northbound 6 trains are running with delays because of signal problems at 33 St") == "signal"
    assert al.classify_cause("delays because of a person struck by a train") == "person_on_track"
    assert al.classify_cause("NYPD activity at 14 St") == "police"
    assert al.classify_cause("a train with mechanical problems at Canal St") == "rolling_stock"
    assert al.classify_cause("service change: planned work") == "planned_work"
    assert al.classify_cause("some text") == "unknown"
    assert al.is_planned("Planned - Part Suspended")
    assert not al.is_planned("Delays")
    assert al.alert_kind("Boarding Change") == "notice" and al.alert_kind("Reduced Service") == "delay"
    assert al.alert_kind("Delays") == "delay" and al.alert_kind("Special Schedule") == "planned"
    assert al.classify_cause("[6] runs every 8 minutes") == "reduced_service"
    assert al.classify_cause("all [2] trains at E 180 St board from the uptown platform") == "unknown"
    assert al.classify_cause("delays after we conducted urgent track maintenance at Grand Central") == "track"


def test_alerts_frame_from_fixture():
    df = al.alerts_frame(json.loads((FIX / "subway_alerts_sample.json").read_text()))
    assert len(df) == 3
    sig = df[df["alert_id"] == "lmm:alert:1001"].iloc[0]
    assert sig["alert_type"] == "Delays" and sig["cause_category"] == "signal" and not sig["planned"]
    assert sig["routes"] == ["6"] and sig["active_start"] == 1757336400
    planned = df[df["planned"]]
    assert len(planned) == 1 and planned.iloc[0]["routes"] == ["4", "5"]
    active = al.alerts_active_at(df, 1757337000, route_id="6")
    assert set(active["alert_id"]) == {"lmm:alert:1001"}
    assert al.alerts_active_at(df, 1757337000, route_id="L").empty


def test_open_data_normalizers():
    rows = json.loads((FIX / "trains_delayed_sample.json").read_text())
    td = od.normalize_trains_delayed(pd.DataFrame(rows))
    assert set(td.columns) >= {"month", "line", "reporting_category", "delays"}
    assert td["delays"].sum() == 2600
    assert td["month"].iloc[0] == pd.Timestamp("2026-08-01")
    cj = od.normalize_customer_journey(pd.DataFrame([{"month": "2026-08-01T00:00:00", "line": "6", "period": "peak",
                                                      "additional_platform_time": "1.23", "customer_journey_time_performance": "0.81"}]))
    assert cj["additional_platform_time"].iloc[0] == 1.23
    hourly = od.normalize_hourly_ridership(pd.DataFrame([
        {"transit_timestamp": "2026-09-09T08:00:00", "station_complex_id": "610", "station_complex": "GC", "ridership": "100", "transfers": "5"},
        {"transit_timestamp": "2026-09-09T08:00:00", "station_complex_id": "610", "station_complex": "GC", "ridership": "50", "transfers": "1"},
        {"transit_timestamp": "2026-09-13T08:00:00", "station_complex_id": "610", "station_complex": "GC", "ridership": "40", "transfers": "1"},
    ]))
    assert len(hourly) == 2 and hourly["ridership"].max() == 150
    prof = od.ridership_profile(hourly)
    assert prof[prof["day_type"] == "weekday"]["riders_per_hour"].iloc[0] == 150
    assert prof[prof["day_type"] == "weekend"]["riders_per_hour"].iloc[0] == 40


def test_weather_frame_and_daily():
    payload = {"hourly": {"time": ["2026-09-09T00:00", "2026-09-09T01:00"], "temperature_2m": [20, 33],
                          "precipitation": [6.0, 0.0], "rain": [6.0, 0.0], "snowfall": [0, 0],
                          "wind_speed_10m": [10, 55], "weather_code": [61, 0]}}
    df = wx.weather_frame(payload)
    assert df["heavy_rain"].tolist() == [True, False]
    assert df["extreme_heat"].tolist() == [False, True]
    assert df["adverse"].all()
    daily = wx.daily_summary(df)
    assert daily.iloc[0]["adverse_hours"] == 2 and daily.iloc[0]["precip_mm"] == 6.0
