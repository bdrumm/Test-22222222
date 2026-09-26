"""Client prediction engine: tables fitted at build time that the browser and the phone apply to the live feeds.

The server-side engine (learned quantile model, look-back propagation model, forward simulation) needs the
arrival store. The clients only have the feeds, the timetable extract and the published tables, so the
prediction engine they run is table-driven and fitted here from the collected history:

* **ETA calibration** — the feed's error (arrival − feed ETA) by route and forecast horizon: bias and the
  p10/p90 spread from the ETA samples matched to arrivals, shrunk toward the all-routes value.
* **Hold survival** — from the hold log: given a train has already been held ``t`` seconds, the expected,
  median and p90 remaining hold (the fixed "10 more minutes" scenario was +11 min biased on the scored
  live forecasts) and the chance it clears within two minutes.
* **Lateness carry** — from the arrivals: how lateness at the train's current stop maps to lateness ``k``
  stops later, per route (slope, intercept, residual spread), shrunk toward slope 1 / intercept 0.

``predict_line`` is the reference implementation of what the clients compute (``site/rt-client.js`` and the
iOS app port it and are tested against it): per train and downstream stop a calibrated feed ETA, a state
ETA from the lateness carry, their inverse-variance blend, the hold correction for held trains, and the
headway cascade (no train arrives within ``min_headway_sec`` of the one ahead) under the scenarios
``baseline`` (expected remaining hold), ``hold_persists`` (p90 remaining hold) and ``clears_now``.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..analysis.holds import terminals
from ..analysis.schedule_match import match_arrivals
from ..collect.dwells import HOLD_SEC

VERSION = 1
HORIZON_EDGES = [0, 120, 300, 600, 1200, 2400, 3600]     # the last bucket is open-ended
ELAPSED_GRID = [150, 240, 360, 600, 900, 1800]           # seconds already held
PRIOR_N = 20.0
MAX_K = 12
MIN_HEADWAY_SEC = 90.0
MIN_STOP_GAP_SEC = 30.0
SCENARIOS = ("baseline", "hold_persists", "clears_now")
PRIOR_HOLD = {"expected": 300.0, "p50": 180.0, "p90": 720.0, "clears_2min": 0.35}


def prior_spread(h: float) -> tuple[float, float]:
    """Default p10/p90 of the feed's ETA error (seconds) at horizon h: widens with lead time."""
    return (-45.0 - 0.05 * h, 60.0 + 0.15 * h)


def horizon_index(h: float) -> int:
    for i in range(len(HORIZON_EDGES) - 1):
        if HORIZON_EDGES[i] <= h < HORIZON_EDGES[i + 1]:
            return i
    return 0 if h < 0 else len(HORIZON_EDGES) - 2


def _bucket_mid(i: int) -> float:
    lo = HORIZON_EDGES[i]
    hi = HORIZON_EDGES[i + 1] if i + 1 < len(HORIZON_EDGES) else lo + 1200
    return (lo + hi) / 2


def _prior_buckets() -> list[dict]:
    out = []
    for i in range(len(HORIZON_EDGES) - 1):
        p10, p90 = prior_spread(_bucket_mid(i))
        out.append({"n": 0, "bias": 0.0, "p10": round(p10, 1), "p90": round(p90, 1)})
    return out


def _shrink(g: pd.Series, prior: dict) -> dict:
    n = int(len(g))
    w = n / (n + PRIOR_N)
    if n == 0:
        return {"n": 0, "bias": prior["bias"], "p10": prior["p10"], "p90": prior["p90"]}
    bias = w * float(g.median()) + (1 - w) * prior["bias"]
    p10 = w * float(g.quantile(0.1)) + (1 - w) * prior["p10"]
    p90 = w * float(g.quantile(0.9)) + (1 - w) * prior["p90"]
    p10, p90 = min(p10, bias - 5.0), max(p90, bias + 5.0)
    return {"n": n, "bias": round(bias, 1), "p10": round(p10, 1), "p90": round(p90, 1)}


