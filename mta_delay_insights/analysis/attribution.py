"""Cause attribution: several independent *lenses* each produce evidence.

Each lens answers "what fraction of the problem events at the target does this
explanation cover, and how much more likely is a problem when the explanation
is present?" (share explained and lift). Ranking combines lenses per cause.

Evidence comes in two kinds:

* **location** evidence says *where* the delay originates (inherited from
  upstream, accumulated on the approach, already late at the terminal);
* **cause** evidence says *why* (signal alert active, planned work, merge
  conflicts at an interlining point, missing trains, dwell-time pattern,
  weather, MTA-reported incident categories, ...).

Lenses
------
temporal            - are problems concentrated in a few hours / day types?
alerts              - were unplanned MTA alerts (signal, track, police...) active?
planned_work        - were planned service changes active?
upstream            - was the train already late upstream (inherited) or did it
                      lose time on the approach (local)? which segment loses time?
run_time_pattern    - is time lost on one segment (restriction / signal timer)
                      or spread across stops (dwell)?
gap_inheritance     - did headway gaps already exist upstream?
merge               - do trains held behind another route at an interlining
                      stop lose time there?
terminal            - were trains already late leaving the terminal?
service_delivered   - are scheduled trains missing (cancellations)?
incidents           - which MTA-reported delay categories over-index for the line?
weather             - do problems correlate with precipitation / temperature?
prediction_volatility - did ETAs drift more on problem arrivals (holds)?
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field

import numpy as np
import pandas as pd
from scipy import stats

from .. import config
from ..sources.alerts import alert_kind
from ..sources.gtfs_static import StaticGTFS
from . import trends as tr
from .significance import mann_whitney_p

PEAK_HOURS = {7, 8, 9, 16, 17, 18, 19}
LATE_NIGHT_HOURS = {22, 23, 0, 1, 2, 3, 4, 5}
LOCATION_LENSES = {"upstream", "gap_inheritance"}
LIFT_LENSES = {"alerts", "planned_work", "weather", "incidents"}   # lenses whose lift is informative for ranking

MIN_PROBLEMS = 10       # fewest problem arrivals for a pattern lens to speak
MIN_LATE = 5            # fewest late arrivals for the upstream / terminal lenses
MIN_GAPS = 8            # fewest gap arrivals for the gap-inheritance lens
MILD_LATE_SEC = 120     # fallback lateness threshold when few trains are formally late


@dataclass
class Evidence:
    lens: str
    cause: str
    share_explained: float
    lift: float
    confidence: float
    summary: str
    details: dict = field(default_factory=dict)

    @property
    def kind(self) -> str:
        """'location' evidence says *where* delay originates; 'cause' evidence says *why*."""
        return "location" if self.lens in LOCATION_LENSES else "cause"

    def as_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind
        d["details"] = _jsonable(self.details)
        return d


def _jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return None if np.isnan(obj) else float(obj)
    if isinstance(obj, float) and np.isnan(obj):
        return None
    if isinstance(obj, pd.DataFrame):
        return _jsonable(obj.to_dict(orient="records"))
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


def _lift(p_with: float, p_without: float) -> float:
    if not np.isfinite(p_with) or not np.isfinite(p_without):
        return float("nan")
    if p_without <= 0:
        return float("inf") if p_with > 0 else 1.0
    return float(p_with / p_without)


def _finite_lift(x: float) -> float:
    return 1.0 if not np.isfinite(x) else x


# --------------------------------------------------------------------------- #
# Lenses
# --------------------------------------------------------------------------- #
def temporal_lens(flagged: pd.DataFrame) -> list[Evidence]:
    out: list[Evidence] = []
    if flagged.empty or flagged["problem"].sum() < MIN_PROBLEMS:
        return out
    hp = tr.hour_pattern(flagged)
    conc = tr.concentration(hp, top_n=2)
    top = set(conc["top_hours"])
    in_top = flagged["hour"].isin(top)
    lift = _lift(flagged.loc[in_top, "problem"].mean(), flagged.loc[~in_top, "problem"].mean()) if (~in_top).any() else float("nan")
    if conc["concentrated"]:
        if top & PEAK_HOURS:
            cause, conf = "peak_capacity_dwell", 0.3
            why = "problems cluster in peak hours, consistent with dwell-time / crowding / capacity limits"
        elif top & LATE_NIGHT_HOURS:
            cause, conf = "planned_work", 0.4
            why = "problems cluster late at night, typical of planned track work windows"
        else:
            cause, conf = "time_specific", 0.35
            why = "problems cluster in specific off-peak hours"
        out.append(Evidence("temporal", cause, conc["problem_share"], _finite_lift(lift), conf,
                            f"{conc['problem_share']:.0%} of problem arrivals fall in hours {sorted(top)} "
                            f"(which carry {conc['arrival_share']:.0%} of arrivals); {why}.",
                            {"hour_pattern": hp, "top_hours": sorted(top)}))
    dp = tr.dow_pattern(flagged)
    if not dp.empty and dp["problems"].sum() > 0:
        wk = dp[dp["dow"] >= 5]
        wd = dp[dp["dow"] < 5]
        if len(wk) and len(wd) and wk["arrivals"].sum() > 0 and wd["arrivals"].sum() > 0:
            rate_wk = wk["problems"].sum() / wk["arrivals"].sum()
            rate_wd = wd["problems"].sum() / wd["arrivals"].sum()
            l = _lift(rate_wk, rate_wd)
            if np.isfinite(l) and l >= 1.5 and wk["problems"].sum() >= MIN_PROBLEMS:
                out.append(Evidence("temporal", "planned_work", float(wk["problems"].sum() / dp["problems"].sum()), l, 0.4,
                                    f"Weekend problem rate is {l:.1f}x the weekday rate; weekend service changes / planned work likely.",
                                    {"dow_pattern": dp}))
    return out


def alerts_lens(flagged: pd.DataFrame, alerts: pd.DataFrame, route_ids: list[str], stop_ids: list[str],
                open_end_grace_sec: float = 3 * 3600, defaults: config.AnalysisDefaults = config.DEFAULTS) -> list[Evidence]:
    out: list[Evidence] = []
    if flagged.empty or alerts is None or alerts.empty:
        return out
    ts = flagged["arrival_ts"].values
    routes = flagged["route_id"].astype(str).values
    unplanned_cat = np.array([None] * len(flagged), dtype=object)
    planned_hit = np.zeros(len(flagged), dtype=bool)
    stop_set = set(stop_ids)
    for a in alerts.itertuples(index=False):
        if alert_kind(a.alert_type, a.header) == "notice":
            continue  # boarding changes, station notices etc. are not delay conditions
        if a.routes and not (set(a.routes) & set(route_ids)):
            continue
        if a.stops and stop_set and not (set(a.stops) & stop_set) and not a.routes:
            continue
        start = a.active_start if a.active_start is not None and np.isfinite(a.active_start) else -np.inf
        end = a.active_end if a.active_end is not None and np.isfinite(a.active_end) else (
            (a.updated_at if a.updated_at is not None and np.isfinite(a.updated_at) else start) + open_end_grace_sec)
        m = (ts >= start) & (ts <= end)
        if a.routes:
            m &= np.isin(routes, list(a.routes))
        if not m.any():
            continue
        if a.planned:
            planned_hit |= m
        else:
            for i in np.where(m)[0]:
                if unplanned_cat[i] is None or unplanned_cat[i] == "unknown":
                    unplanned_cat[i] = a.cause_category
    prob = flagged["problem"].values.astype(bool)
    n_prob = prob.sum()
    if n_prob < MIN_LATE:
        return out
    covered = unplanned_cat != None  # noqa: E711
    if n_prob and covered.any() and (~covered).any():
        p_with = prob[covered].mean()
        p_without = prob[~covered].mean()
        lift = _lift(p_with, p_without)
        cats = pd.Series(unplanned_cat[prob & covered]).value_counts()
        for cat, n in cats.items():
            cat_share = n / n_prob
            if cat_share < 0.03:
                continue
            conf = 0.7 if lift >= defaults.alert_lift_threshold else 0.45
            out.append(Evidence("alerts", cat, float(cat_share), _finite_lift(lift), conf,
                                f"{cat_share:.0%} of problem arrivals happened while an MTA '{cat}' alert was active "
                                f"for the line (problem rate {p_with:.0%} with an alert vs {p_without:.0%} without, lift {lift:.1f}x).",
                                {"alert_category": cat, "n_problem_covered": int(n), "p_with": p_with, "p_without": p_without}))
    if n_prob and planned_hit.any():
        p_with = prob[planned_hit].mean()
        p_without = prob[~planned_hit].mean() if (~planned_hit).any() else float("nan")
        lift = _lift(p_with, p_without)
        share = prob[planned_hit].sum() / n_prob
        if share >= 0.03:
            out.append(Evidence("planned_work", "planned_work", float(share), _finite_lift(lift),
                                0.6 if lift >= 1.3 else 0.35,
                                f"{share:.0%} of problem arrivals occurred during planned service changes (lift {lift:.1f}x).",
                                {"p_with": p_with, "p_without": p_without}))
    return out


def upstream_lens(flagged: pd.DataFrame, upstream_matched: pd.DataFrame, upstream_stops: list[str],
                  static: StaticGTFS | None, defaults: config.AnalysisDefaults = config.DEFAULTS,
                  route_label: str = "") -> list[Evidence]:
    """``upstream_stops`` nearest-first. ``upstream_matched`` has lateness at those stops.

    Emits a *location* evidence (inherited vs local) and, from the per-segment
    run-time excess, a *cause* evidence (single slow segment vs spread-out dwell loss).
    """
    out: list[Evidence] = []
    if upstream_matched is None or upstream_matched.empty or not upstream_stops or flagged.empty:
        return out
    late = flagged[flagged["is_late"]]
    thr = defaults.late_threshold_sec
    if len(late) < MIN_LATE:
        late = flagged[flagged["lateness_sec"] >= MILD_LATE_SEC]
        thr = MILD_LATE_SEC
    if len(late) < MIN_LATE:
        return out
    name = (lambda s: static.stop_name(s) if static else s)
    rl = f"[{route_label}] " if route_label else ""
    piv = upstream_matched.pivot_table(index="trip_key", columns="stop_id", values="lateness_sec", aggfunc="first")
    arr_piv = upstream_matched.pivot_table(index="trip_key", columns="stop_id", values="arrival_ts", aggfunc="first")
    sch_piv = upstream_matched.pivot_table(index="trip_key", columns="stop_id", values="sched_arrival_ts", aggfunc="first")
    chain = list(reversed(upstream_stops))  # farthest -> nearest
    origins: dict[str, int] = {}
    inherited = 0
    n_eval = 0
    for row in late.itertuples(index=False):
        if row.trip_key not in piv.index:
            continue
        lats = piv.loc[row.trip_key]
        known = [(s, lats.get(s, np.nan)) for s in chain]
        known = [(s, l) for s, l in known if np.isfinite(l)]
        if not known:
            continue
        n_eval += 1
        nearest_stop, nearest_lat = known[-1]
        if nearest_lat >= thr * 0.6:
            inherited += 1
            origin = next((s for s, l in known if l >= thr * 0.6), known[0][0])
            if origin == known[0][0] and known[0][1] >= thr * 0.6:
                origin = f"at_or_before:{known[0][0]}"
        else:
            origin = f"approach:{nearest_stop}->target"
        origins[origin] = origins.get(origin, 0) + 1
    if n_eval < MIN_LATE:
        return out
    inh_share = inherited / n_eval
    local_share = 1 - inh_share
    # Segment run-time excess (actual minus scheduled) between consecutive stops for late trips.
    seg_rows = []
    stops_chain = chain + ["__target__"]
    tgt_arr = late.set_index("trip_key")["arrival_ts"]
    tgt_sch = late.set_index("trip_key")["sched_arrival_ts"]
    for i in range(len(stops_chain) - 1):
        s_from, s_to = stops_chain[i], stops_chain[i + 1]
        excess = []
        for tk in late["trip_key"]:
            if tk not in arr_piv.index:
                continue
            a0 = arr_piv.loc[tk].get(s_from, np.nan); c0 = sch_piv.loc[tk].get(s_from, np.nan)
            if s_to == "__target__":
                a1 = tgt_arr.get(tk, np.nan); c1 = tgt_sch.get(tk, np.nan)
            else:
                a1 = arr_piv.loc[tk].get(s_to, np.nan); c1 = sch_piv.loc[tk].get(s_to, np.nan)
            if all(np.isfinite(v) for v in (a0, a1, c0, c1)):
                excess.append((a1 - a0) - (c1 - c0))
        if excess:
            seg_rows.append({"from": s_from, "to": s_to, "from_name": name(s_from),
                             "to_name": "target" if s_to == "__target__" else name(s_to),
                             "n": len(excess), "median_excess_sec": float(np.median(excess)),
                             "p75_excess_sec": float(np.quantile(excess, 0.75))})
    segs = pd.DataFrame(seg_rows)
    worst = segs.sort_values("median_excess_sec", ascending=False).iloc[0] if len(segs) else None
    origin_tbl = sorted(origins.items(), key=lambda kv: -kv[1])
    origin_desc = ", ".join(f"{_origin_label(o, name)}: {n}" for o, n in origin_tbl[:4])
    qual = "late" if thr == defaults.late_threshold_sec else f"mildly late (>= {MILD_LATE_SEC}s)"
    if inh_share >= defaults.upstream_origin_share_threshold:
        cause, conf = "upstream_propagation", 0.7
        summ = (f"{rl}{inh_share:.0%} of {qual} trains were already late at {name(upstream_stops[0])} (inherited delay). "
                f"Origins: {origin_desc}.")
    elif local_share >= defaults.upstream_origin_share_threshold:
        cause, conf = "local_segment", 0.7
        summ = (f"{rl}{local_share:.0%} of {qual} trains lost their time between {name(upstream_stops[0])} and the target "
                f"(local cause on the approach: signals/timers, merge holds, dwell). Origins: {origin_desc}.")
    else:
        cause, conf = "mixed_origin", 0.4
        summ = f"{rl}Delay origin is mixed: {inh_share:.0%} inherited upstream, {local_share:.0%} on the approach. Origins: {origin_desc}."
    if worst is not None and worst["median_excess_sec"] > 30:
        summ += (f" Largest time loss on segment {worst['from_name']} -> {worst['to_name']} "
                 f"(median +{worst['median_excess_sec']:.0f}s vs schedule).")
    out.append(Evidence("upstream", cause, float(max(inh_share, local_share)), float("nan"), conf, summ,
                        {"route": route_label, "inherited_share": inh_share, "local_share": local_share, "n_evaluated": n_eval,
                         "lateness_threshold_sec": thr,
                         "origins": [{"origin": o, "label": _origin_label(o, name), "n": n} for o, n in origin_tbl],
                         "segments": segs}))
    # Run-time pattern: one slow segment vs. loss spread across stops.
    if len(segs) >= 2:
        lossy = segs[segs["median_excess_sec"] >= 20]
        total = float(segs["median_excess_sec"].clip(lower=0).sum())
        if total > 0 and worst is not None:
            top_share = float(worst["median_excess_sec"] / total)
            seg_desc = ", ".join(f"{r.from_name} -> {r.to_name} +{r.median_excess_sec:.0f}s" for r in lossy.itertuples(index=False))
            if len(lossy) >= 3 and top_share < 0.5:
                out.append(Evidence("run_time_pattern", "dwell_time", float(min(1.0, len(lossy) / max(len(segs), 1))), float("nan"), 0.5,
                                    f"{rl}Time is lost a little at every stop ({seg_desc}): a dwell-time / crowding pattern rather than one bad segment.",
                                    {"route": route_label, "segments": lossy, "top_segment_share": top_share}))
            elif len(segs) >= 3 and worst["median_excess_sec"] >= 60 and top_share >= 0.6:
                out.append(Evidence("run_time_pattern", "segment_restriction", top_share, float("nan"), 0.5,
                                    f"{rl}{top_share:.0%} of the run-time loss sits on one segment ({worst['from_name']} -> {worst['to_name']}, "
                                    f"median +{worst['median_excess_sec']:.0f}s): a signal timer, speed restriction, or failure on that segment.",
                                    {"route": route_label, "segment": f"{worst['from_name']} -> {worst['to_name']}", "top_segment_share": top_share}))
    return out


def _origin_label(origin: str, name) -> str:
    if origin.startswith("at_or_before:"):
        return f"at or before {name(origin.split(':', 1)[1])}"
    if origin.startswith("approach:"):
        return f"on approach from {name(origin.split(':', 1)[1].split('->')[0])}"
    return f"at {name(origin)}"


def gap_inheritance_lens(flagged: pd.DataFrame, upstream_matched: pd.DataFrame, nearest_upstream: str | None,
                         static: StaticGTFS | None, defaults: config.AnalysisDefaults = config.DEFAULTS,
                         route_label: str = "") -> list[Evidence]:
    out: list[Evidence] = []
    gaps = flagged[flagged["is_gap"]]
    if len(gaps) < MIN_GAPS or upstream_matched is None or upstream_matched.empty or not nearest_upstream:
        return out
    up = upstream_matched[upstream_matched["stop_id"] == nearest_upstream].sort_values("arrival_ts").copy()
    if route_label:
        up = up[up["route_id"].astype(str) == route_label]
    if up.empty:
        return out
    up["headway_sec"] = up.groupby("route_id")["arrival_ts"].diff()
    ref = up["sched_headway_sec"] if "sched_headway_sec" in up else pd.Series(np.nan, index=up.index)
    fallback = up.groupby(pd.to_datetime(up["arrival_ts"], unit="s", utc=True).dt.hour)["headway_sec"].transform("median")
    up["ratio"] = up["headway_sec"] / ref.fillna(fallback)
    up_gap = up.set_index("trip_key")["ratio"]
    ratios = gaps["trip_key"].map(up_gap)
    known = ratios.dropna()
    if len(known) < MIN_GAPS:
        return out
    inherited = float((known >= defaults.gap_ratio).mean())
    nm = static.stop_name(nearest_upstream) if static else nearest_upstream
    rl = f"[{route_label}] " if route_label else ""
    if inherited >= 0.6:
        cause, conf, txt = "upstream_propagation", 0.6, f"{rl}{inherited:.0%} of headway gaps at the target already existed at {nm}."
    elif inherited <= 0.3:
        cause, conf, txt = "local_segment", 0.6, f"{rl}Only {inherited:.0%} of gaps existed at {nm}: gaps open up on the approach (holds, merges, slow segment)."
    else:
        cause, conf, txt = "mixed_origin", 0.3, f"{rl}{inherited:.0%} of gaps existed at {nm}."
    out.append(Evidence("gap_inheritance", cause, inherited if cause != "local_segment" else 1 - inherited, float("nan"), conf, txt,
                        {"route": route_label, "inherited_gap_share": inherited, "n": int(len(known))}))
    return out


def merge_lens(flagged: pd.DataFrame, upstream_matched: pd.DataFrame, route_id: str, upstream_stops: list[str],
               merge_stops: dict[str, list[str]], static: StaticGTFS | None, conflict_window_sec: int = 180,
               min_extra_sec: float = 45.0) -> list[Evidence]:
    """Do trains of ``route_id`` lose time at an interlining stop when another route's train
    is just ahead of where they *would* have arrived?

    ``merge_stops`` maps other route -> stops they share with this route on the approach.
    Conflict = another route's train arrived at the merge stop within ``conflict_window_sec``
    before this train's unimpeded arrival (scheduled arrival + lateness at the previous stop).
    """
    out: list[Evidence] = []
    if upstream_matched is None or upstream_matched.empty or flagged.empty or not upstream_stops:
        return out
    chain = list(reversed(upstream_stops))  # farthest -> nearest
    um = upstream_matched.dropna(subset=["sched_arrival_ts"]).copy()
    um["route_id"] = um["route_id"].astype(str)
    mine = um[um["route_id"] == str(route_id)]
    others = um[um["route_id"] != str(route_id)]
    if mine.empty or others.empty:
        return out
    tgt = flagged[flagged["route_id"].astype(str) == str(route_id)].set_index("trip_key")
    shared = {s for stops in merge_stops.values() for s in stops}
    best = None
    for i, s in enumerate(chain):
        if s not in shared or i == 0:
            continue
        prev = chain[i - 1]
        at_s = mine[mine["stop_id"] == s].set_index("trip_key")
        at_prev = mine[mine["stop_id"] == prev].set_index("trip_key")[["lateness_sec"]].rename(columns={"lateness_sec": "lat_prev"})
        j = at_s.join(at_prev, how="inner")
        if len(j) < 40:
            continue
        j["projected"] = j["sched_arrival_ts"] + j["lat_prev"]
        oth = np.sort(others[others["stop_id"] == s]["arrival_ts"].values)
        if oth.size == 0:
            continue
        idx = np.searchsorted(oth, j["projected"].values, side="right") - 1
        prev_other = np.where(idx >= 0, oth[np.clip(idx, 0, None)], -np.inf)
        j["conflict"] = (j["projected"].values - prev_other) <= conflict_window_sec
        j["delta"] = j["lateness_sec"] - j["lat_prev"]
        c, nc = j[j["conflict"]], j[~j["conflict"]]
        if len(c) < 15 or len(nc) < 15:
            continue
        extra = float(c["delta"].mean() - nc["delta"].mean())
        p = mann_whitney_p(c["delta"].values, nc["delta"].values)
        if extra >= min_extra_sec and p < 0.05 and (best is None or extra > best["extra"]):
            tgt_c = tgt.reindex(c.index)
            share = float(tgt_c["problem"].sum() / max(tgt["problem"].sum(), 1)) if "problem" in tgt else float("nan")
            leaders = others[others["stop_id"] == s]["route_id"].value_counts().to_dict()
            best = {"stop": s, "extra": extra, "p": p, "n_conflict": int(len(c)), "n_free": int(len(nc)),
                    "share": share, "leaders": leaders, "conflict_rate": float(len(c) / len(j))}
    if best:
        nm = static.stop_name(best["stop"]) if static else best["stop"]
        out.append(Evidence("merge", "interlining_merge", best["share"], float("nan"), 0.6,
                            f"[{route_id}] Trains that would arrive at {nm} within {conflict_window_sec}s behind a "
                            f"{'/'.join(map(str, best['leaders']))} train lose {best['extra']:.0f}s more there than others "
                            f"(p={best['p']:.3f}; {best['conflict_rate']:.0%} of trips affected); "
                            f"{best['share']:.0%} of problem arrivals at the target had such a conflict.",
                            {"merge_stop": best["stop"], "merge_stop_name": nm, "leader_routes": best["leaders"],
                             "extra_sec": best["extra"], "p": best["p"], "n_conflict": best["n_conflict"], "n_free": best["n_free"]}))
    return out


def terminal_lens(flagged: pd.DataFrame, terminal_matched: pd.DataFrame | None, terminal_stop: str | None,
                  static: StaticGTFS | None, defaults: config.AnalysisDefaults = config.DEFAULTS,
                  route_label: str = "") -> list[Evidence]:
    out: list[Evidence] = []
    late = flagged[flagged["is_late"]]
    if len(late) < MIN_LATE or terminal_matched is None or terminal_matched.empty or not terminal_stop:
        return out
    t = terminal_matched[terminal_matched["stop_id"] == terminal_stop].set_index("trip_key")["lateness_sec"]
    l = late["trip_key"].map(t).dropna()
    if len(l) < MIN_LATE:
        return out
    share = float((l >= defaults.late_threshold_sec * 0.6).mean())
    nm = static.stop_name(terminal_stop) if static else terminal_stop
    rl = f"[{route_label}] " if route_label else ""
    if share >= 0.4:
        out.append(Evidence("terminal", "terminal_dispatch", share, float("nan"), 0.6,
                            f"{rl}{share:.0%} of late trains were already late leaving {nm}: crew/rolling-stock availability or terminal dispatch.",
                            {"route": route_label, "n": int(len(l)), "terminal": terminal_stop}))
    return out


def service_delivered_lens(profile: pd.DataFrame) -> list[Evidence]:
    out: list[Evidence] = []
    if profile.empty or profile["n_sched"].isna().all():
        return out
    p = profile.dropna(subset=["service_delivered"])
    p = p[p["n_actual"] >= 4]
    prob_buckets = p[p["problem_share"] > 0]
    if len(prob_buckets) < 3:
        return out
    missing = prob_buckets["service_delivered"] < 0.9
    share = float(missing.mean())
    overall = float(p["service_delivered"].mean())
    if share >= 0.3 and overall < 0.97:
        out.append(Evidence("service_delivered", "missing_service", share, float("nan"), 0.7,
                            f"{share:.0%} of hours with problems ran fewer than 90% of scheduled trains "
                            f"(overall service delivered {overall:.0%}): cancellations or missing trips are creating gaps.",
                            {"service_delivered_overall": overall, "hours_below_90pct": int(missing.sum())}))
    return out


MTA_CATEGORY_MAP = {
    # Current Open Data reporting categories (Trains Delayed / Delay-Causing Incidents)
    "police & medical": "police_medical", "operating conditions": "operating_conditions",
    "external factors": "external_environment", "infrastructure & equipment": "infrastructure",
    "crew availability": "crew", "planned row work": "planned_work",
    # Older / more detailed category names
    "signals": "signal", "track": "track", "subway car": "rolling_stock", "rolling stock": "rolling_stock",
    "persons on trackbed/police/medical": "police_medical", "police": "police_medical", "medical": "police_medical",
    "planned row work": "planned_work", "planned work": "planned_work", "infrastructure & equipment": "infrastructure",
    "infrastructure and equipment": "infrastructure", "operating environment": "external_environment",
    "crew availability": "crew", "external factors": "external_environment", "other": "unknown",
    "capacity": "peak_capacity_dwell", "service delivery": "peak_capacity_dwell", "unruly customer": "police_medical",
    "weather": "weather", "stations & structure": "infrastructure",
}


def map_mta_category(cat: str | None) -> str:
    c = (cat or "").strip().lower()
    for k in sorted(MTA_CATEGORY_MAP, key=len, reverse=True):  # most specific label first
        if k in c:
            return MTA_CATEGORY_MAP[k]
    return "unknown" if not c else c.replace(" ", "_")


def incidents_lens(incidents: pd.DataFrame | None, lines: list[str], months: list[pd.Timestamp] | None = None) -> list[Evidence]:
    """Over-index the line's delay/incident category mix against all lines (Open Data)."""
    out: list[Evidence] = []
    if incidents is None or incidents.empty:
        return out
    df = incidents.copy()
    count_col = "delays" if "delays" in df else ("incidents" if "incidents" in df else "count")
    cat_col = "reporting_category" if "reporting_category" in df else "category"
    if cat_col not in df:
        return out
    if months:
        df = df[pd.to_datetime(df["month"]).isin(months)]
    if df.empty:
        return out
    df["line_norm"] = df["line"].astype(str).str.upper().str.strip()
    wanted = {l.upper() for l in lines}
    line_df = df[df["line_norm"].isin(wanted)]
    if line_df.empty:
        return out
    line_mix = line_df.groupby(cat_col)[count_col].sum()
    sys_mix = df.groupby(cat_col)[count_col].sum()
    line_share = line_mix / line_mix.sum()
    sys_share = sys_mix / sys_mix.sum()
    tbl = pd.DataFrame({"line_share": line_share, "system_share": sys_share.reindex(line_share.index)}).fillna(0)
    tbl["over_index"] = tbl["line_share"] / tbl["system_share"].replace(0, np.nan)
    tbl = tbl.sort_values("line_share", ascending=False)
    for cat, row in tbl.head(3).iterrows():
        if row["line_share"] < 0.08:
            continue
        cause = map_mta_category(cat)
        oi = row["over_index"] if np.isfinite(row["over_index"]) else 1.0
        conf = 0.45 if oi >= 1.2 else 0.2
        out.append(Evidence("incidents", cause, float(row["line_share"]), float(oi), conf,
                            f"MTA reports '{cat}' as {row['line_share']:.0%} of delays on line(s) {', '.join(lines)} "
                            f"({oi:.1f}x the system share) in the analysed months.",
                            {"category": cat, "table": tbl.reset_index().rename(columns={cat_col: "category"})}))
    return out


