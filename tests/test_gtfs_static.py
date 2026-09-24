from datetime import date

from mta_delay_insights.sources.gtfs_static import (direction_from_stop_id, direction_from_trip_id,
                                                    gtfs_time_to_seconds, origin_time_seconds, rt_trip_suffix)


def test_time_helpers():
    assert gtfs_time_to_seconds("00:06:00") == 360
    assert gtfs_time_to_seconds("25:10:30") == 25 * 3600 + 630
    assert direction_from_stop_id("631N") == "N" and direction_from_stop_id("631") is None
    assert direction_from_trip_id("089150_6..S01R") == "S"
    assert rt_trip_suffix("ASP26GEN-6091-Weekday-00_007800_6..N01R") == "007800_6..N01R"
    assert rt_trip_suffix("007800_6..N01R") == "007800_6..N01R"
    assert origin_time_seconds("007800_6..N01R") == 78 * 60


def test_station_lookup_and_topology(static):
    st = static.find_stations("Grand Central")
    assert list(st["stop_id"]) == ["631"]
    assert static.platform_for("631", "N") == "631N"
    assert static.routes_serving("631N") == ["4", "6"]
    assert static.upstream_stops("6", "N", "631N", 3) == ["632N", "633N", "634N"]
    assert static.upstream_stops("4", "N", "631N", 3) == ["635N", "640N"]
    assert static.terminal_stop("6", "N") == "640N"
    merges = static.merge_routes_upstream("6", "N", "631N", 6)
    assert "4" in merges and "635N" in merges["4"]


def test_schedule_events_and_headways(static):
    d = date(2026, 9, 9)  # Wednesday
    assert static.active_services(d) == {"Weekday"}
    ev = static.scheduled_stop_events("631N", d, ["6"])
    assert len(ev) > 100
    assert (ev["direction"] == "N").all()
    hw = static.scheduled_headways("631N", d, ["6"])
    peak = hw[(hw["arrival_sec"] >= 8 * 3600) & (hw["arrival_sec"] < 9 * 3600)]
    assert peak["headway_sec"].median() == 240
    rt_id = ev.iloc[10]["trip_id"].split("_", 1)[1]
    assert static.match_trip(rt_id, d) == ev.iloc[10]["trip_id"]
    assert static.match_trip("999999_6..N01R", d) is None