def fit_eta_calibration(eta_samples: pd.DataFrame | None, arrivals: pd.DataFrame | None, min_route_n: int = 30) -> dict:
    """Feed ETA error by route × horizon bucket."""
    out = {"horizons": list(HORIZON_EDGES), "n": 0, "all": _prior_buckets(), "by_route": {}}
    if eta_samples is None or eta_samples.empty or arrivals is None or arrivals.empty:
        return out
    arr = arrivals[["trip_key", "stop_id", "arrival_ts"]].drop_duplicates(["trip_key", "stop_id"])
    j = eta_samples.merge(arr, on=["trip_key", "stop_id"])
    if j.empty:
        return out
    j = j.assign(err=j["arrival_ts"] - j["eta_ts"], h=j["eta_ts"] - j["at_ts"])
    j = j[(j["err"].abs() < 3600) & (j["h"] >= -60) & (j["h"] < 7200)]
    if j.empty:
        return out
    j = j.assign(b=j["h"].map(horizon_index))
    out["n"] = int(len(j))
    prior = _prior_buckets()
    out["all"] = [_shrink(j.loc[j["b"] == i, "err"], prior[i]) for i in range(len(prior))]
    for r, gr in j.groupby("route_id"):
        if len(gr) < min_route_n or not isinstance(r, str) or not r:
            continue
        out["by_route"][r] = [_shrink(gr.loc[gr["b"] == i, "err"], out["all"][i]) for i in range(len(prior))]
    return out


def fit_hold_survival(holds: pd.DataFrame | None, static=None, hold_sec: float = HOLD_SEC) -> dict:
    """Remaining hold given the time already held, from the hold log (terminals excluded)."""
    out = {"elapsed": list(ELAPSED_GRID), "n": [], "expected": [], "p50": [], "p90": [], "clears_2min": [], "n_holds": 0}
    d = pd.Series(dtype=float)
    if holds is not None and not holds.empty and "dwell_sec" in holds:
        h = holds[holds["dwell_sec"] >= hold_sec]
        if static is not None and "route_id" in h and "stop_id" in h:
            origins = terminals(static, h["route_id"].dropna().unique())
            h = h[~h["stop_id"].isin(origins)]
        d = h["dwell_sec"].astype(float).clip(upper=7200.0)
    out["n_holds"] = int(len(d))
    for e in ELAPSED_GRID:
        r = d[d >= e] - e if len(d) else d
        n = int(len(r))
        w = n / (n + PRIOR_N)
        mean = float(r.mean()) if n else 0.0
        p50 = float(r.median()) if n else 0.0
        p90 = float(r.quantile(0.9)) if n else 0.0
        clears = float((r <= 120).mean()) if n else 0.0
        out["n"].append(n)
        out["expected"].append(round(w * mean + (1 - w) * PRIOR_HOLD["expected"], 1))
        out["p50"].append(round(w * p50 + (1 - w) * PRIOR_HOLD["p50"], 1))
        out["p90"].append(round(w * p90 + (1 - w) * PRIOR_HOLD["p90"], 1))
        out["clears_2min"].append(round(w * clears + (1 - w) * PRIOR_HOLD["clears_2min"], 3))
    return out


def remaining_hold(survival: dict | None, elapsed: float) -> dict:
    """Expected / p50 / p90 remaining hold for a train held ``elapsed`` seconds (linear between grid points)."""
    if not survival or not survival.get("elapsed"):
        return {"expected": PRIOR_HOLD["expected"], "p50": PRIOR_HOLD["p50"], "p90": PRIOR_HOLD["p90"], "clears_2min": PRIOR_HOLD["clears_2min"]}
    grid = survival["elapsed"]
    keys = ("expected", "p50", "p90", "clears_2min")
    if elapsed <= grid[0]:
        return {k: survival[k][0] for k in keys}
    if elapsed >= grid[-1]:
        return {k: survival[k][-1] for k in keys}
    for i in range(len(grid) - 1):
        if grid[i] <= elapsed < grid[i + 1]:
            f = (elapsed - grid[i]) / (grid[i + 1] - grid[i])
            return {k: survival[k][i] + f * (survival[k][i + 1] - survival[k][i]) for k in keys}
    return {k: survival[k][-1] for k in keys}


