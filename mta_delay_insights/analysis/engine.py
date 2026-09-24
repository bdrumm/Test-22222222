"""Orchestrates one station x line analysis end to end.

Steps
-----
1. Resolve the station / platform / routes from the static GTFS.
2. Load observed arrivals for baseline + window, match to the schedule, flag problems.
3. Detect *focus hours*: hours of the day whose problem share or additional
   platform time worsened significantly versus the baseline (unless the caller
   fixed the hours).
4. Compare window vs baseline (overall and in the focus hours) with bootstrap
   confidence intervals, Mann-Whitney tests and effect sizes; test trends.
5. Run the attribution lenses (where the delay originates, and why).
6. Estimate rider impact and a severity score; build recommendations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from .. import config
from ..sources.gtfs_static import NY_TZ, StaticGTFS
from ..storage.db import Store
from . import attribution as at
from . import metrics as mt
from . import recommendations as rc
from . import significance as sg
from . import trends as tr
from .report import InsightReport
from .schedule_match import match_arrivals, scheduled_counts


@dataclass
class AnalysisRequest:
    station: str                       # station name query or parent stop id
    direction: str                     # "N" or "S"
    routes: list[str] | None = None    # default: all routes at the platform
    window_start: datetime | None = None
    window_end: datetime | None = None
    baseline_start: datetime | None = None
    baseline_end: datetime | None = None
    hours: list[int] | None = None     # focus hours (local); default: auto-detect
    route_share_of_entries: float = 0.5
    defaults: config.AnalysisDefaults = field(default_factory=lambda: config.DEFAULTS)

    def resolve_windows(self, now: datetime | None = None) -> None:
        now = now or datetime.now(NY_TZ)
        if self.window_end is None:
            self.window_end = now
        if self.window_start is None:
            self.window_start = self.window_end - timedelta(days=14)
        if self.baseline_end is None:
            self.baseline_end = self.window_start
        if self.baseline_start is None:
            self.baseline_start = self.baseline_end - (self.window_end - self.window_start)
        for k in ("window_start", "window_end", "baseline_start", "baseline_end"):
            v = getattr(self, k)
            if v.tzinfo is None:
                setattr(self, k, v.replace(tzinfo=NY_TZ))


def _dates_between(a: datetime, b: datetime) -> list[date]:
    d0 = a.astimezone(NY_TZ).date()
    d1 = b.astimezone(NY_TZ).date()
    out = []
    d = d0 - timedelta(days=1)  # previous service day can spill past midnight
    while d <= d1:
        out.append(d)
        d += timedelta(days=1)
    return out


def resolve_target(static: StaticGTFS, req: AnalysisRequest) -> dict:
    st = static.find_stations(req.station)
    if st.empty:
        raise ValueError(f"no station matches {req.station!r}")
    if len(st) > 1 and req.routes:
        keep = []
        for r in st.itertuples(index=False):
            try:
                pid = static.platform_for(r.stop_id, req.direction)
            except KeyError:
                continue
            if set(req.routes) & set(static.routes_serving(pid)):
                keep.append(r)
        if keep:
            st = pd.DataFrame(keep)
    row = st.iloc[0]
    stop_id = static.platform_for(row.stop_id, req.direction)
    routes = req.routes or static.routes_serving(stop_id)
    return {"station_id": row.stop_id, "station_name": row.stop_name, "stop_id": stop_id,
            "direction": req.direction.upper(), "routes": [str(r) for r in routes],
            "candidates": st["stop_id"].tolist()}


def _compare_sets(fw: pd.DataFrame, fb: pd.DataFrame, prof_w: pd.DataFrame, prof_b: pd.DataFrame,
                  d: config.AnalysisDefaults) -> list[sg.Comparison]:
    if fb.empty or fw.empty:
        return []
    comps = [sg.compare_metric("lateness_sec", fw["lateness_sec"].values, fb["lateness_sec"].values, True, d),
             sg.compare_metric("headway_sec", fw["headway_sec"].values, fb["headway_sec"].values, True, d)]
    for metric, worse in (("apt_sec", True), ("late_share", True), ("gap_share", True), ("bunching_share", True),
                          ("headway_cv", True), ("problem_share", True), ("service_delivered", False)):
        comps.append(sg.compare_metric(metric, prof_w[metric].values, prof_b[metric].values, worse, d))
    return comps


def detect_focus_hours(prof_w: pd.DataFrame, prof_b: pd.DataFrame, d: config.AnalysisDefaults) -> tuple[list[int], pd.DataFrame]:
    """Per-hour window vs baseline tests. Returns (significantly worse hours, table)."""
    rows = []
    hours = sorted(set(prof_w["hour"]) & set(prof_b["hour"])) if not prof_b.empty else []
    for h in hours:
        w, b = prof_w[prof_w["hour"] == h], prof_b[prof_b["hour"] == h]
        me = sg.MIN_EFFECT_FOCUS
        cp = sg.compare_metric("problem_share", w["problem_share"].values, b["problem_share"].values, True, d, me)
        ca = sg.compare_metric("apt_sec", w["apt_sec"].values, b["apt_sec"].values, True, d, me)
        cl = sg.compare_metric("late_share", w["late_share"].values, b["late_share"].values, True, d, me)
        cm = sg.compare_metric("lateness_median_sec", w["lateness_median_sec"].values, b["lateness_median_sec"].values, True, d, me)
        cn = sg.compare_metric("lateness_mean_sec", w["lateness_mean_sec"].values, b["lateness_mean_sec"].values, True, d, me)
        worse = any(c.direction == "worse" for c in (cp, ca, cl, cm, cn))
        better = any(c.direction == "better" for c in (cp, ca, cl, cm, cn)) and not worse
        rows.append({"hour": int(h), "arrivals_window": int(w["n_actual"].sum()), "arrivals_baseline": int(b["n_actual"].sum()),
                     "problem_rate_window": cp.window_value, "problem_rate_baseline": cp.baseline_value,
                     "problem_rate_p": cp.p_value, "late_share_window": cl.window_value, "late_share_baseline": cl.baseline_value,
                     "lateness_median_window_sec": cm.window_value, "lateness_median_baseline_sec": cm.baseline_value,
                     "lateness_mean_window_sec": cn.window_value, "lateness_mean_baseline_sec": cn.baseline_value,
                     "apt_window_sec": ca.window_value, "apt_baseline_sec": ca.baseline_value, "apt_p": ca.p_value,
                     "verdict": "worse" if worse else ("better" if better else "flat")})
    tbl = pd.DataFrame(rows)
    focus = [int(r["hour"]) for r in rows if r["verdict"] == "worse"]
    return focus, tbl


def analyze_station(store: Store, static: StaticGTFS, req: AnalysisRequest,
                    ridership_profile: pd.DataFrame | None = None, incidents: pd.DataFrame | None = None,
                    weather_daily: pd.DataFrame | None = None, alerts: pd.DataFrame | None = None,
                    coverage_intervals: list[tuple[float, float]] | None = None) -> InsightReport:
    """``coverage_intervals``: (start_ts, end_ts) periods during which the feed was polled.
    Defaults to the store's snapshot log; pass an empty list for a continuous collector."""
    req.resolve_windows()
    d = req.defaults
    target = resolve_target(static, req)
    stop_id, routes = target["stop_id"], target["routes"]
    sources_used = ["static GTFS", "GTFS-RT trip updates (observed arrivals)"]
    caveats: list[str] = []

    # ---- 2. arrivals + schedule ------------------------------------------- #
    arrivals = store.arrivals(stop_id, req.baseline_start.timestamp(), req.window_end.timestamp(), routes)
    if arrivals.empty:
        raise ValueError(f"no observed arrivals for {stop_id} routes {routes} in the requested period")
    matched = match_arrivals(arrivals, static, d.schedule_match_tolerance_sec)
    dates = _dates_between(req.baseline_start, req.window_end)
    sched = scheduled_counts(static, stop_id, routes, dates)
    flagged = mt.flag_arrivals(matched, sched, d)
    if req.hours:
        flagged = flagged[flagged["hour"].isin(req.hours)]
    in_window = (flagged["arrival_ts"] >= req.window_start.timestamp()) & (flagged["arrival_ts"] < req.window_end.timestamp())
    in_base = (flagged["arrival_ts"] >= req.baseline_start.timestamp()) & (flagged["arrival_ts"] < req.baseline_end.timestamp())
    fw_all, fb_all = flagged[in_window], flagged[in_base]
    intervals = coverage_intervals if coverage_intervals is not None else store.coverage_intervals()
    prof_w_all = mt.apply_coverage(mt.hourly_profile(fw_all, sched, d), intervals)
    prof_b_all = mt.apply_coverage(mt.hourly_profile(fb_all, sched, d), intervals)
    prof_all = mt.apply_coverage(mt.hourly_profile(flagged, sched, d), intervals)

    # ---- 3. focus hours ---------------------------------------------------- #
    hour_table = pd.DataFrame()
    if req.hours:
        focus_hours, focus_mode = list(req.hours), "requested"
    else:
        focus_hours, hour_table = detect_focus_hours(prof_w_all, prof_b_all, d)
        focus_mode = "detected" if focus_hours else "none"
    if focus_hours and focus_mode == "detected":
        fw, fb = fw_all[fw_all["hour"].isin(focus_hours)], fb_all[fb_all["hour"].isin(focus_hours)]
        prof_w, prof_b = prof_w_all[prof_w_all["hour"].isin(focus_hours)], prof_b_all[prof_b_all["hour"].isin(focus_hours)]
    else:
        fw, fb, prof_w, prof_b = fw_all, fb_all, prof_w_all, prof_b_all
    sum_w, sum_b = mt.summarize_profile(prof_w), mt.summarize_profile(prof_b)
    sum_w_all, sum_b_all = mt.summarize_profile(prof_w_all), mt.summarize_profile(prof_b_all)

    # ---- 4. significance + trends ---------------------------------------- #
    comparisons = _compare_sets(fw, fb, prof_w, prof_b, d)
    comparisons_all = _compare_sets(fw_all, fb_all, prof_w_all, prof_b_all, d) if focus_mode == "detected" else []
    if fb_all.empty:
        caveats.append("no baseline arrivals: comparisons and severity are not available; the report describes the window only")
    trend_hours = focus_hours if focus_hours else None
    trends = [tr.trend_test(prof_all, "apt_sec", trend_hours, True, d.alpha),
              tr.trend_test(prof_all, "problem_share", trend_hours, True, d.alpha)]
    hour_pat = tr.hour_pattern(fw_all)

    # ---- 5. attribution ---------------------------------------------------- #
    evidences: list[at.Evidence] = []
    evidences += at.temporal_lens(fw_all)
    alerts_df = alerts if alerts is not None else store.alerts(req.window_start.timestamp(), req.window_end.timestamp())
    if alerts_df is not None and not alerts_df.empty:
        sources_used.append("service alerts")
        evidences += at.alerts_lens(fw, alerts_df, routes, [stop_id, target["station_id"]], defaults=d)
    ups_by_route = {r: static.upstream_stops(r, target["direction"], stop_id, d.upstream_stops_to_check) for r in routes}
    terminals = {r: static.terminal_stop(r, target["direction"]) for r in routes}
    ctx_stops = sorted({s for v in ups_by_route.values() for s in v} | {t for t in terminals.values() if t})
    upstream_matched = pd.DataFrame()
    if ctx_stops and not fw.empty:
        up = store.arrivals_for_trips(fw["trip_key"].unique())
        up = up[up["stop_id"].isin(ctx_stops)]
        if not up.empty:
            upstream_matched = match_arrivals(up, static, d.schedule_match_tolerance_sec)
            for r in routes:
                ups = ups_by_route.get(r, [])
                if not ups:
                    continue
                fw_r = fw[fw["route_id"] == r]
                if fw_r.empty:
                    continue
                near = store.arrivals(ups[0], req.window_start.timestamp() - 3600, req.window_end.timestamp(), [r])
                near_m = match_arrivals(near, static, d.schedule_match_tolerance_sec) if not near.empty else pd.DataFrame()
                um = upstream_matched[upstream_matched["route_id"].astype(str) == r]
                if not near_m.empty:
                    um = pd.concat([um, near_m[~near_m["trip_key"].isin(um["trip_key"])]], ignore_index=True)
                label = r if len(routes) > 1 else ""
                evidences += at.upstream_lens(fw_r, um, ups, static, d, route_label=label)
                evidences += at.gap_inheritance_lens(fw_r, um, ups[0], static, d, route_label=label)
                evidences += at.terminal_lens(fw_r, um, terminals.get(r), static, d, route_label=label)
                merge_stops = static.merge_routes_upstream(r, target["direction"], stop_id, d.upstream_stops_to_check)
                if merge_stops:
                    evidences += at.merge_lens(fw_r, upstream_matched, r, ups, merge_stops, static)
        else:
            caveats.append("no upstream arrivals stored for the window's trips: upstream/terminal lenses skipped "
                           "(collect with stops_of_interest including upstream stops, or all stops)")
    primary_route = max(routes, key=lambda r: (fw["route_id"] == r).sum()) if routes and not fw.empty else None
    evidences += at.service_delivered_lens(prof_w)
    inc = incidents if incidents is not None else store.get_frame("trains_delayed")
    if inc is not None and not inc.empty:
        sources_used.append("NY Open Data: trains delayed / incidents")
        months = sorted({pd.Timestamp(dt.year, dt.month, 1) for dt in (req.window_start, req.window_end)})
        found = at.incidents_lens(inc, routes, months)
        evidences += found if found else at.incidents_lens(inc, routes, None)
    wx = weather_daily if weather_daily is not None else store.get_frame("weather_daily")
    if wx is not None and not wx.empty:
        sources_used.append("Open-Meteo weather")
        evidences += at.weather_lens(prof_all if not focus_hours else prof_all[prof_all["hour"].isin(focus_hours)], wx, d)
    evidences += at.prediction_volatility_lens(fw)
    ranked_causes = at.rank_causes(evidences, "cause")
    ranked_locations = at.rank_causes(evidences, "location")

    # ---- 6. impact, severity, recommendations ----------------------------- #
    rp = ridership_profile if ridership_profile is not None else store.get_frame("ridership_profile")
    if rp is not None and not rp.empty:
        sources_used.append("NY Open Data: hourly ridership")
        if "day_type" in rp and (rp["day_type"] == "weekday").any():
            rp = rp[rp["day_type"] == "weekday"]
    impact_hours = focus_hours or ([int(h) for h in hour_pat.sort_values("problems", ascending=False).head(4)["hour"]] if not hour_pat.empty else None)
    if fb_all.empty:
        impact = sg.RiderImpact(0.0, 0.0, 0.0, 0.0, [], "no_baseline")
    else:
        impact = sg.rider_impact(prof_w_all, prof_b_all, rp if rp is not None and not rp.empty else None, impact_hours, req.route_share_of_entries)
    score, comps = sg.severity_score(comparisons, impact)

    hours_txt = ", ".join(f"{h:02d}:00-{h + 1:02d}:00" for h in sorted(focus_hours)[:4]) if focus_hours else "the affected hours"
    other_routes = [r for r in static.routes_serving(stop_id) if r not in routes]
    prim_ups = ups_by_route.get(primary_route, []) if primary_route else []
    ctx = {
        "station": target["station_name"], "routes": ", ".join(routes), "hours": hours_txt,
        "alternates": ", ".join(other_routes) if other_routes else "other lines at the station",
        "nearest_upstream": static.stop_name(prim_ups[0]) if prim_ups else "the previous stop",
        "terminal": static.stop_name(terminals[primary_route]) if primary_route and terminals.get(primary_route) else "the terminal",
        "apt": f"{sum_w.get('apt_sec', 0) / 60:.1f} min" if sum_w else "extra",
        "gap_min": f"{(sum_w.get('sched_headway_sec') or 300) * d.gap_ratio / 60:.0f}" if sum_w else "10",
    }
    recs = rc.build_recommendations(ranked_causes + ranked_locations, evidences, ctx, sum_w)

    coverage = {
        "arrivals_in_window": int(len(fw_all)), "arrivals_in_baseline": int(len(fb_all)),
        "focus_hours": f"{focus_mode}: {sorted(focus_hours)}" if focus_hours else "none (all hours)",
        "arrivals_in_focus_hours": int(len(fw)),
        "schedule_match_rate": f"{fw_all['sched_arrival_ts'].notna().mean():.0%}" if len(fw_all) else "n/a",
        "mean_confidence": f"{fw_all['confidence'].mean():.2f}" if len(fw_all) else "n/a",
        "upstream_stops_used": {r: [static.stop_name(s) for s in v] for r, v in ups_by_route.items()},
        "upstream_arrivals": int(len(upstream_matched)),
        "alerts_in_window": int(len(alerts_df)) if alerts_df is not None else 0,
        "days_in_window": int(fw_all["service_date"].nunique()) if len(fw_all) else 0,
        "polling_intervals": len(intervals),
    }
    if impact.ridership_source == "placeholder_1000_per_hour":
        caveats.append("rider impact uses a placeholder of 1,000 entries/hour; load hourly ridership for a real estimate")
    if intervals:
        caveats.append(f"scheduled counts scaled by observed coverage ({len(intervals)} polling intervals); hours covered < 50% excluded from service delivered")
    if len(fw_all) and fw_all["confidence"].mean() < 0.7:
        caveats.append("many observed arrivals have low confidence (trips vanished from the feed); consider a shorter poll interval")
    if focus_mode == "none" and not req.hours:
        caveats.append("no hour of the day worsened significantly versus the baseline; results describe the whole day")
    # Series for charts: daily (weighted by arrivals) and the day x hour grid.
    daily = pd.DataFrame({"date": []})
    if not prof_all.empty:
        cols = {}
        for m in ("apt_sec", "problem_share", "lateness_mean_sec", "late_share", "gap_share"):
            cols[m] = tr.daily_series(prof_all, m, focus_hours or None)
        daily = pd.DataFrame(cols)
        n_by_day = prof_all.groupby("service_date")["n_actual"].sum()
        daily["n_actual"] = n_by_day.reindex(daily.index).fillna(0).astype(int)
        daily.index.name = "date"
        daily = daily.reset_index()
        daily["date"] = pd.to_datetime(daily["date"]).dt.strftime("%Y-%m-%d")
        daily["in_window"] = daily["date"] >= req.window_start.astimezone(NY_TZ).strftime("%Y-%m-%d")
    grid = prof_all[["service_date", "hour", "n_actual", "problem_share", "apt_sec", "lateness_mean_sec", "late_share"]].copy() if not prof_all.empty else pd.DataFrame()
    if not grid.empty:
        grid["service_date"] = pd.to_datetime(grid["service_date"]).dt.strftime("%Y-%m-%d")
    worse = [c.metric for c in comparisons if c.direction == "worse"]
    if worse:
        verdict = (f"Service in the {'focus hours' if focus_hours else 'window'} is significantly worse than the baseline on "
                   f"{', '.join(worse)}.")
    elif comparisons:
        verdict = ("No significant, material deterioration versus the baseline. The location and cause sections describe the "
                   "background pattern of the (few) problem arrivals, not a new issue.")
    else:
        verdict = "No baseline available; the report describes the window only."

    return InsightReport(
        target={k: v for k, v in target.items() if k != "candidates"},
        window={"start": req.window_start.isoformat(), "end": req.window_end.isoformat()},
        baseline={"start": req.baseline_start.isoformat(), "end": req.baseline_end.isoformat()},
        severity_score=score, severity_label=sg.severity_label(score), severity_components=comps,
        window_summary=sum_w, baseline_summary=sum_b, comparisons=comparisons, trends=trends,
        hour_pattern=hour_pat, ranked_causes=ranked_causes, evidence=evidences, impact=impact,
        recommendations=recs, coverage=coverage, sources_used=sources_used, caveats=caveats,
        focus_hours=sorted(focus_hours), focus_mode=focus_mode, hour_table=hour_table,
        comparisons_all=comparisons_all, ranked_locations=ranked_locations,
        window_summary_all=sum_w_all, baseline_summary_all=sum_b_all, verdict=verdict,
        daily_series=daily, bucket_grid=grid,
    )
