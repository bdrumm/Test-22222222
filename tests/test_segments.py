"""Realized inter-station runs: tracker, distances, speed profile."""
from __future__ import annotations

import pandas as pd

from mta_delay_insights.analysis.segments import segment_profile
from mta_delay_insights.collect.dwells import SegmentTracker
from pipeline import lib

T0 = int(pd.Timestamp("2026-09-21 08:00", tz="America/New_York").timestamp())


def _veh(status, stop, ts, trip="t1"):
    return pd.DataFrame([{"snapshot_ts": ts, "feed": "x", "trip_id": trip, "route_id": "6", "start_date": "20260921", "direction": "N",
                          "stop_id": stop, "current_stop_sequence": 1, "current_status": status, "vehicle_ts": ts}])


def test_segment_tracker_times_a_run_from_state_transitions():
    st = SegmentTracker()
    assert st.update(_veh("STOPPED_AT", "633N", T0), T0 + 5).empty                       # at 28 St
    assert st.update(_veh("IN_TRANSIT_TO", "632N", T0 + 40), T0 + 35).empty             # departed 08:00:40 (feed timestamp), seen at the next poll
    assert st.update(_veh("IN_TRANSIT_TO", "632N", T0 + 40), T0 + 65).empty
    out = st.update(_veh("STOPPED_AT", "632N", T0 + 133), T0 + 95)                        # arrived 08:02:13
    assert len(out) == 1
    r = out.iloc[0]
    assert r["from_stop"] == "633N" and r["to_stop"] == "632N" and r["run_sec"] == 93 and r["depart_ts"] == T0 + 40 and r["arrive_ts"] == T0 + 133
    # a train that jumps from stop to stop without a transit observation gives no run (departure time unknown)
    assert st.update(_veh("STOPPED_AT", "631N", T0 + 300), T0 + 305).empty
    # a status-less vehicle counts as in transit (GTFS-RT default); future-dated vehicles are ignored
    st2 = SegmentTracker(); st2.update(_veh("STOPPED_AT", "633N", T0), T0)
    st2.update(_veh(None, "632N", T0 + 30), T0 + 30)
    assert len(st2.update(_veh("STOPPED_AT", "632N", T0 + 110), T0 + 120)) == 1
    assert st2.update(_veh("STOPPED_AT", "631N", T0 + 9000), T0 + 150).empty


def test_segment_lengths_and_profile(static, tmp_path):
    dist = static.segment_lengths("6", "N"); seq = static.canonical_stop_sequence("6", "N")
    assert len(dist) == len(seq) - 1 and all(d and 200 < d < 3000 for d in dist)
    run = static.canonical_run_sec("6", "N")
    assert len(run) == len(dist) and all(r and 30 <= r <= 600 for r in run)
    rows = []
    for day in range(2):
        for k in range(8):
            t = T0 + day * 86400 + k * 600
            for i in range(len(seq) - 1):
                slow = 1.5 if seq[i + 1] == "632N" else 1.0          # 33 St is a slow zone
                rows.append({"trip_key": f"d|{day}-{k}", "route_id": "6", "direction": "N", "from_stop": seq[i], "to_stop": seq[i + 1],
                             "depart_ts": t + i * 100, "arrive_ts": t + i * 100 + run[i] * slow, "run_sec": run[i] * slow})
    df = pd.DataFrame(rows)
    prof = segment_profile(df, static)
    assert prof["n"] == len(df) and prof["days"] == 2 and prof["n_segments"] == len(seq) - 1
    key = "6_N|632N"; sg = prof["by_key"][key]
    assert sg["consecutive"] and sg["dist_m"] and sg["sched_run_sec"] and abs(sg["ratio"] - 1.5) < 0.05 and sg["speed_kmh"] < sg["sched_speed_kmh"]
    assert prof["slow"] and prof["slow"][0]["to_stop"] == "632N" and all(x["ratio"] < 1.2 for x in prof["fast"][:1])
    assert sg["by_hour_run_sec"][8] is not None and sum(1 for v in sg["by_hour_run_sec"] if v is not None) >= 1
    assert segment_profile(None, static)["n"] == 0
    assert lib.save_segment_runs(tmp_path, df) and len(lib.load_segment_runs(tmp_path)) == len(df)
