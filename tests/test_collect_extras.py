"""NYCT extension fields, ETA samples at upstream stops, dwell estimates, pipeline persistence."""
import pandas as pd

from mta_delay_insights.collect.collector import Collector
from mta_delay_insights.collect.dwells import DwellTracker
from mta_delay_insights.sources import gtfs_realtime as rt
from mta_delay_insights.sources import nyct_ext
from mta_delay_insights.storage.db import Store
from pipeline import lib

T0 = 1_790_000_000.0


def _snap(t, stops, train_id="06 0118+ PEL/BBR", tracks=None, vehicle=None):
    return rt.encode_trip_updates([{"trip_id": "007800_6..N01R", "route_id": "6", "start_date": "20260925", "train_id": train_id,
                                    "is_assigned": True, "tracks": tracks or {}, "stops": stops, "vehicle": vehicle}], t)


def test_nyct_extension_roundtrip():
    data = _snap(T0, [("633N", T0 + 60, T0 + 90), ("632N", T0 + 180, T0 + 210)], tracks={"632N": ("4", "3")})
    msg = rt.parse_feed(data)
    df = rt.trip_updates_frame(msg, "1234567S", T0)
    assert list(df["train_id"].unique()) == ["06 0118+ PEL/BBR"] and df["is_assigned"].all()
    row = df[df["stop_id"] == "632N"].iloc[0]
    assert row["sched_track"] == "4" and row["actual_track"] == "3"
    assert nyct_ext.trip_fields(msg.entity[0].trip_update.trip)["nyct_direction"] is None


def test_eta_samples_and_track_fields_through_collector():
    store = Store(":memory:")
    col = Collector(store, ["1234567S"], stops_of_interest={"633N", "632N", "631N", "630N"}, poll_interval_sec=30,
                    fetcher=lambda k: b"", track_dwells=False)
    seq = [("634N", 40), ("633N", 130), ("632N", 220), ("631N", 310), ("630N", 400)]
    # the train advances one stop per two polls; ETAs drift a little
    polls = []
    for i in range(0, 11):
        t = T0 + 30 * i
        remaining = [(s, T0 + off + 5 * i, T0 + off + 5 * i + 30) for s, off in seq if T0 + off + 5 * i > t - 10]
        if not remaining:
            break
        polls.append((t, remaining))
    for t, remaining in polls:
        col.ingest("1234567S", _snap(t, remaining, tracks={"631N": ("4", "4")}), t)
    col.flush(T0 + 600)
    arr = store.arrivals()
    assert set(arr["stop_id"]) >= {"633N", "632N", "631N"}
    assert (arr["train_id"] == "06 0118+ PEL/BBR").all()
    assert arr[arr["stop_id"] == "631N"].iloc[0]["sched_track"] == "4"
    samples = store.eta_samples()
    assert not samples.empty
    # when 634N was served, the ETA for 633N (1 stop ahead) and 632N (2 ahead) was recorded
    s1 = samples[(samples["at_stop"] == "634N") & (samples["stop_id"] == "633N")]
    assert len(s1) == 1 and s1.iloc[0]["stops_ahead"] == 1 and s1.iloc[0]["eta_ts"] > T0
    assert set(samples["stops_ahead"]) <= {1, 2, 3, 5, 8, 12}
    # sample ETA error vs the observed arrival is computable
    j = samples.merge(arr[["trip_key", "stop_id", "arrival_ts"]], on=["trip_key", "stop_id"])
    assert (j["arrival_ts"] - j["eta_ts"]).abs().max() < 120


def test_dwell_tracker_estimates_stop_time():
    dt = DwellTracker({"631N"})
    def veh(status, stop, ts):
        return pd.DataFrame([{"snapshot_ts": ts, "feed": "x", "trip_id": "t1", "route_id": "6", "start_date": "20260925", "direction": "N",
                              "stop_id": stop, "current_stop_sequence": 1, "current_status": status, "vehicle_ts": ts}])
    assert dt.update(veh("IN_TRANSIT_TO", "631N", T0), T0).empty
    assert dt.update(veh("STOPPED_AT", "631N", T0 + 30), T0 + 30).empty
    assert dt.update(veh("STOPPED_AT", "631N", T0 + 30), T0 + 60).empty
    out = dt.update(veh("IN_TRANSIT_TO", "630N", T0 + 90), T0 + 90)
    assert len(out) == 1 and out.iloc[0]["stop_id"] == "631N" and out.iloc[0]["dwell_sec"] == 30 and out.iloc[0]["polls"] == 2
    # stops outside the set of interest are dropped; vanished trains close their dwell
    assert dt.update(veh("STOPPED_AT", "629N", T0 + 120), T0 + 120).empty
    assert dt.update(pd.DataFrame(columns=veh("STOPPED_AT", "629N", 0).columns), T0 + 150).empty


def test_pipeline_persists_samples_and_dwells(tmp_path):
    samples = pd.DataFrame([{"trip_key": "d|t", "route_id": "6", "stop_id": "631N", "at_stop": "633N", "at_ts": T0, "stops_ahead": 2, "eta_ts": T0 + 200}])
    dw = pd.DataFrame([{"trip_key": "d|t", "route_id": "6", "direction": "N", "stop_id": "631N", "stopped_from_ts": T0, "stopped_to_ts": T0 + 40, "dwell_sec": 40, "polls": 2}])
    assert lib.save_eta_samples(tmp_path, samples) and lib.save_dwells(tmp_path, dw)
    assert lib.save_eta_samples(tmp_path, samples)  # idempotent merge
    assert len(lib.load_eta_samples(tmp_path)) == 1 and len(lib.load_dwells(tmp_path, stops={"631N"})) == 1
    assert lib.load_dwells(tmp_path, stops={"999N"}).empty
    net = pd.DataFrame([{"trip_key": "d|t", "trip_id": "t", "route_id": "6", "stop_id": s, "arrival_ts": T0 + i * 86400, "confidence": 1.0}
                        for i, s in enumerate(["631N", "632N", "633N"])])
    lib.save_network_arrivals(tmp_path, net)
    assert len(list((tmp_path / "arrivals_all").glob("*.csv.gz"))) == 3
    assert lib.prune_daily(tmp_path, "arrivals_all", 2) and len(list((tmp_path / "arrivals_all").glob("*.csv.gz"))) == 2
    assert lib.collect_options({"collect": {"all_stops": True}})["all_stops"] is True