def _prior_resid(k: int) -> float:
    return 60.0 + 20.0 * k


def fit_lateness_carry(arrivals: pd.DataFrame | None, static, max_rows: int = 200000, max_k: int = MAX_K, min_route_n: int = 200) -> dict:
    """Lateness k stops ahead as a linear function of lateness now, per route (shrunk toward carry-through)."""
    out = {"max_k": max_k, "all": None, "by_route": {}, "n": 0}
    prior = {"slope": [1.0] * max_k, "intercept": [0.0] * max_k, "resid_std": [_prior_resid(k) for k in range(1, max_k + 1)], "n": [0] * max_k}
    out["all"] = prior
    if arrivals is None or arrivals.empty or static is None:
        return out
    a = arrivals.dropna(subset=["trip_key", "stop_id", "arrival_ts"]).sort_values("arrival_ts")
    if len(a) > max_rows:
        a = a.tail(max_rows)
    try:
        m = match_arrivals(a, static)
    except Exception:
        return out
    m = m[np.isfinite(m["lateness_sec"].astype(float))]
    m = m[m["lateness_sec"].abs() < 1800]
    if m.empty:
        return out
    seq_cache: dict[tuple[str, str], dict[str, int]] = {}

    def seq_index(route: str, direction: str) -> dict[str, int]:
        key = (route, direction)
        if key not in seq_cache:
            try:
                seq_cache[key] = {s: i for i, s in enumerate(static.canonical_stop_sequence(route, direction))}
            except Exception:
                seq_cache[key] = {}
        return seq_cache[key]

    pairs: dict[str, dict[int, list[tuple[float, float]]]] = {}
    for (tk, route, direction), g in m.groupby(["trip_key", "route_id", "direction"], sort=False):
        if not isinstance(route, str) or len(g) < 2:
            continue
        idx = seq_index(route, str(direction or "N"))
        g = g.sort_values("arrival_ts")
        pos = [idx.get(s) for s in g["stop_id"]]
        lat = g["lateness_sec"].astype(float).tolist()
        if any(p is None for p in pos):
            pos = list(range(len(lat)))
        bucket = pairs.setdefault(route, {})
        for i in range(len(lat)):
            for j in range(i + 1, len(lat)):
                k = pos[j] - pos[i]
                if k < 1:
                    continue
                if k > max_k:
                    break
                bucket.setdefault(k, []).append((lat[i], lat[j]))

    def fit(rows: dict[int, list[tuple[float, float]]], base: dict) -> dict:
        res = {"slope": [], "intercept": [], "resid_std": [], "n": []}
        for k in range(1, max_k + 1):
            pts = rows.get(k, [])
            n = len(pts)
            w = n / (n + PRIOR_N)
            b_slope, b_int, b_res = base["slope"][k - 1], base["intercept"][k - 1], base["resid_std"][k - 1]
            if n >= 3:
                u = np.array([p[0] for p in pts]); d = np.array([p[1] for p in pts])
                if float(u.std()) > 1e-6:
                    slope, intercept = np.polyfit(u, d, 1)
                else:
                    slope, intercept = 1.0, float((d - u).mean())
                slope = float(min(max(slope, 0.0), 1.5))
                resid = float((d - (slope * u + intercept)).std())
            else:
                slope, intercept, resid = b_slope, b_int, b_res
            res["slope"].append(round(w * slope + (1 - w) * b_slope, 3))
            res["intercept"].append(round(w * float(intercept) + (1 - w) * b_int, 1))
            res["resid_std"].append(round(max(15.0, w * resid + (1 - w) * b_res), 1))
            res["n"].append(int(n))
        return res

    all_rows: dict[int, list[tuple[float, float]]] = {}
    for rows in pairs.values():
        for k, pts in rows.items():
            all_rows.setdefault(k, []).extend(pts)
    out["n"] = int(sum(len(v) for v in all_rows.values()))
    out["all"] = fit(all_rows, prior)
    for route, rows in pairs.items():
        if sum(len(v) for v in rows.values()) >= min_route_n:
            out["by_route"][route] = fit(rows, out["all"])
    return out


