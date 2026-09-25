import io
import tarfile
from datetime import date

import pandas as pd

from mta_delay_insights.sources import subwaydata

TRIPS = """trip_uid,trip_id,route_id,direction_id,start_time,vehicle_id,last_observed,marked_past,num_updates,num_schedule_changes,num_schedule_rewrites
1790136000_7..S,024000_7..S,7,1,1790136000,07 0400 MST/34H,1790153339,1790153342,1933,0,0
1790136420_4..N13R,024700_4..N13R,4,0,1790136420,04 0407  NLT/WDL,1790156261,,2848,0,0
"""
STOPS = """trip_uid,stop_id,track,arrival_time,departure_time,last_observed,marked_past
1790136000_7..S,701S,2,,1790150400,1790150420,1790150423
1790136000_7..S,702S,1,1790150632,1790150652,1790150654,1790150657
1790136420_4..N13R,401N,1,1790140000,1790140020,1790140030,
1790136420_4..N13R,402N,1,,,1790140100,
"""


def _archive() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tf:
        for name, text in (("subwaydatanyc_2026-09-23_trips.csv", TRIPS), ("subwaydatanyc_2026-09-23_stop_times.csv", STOPS)):
            data = text.encode(); info = tarfile.TarInfo(name); info.size = len(data); tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_parse_and_normalise():
    trips, stops = subwaydata.parse_archive(_archive())
    arr = subwaydata.to_arrivals(trips, stops)
    assert len(arr) == 3
    # start_time is the service day at 00:00 UTC plus the origin time in the trip id (04:00 -> 240.00)
    assert subwaydata._origin_sec("024000_7..S") == 14400 and (arr["start_date"] == "20260923").all()
    assert (subwaydata.to_arrivals(trips, stops, service_date=date(2026, 9, 23))["start_date"] == "20260923").all()                      # the row with neither arrival nor departure is dropped
    r = arr[arr["stop_id"] == "701S"].iloc[0]
    assert r["arrival_ts"] == 1790150400 and r["direction"] == "S" and r["train_id"] == "07 0400 MST/34H" and r["actual_track"] == "2"
    assert r["trip_key"] == "20260923|024000_7..S" and r["confidence"] == 0.95 and r["source"] == "subwaydata"
    r4 = arr[arr["stop_id"] == "401N"].iloc[0]
    assert r4["confidence"] == 0.75 and r4["route_id"] == "4"
    assert list(arr.columns) == list(subwaydata.ARRIVAL_COLUMNS)


def test_backfill_days_tolerates_failures():
    def fetch(d):
        if d == date(2026, 9, 22):
            raise RuntimeError("404")
        return _archive()
    out = subwaydata.backfill_days([date(2026, 9, 23), date(2026, 9, 22)], fetch=fetch)
    assert len(out[date(2026, 9, 23)]) == 3 and out[date(2026, 9, 22)].empty and "404" in out[date(2026, 9, 22)].attrs["error"]
    assert subwaydata.missing_days({"2026-09-24"}, 3, today=date(2026, 9, 25)) == [date(2026, 9, 23), date(2026, 9, 22)]
