"""When a train is really at a platform (the vehicle feed: first poll STOPPED_AT the stop, and the poll it
moved on) against the time the collector records for it (the trip update's last time for the stop before it
dropped off; the store's arrival_ts) and the feed's ETA minutes before."""
import os, sqlite3, sys, glob
import numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = sys.argv[1] if len(sys.argv) > 1 else sorted(glob.glob(os.path.join(ROOT, "data", "platform_timing", "*")))[-1]
vp = pd.read_csv(os.path.join(D, "positions.csv"))
tu = pd.read_csv(os.path.join(D, "updates.csv"))
vp = vp.dropna(subset=["stop_id", "trip_id"])
polls = np.sort(vp["poll_ts"].unique())
step = np.median(np.diff(polls))
rows = []
for (trip, stop), g in vp.groupby(["trip_id", "stop_id"], sort=False):
    st = g[g["status"] == "STOPPED_AT"]
    if st.empty: continue
    first_poll, last_poll = st["poll_ts"].min(), st["poll_ts"].max()
    if first_poll <= polls[0] + 1: continue                       # already standing when sampling began
    # the poll before the first STOPPED_AT must have seen the train (approaching), else the arrival is unknown
    seen = vp[(vp["trip_id"] == trip)]["poll_ts"]
    if not ((seen < first_poll) & (seen >= first_poll - 2.5 * step)).any(): continue
    after = vp[(vp["trip_id"] == trip) & (vp["poll_ts"] > last_poll)]
    if after.empty: continue                                       # never seen moving on
    moved = after["poll_ts"].min()
    if moved - last_poll > 2.5 * step: continue
    vts = st["vehicle_ts"].dropna()
    arr_vts = float(vts.min()) if len(vts) else np.nan
    rows.append({"trip_id": trip, "stop_id": stop, "route": g["route_id"].iloc[0],
                 "arr_poll": first_poll, "arr_vts": arr_vts, "dep_poll_last_stopped": last_poll, "dep_poll_moved": moved,
                 "dep_vts": float(after.sort_values("poll_ts")["vehicle_ts"].dropna().iloc[0]) if after["vehicle_ts"].notna().any() else np.nan})
ev = pd.DataFrame(rows)
# the trip update's last time for the stop before it dropped (what the collector records)
tu = tu.dropna(subset=["arrival_ts"])
last_tu = tu.sort_values("poll_ts").groupby(["trip_id", "stop_id"]).tail(1)[["trip_id", "stop_id", "arrival_ts", "poll_ts"]].rename(columns={"arrival_ts": "feed_last", "poll_ts": "feed_last_poll"})
ev = ev.merge(last_tu, on=["trip_id", "stop_id"], how="left")
# the store's recorded arrival
con = sqlite3.connect(os.path.join(ROOT, "data", "mta.sqlite"))     # the local server's store, running meanwhile
t0, t1 = polls[0] - 600, polls[-1] + 600
st = pd.read_sql("select trip_id, stop_id, arrival_ts as recorded from arrivals where arrival_ts between ? and ?", con, params=[t0, t1])
ev = ev.merge(st, on=["trip_id", "stop_id"], how="left")
ev["arr"] = ev[["arr_vts", "arr_poll"]].min(axis=1)                     # the vehicle feed's own timestamp when it has one
ev["dep"] = (ev["dep_poll_last_stopped"] + ev["dep_poll_moved"]) / 2
ev["dwell"] = ev["dep"] - ev["arr"]
ev["rec_minus_arr"] = ev["recorded"] - ev["arr"]
ev["rec_minus_dep"] = ev["recorded"] - ev["dep"]
ev["feedlast_minus_arr"] = ev["feed_last"] - ev["arr"]
term = ev["dwell"] > 240                                                  # layovers at terminals
e = ev[~term & ev["recorded"].notna()]
q = lambda s: f"median {s.median():+.0f} s  (p25 {s.quantile(.25):+.0f}, p75 {s.quantile(.75):+.0f}, n={s.notna().sum()})"
print(f"polls {len(polls)} every {step:.0f} s over {(polls[-1]-polls[0])/60:.0f} min; platform stops seen arriving and leaving: {len(ev)}; with a recorded time: {len(e)} (layovers out: {int(term.sum())})")
print("dwell (left − arrived):              ", q(e["dwell"]))
print("recorded − arrived at the platform:  ", q(e["rec_minus_arr"]))
print("recorded − left the platform:        ", q(e["rec_minus_dep"]))
print("feed's last time − arrived:          ", q(e["feedlast_minus_arr"]))
print("vehicle timestamp − first poll seen standing:", q((e["arr_vts"] - e["arr_poll"])))
by = e.groupby("route").agg(n=("rec_minus_arr", "size"), rec_minus_arr=("rec_minus_arr", "median"), rec_minus_dep=("rec_minus_dep", "median"), dwell=("dwell", "median"))
print(by[by.n >= 8].round(0).to_string())
ev.to_csv(os.path.join(D, "events.csv"), index=False)
# the feed's ETA some minutes ahead against the real arrival (what the countdown should aim at)
tu2 = tu.merge(e[["trip_id", "stop_id", "arr", "recorded"]], on=["trip_id", "stop_id"])
tu2["h"] = tu2["arrival_ts"].astype(float) - tu2["poll_ts"]
tu2 = tu2[(tu2["poll_ts"] < tu2["arr"])]
for lo, hi in ((60, 120), (120, 300), (300, 600)):
    s = tu2[(tu2["h"] >= lo) & (tu2["h"] < hi)]
    print(f"feed ETA {lo//60}-{hi//60} min ahead:  ETA − arrived {q(s['arrival_ts'] - s['arr'])};  recorded − ETA {q(s['recorded'] - s['arrival_ts'])}")
