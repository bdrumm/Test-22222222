"""Cross-line effects at transfer stations and end-to-end route analysis.

Three questions, answered from the collected arrival history:

1. **Connections.** For riders stepping off line A at a transfer station and
   boarding line B: how long do they wait, how does that compare with the
   schedule, how often do they *miss* the B train they would have caught had A
   been on time, and how much does A's lateness cost them on B?

2. **Co-movement.** Do the lines' lateness series move together at the station
   (shared causes such as incidents, crowding, or one line holding the other),
   and which one leads?

3. **Track interaction.** Where two routes share stops, does a route-A train
   lose time when a route-B train is just ahead of where it would have arrived
   (merge conflicts, following on shared track), and how much more when that
   leader is late?

`analyze_routes` combines these per configured journey with a decomposition of
end-to-end excess time (origin wait, each ride, each transfer) so the dominant
source of delay on a route, and the share that is cross-line, is explicit.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from scipy import stats

from ..realtime.journey import JourneySpec, LegSpec, build_leg_training
from ..sources.gtfs_static import NY_TZ, StaticGTFS
from ..storage.db import Store
from .metrics import expected_wait
from .schedule_match import match_arrivals
from .significance import bootstrap_mean_diff, cliffs_delta, mann_whitney_p

MIN_CONN = 20            # connections needed before a transfer is summarised
LATE_SEC = 180.0         # a feeder / leader is "late" from here
ON_TIME_SEC = 120.0
CONFLICT_WINDOW_SEC = 180.0
MIN_EXTRA_SEC = 45.0
WAIT_CAP_SEC = 1500.0    # without coverage information, longer waits are treated as censored


# --------------------------------------------------------------------------- #
# Specs
# --------------------------------------------------------------------------- #
@dataclass
class TransferSpec:
    id: str
    station_name: str
    from_stop: str
    from_routes: list[str]
    to_stop: str
    to_routes: list[str]
    walk_sec: float
    journey_ids: list[str] = field(default_factory=list)
    leg_index: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


def transfers_from_journeys(specs: list[JourneySpec]) -> list[TransferSpec]:
    """One TransferSpec per distinct (feeder platform/routes → connecting platform/routes, walk)."""
    out: dict[tuple, TransferSpec] = {}
    for sp in specs:
        for i in range(len(sp.legs) - 1):
            a, b = sp.legs[i], sp.legs[i + 1]
            key = (a.to_stop, tuple(a.routes), b.from_stop, tuple(b.routes), float(b.transfer_min))
            if key in out:
                out[key].journey_ids.append(sp.id)
                continue
            tid = f"{a.to_stop}-{''.join(a.routes)}-to-{b.from_stop}-{''.join(b.routes)}"
            out[key] = TransferSpec(tid, a.to_name or a.to_stop, a.to_stop, list(a.routes), b.from_stop, list(b.routes),
                                    float(b.transfer_min) * 60.0, [sp.id], i)
    return list(out.values())


# --------------------------------------------------------------------------- #
# 1. Connections
# --------------------------------------------------------------------------- #
CONN_COLUMNS = ["feeder_trip_key", "feeder_route", "arrival_ts", "ready_ts", "lateness_sec", "hour", "dow", "service_date",
                "conn_trip_key", "conn_route", "conn_ts", "conn_lateness_sec", "wait_sec", "sched_wait_sec", "excess_wait_sec",
                "planned_trip_id", "planned_observed", "caught_planned", "missed"]


def _same_interval(t1: float, t2: float, coverage: list[tuple[float, float]], slack: float = 120.0) -> bool:
    for a, b in coverage:
        if a - slack <= t1 <= b + slack:
            return a - slack <= t2 <= b + slack
    return False


def connection_table(store: Store, static: StaticGTFS, tr: TransferSpec, start_ts: float, end_ts: float,
                     coverage: list[tuple[float, float]] | None = None) -> pd.DataFrame:
    """One row per feeder arrival: the connection actually available and the one the schedule promised."""
    feed = store.arrivals(tr.from_stop, start_ts, end_ts, tr.from_routes)
    conn = store.arrivals(tr.to_stop, start_ts, end_ts + 3600, tr.to_routes)
    if feed.empty or conn.empty:
        return pd.DataFrame(columns=CONN_COLUMNS)
    fm = match_arrivals(feed, static)
    cm = match_arrivals(conn, static).sort_values("arrival_ts").reset_index(drop=True)
    c_ts = cm["arrival_ts"].values.astype(float)
    obs_by_trip = {(sd, tid): float(ts) for sd, tid, ts in zip(cm["service_date"], cm["sched_trip_id"], cm["arrival_ts"]) if tid}
    sched_cache: dict = {}
    rows = []
    for f in fm.itertuples(index=False):
        ready = float(f.arrival_ts) + tr.walk_sec
        k = int(np.searchsorted(c_ts, ready, side="left"))
        if k >= len(c_ts):
            continue
        c = cm.iloc[k]
        wait = float(c_ts[k] - ready)
        sched_wait = planned_tid = planned_obs = None
        if f.sched_arrival_ts is not None and np.isfinite(f.sched_arrival_ts):
            sd = f.service_date
            ev = sched_cache.get(sd)
            if ev is None:
                ev = static.scheduled_stop_events(tr.to_stop, sd, tr.to_routes).sort_values("arrival_ts").reset_index(drop=True)
                sched_cache[sd] = ev
            s_ready = float(f.sched_arrival_ts) + tr.walk_sec
            if len(ev):
                kk = int(np.searchsorted(ev["arrival_ts"].values.astype(float), s_ready, side="left"))
                if kk < len(ev):
                    planned_tid = ev["trip_id"].iloc[kk]
                    sched_wait = float(ev["arrival_ts"].iloc[kk]) - s_ready
                    planned_obs = obs_by_trip.get((sd, planned_tid))
        caught = None if planned_obs is None else bool(planned_obs >= ready - 15)
        local = datetime.fromtimestamp(float(f.arrival_ts), NY_TZ)
        rows.append({
            "feeder_trip_key": f.trip_key, "feeder_route": str(f.route_id), "arrival_ts": float(f.arrival_ts), "ready_ts": ready,
            "lateness_sec": float(f.lateness_sec) if f.lateness_sec is not None and np.isfinite(f.lateness_sec) else np.nan,
            "hour": local.hour, "dow": local.weekday(), "service_date": f.service_date,
            "conn_trip_key": c["trip_key"], "conn_route": str(c["route_id"]), "conn_ts": float(c_ts[k]),
            "conn_lateness_sec": float(c["lateness_sec"]) if pd.notna(c["lateness_sec"]) else np.nan,
            "wait_sec": wait, "sched_wait_sec": sched_wait,
            "excess_wait_sec": (wait - sched_wait) if sched_wait is not None else np.nan,
            "planned_trip_id": planned_tid, "planned_observed": planned_obs is not None,
            "caught_planned": caught, "missed": (None if caught is None else (not caught)),
        })
    df = pd.DataFrame(rows, columns=CONN_COLUMNS)
    if df.empty:
        return df
    cov = coverage
    if cov is None:
        try:
            cov = store.coverage_intervals()
        except Exception:
            cov = []
    if cov:
        keep = [_same_interval(r, c, cov) for r, c in zip(df["ready_ts"], df["conn_ts"])]
        df = df[np.array(keep, dtype=bool)]
    else:
        df = df[df["wait_sec"] <= WAIT_CAP_SEC]
    return df.reset_index(drop=True)


def _q(x, q):
    x = np.asarray(x, float); x = x[np.isfinite(x)]
    return float(np.quantile(x, q)) if x.size else None


def _mean(x):
    x = np.asarray(x, float); x = x[np.isfinite(x)]
    return float(x.mean()) if x.size else None


def _rate(s: pd.Series) -> float | None:
    s = s.dropna()
    return float(s.astype(bool).mean()) if len(s) else None


def transfer_summary(t: pd.DataFrame) -> dict:
    """Connection waits, missed connections and the cost of feeder lateness."""
    out: dict = {"n": int(len(t)), "ok": bool(len(t) >= MIN_CONN)}
    if not out["ok"]:
        return out
    out.update({
        "wait_median_sec": _q(t["wait_sec"], 0.5), "wait_p90_sec": _q(t["wait_sec"], 0.9),
        "sched_wait_median_sec": _q(t["sched_wait_sec"], 0.5), "excess_wait_mean_sec": _mean(t["excess_wait_sec"]),
        "missed_rate": _rate(t["missed"]), "n_planned_observed": int(t["missed"].notna().sum()),
        "planned_missing_rate": float(((t["planned_trip_id"].notna()) & (~t["planned_observed"].astype(bool))).mean()),
        "routes_from": sorted(t["feeder_route"].unique().tolist()), "routes_to": sorted(t["conn_route"].unique().tolist()),
    })
    by_hour = []
    for h in range(24):
        g = t[t["hour"] == h]
        by_hour.append({"hour": h, "n": int(len(g)), "wait_median_sec": _q(g["wait_sec"], 0.5) if len(g) >= 3 else None,
                        "wait_p90_sec": _q(g["wait_sec"], 0.9) if len(g) >= 5 else None,
                        "sched_wait_median_sec": _q(g["sched_wait_sec"], 0.5) if len(g) >= 3 else None,
                        "missed_rate": _rate(g["missed"]) if g["missed"].notna().sum() >= 5 else None})
    out["by_hour"] = by_hour
    lat = t["lateness_sec"]
    buckets = [("on_time", lat < ON_TIME_SEC), ("late_2_5", (lat >= ON_TIME_SEC) & (lat < 300)), ("late_5_plus", lat >= 300)]
    out["by_feeder_lateness"] = [{"bucket": name, "n": int(m.sum()), "wait_median_sec": _q(t.loc[m, "wait_sec"], 0.5) if m.sum() >= 5 else None,
                                  "excess_wait_mean_sec": _mean(t.loc[m, "excess_wait_sec"]) if m.sum() >= 5 else None,
                                  "missed_rate": _rate(t.loc[m, "missed"]) if t.loc[m, "missed"].notna().sum() >= 5 else None}
                                 for name, m in buckets]
    late, ok_ = t[lat >= LATE_SEC], t[lat < ON_TIME_SEC]
    eff = {"n_late": int(len(late)), "n_on_time": int(len(ok_))}
    if len(late) >= 8 and len(ok_) >= 8:
        a, b = late["excess_wait_sec"].dropna().values, ok_["excess_wait_sec"].dropna().values
        if a.size >= 8 and b.size >= 8:
            diff, lo, hi = bootstrap_mean_diff(a, b)
            eff.update({"excess_wait_diff_sec": float(diff), "ci_lo": float(lo), "ci_hi": float(hi),
                        "p_value": float(mann_whitney_p(a, b)), "cliffs_delta": float(cliffs_delta(a, b)),
                        "significant": bool(np.isfinite(lo) and lo > 0)})
        mr_l, mr_o = _rate(late["missed"]), _rate(ok_["missed"])
        eff.update({"missed_rate_late": mr_l, "missed_rate_on_time": mr_o,
                    "missed_lift": (mr_l / mr_o) if (mr_l is not None and mr_o) else None})
    fin = t[np.isfinite(t["lateness_sec"]) & np.isfinite(t["excess_wait_sec"]) & (t["lateness_sec"].abs() < 1800)]
    if len(fin) >= 20 and fin["lateness_sec"].std() > 0:
        slope = float(np.polyfit(fin["lateness_sec"] / 60.0, fin["excess_wait_sec"], 1)[0])
        eff["excess_wait_per_late_minute_sec"] = slope
    out["feeder_lateness_effect"] = eff
    return out


# --------------------------------------------------------------------------- #
# 2. Co-movement of lateness between lines
# --------------------------------------------------------------------------- #
def _binned_lateness(store: Store, static: StaticGTFS, stop: str, routes: list[str], start: float, end: float, bin_sec: int) -> pd.Series:
    a = store.arrivals(stop, start, end, routes)
    if a.empty:
        return pd.Series(dtype=float)
    m = match_arrivals(a, static).dropna(subset=["lateness_sec"])
    if m.empty:
        return pd.Series(dtype=float)
    m["bin"] = (m["arrival_ts"] // bin_sec).astype(int)
    return m.groupby("bin")["lateness_sec"].mean().clip(-300, 1800)


def cross_line_comovement(store: Store, static: StaticGTFS, stop_a: str, routes_a: list[str], stop_b: str, routes_b: list[str],
                          start: float, end: float, bin_sec: int = 900, disrupted_sec: float = 240.0) -> dict:
    a = _binned_lateness(store, static, stop_a, routes_a, start, end, bin_sec)
    b = _binned_lateness(store, static, stop_b, routes_b, start, end, bin_sec)
    out = {"routes_a": routes_a, "routes_b": routes_b, "bin_sec": bin_sec, "n_bins": 0, "ok": False}
    if a.empty or b.empty:
        return out
    j = pd.concat([a.rename("a"), b.rename("b")], axis=1, join="inner")
    out["n_bins"] = int(len(j))
    if len(j) < 30:
        return out
    rho, p = stats.spearmanr(j["a"], j["b"])
    lags = {}
    for k in (-2, -1, 0, 1, 2):
        bb = b.copy(); bb.index = bb.index - k     # b at bin+k aligned with a at bin
        jj = pd.concat([a.rename("a"), bb.rename("b")], axis=1, join="inner")
        if len(jj) >= 30 and jj["a"].std() > 0 and jj["b"].std() > 0:
            lags[str(k)] = float(stats.spearmanr(jj["a"], jj["b"])[0])
    la, lb = j["a"] >= disrupted_sec, j["b"] >= disrupted_sec
    p_joint, pa, pb = float((la & lb).mean()), float(la.mean()), float(lb.mean())
    lift = (p_joint / (pa * pb)) if pa * pb > 0 else None
    best = max(lags.items(), key=lambda kv: abs(kv[1])) if lags else None
    out.update({"ok": True, "spearman": float(rho) if np.isfinite(rho) else None, "p_value": float(p) if np.isfinite(p) else None,
                "lags": lags, "best_lag": int(best[0]) if best else None, "best_lag_corr": best[1] if best else None,
                "disrupted_share_a": pa, "disrupted_share_b": pb, "joint_disrupted_share": p_joint, "joint_lift": lift})
    return out


# --------------------------------------------------------------------------- #
# 3. Shared-track interaction between routes
# --------------------------------------------------------------------------- #
PERIOD_NAMES = {"am_peak": "AM peak (7-10)", "midday": "midday (10-16)", "pm_peak": "PM peak (16-19)", "evening": "evening/night", "weekend": "weekend"}


def _period_of(ts: float) -> str:
    d = datetime.fromtimestamp(float(ts), NY_TZ)
    if d.weekday() >= 5:
        return "weekend"
    h = d.hour
    return "am_peak" if 7 <= h < 10 else "midday" if 10 <= h < 16 else "pm_peak" if 16 <= h < 19 else "evening"


def cross_line_interaction(store: Store, static: StaticGTFS, route_a: str, route_b: str, direction: str, stops: list[str],
                           start: float, end: float, window_sec: float = CONFLICT_WINDOW_SEC, min_extra_sec: float = MIN_EXTRA_SEC) -> dict | None:
    """Time route-A trains lose at each stop shared with route B when a B train is just ahead."""
    full = static.canonical_stop_sequence(route_a, direction)
    idxs = [i for i, s in enumerate(full) if s in set(stops)]
    if not idxs:
        return None
    # include the stop just before the first stop of interest so an interaction *at* that stop is measurable
    seq = full[max(0, idxs[0] - 1):idxs[-1] + 1]
    if len(seq) < 2:
        return None
    served_b = set(static.canonical_stop_sequence(route_b, direction))
    arr = store.arrivals(seq, start, end, [route_a, route_b])
    if arr.empty:
        return None
    m = match_arrivals(arr, static)
    m["route_id"] = m["route_id"].astype(str)
    mine = m[m["route_id"] == str(route_a)].dropna(subset=["sched_arrival_ts"])
    others = m[m["route_id"] == str(route_b)]
    if mine.empty or others.empty:
        return None
    per_stop, n_trips = [], 0
    for i in range(1, len(seq)):
        s, p = seq[i], seq[i - 1]
        if s not in served_b:
            continue
        at_s = mine[mine["stop_id"] == s].drop_duplicates("trip_key").set_index("trip_key")
        at_p = mine[mine["stop_id"] == p].drop_duplicates("trip_key").set_index("trip_key")[["lateness_sec"]].rename(columns={"lateness_sec": "lat_prev"})
        j = at_s.join(at_p, how="inner").dropna(subset=["lat_prev", "lateness_sec"])
        if len(j) < 40:
            continue
        n_trips = max(n_trips, len(j))
        j["projected"] = j["sched_arrival_ts"] + j["lat_prev"]
        oth = others[others["stop_id"] == s].sort_values("arrival_ts")
        if oth.empty:
            continue
        o_ts = oth["arrival_ts"].values.astype(float)
        o_lat = oth["lateness_sec"].values.astype(float)
        idx = np.searchsorted(o_ts, j["projected"].values, side="right") - 1
        has = idx >= 0
        gap = np.where(has, j["projected"].values - o_ts[np.clip(idx, 0, None)], np.inf)
        leader_late = np.where(has, o_lat[np.clip(idx, 0, None)], np.nan)
        j["conflict"] = gap <= window_sec
        j["leader_late"] = leader_late
        j["delta"] = j["lateness_sec"] - j["lat_prev"]
        j["period"] = [_period_of(ts) for ts in j["arrival_ts"]]

        def _stats(jj: pd.DataFrame) -> dict | None:
            c, nc = jj[jj["conflict"]], jj[~jj["conflict"]]
            if len(c) < 15 or len(nc) < 15:
                return None
            diff, lo, hi = bootstrap_mean_diff(c["delta"].values, nc["delta"].values)
            pval = mann_whitney_p(c["delta"].values, nc["delta"].values)
            cl = c[c["leader_late"] >= LATE_SEC]
            extra_late = float(cl["delta"].mean() - nc["delta"].mean()) if len(cl) >= 8 else None
            return {"n": int(len(jj)), "n_conflict": int(len(c)), "conflict_rate": float(len(c) / len(jj)), "extra_sec": float(diff),
                    "ci_lo": float(lo), "ci_hi": float(hi), "p_value": float(pval), "extra_when_leader_late_sec": extra_late,
                    "n_leader_late": int(len(cl)), "significant": bool(np.isfinite(lo) and lo > 0 and pval < 0.05 and diff >= min_extra_sec)}

        overall = _stats(j)
        if overall is None:
            continue
        periods = {}
        for per, g in j.groupby("period"):
            st = _stats(g)
            if st:
                periods[per] = st
        sig_periods = {k: v for k, v in periods.items() if v["significant"]}
        worst = max(sig_periods.items(), key=lambda kv: kv[1]["extra_sec"]) if sig_periods else None
        entry = {"stop_id": s, "stop_name": static.stop_name(s), **overall, "periods": periods,
                 "worst_period": worst[0] if worst else None, "worst_period_extra_sec": worst[1]["extra_sec"] if worst else None}
        # a clear peak-period effect counts even when the all-day average is diluted
        if not entry["significant"] and worst is not None:
            entry.update({"significant": True, "extra_sec": worst[1]["extra_sec"], "ci_lo": worst[1]["ci_lo"], "ci_hi": worst[1]["ci_hi"],
                          "p_value": worst[1]["p_value"], "conflict_rate": worst[1]["conflict_rate"], "n_conflict": worst[1]["n_conflict"],
                          "extra_when_leader_late_sec": worst[1]["extra_when_leader_late_sec"], "scope": worst[0]})
        else:
            entry["scope"] = "all"
        per_stop.append(entry)
    if not per_stop:
        return None
    sig = [x for x in per_stop if x["significant"]]
    best = max(sig, key=lambda x: x["extra_sec"]) if sig else None
    return {"route": route_a, "leader_route": route_b, "direction": direction, "window_sec": window_sec, "n_trips": int(n_trips),
            "per_stop": per_stop, "best": best,
            "expected_loss_per_trip_sec": float(sum(x["extra_sec"] * x["conflict_rate"] for x in sig)),
            "significant": bool(sig)}


# --------------------------------------------------------------------------- #
# 4. End-to-end decomposition
# --------------------------------------------------------------------------- #
def _hourly_mean(df: pd.DataFrame, ts_col: str, val_col: str, min_n: int = 3) -> tuple[list, list]:
    if df.empty:
        return [None] * 24, [0] * 24
    hours = pd.to_datetime(df[ts_col], unit="s", utc=True).dt.tz_convert(NY_TZ).dt.hour
    vals = df[val_col].astype(float)
    means, ns = [], []
    for h in range(24):
        v = vals[(hours == h) & np.isfinite(vals)]
        ns.append(int(len(v)))
        means.append(float(v.mean()) if len(v) >= min_n else None)
    return means, ns


def origin_wait_excess(store: Store, static: StaticGTFS, leg: LegSpec, start: float, end: float) -> tuple[list, list]:
    """Expected wait from observed headways minus the scheduled expected wait, by hour."""
    obs = store.arrivals(leg.from_stop, start, end, leg.routes).sort_values("arrival_ts")
    if len(obs) < 10:
        return [None] * 24, [0] * 24
    obs["hw"] = obs["arrival_ts"].diff()
    obs = obs[(obs["hw"] > 30) & (obs["hw"] < 3600)]
    obs["hour"] = pd.to_datetime(obs["arrival_ts"], unit="s", utc=True).dt.tz_convert(NY_TZ).dt.hour
    # scheduled reference: the most recent weekday in range
    d = datetime.fromtimestamp(end, NY_TZ).date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    ev = static.scheduled_stop_events(leg.from_stop, d, leg.routes).sort_values("arrival_ts")
    ev["hw"] = ev["arrival_ts"].diff()
    ev["hour"] = ((ev["arrival_sec"] // 3600) % 24).astype(int)
    sched = {h: expected_wait(g["hw"].dropna()) for h, g in ev.groupby("hour") if len(g["hw"].dropna()) >= 2}
    means, ns = [], []
    for h in range(24):
        g = obs[obs["hour"] == h]["hw"]
        ns.append(int(len(g)))
        if len(g) >= 5 and h in sched:
            means.append(float(expected_wait(g) - sched[h]))
        else:
            means.append(None)
    return means, ns


def route_decomposition(store: Store, static: StaticGTFS, spec: JourneySpec, conn_tables: dict[int, pd.DataFrame],
                        start: float, end: float) -> dict:
    """Mean excess over schedule by hour for each component of the journey, and the shares."""
    days = max(1, math.ceil((end - start) / 86400))
    comps = []
    w_mean, w_n = origin_wait_excess(store, static, spec.legs[0], start, end)
    comps.append({"name": f"wait for the {'/'.join(spec.legs[0].routes)} at {spec.legs[0].from_name}", "kind": "wait", "leg": 0,
                  "by_hour": w_mean, "n_by_hour": w_n})
    leg_tables: dict[int, pd.DataFrame] = {}
    for i, leg in enumerate(spec.legs):
        tr = build_leg_training(store, static, leg, None, None, None, end, lookback_days=days)
        tr = tr[(tr["excess_sec"] > -600) & (tr["excess_sec"] < 1800)] if not tr.empty else tr
        leg_tables[i] = tr
        m, n = _hourly_mean(tr, "depart_ts", "excess_sec")
        comps.append({"name": f"ride on the {'/'.join(leg.routes)}: {leg.from_name} → {leg.to_name}", "kind": "ride", "leg": i,
                      "by_hour": m, "n_by_hour": n, "n": int(len(tr)), "mean_excess_sec": _mean(tr["excess_sec"]) if not tr.empty else None})
        if i + 1 < len(spec.legs):
            ct = conn_tables.get(i)
            if ct is not None and not ct.empty:
                ct = ct[np.isfinite(ct["excess_wait_sec"])]
                m, n = _hourly_mean(ct, "arrival_ts", "excess_wait_sec")
                comps.append({"name": f"transfer at {leg.to_name} to the {'/'.join(spec.legs[i + 1].routes)}", "kind": "transfer", "leg": i,
                              "by_hour": m, "n_by_hour": n, "n": int(len(ct)), "mean_excess_sec": _mean(ct["excess_wait_sec"])})
            else:
                comps.append({"name": f"transfer at {leg.to_name} to the {'/'.join(spec.legs[i + 1].routes)}", "kind": "transfer", "leg": i,
                              "by_hour": [None] * 24, "n_by_hour": [0] * 24, "n": 0, "mean_excess_sec": None})
    total_by_hour = []
    for h in range(24):
        vals = [c["by_hour"][h] for c in comps if c["by_hour"][h] is not None]
        total_by_hour.append(float(sum(vals)) if vals else None)
    # shares: weighted by the hours where every component with data is present
    overall = {}
    for c in comps:
        v = [x for x in c["by_hour"] if x is not None]
        overall[c["name"]] = float(np.mean(v)) if v else None
    pos = {k: max(0.0, v) for k, v in overall.items() if v is not None}
    tot = sum(pos.values())
    shares = {k: (v / tot if tot > 0 else None) for k, v in pos.items()}
    dominant = max(pos.items(), key=lambda kv: kv[1])[0] if pos and tot > 0 else None
    cross_line = sum(v for k, v in pos.items() if any(c["name"] == k and c["kind"] == "transfer" for c in comps))
    # Conditional: a late feeder's downstream cost (transfer wait + next ride)
    conditional = []
    for i in range(len(spec.legs) - 1):
        ct = conn_tables.get(i)
        nxt = leg_tables.get(i + 1)
        if ct is None or ct.empty or nxt is None or nxt.empty:
            continue
        ride_by_key = nxt.set_index("trip_key")["excess_sec"]
        c = ct.copy()
        c["next_ride_excess"] = c["conn_trip_key"].map(ride_by_key)
        c = c[np.isfinite(c["excess_wait_sec"]) & np.isfinite(c["lateness_sec"])]
        late, ok_ = c[c["lateness_sec"] >= LATE_SEC], c[c["lateness_sec"] < ON_TIME_SEC]
        if len(late) < 8 or len(ok_) < 8:
            continue
        d_wait = float(late["excess_wait_sec"].mean() - ok_["excess_wait_sec"].mean())
        lr, orr = late["next_ride_excess"].dropna(), ok_["next_ride_excess"].dropna()
        d_ride = float(lr.mean() - orr.mean()) if len(lr) >= 5 and len(orr) >= 5 else None
        conditional.append({"transfer_leg": i, "at": spec.legs[i].to_name, "feeder": "/".join(spec.legs[i].routes), "next": "/".join(spec.legs[i + 1].routes),
                            "n_late": int(len(late)), "n_on_time": int(len(ok_)), "feeder_late_mean_sec": float(late["lateness_sec"].mean()),
                            "extra_transfer_wait_sec": d_wait, "extra_next_ride_sec": d_ride,
                            "extra_downstream_sec": d_wait + (d_ride or 0.0)})
    return {"components": comps, "total_by_hour": total_by_hour, "overall_mean_sec": overall, "shares": shares, "dominant": dominant,
            "cross_line_share": (cross_line / tot) if tot > 0 else None, "conditional": conditional,
            "n_rides": {i: int(len(t)) for i, t in leg_tables.items()}}


# --------------------------------------------------------------------------- #
# Findings and the top-level analysis
# --------------------------------------------------------------------------- #
def _m(sec: float | None) -> str:
    return "–" if sec is None else f"{sec / 60:.1f} min"


def transfer_findings(tr: TransferSpec, s: dict) -> list[dict]:
    out = []
    if not s.get("ok"):
        return out
    fr, to = "/".join(tr.from_routes), "/".join(tr.to_routes)
    txt = (f"At {tr.station_name}, riders leaving the {fr} for the {to} wait a median {_m(s['wait_median_sec'])} "
           f"(schedule: {_m(s['sched_wait_median_sec'])}; p90 {_m(s['wait_p90_sec'])}, n={s['n']})")
    if s.get("missed_rate") is not None:
        txt += f"; {s['missed_rate']:.0%} miss the {to} train they would have caught on schedule"
    if s.get("planned_missing_rate"):
        txt += f"; the planned connecting train was never observed {s['planned_missing_rate']:.0%} of the time (cancelled or reassigned)"
    out.append({"kind": "transfer", "severity": "info", "text": txt + "."})
    eff = s.get("feeder_lateness_effect", {})
    if eff.get("excess_wait_diff_sec") is not None:
        sev = "high" if eff.get("significant") and eff["excess_wait_diff_sec"] >= 120 else ("medium" if eff.get("significant") else "low")
        if eff.get("significant") and eff["excess_wait_diff_sec"] >= 30:
            t2 = (f"When the {fr} arrives ≥{LATE_SEC / 60:.0f} min late, the connection wait is {eff['excess_wait_diff_sec'] / 60:+.1f} min "
                  f"vs an on-time arrival (95% CI {eff['ci_lo'] / 60:+.1f} to {eff['ci_hi'] / 60:+.1f}, p={eff['p_value']:.3f}, n={eff['n_late']} late / {eff['n_on_time']} on time)")
        else:
            t2 = (f"A {fr} arriving ≥{LATE_SEC / 60:.0f} min late adds no extra connection wait beyond the lateness itself "
                  f"({eff['excess_wait_diff_sec'] / 60:+.1f} min, 95% CI {eff['ci_lo'] / 60:+.1f} to {eff['ci_hi'] / 60:+.1f}, n={eff['n_late']} late / {eff['n_on_time']} on time)")
        if eff.get("missed_rate_late") is not None and eff.get("missed_rate_on_time") is not None:
            t2 += f"; missed connections {eff['missed_rate_late']:.0%} vs {eff['missed_rate_on_time']:.0%}"
        if eff.get("excess_wait_per_late_minute_sec") is not None:
            t2 += f"; each minute of {fr} lateness adds about {eff['excess_wait_per_late_minute_sec']:.0f} s of {to} wait"
        out.append({"kind": "cross_line_cost", "severity": sev, "text": t2 + "."})
    return out


def comovement_findings(tr: TransferSpec, c: dict) -> list[dict]:
    if not c.get("ok") or c.get("spearman") is None:
        return []
    fr, to = "/".join(tr.from_routes), "/".join(tr.to_routes)
    rho, lift = c["spearman"], c.get("joint_lift")
    if abs(rho) < 0.15 and (lift is None or lift < 1.5):
        return [{"kind": "comovement", "severity": "low",
                 "text": f"{fr} and {to} lateness at {tr.station_name} move independently (Spearman {rho:+.2f} over {c['n_bins']} 15-min bins): delays on one line rarely coincide with delays on the other."}]
    lead = ""
    if c.get("best_lag") not in (None, 0) and abs(c.get("best_lag_corr") or 0) > abs(rho) + 0.03:
        k = c["best_lag"]
        lead = f" The correlation peaks when {fr if k > 0 else to} leads by {abs(k) * 15} min, so {fr if k > 0 else to} delays precede {to if k > 0 else fr} delays."
    joint = f" Both lines are disrupted (mean lateness ≥4 min) in the same 15 min {lift:.1f}× more often than chance." if lift else ""
    sev = "high" if rho >= 0.4 or (lift or 0) >= 3 else "medium"
    return [{"kind": "comovement", "severity": sev,
             "text": f"{fr} and {to} lateness at {tr.station_name} move together (Spearman {rho:+.2f}, p={c['p_value']:.3f}, {c['n_bins']} bins).{joint}{lead}"}]


def interaction_findings(x: dict | None) -> list[dict]:
    if not x or not x.get("significant"):
        return []
    b = x["best"]
    scope = f" in the {PERIOD_NAMES.get(b['scope'], b['scope'])}" if b.get("scope") and b["scope"] != "all" else ""
    t = (f"{x['route']} trains that would reach {b['stop_name']} within {x['window_sec']:.0f} s behind a {x['leader_route']}{scope} lose "
         f"{b['extra_sec']:.0f} s more there than free-running {x['route']} trains (95% CI {b['ci_lo']:.0f}–{b['ci_hi']:.0f} s, p={b['p_value']:.3f}); "
         f"{b['conflict_rate']:.0%} of {x['route']} trips are in that situation")
    if b.get("extra_when_leader_late_sec") is not None:
        t += f", and when that {x['leader_route']} is itself ≥{LATE_SEC / 60:.0f} min late the loss is {b['extra_when_leader_late_sec']:.0f} s"
    n_sig = sum(1 for p in x["per_stop"] if p["significant"])
    if n_sig > 1:
        t += f"; across {n_sig} shared stops the expected loss is {x['expected_loss_per_trip_sec']:.0f} s per {x['route']} trip"
    sev = "high" if b["extra_sec"] >= 120 else "medium"
    return [{"kind": "interaction", "severity": sev, "text": t + "."}]


def decomposition_findings(spec: JourneySpec, d: dict) -> list[dict]:
    out = []
    tot = [v for v in d["total_by_hour"] if v is not None]
    if not tot or d.get("dominant") is None:
        return out
    peak_h = int(np.nanargmax([v if v is not None else np.nan for v in d["total_by_hour"]]))
    sh = d["shares"].get(d["dominant"])
    txt = (f"End to end, the mean excess over schedule is {_m(float(np.mean(tot)))} (worst hour {peak_h:02d}:00, {_m(d['total_by_hour'][peak_h])}); "
           f"the largest component is the {d['dominant']}" + (f" ({sh:.0%} of the excess)" if sh is not None else ""))
    if d.get("cross_line_share") is not None:
        txt += f"; transfers account for {d['cross_line_share']:.0%}"
    out.append({"kind": "decomposition", "severity": "info", "text": txt + "."})
    for c in d.get("conditional", []):
        if c["extra_downstream_sec"] < 30:
            continue   # the transfer finding already says the lateness costs nothing beyond itself
        sev = "high" if c["extra_downstream_sec"] >= 240 else ("medium" if c["extra_downstream_sec"] >= 90 else "low")
        t = (f"A {c['feeder']} arriving ≥{LATE_SEC / 60:.0f} min late at {c['at']} (mean {_m(c['feeder_late_mean_sec'])} late, n={c['n_late']}) costs riders "
             f"{_m(c['extra_downstream_sec'])} more downstream than an on-time arrival: {_m(c['extra_transfer_wait_sec'])} at the transfer")
        if c.get("extra_next_ride_sec") is not None:
            t += f" and {_m(c['extra_next_ride_sec'])} on the {c['next']} ride"
        out.append({"kind": "late_feeder_cost", "severity": sev, "text": t + "."})
    return out


def analyze_routes(store: Store, static: StaticGTFS, specs: list[JourneySpec], start: float, end: float,
                   coverage: list[tuple[float, float]] | None = None) -> dict:
    """Cross-line and end-to-end analysis for every configured journey. JSON-serialisable."""
    transfers = transfers_from_journeys(specs)
    tables: dict[str, pd.DataFrame] = {}
    t_out = []
    for tr in transfers:
        tbl = connection_table(store, static, tr, start, end, coverage)
        tables[tr.id] = tbl
        summ = transfer_summary(tbl)
        com = cross_line_comovement(store, static, tr.from_stop, tr.from_routes, tr.to_stop, tr.to_routes, start, end)
        t_out.append({**tr.as_dict(), "summary": summ, "comovement": com,
                      "findings": transfer_findings(tr, summ) + comovement_findings(tr, com)})
    by_id = {t["id"]: t for t in t_out}
    routes_out = []
    for sp in specs:
        conn_tables: dict[int, pd.DataFrame] = {}
        my_transfers = []
        for i in range(len(sp.legs) - 1):
            for tr in transfers:
                if sp.id in tr.journey_ids and tr.leg_index == i:
                    conn_tables[i] = tables[tr.id]
                    my_transfers.append(tr.id)
        interactions = []
        for leg in sp.legs:
            direction = leg.from_stop[-1] if leg.from_stop[-1] in "NS" else "N"
            others = set()
            for s in leg.stops:
                others |= set(static.routes_serving(s))
            for a in leg.routes:
                for b in sorted(others - {a}):
                    try:
                        x = cross_line_interaction(store, static, a, b, direction, leg.stops, start, end)
                    except Exception:
                        x = None
                    if x and x["per_stop"]:
                        interactions.append(x)
        interactions.sort(key=lambda x: (not x["significant"], -(x["best"]["extra_sec"] if x["best"] else 0)))
        try:
            dec = route_decomposition(store, static, sp, conn_tables, start, end)
        except Exception as exc:
            dec = {"error": str(exc)[:200], "components": [], "total_by_hour": [None] * 24, "shares": {}, "dominant": None, "conditional": []}
        findings = decomposition_findings(sp, dec) if "error" not in dec else []
        for tid in my_transfers:
            findings += by_id[tid]["findings"]
        for x in interactions:
            findings += interaction_findings(x)
        order = {"high": 0, "medium": 1, "low": 3, "info": 2}
        findings.sort(key=lambda f: order.get(f["severity"], 9))
        routes_out.append({"id": sp.id, "label": sp.label, "legs": [l.as_dict() for l in sp.legs], "transfers": my_transfers,
                           "decomposition": dec, "interactions": interactions, "findings": findings,
                           "status": "ok" if any(c.get("n", 0) >= MIN_CONN for c in dec.get("components", []) if c["kind"] == "ride") else "collecting"})
    return {"start_ts": start, "end_ts": end, "generated_at": datetime.fromtimestamp(end, NY_TZ).isoformat(),
            "transfers": t_out, "routes": routes_out}
