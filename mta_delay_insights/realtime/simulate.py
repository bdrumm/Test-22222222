"""Forward simulation of a line from the current live state.

Trains are projected stop by stop for the next hour under a scenario, with a
minimum-headway constraint so that a delayed leader holds up its followers
(the cascade the feed's per-train ETAs do not show):

* ``baseline``: each train runs at the feed's ETAs corrected by the learned
  model (or the schedule when neither exists), with the position-implied
  lateness applied;
* ``hold_persists``: trains that are holding or stalled right now stay put for
  ``hold_extra_sec`` more before resuming;
* ``clears_now``: those trains resume immediately at scheduled running times.

The output is a projected stringline per scenario plus, per stop, the predicted
headways and the largest gap, so the effect of "what if this hold lasts ten
more minutes" is visible on every downstream platform.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..sources.gtfs_static import StaticGTFS
from .status import LiveTrain

SCENARIOS = ("baseline", "hold_persists", "clears_now")
MIN_HEADWAY_SEC = 90.0
DEFAULT_HOLD_EXTRA_SEC = 600.0


@dataclass
class _Proj:
    train: LiveTrain
    times: dict          # stop_id -> projected arrival ts
    knock_on_sec: float = 0.0


def _sched(static: StaticGTFS, train: LiveTrain, stop: str) -> float | None:
    return static.scheduled_arrival(train.trip_id, stop, train.service_date) if train.service_date else None


def _base_times(train: LiveTrain, seq: list[str], static: StaticGTFS, now: float, learned, scenario: str, hold_extra: float) -> dict:
    """Unconstrained projected arrival per remaining stop of the sequence."""
    times = {}
    idx = {s: i for i, s in enumerate(seq)}
    remaining = [(s, e) for s, e in train.stops if s in idx]
    if not remaining:
        return times
    # extra time the train will still lose in its current state
    extra = 0.0
    if train.holding or train.stalled:
        if scenario == "hold_persists":
            extra = hold_extra
        elif scenario == "clears_now":
            extra = 0.0
    lp_cache = {}
    if learned is not None:
        try:
            for s, _ in remaining[:24]:
                lp = learned.predict(train, s, feed_spread=240.0)
                if lp is not None:
                    lp_cache[s] = lp["eta_ts"]
        except Exception:
            lp_cache = {}
    prev_t = None
    for s, feed_eta in remaining:
        t = lp_cache.get(s, feed_eta)
        # the position proves the feed optimistic: shift the whole trajectory by the difference
        if train.position_lateness_sec is not None and train.lateness_sec is not None and train.position_lateness_sec > train.lateness_sec + 60 and s not in lp_cache:
            t += train.position_lateness_sec - train.lateness_sec
        t = max(t + extra, now)
        if prev_t is not None:
            t = max(t, prev_t + 30.0)
        times[s] = t
        prev_t = t
    return times


def simulate_line(trains: list[LiveTrain], static: StaticGTFS, route: str, direction: str, now: float, learned=None,
                  scenario: str = "baseline", hold_extra_sec: float = DEFAULT_HOLD_EXTRA_SEC, horizon_sec: float = 3600.0,
                  min_headway_sec: float = MIN_HEADWAY_SEC) -> dict:
    seq = static.canonical_stop_sequence(route, direction)
    idx = {s: i for i, s in enumerate(seq)}
    mine = [t for t in trains if str(t.route_id) == str(route) and t.started and (t.direction or direction) == direction and any(s in idx for s, _ in t.stops)]
    # order by progress: furthest along first (smallest next-stop index; ties by ETA)
    def progress(t: LiveTrain):
        nxt = next((idx[s] for s, _ in t.stops if s in idx), len(seq))
        return (-nxt, t.next_eta_ts or now)
    mine.sort(key=progress)
    mine = sorted(mine, key=lambda t: (next((idx[s] for s, _ in t.stops if s in idx), len(seq)), t.next_eta_ts or now), reverse=True)
    projs: list[_Proj] = []
    last_at: dict[str, float] = {}     # stop -> latest projected arrival of a train ahead
    for t in mine:
        base = _base_times(t, seq, static, now, learned, scenario, hold_extra_sec)
        times, knock = {}, 0.0
        shift = 0.0
        for s in seq:
            if s not in base:
                continue
            want = base[s] + shift
            ahead = last_at.get(s)
            if ahead is not None and want < ahead + min_headway_sec:
                delta = ahead + min_headway_sec - want
                shift += delta; knock += delta
                want = ahead + min_headway_sec
            times[s] = want
            last_at[s] = want
        projs.append(_Proj(t, times, knock))
    # per-stop headways from the projected arrivals in the horizon
    stops_out = []
    worst = {"gap_sec": 0.0, "stop_id": None, "at_ts": None}
    for s in seq:
        arr = sorted(v for p in projs for k, v in p.times.items() if k == s and v <= now + horizon_sec)
        hws = list(np.diff(arr)) if len(arr) >= 2 else []
        gap = max(hws) if hws else None
        if gap and gap > worst["gap_sec"]:
            worst = {"gap_sec": float(gap), "stop_id": s, "at_ts": float(arr[int(np.argmax(hws)) + 1])}
        stops_out.append({"stop_id": s, "name": static.stop_name(s), "n_arrivals": len(arr), "next_ts": arr[0] if arr else None,
                          "max_headway_sec": float(gap) if gap else None, "mean_headway_sec": float(np.mean(hws)) if hws else None})
    return {"route": route, "direction": direction, "scenario": scenario, "hold_extra_sec": hold_extra_sec if scenario == "hold_persists" else 0.0,
            "now": now, "stops": [{"stop_id": s, "name": static.stop_name(s)} for s in seq],
            "trains": [{"trip_id": p.train.trip_id, "train_id": p.train.train_id, "points": [[idx[s], round(v)] for s, v in p.times.items() if v <= now + horizon_sec + 900],
                        "knock_on_sec": round(p.knock_on_sec), "holding": p.train.holding, "stalled": p.train.stalled,
                        "lateness_sec": p.train.effective_lateness_sec} for p in projs],
            "per_stop": stops_out, "worst_gap": worst if worst["stop_id"] else None,
            "n_knock_on": sum(1 for p in projs if p.knock_on_sec >= 60), "knock_on_total_sec": round(sum(p.knock_on_sec for p in projs))}


def simulate_routes(trains: list[LiveTrain], static: StaticGTFS, routes: list[tuple[str, str]], now: float, learned=None,
                    scenarios: tuple = SCENARIOS, hold_extra_sec: float = DEFAULT_HOLD_EXTRA_SEC) -> list[dict]:
    """Baseline for every (route, direction); the hold scenarios only where a train is holding or stalled."""
    out = []
    for route, direction in routes:
        base = simulate_line(trains, static, route, direction, now, learned, "baseline", hold_extra_sec)
        if not base["trains"]:
            continue
        entry = {"route": route, "direction": direction, "scenarios": {"baseline": base}}
        disturbed = any(t["holding"] or t["stalled"] for t in base["trains"])
        if disturbed:
            for sc in scenarios:
                if sc != "baseline":
                    entry["scenarios"][sc] = simulate_line(trains, static, route, direction, now, learned, sc, hold_extra_sec)
        entry["disturbed"] = disturbed
        out.append(entry)
    return out


def station_scenarios(sims: list[dict], stop_id: str, now: float, n: int = 4) -> dict | None:
    """For a monitored platform: per route, next arrivals under each scenario and the effect of a persisting hold."""
    per_route = [x for x in (_station_scenario(e, stop_id, now, n) for e in sims) if x]
    if not per_route:
        return None
    worst = max((r for r in per_route if r.get("hold_effect")), key=lambda r: r["hold_effect"].get("max_headway_sec") or 0, default=None)
    return {"disturbed": any(r["disturbed"] for r in per_route), "routes": per_route,
            "headline": (f"If the hold on the {worst['route']} persists {worst['hold_effect']['hold_extra_sec'] / 60:.0f} more minutes, the next {worst['route']} trains here arrive "
                         f"{'/'.join(f'{d / 60:.0f}' for d in worst['hold_effect']['extra_sec'][:3])} min later"
                         + (f" and the gap grows to {worst['hold_effect']['max_headway_sec'] / 60:.0f} min" if worst['hold_effect'].get('max_headway_sec') else ""))
            if worst else None}


def _station_scenario(entry: dict, stop_id: str, now: float, n: int) -> dict | None:
    for _ in (0,):
        base = entry["scenarios"]["baseline"]
        if stop_id not in {s["stop_id"] for s in base["stops"]}:
            continue
        idx = {s["stop_id"]: i for i, s in enumerate(base["stops"])}
        i = idx[stop_id]
        def nexts(sim):
            arr = sorted((p[1], t["trip_id"]) for t in sim["trains"] for p in t["points"] if p[0] == i and p[1] >= now)
            return arr[:n]
        out = {"route": entry["route"], "direction": entry["direction"], "disturbed": entry["disturbed"],
               "baseline": [{"trip_id": tid, "eta_ts": ts} for ts, tid in nexts(base)]}
        if "hold_persists" in entry["scenarios"]:
            hp = nexts(entry["scenarios"]["hold_persists"]); cl = nexts(entry["scenarios"]["clears_now"])
            out["hold_persists"] = [{"trip_id": tid, "eta_ts": ts} for ts, tid in hp]
            out["clears_now"] = [{"trip_id": tid, "eta_ts": ts} for ts, tid in cl]
            base_map = {tid: ts for ts, tid in nexts(base)}
            deltas = [ts - base_map[tid] for ts, tid in hp if tid in base_map]
            hws = [b - a for a, b in zip([t for t, _ in hp][:-1], [t for t, _ in hp][1:])]
            out["hold_effect"] = {"extra_sec": [round(d) for d in deltas], "max_headway_sec": round(max(hws)) if hws else None,
                                  "hold_extra_sec": entry["scenarios"]["hold_persists"]["hold_extra_sec"]}
        return out
    return None
