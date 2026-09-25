from datetime import timedelta

from mta_delay_insights import synthetic
from mta_delay_insights.realtime import build_live, live_trains
from google.transit import gtfs_realtime_pb2 as pb

from mta_delay_insights.sources import gtfs_realtime as rt
from mta_delay_insights.sources.gtfs_static import service_midnight
from mta_delay_insights.storage.db import Store
from tests.conftest import START


def _snapshot(static, hold_sec=0.0):
    sc = synthetic.Scenario("t", START, START + timedelta(days=1), START + timedelta(days=3), issues=[], directions=("N",))
    sim = synthetic.simulate(static, sc)
    last = sc.end - timedelta(days=1)
    now = service_midnight(last).timestamp() + 8 * 3600 + 20 * 60
    feed_key, _, data = synthetic.to_rt_snapshots(sim.arrivals, now, now)[0]
    if hold_sec:
        msg = rt.parse_feed(data)
        held = None
        for ent in msg.entity:
            if ent.HasField("vehicle") and ent.vehicle.current_status == pb.VehiclePosition.VehicleStopStatus.Value("STOPPED_AT") and ent.vehicle.stop_id == "633N":
                ent.vehicle.timestamp = int(now - hold_sec); held = ent.vehicle.trip.trip_id
                break
        assert held, "no train stopped at 28 St in this snapshot"
        # the feed has not caught up with the hold: its ETAs still say the train is about to move (optimistic)
        for ent in msg.entity:
            if ent.HasField("trip_update") and ent.trip_update.trip.trip_id == held:
                for stu in ent.trip_update.stop_time_update:
                    if stu.HasField("arrival"):
                        stu.arrival.time = max(int(now + 5), stu.arrival.time - 200)
                    if stu.HasField("departure"):
                        stu.departure.time = max(int(now + 35), stu.departure.time - 200)
        data = msg.SerializeToString()
        return sim, now, {feed_key: data}, held
    return sim, now, {feed_key: data}, None


def test_positions_are_fused_into_live_trains(static):
    sim, now, feeds, _ = _snapshot(static)
    trains = [t for t in live_trains(feeds, static, now) if t.started]
    with_pos = [t for t in trains if t.pos_status]
    assert len(with_pos) >= len(trains) * 0.8
    assert {t.pos_status for t in with_pos} <= {"STOPPED_AT", "IN_TRANSIT_TO", "INCOMING_AT"}
    moving = [t for t in with_pos if t.pos_status == "IN_TRANSIT_TO"]
    assert moving and all(t.expected_run_sec is not None and t.expected_run_sec >= 30 for t in moving)
    assert not any(t.stalled for t in with_pos) and not any(t.holding for t in with_pos)
    assert all(t.corroboration in ("agree", "position_unknown", "feed_optimistic") for t in with_pos)
    assert sum(1 for t in with_pos if t.corroboration == "feed_optimistic") <= 2
    d = with_pos[0].as_dict(static)
    assert d["position"]["status"] == with_pos[0].pos_status and "effective_lateness_sec" in d


def test_held_train_is_flagged_and_raises_lateness(static):
    sim, now, feeds, held = _snapshot(static, hold_sec=400)
    trains = {t.trip_id: t for t in live_trains(feeds, static, now)}
    t = trains[held]
    assert t.pos_status == "STOPPED_AT" and t.holding and t.since_update_sec >= 400
    assert t.position_lateness_sec is not None and t.position_lateness_sec > (t.lateness_sec or 0) + 60
    assert t.corroboration == "feed_optimistic" and t.effective_lateness_sec == t.position_lateness_sec
    live = build_live(feeds, None, static, [], None, now)
    r6 = next(r for r in live["routes"] if r["route_id"] == "6" and r["direction"] == "N")
    assert r6["positions"]["holding"] >= 1 and r6["status"] in ("degraded", "disrupted")
    kinds = {x.get("kind") for x in live["incidents_developing"]}
    assert "holding" in kinds and any("no alert posted yet" in x["text"] for x in live["incidents_developing"])


def test_unmatched_trip_id_falls_back_to_the_nearest_scheduled_trip(static):
    sim, now, feeds, _ = _snapshot(static)
    key, data = next(iter(feeds.items()))
    msg = rt.parse_feed(data)
    renamed = None
    for ent in msg.entity:
        if ent.HasField("trip_update") and ent.trip_update.trip.route_id == "6" and len(ent.trip_update.stop_time_update) > 3:
            renamed = ent.trip_update.trip.trip_id
            new_id = renamed.replace(renamed.split("_")[0], "999999", 1)     # origin time not in the timetable
            ent.trip_update.trip.trip_id = new_id
            for v in msg.entity:
                if v.HasField("vehicle") and v.vehicle.trip.trip_id == renamed:
                    v.vehicle.trip.trip_id = new_id
            renamed = new_id
            break
    assert renamed
    trains = {t.trip_id: t for t in live_trains({key: msg.SerializeToString()}, static, now)}
    t = trains[renamed]
    assert static.match_trip(renamed, t.service_date) is None
    assert t.sched_matched and t.sched_method == "nearest" and t.sched_trip_id and t.lateness_sec is not None and abs(t.lateness_sec) < 900
    if t.pos_status:
        assert t.corroboration in ("agree", "feed_optimistic")
