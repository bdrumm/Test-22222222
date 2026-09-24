from datetime import date, timedelta

import numpy as np
import pandas as pd

from mta_delay_insights import synthetic
from mta_delay_insights.collect.arrivals import ArrivalTracker
from mta_delay_insights.collect.collector import Collector
from mta_delay_insights.sources import gtfs_realtime as rt
from mta_delay_insights.sources.gtfs_static import service_midnight
from mta_delay_insights.storage.db import Store


def test_encode_parse_roundtrip():
    data = rt.encode_trip_updates([{"trip_id": "007800_6..N01R", "route_id": "6", "start_date": "20260909",
                                    "stops": [("633N", 1000, 1030), ("632N", 1100, None)],
                                    "vehicle": {"stop_id": "633N", "status": "INCOMING_AT"}}], feed_ts=900)
    msg = rt.parse_feed(data)
    tu = rt.trip_updates_frame(msg, "1234567S", snapshot_ts=900)
    assert list(tu["stop_id"]) == ["633N", "632N"]
    assert tu["direction"].tolist() == ["N", "N"]
    assert tu.loc[1, "departure_ts"] == 1100  # departure falls back to arrival
    vp = rt.vehicle_positions_frame(msg, "1234567S", 900)
    assert vp.loc[0, "current_status"] == "INCOMING_AT"


def test_tracker_emits_arrival_when_stop_drops_off():
    t = ArrivalTracker(poll_interval_sec=30)
    cols = ["snapshot_ts", "feed_ts", "feed", "trip_id", "route_id", "start_date", "direction", "stop_id",
            "stop_sequence", "arrival_ts", "departure_ts", "schedule_relationship"]
    def frame(ts, stops):
        return pd.DataFrame([[ts, ts, "f", "t1", "6", "20260909", "N", s, None, a, a, "SCHEDULED"] for s, a in stops], columns=cols)
    assert t.update(frame(1000, [("633N", 1050), ("632N", 1150)]), 1000).empty
    assert t.update(frame(1030, [("633N", 1055), ("632N", 1150)]), 1030).empty
    out = t.update(frame(1060, [("632N", 1152)]), 1060)
    assert len(out) == 1 and out.iloc[0]["stop_id"] == "633N"
    assert out.iloc[0]["arrival_ts"] == 1055 and out.iloc[0]["source"] == "rt_dropoff"
    assert out.iloc[0]["n_predictions"] == 2 and out.iloc[0]["pred_drift_sec"] == 5
    # Trip vanishes: remaining stop with a past prediction is emitted at lower confidence.
    assert t.update(frame(1090, []), 1090).empty          # one missed poll tolerated
    out2 = t.update(frame(1200, []), 1200)
    assert len(out2) == 1 and out2.iloc[0]["source"] == "trip_vanished" and out2.iloc[0]["confidence"] < 1.0


def test_collector_replay_recovers_true_arrivals(static):
    day = date(2026, 9, 9)
    sc = synthetic.Scenario("none", day, day, day + timedelta(days=1), issues=[], directions=("N",))
    sim = synthetic.simulate(static, sc)
    t0 = service_midnight(day).timestamp() + 8 * 3600
    snaps = synthetic.to_rt_snapshots(sim.arrivals, t0, t0 + 3600, poll_interval=30)
    store = Store(":memory:")
    col = Collector(store, ["1234567S"], stops_of_interest={"631N", "632N"}, poll_interval_sec=30)
    n = col.replay(snaps)
    assert n > 0
    got = store.arrivals("631N", t0 + 300, t0 + 3300)
    truth = sim.arrivals[(sim.arrivals["stop_id"] == "631N") & (sim.arrivals["arrival_ts"].between(t0 + 300, t0 + 3300))]
    assert abs(len(got) - len(truth)) <= 2
    j = got.merge(truth[["trip_key", "arrival_ts"]], on="trip_key", suffixes=("", "_true"))
    err = (j["arrival_ts"] - j["arrival_ts_true"]).abs()
    assert err.median() < 30
    assert (got["confidence"] >= 0.8).mean() > 0.8
    stats = store.snapshot_stats()
    assert int(stats["polls"].iloc[0]) == len(snaps)