def fit_client_model(eta_samples, arrivals, holds, static, now_iso: str | None = None) -> dict:
    return {"version": VERSION, "generated_at": now_iso, "eta_calibration": fit_eta_calibration(eta_samples, arrivals),
            "hold_survival": fit_hold_survival(holds, static), "lateness_carry": fit_lateness_carry(arrivals, static),
            "constants": {"min_headway_sec": MIN_HEADWAY_SEC, "min_stop_gap_sec": MIN_STOP_GAP_SEC, "hold_sec": HOLD_SEC, "max_k": MAX_K}}


# ---------------------------------------------------------------- reference predictor

def calibration_at(model: dict, route: str, horizon_sec: float) -> dict:
    cal = (model or {}).get("eta_calibration") or {}
    i = horizon_index(horizon_sec)
    table = (cal.get("by_route") or {}).get(route) or cal.get("all")
    if table and i < len(table):
        return table[i]
    p10, p90 = prior_spread(max(0.0, horizon_sec))
    return {"n": 0, "bias": 0.0, "p10": p10, "p90": p90}


def carry_at(model: dict, route: str, k: int) -> dict | None:
    lc = (model or {}).get("lateness_carry") or {}
    table = (lc.get("by_route") or {}).get(route) or lc.get("all")
    if not table or k < 1 or k > len(table.get("slope", [])):
        return None
    return {"slope": table["slope"][k - 1], "intercept": table["intercept"][k - 1], "resid_std": table["resid_std"][k - 1]}


def predict_train(train: dict, line: dict, model: dict, now: float, scenario: str = "baseline") -> dict:
    """Unconstrained projection of one train over its remaining stops.

    ``train``: {"trip_id", "route", "next_idx", "points": [[idx, feed_eta_ts], ...] (ascending idx),
    "lateness_sec", "effective_lateness_sec", "sched_ts" (schedule at next_idx) or None,
    "position": {"status", "since_sec", "holding", "stalled"} or None}. ``line``: {"stops", "run_sec"}.
    """
    route = str(train.get("route") or "")
    pts = [(int(i), float(t)) for i, t in (train.get("points") or []) if t is not None]
    pts.sort()
    out = {"trip_id": train.get("trip_id"), "points": [], "hold_extra_sec": 0.0, "knock_on_sec": 0.0, "scenario": scenario}
    if not pts:
        return out
    next_idx = int(train.get("next_idx") if train.get("next_idx") is not None else pts[0][0])
    lat = train.get("lateness_sec")
    eff = train.get("effective_lateness_sec")
    if eff is None:
        eff = lat
    optimistic = max(0.0, float(eff) - float(lat)) if (eff is not None and lat is not None) else 0.0
    pos = train.get("position") or {}
    held = bool(pos.get("holding") or pos.get("stalled"))
    extra = 0.0
    if held:
        rem = remaining_hold((model or {}).get("hold_survival"), float(pos.get("since_sec") or 0.0))
        extra = {"baseline": rem["expected"], "hold_persists": rem["p90"], "clears_now": 0.0}.get(scenario, rem["expected"])
    out["hold_extra_sec"] = extra
    run = line.get("run_sec") or []
    sched_next = train.get("sched_ts")
    prev_t = None
    for idx, feed in pts:
        h = feed - now
        cal = calibration_at(model, route, h)
        eta_f = feed + float(cal["bias"]) + optimistic
        var_f = max(((float(cal["p90"]) - float(cal["p10"])) / 2.56) ** 2, 1.0)
        eta, lo, hi, source = eta_f, feed + float(cal["p10"]) + optimistic, feed + float(cal["p90"]) + optimistic, "feed"
        k = idx - next_idx
        if sched_next is not None and eff is not None and k >= 1:
            run_sum, ok = 0.0, True
            for s in range(next_idx, idx):
                r = run[s] if s < len(run) else None
                if r is None:
                    ok = False
                    break
                run_sum += float(r)
            carry = carry_at(model, route, k)
            if ok and carry is not None:
                sched_d = float(sched_next) + run_sum
                eta_s = sched_d + float(carry["intercept"]) + float(carry["slope"]) * float(eff)
                var_s = max(float(carry["resid_std"]) ** 2, 1.0)
                w = var_f / (var_f + var_s)               # weight on the state estimate
                eta = w * eta_s + (1 - w) * eta_f
                sd = math.sqrt(1.0 / (1.0 / var_f + 1.0 / var_s))
                lo, hi, source = eta - 1.28 * sd, eta + 1.28 * sd, "blend"
        eta, lo, hi = eta + extra, lo + extra, hi + extra
        t = max(eta, now)
        if prev_t is not None and t < prev_t + MIN_STOP_GAP_SEC:
            t = prev_t + MIN_STOP_GAP_SEC
        shift = t - eta
        out["points"].append({"idx": idx, "feed_ts": feed, "eta_ts": t, "lo_ts": lo + shift, "hi_ts": hi + shift, "source": source})
        prev_t = t
    return out


