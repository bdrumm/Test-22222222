"""Playbook: cause category -> operator, rider and monitoring actions.

Recommendations are parameterised with the evidence (hours, stop names, routes)
so the output reads as a specific plan rather than generic advice.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field

from .attribution import Evidence


@dataclass
class Recommendation:
    cause: str
    audience: str            # operator | rider | monitoring
    action: str
    rationale: str
    expected_effect: str
    priority: int = 2        # 1 = highest
    tags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


PLAYBOOK: dict[str, list[dict]] = {
    "signal": [
        dict(audience="operator", priority=1,
             action="Prioritise signal maintenance / CBTC inspection on the approach segment {segment}",
             rationale="Signal alerts coincide with problem arrivals; recurring signal faults on one segment are usually a component issue.",
             expected_effect="Removes the recurring gap source; typical APT reduction equals the segment's median excess run time."),
        dict(audience="operator", priority=2,
             action="Pre-position a signal maintainer during {hours} on days with active work orders",
             rationale="Response time dominates the delay length for signal failures.",
             expected_effect="Shorter incident duration, fewer 50+ train major incidents."),
        dict(audience="rider", priority=2,
             action="During {hours}, check alerts for '{routes}' before entering; use the alternate routes {alternates} when a signal alert is posted",
             rationale="Signal-caused gaps last tens of minutes; alternates are usually unaffected.",
             expected_effect="Avoids the longest waits."),
    ],
    "track": [
        dict(audience="operator", priority=1, action="Schedule rail-condition inspection / grinding on the {segment} segment",
             rationale="Track alerts (rail condition, track fire, debris) over-index at this location.",
             expected_effect="Removes speed restrictions and the associated run-time excess."),
        dict(audience="operator", priority=2, action="Coordinate planned track work windows to avoid {hours}",
             rationale="Work windows overlapping busy hours amplify passenger impact.",
             expected_effect="Same work, lower passenger-minutes lost."),
    ],
    "rolling_stock": [
        dict(audience="operator", priority=1, action="Review car-class reliability for {routes}; pull chronic door/brake defects from peak assignments",
             rationale="Mechanical alerts correlate with problem arrivals.",
             expected_effect="Fewer disabled trains blocking the line."),
        dict(audience="operator", priority=2, action="Stage a spare train set near {terminal} during {hours}",
             rationale="A ready spare turns a 20-minute gap into a 5-minute one.",
             expected_effect="Caps the size of mechanical-caused gaps."),
    ],
    "police_medical": [
        dict(audience="operator", priority=2, action="Increase platform staffing / EMS coordination at {station} during {hours}",
             rationale="Sick-customer and police holds are shortened by fast on-site response.",
             expected_effect="Shorter holds; fewer knock-on delays."),
        dict(audience="rider", priority=3, action="Expect sporadic holds; allow 5 extra minutes during {hours}",
             rationale="These events are random but frequent enough to matter.",
             expected_effect="Reduces missed connections."),
    ],
    "person_on_track": [
        dict(audience="operator", priority=1, action="Evaluate platform barriers / intrusion detection at {station} and upstream stops",
             rationale="Track intrusions recur at specific stations and cause long suspensions.",
             expected_effect="Fewer intrusions and faster clearance."),
    ],
    "planned_work": [
        dict(audience="operator", priority=2, action="Review the general-order (planned work) schedule for {routes}; add supplement trains or move work windows off {hours}",
             rationale="Problems coincide with planned service changes.",
             expected_effect="Planned changes stop degrading the analysed hours."),
        dict(audience="rider", priority=1, action="Check weekend / late-night planned service changes for {routes}; use alternates {alternates}",
             rationale="Planned work is announced in advance.",
             expected_effect="Avoids known disruptions entirely."),
    ],
    "upstream_propagation": [
        dict(audience="operator", priority=1, action="Focus the investigation upstream: {origin_desc}. Re-run this analysis with that station as the target",
             rationale="Trains arrive already late; fixing the approach to {station} will not help.",
             expected_effect="Addresses the root cause instead of the symptom."),
        dict(audience="operator", priority=2, action="Apply headway-based dispatch/holding at {nearest_upstream} to even out gaps before {station}",
             rationale="Even if the origin is upstream, regulating headways closer to the target reduces the wait riders experience.",
             expected_effect="Lower expected wait for the same number of trains."),
    ],
    "local_segment": [
        dict(audience="operator", priority=1, action="Investigate the approach segment {segment}: signal timers, speed restrictions, merge holds and dwell at the previous stop",
             rationale="Trains lose time between {nearest_upstream} and {station}; the cause is local.",
             expected_effect="Recovers the segment's median excess run time on every late trip."),
        dict(audience="operator", priority=2, action="Review timer/grade-time signals and civil speed limits on {segment} for possible relief",
             rationale="Local run-time inflation is often a conservative timer setting.",
             expected_effect="Small per-train gain, applied to every train."),
    ],
    "mixed_origin": [
        dict(audience="operator", priority=2, action="Split the investigation: audit {segment} locally and re-run the analysis targeting {nearest_upstream}",
             rationale="Delay origin is mixed between upstream and the approach.",
             expected_effect="Isolates the larger contributor."),
    ],
    "interlining_merge": [
        dict(audience="operator", priority=1, action="Review merge priority and dispatch spacing at {merge_stop}, where {leader_routes} trains run ahead of {merge_route} trains bound for {station}",
             rationale="Arrivals following a train of another route are disproportionately delayed.",
             expected_effect="Fewer merge conflicts; smoother headways for both services."),
        dict(audience="operator", priority=2, action="Adjust the schedule so {leader_routes} and {merge_route} slots are offset by at least half a headway at {merge_stop}",
             rationale="Scheduled conflicts guarantee real conflicts.",
             expected_effect="Structural fix that survives day-to-day variability."),
    ],
    "terminal_dispatch": [
        dict(audience="operator", priority=1, action="Audit terminal operations at {terminal} during {hours}: crew reporting, car availability, turnaround time",
             rationale="Trains leave the terminal already late.",
             expected_effect="On-time departures remove the delay at the source."),
    ],
    "missing_service": [
        dict(audience="operator", priority=1, action="Fill cancelled trips on {routes} during {hours}: check crew availability and car assignments",
             rationale="Fewer trains than scheduled are running in the problem hours.",
             expected_effect="Restores scheduled headways; largest APT lever available."),
    ],
    "peak_capacity_dwell": [
        dict(audience="operator", priority=1, action="Deploy platform controllers and door-closing announcements at {station} and the two stops upstream during {hours}",
             rationale="Peak-concentrated problems are usually dwell-time driven.",
             expected_effect="Shorter dwells, less bunching; typically 5-10% headway variability reduction."),
        dict(audience="operator", priority=2, action="Consider even-headway (rather than schedule) dispatching for {routes} in {hours}",
             rationale="Bunching amplifies through the peak; headway regulation dampens it.",
             expected_effect="Lower expected wait for the same fleet."),
        dict(audience="rider", priority=2, action="Shift travel 15 minutes before or after {hours} where possible; expect {apt} extra wait in the peak",
             rationale="Problems are concentrated in a narrow window.",
             expected_effect="Avoids the worst of the delay."),
    ],
    "dwell_time": [
        dict(audience="operator", priority=1, action="Dwell-time programme at {station} and the stops before it during {hours}: platform controllers, door-closing announcements, step-back crews",
             rationale="Run-time loss is spread across every stop on the approach, the signature of long dwells.",
             expected_effect="Each stop recovers its excess dwell; the effect compounds along the line."),
        dict(audience="operator", priority=2, action="Check whether scheduled dwell and run times on the approach still match observed loads; re-time the schedule if not",
             rationale="A schedule with unrealistic dwells makes every train late by design.",
             expected_effect="Lateness disappears without changing operations."),
    ],
    "segment_restriction": [
        dict(audience="operator", priority=1, action="Inspect the {segment} segment for a speed restriction, grade-time signal or signal fault; check recent work orders",
             rationale="Most of the run-time loss sits on one segment.",
             expected_effect="Removing the restriction recovers the segment's excess on every trip."),
    ],
    "weather": [
        dict(audience="operator", priority=2, action="Activate the adverse-weather plan (drainage checks, heat-related speed restrictions review, snow plan) on forecast adverse days",
             rationale="Problem rate rises on adverse-weather days.",
             expected_effect="Reduced weather-induced delays."),
        dict(audience="rider", priority=3, action="On rainy/snowy days budget extra time for {routes} at {station}",
             rationale="Weather sensitivity is measurable at this station.",
             expected_effect="Fewer late arrivals."),
    ],
    "operational_holds": [
        dict(audience="operator", priority=3, action="Review dispatcher holding practice upstream of {station}; consider publishing hold reasons in the feed",
             rationale="ETAs drift substantially before problem arrivals, indicating holds.",
             expected_effect="Fewer discretionary holds; more accurate countdown clocks."),
    ],
    "external_environment": [
        dict(audience="operator", priority=3, action="Coordinate with external parties (utilities, events) affecting {routes}",
             rationale="Operating-environment delays over-index for the line.",
             expected_effect="Fewer external disruptions."),
    ],
    "infrastructure": [
        dict(audience="operator", priority=2, action="Prioritise infrastructure & equipment work orders on the {routes} corridor near {station}",
             rationale="Infrastructure categories over-index in MTA-reported delays for the line.",
             expected_effect="Fewer equipment-caused gaps."),
    ],
    "crew": [
        dict(audience="operator", priority=1, action="Address crew availability for {routes} in {hours} (extra board, overtime caps)",
             rationale="Crew availability is a reported cause and trips are missing.",
             expected_effect="Restores scheduled service."),
    ],
    "fire_smoke": [
        dict(audience="operator", priority=2, action="Track-bed cleaning and debris removal on the {segment} segment",
             rationale="Fire/smoke alerts over-index; debris is the usual fuel.",
             expected_effect="Fewer smoke conditions and evacuations."),
    ],
    "operating_conditions": [
        dict(audience="operator", priority=2, action="Review operating conditions on {routes} in {hours}: crowding and dwell management at {station} and upstream, holding practice, and schedule run times",
             rationale="The MTA attributes an outsized share of this line's delays to operating conditions (dwell, crowding, holds).",
             expected_effect="Fewer discretionary holds and shorter dwells; smoother headways."),
    ],
    "unknown": [
        dict(audience="monitoring", priority=3, action="Increase collection: record all stops (not only the target) and keep predictions to strengthen upstream/terminal lenses",
             rationale="The available evidence does not isolate a cause.",
             expected_effect="Better attribution in the next run."),
    ],
}

METRIC_PLAYBOOK = [
    ("bunching_share", 0.25, dict(cause="headway_regularity", audience="operator", priority=2,
                                  action="Introduce holding points / even-headway dispatch for {routes} before {station}",
                                  rationale="More than a quarter of arrivals are bunched (headway < 50% of scheduled).",
                                  expected_effect="Bunching converts directly into longer waits for the train behind.")),
    ("gap_share", 0.2, dict(cause="headway_regularity", audience="operator", priority=2,
                            action="Set a gap-filling rule: dispatch the next available train from {terminal} when a gap over {gap_min} min forms",
                            rationale="A fifth of arrivals follow a large gap.",
                            expected_effect="Caps the longest waits, which dominate the APT metric.")),
]


PLAYBOOK["reduced_service"] = PLAYBOOK["missing_service"]


def build_recommendations(ranked_causes: list[dict], evidences: list[Evidence], context: dict,
                          window_summary: dict, max_causes: int = 4) -> list[Recommendation]:
    recs: list[Recommendation] = []
    ctx = dict(context)
    ctx.setdefault("hours", "the affected hours")
    ctx.setdefault("alternates", "other lines at the station")
    ctx.setdefault("segment", "the approach segment")
    ctx.setdefault("nearest_upstream", "the previous stop")
    ctx.setdefault("terminal", "the terminal")
    ctx.setdefault("origin_desc", "see the upstream evidence")
    ctx.setdefault("leader_routes", "other routes")
    ctx.setdefault("apt", "extra")
    ctx.setdefault("gap_min", "10")
    ctx.setdefault("merge_stop", "the merge point")
    ctx.setdefault("merge_route", ctx.get("routes", "the analysed routes"))
    # Context comes from the strongest evidence: the segment with the largest run-time loss
    # across all upstream findings, the origins of the finding that evaluated most trains.
    best_seg, best_loss, best_n = None, -1.0, -1
    for e in evidences:
        d = e.details or {}
        if e.lens == "upstream":
            segs = d.get("segments")
            try:
                if segs is not None and len(segs):
                    worst = segs.sort_values("median_excess_sec", ascending=False).iloc[0]
                    if worst["median_excess_sec"] > best_loss:
                        best_loss = float(worst["median_excess_sec"])
                        best_seg = f"{worst['from_name']} -> {worst['to_name']}"
            except Exception:
                pass
            if d.get("origins") and d.get("n_evaluated", 0) > best_n:
                best_n = d.get("n_evaluated", 0)
                ctx["origin_desc"] = "; ".join(f"{o['label']} ({o['n']})" for o in d["origins"][:3])
        if e.lens == "merge" and d.get("leader_routes"):
            ctx["leader_routes"] = ", ".join(str(k) for k in d["leader_routes"])
            if d.get("merge_stop_name"):
                ctx["merge_stop"] = d["merge_stop_name"]
            if e.summary.startswith("["):
                ctx["merge_route"] = e.summary[1:e.summary.index("]")]
    if best_seg:
        ctx["segment"] = best_seg
    seen = set()
    for rc in ranked_causes[:max_causes]:
        cause = rc["cause"]
        for item in PLAYBOOK.get(cause, PLAYBOOK["unknown"]):
            key = (cause, item["action"])
            if key in seen:
                continue
            seen.add(key)
            recs.append(Recommendation(cause=cause, audience=item["audience"], action=_fmt(item["action"], ctx),
                                       rationale=_fmt(item["rationale"], ctx), expected_effect=_fmt(item["expected_effect"], ctx),
                                       priority=item["priority"], tags=[cause]))
    for metric, threshold, item in METRIC_PLAYBOOK:
        v = window_summary.get(metric)
        if v is not None and v == v and v >= threshold:
            recs.append(Recommendation(cause=item["cause"], audience=item["audience"], action=_fmt(item["action"], ctx),
                                       rationale=_fmt(item["rationale"], ctx), expected_effect=_fmt(item["expected_effect"], ctx),
                                       priority=item["priority"], tags=[metric]))
    if not recs:
        for item in PLAYBOOK["unknown"]:
            recs.append(Recommendation("unknown", item["audience"], _fmt(item["action"], ctx), item["rationale"],
                                       item["expected_effect"], item["priority"]))
    recs.sort(key=lambda r: (r.priority, r.audience != "operator"))
    return recs


def _fmt(template: str, ctx: dict) -> str:
    class _Safe(dict):
        def __missing__(self, k):
            return "{" + k + "}"
    return template.format_map(_Safe(ctx))