WEATHER_VARS = {"precip_mm": "rain", "snow_cm": "snow", "wind_max_kmh": "wind", "temp_max_c": "heat"}


def weather_lens(profile: pd.DataFrame, weather_daily: pd.DataFrame | None,
                 defaults: config.AnalysisDefaults = config.DEFAULTS) -> list[Evidence]:
    out: list[Evidence] = []
    if weather_daily is None or weather_daily.empty or profile.empty:
        return out
    daily = tr.daily_series(profile, "problem_share").rename("problem_rate").reset_index()
    daily.columns = ["date", "problem_rate"]
    w = weather_daily.copy()
    w["date"] = pd.to_datetime(w["date"]).dt.date
    daily["date"] = pd.to_datetime(daily["date"]).dt.date
    j = daily.merge(w, on="date", how="inner")
    if len(j) < 10 or j["problem_rate"].sum() == 0:
        return out
    best = None
    for col, label in WEATHER_VARS.items():
        if col not in j or j[col].nunique() < 3:
            continue
        rho, p = stats.spearmanr(j["problem_rate"], j[col])
        # Only positive associations are physically plausible for these variables.
        if np.isfinite(rho) and rho > 0 and (best is None or rho > best[1]):
            best = (label, float(rho), float(p))
    adverse = j["adverse_hours"] > 0 if "adverse_hours" in j else pd.Series(False, index=j.index)
    lift = _lift(j.loc[adverse, "problem_rate"].mean(), j.loc[~adverse, "problem_rate"].mean()) if adverse.any() and (~adverse).any() else float("nan")
    share = float(j.loc[adverse, "problem_rate"].sum() / max(j["problem_rate"].sum(), 1e-9)) if adverse.any() else 0.0
    if best and best[1] >= max(0.4, defaults.weather_correlation_threshold) and best[2] < 0.05:
        out.append(Evidence("weather", "weather", share, _finite_lift(lift), 0.5,
                            f"Daily problem rate rises with {best[0]} (Spearman rho={best[1]:.2f}, p={best[2]:.3f}); "
                            f"adverse-weather days have {lift:.1f}x the problem rate.",
                            {"variable": best[0], "rho": best[1], "p": best[2], "adverse_days": int(adverse.sum())}))
    elif np.isfinite(lift) and lift >= 1.5 and adverse.sum() >= 3 and share >= 0.25:
        out.append(Evidence("weather", "weather", share, lift, 0.4,
                            f"Adverse-weather days have {lift:.1f}x the problem rate ({int(adverse.sum())} adverse days).",
                            {"adverse_days": int(adverse.sum())}))
    return out