def predict_line(trains: list[dict], line: dict, model: dict, now: float, scenario: str = "baseline",
                 min_headway_sec: float = MIN_HEADWAY_SEC, horizon_sec: float = 3600.0) -> dict:
    """All trains of one line direction, furthest along first, with the headway cascade applied."""
    order = sorted(trains, key=lambda t: (-(t.get("next_idx") if t.get("next_idx") is not None else 0), min((p[1] for p in t.get("points") or []), default=now)))
    projs = []
    last_at: dict[int, float] = {}
    for t in order:
        p = predict_train(t, line, model, now, scenario)
        shift, knock = 0.0, 0.0
        for pt in p["points"]:
            want = pt["eta_ts"] + shift
            ahead = last_at.get(pt["idx"])
            if ahead is not None and want < ahead + min_headway_sec:
                delta = ahead + min_headway_sec - want
                shift += delta
                knock += delta
                want = ahead + min_headway_sec
            pt["eta_ts"], pt["lo_ts"], pt["hi_ts"] = want, pt["lo_ts"] + shift, pt["hi_ts"] + shift
            last_at[pt["idx"]] = want
        p["knock_on_sec"] = knock
        p["points"] = [pt for pt in p["points"] if pt["eta_ts"] <= now + horizon_sec + 900]
        projs.append(p)
    stops = line.get("stops") or []
    per_stop, worst = [], None
    for i in range(len(stops)):
        arr = sorted(pt["eta_ts"] for p in projs for pt in p["points"] if pt["idx"] == i and pt["eta_ts"] <= now + horizon_sec)
        hws = [b - a for a, b in zip(arr[:-1], arr[1:])]
        gap = max(hws) if hws else None
        if gap and (worst is None or gap > worst["gap_sec"]):
            worst = {"gap_sec": gap, "idx": i, "at_ts": arr[hws.index(gap) + 1]}
        per_stop.append({"idx": i, "n_arrivals": len(arr), "next_ts": arr[0] if arr else None, "max_headway_sec": gap})
    return {"scenario": scenario, "now": now, "trains": projs, "per_stop": per_stop, "worst_gap": worst,
            "n_knock_on": sum(1 for p in projs if p["knock_on_sec"] >= 60), "knock_on_total_sec": sum(p["knock_on_sec"] for p in projs)}
