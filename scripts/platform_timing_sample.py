"""Poll the subway feeds every 10 s and keep, per poll, every train's vehicle position and its trip update's
times at its next three stops: the raw material for when a train is really at a platform versus the time the
collector records for it (the PlatformTiming table in the app and RECORDED_LAG in mta_delay_insights/trips.py).

    .venv/bin/python scripts/platform_timing_sample.py 30        # minutes; writes data/platform_timing/<start>/
    .venv/bin/python scripts/platform_timing_analyze.py           # the newest run; prints the table for Core/PlatformTiming.swift
"""
import sys, time, csv, os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from mta_delay_insights.sources import gtfs_realtime as g

FEEDS = ["ace", "bdfm", "g", "l", "nqrw", "1234567S"]
OUT = os.path.join(ROOT, "data", "platform_timing", datetime.now().strftime("%Y%m%d-%H%M")); os.makedirs(OUT, exist_ok=True)
minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 40
end = time.time() + minutes * 60
vp = open(os.path.join(OUT, "positions.csv"), "a", newline=""); vw = csv.writer(vp)
tu = open(os.path.join(OUT, "updates.csv"), "a", newline=""); tw = csv.writer(tu)
if vp.tell() == 0: vw.writerow(["poll_ts", "feed", "feed_ts", "trip_id", "route_id", "stop_id", "status", "vehicle_ts"])
if tu.tell() == 0: tw.writerow(["poll_ts", "feed", "feed_ts", "trip_id", "route_id", "seq", "stop_id", "arrival_ts", "departure_ts"])

def one(key):
    try:
        return key, g.fetch_feed(key)
    except Exception as exc:
        return key, exc

n = 0
with ThreadPoolExecutor(6) as ex:
    while time.time() < end:
        t0 = time.time()
        for key, feed in ex.map(one, FEEDS):
            if isinstance(feed, Exception):
                print("feed", key, "failed:", feed, flush=True); continue
            fts = feed.header.timestamp
            for ent in feed.entity:
                if ent.HasField("vehicle"):
                    v = ent.vehicle
                    st = g.rt.VehiclePosition.VehicleStopStatus.Name(v.current_status) if v.HasField("current_status") else ""
                    vw.writerow([round(t0, 1), key, fts, v.trip.trip_id, v.trip.route_id, v.stop_id, st, v.timestamp if v.HasField("timestamp") else ""])
                if ent.HasField("trip_update"):
                    u = ent.trip_update
                    for i, s in enumerate(u.stop_time_update[:3]):
                        a = s.arrival.time if s.HasField("arrival") else ""
                        d = s.departure.time if s.HasField("departure") else ""
                        tw.writerow([round(t0, 1), key, fts, u.trip.trip_id, u.trip.route_id, i, s.stop_id, a, d])
        vp.flush(); tu.flush(); n += 1
        if n % 30 == 0: print(f"{n} polls", flush=True)
        time.sleep(max(0.5, 10 - (time.time() - t0)))
print("done", n, "polls")