def prediction_volatility_lens(flagged: pd.DataFrame) -> list[Evidence]:
    out: list[Evidence] = []
    if flagged.empty or "pred_drift_sec" not in flagged or flagged["pred_drift_sec"].isna().all():
        return out
    prob = flagged["problem"]
    if prob.sum() < MIN_PROBLEMS or (~prob).sum() < MIN_PROBLEMS:
        return out
    d_p = flagged.loc[prob, "pred_drift_sec"].abs().mean()
    d_n = flagged.loc[~prob, "pred_drift_sec"].abs().mean()
    lift = _lift(d_p, d_n)
    if np.isfinite(lift) and lift >= 1.5 and d_p >= 60:
        out.append(Evidence("prediction_volatility", "operational_holds", float("nan"), lift, 0.3,
                            f"ETAs for problem arrivals drifted {d_p:.0f}s on average vs {d_n:.0f}s otherwise ({lift:.1f}x): "
                            f"trains are being held or re-dispatched upstream.",
                            {"drift_problem_sec": d_p, "drift_other_sec": d_n}))
    return out


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #
def rank_causes(evidences: list[Evidence], kind: str = "cause") -> list[dict]:
    """Aggregate evidence per cause (or per location finding) into a ranked list with a 0-1 support score."""
    buckets: dict[str, list[Evidence]] = {}
    for e in evidences:
        if e.kind != kind:
            continue
        buckets.setdefault(e.cause, []).append(e)
    ranked = []
    for cause, evs in buckets.items():
        score = 0.0
        for e in evs:
            share = e.share_explained if np.isfinite(e.share_explained) else 0.3
            lift = min(_finite_lift(e.lift), 3.0) / 1.5 if (np.isfinite(e.lift) and e.lens in LIFT_LENSES) else 1.0
            score += share * e.confidence * lift
        ranked.append({"cause": cause, "score": round(float(min(score, 1.0)), 3), "lenses": sorted({e.lens for e in evs}),
                       "evidence": [e.summary for e in evs]})
    ranked.sort(key=lambda r: -r["score"])
    return ranked
