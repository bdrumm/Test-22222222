"""Insight report model with Markdown and JSON renderers."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .attribution import Evidence, _jsonable
from .recommendations import Recommendation
from .significance import Comparison, RiderImpact
from .trends import TrendResult


@dataclass
class InsightReport:
    target: dict
    window: dict
    baseline: dict
    severity_score: float
    severity_label: str
    severity_components: dict
    window_summary: dict
    baseline_summary: dict
    comparisons: list[Comparison]
    trends: list[TrendResult]
    hour_pattern: pd.DataFrame
    ranked_causes: list[dict]
    evidence: list[Evidence]
    impact: RiderImpact | None
    recommendations: list[Recommendation]
    coverage: dict
    sources_used: list[str]
    caveats: list[str] = field(default_factory=list)
    focus_hours: list[int] = field(default_factory=list)
    focus_mode: str = "none"
    hour_table: pd.DataFrame = field(default_factory=pd.DataFrame)
    comparisons_all: list[Comparison] = field(default_factory=list)
    ranked_locations: list[dict] = field(default_factory=list)
    window_summary_all: dict = field(default_factory=dict)
    baseline_summary_all: dict = field(default_factory=dict)
    verdict: str = ""

    # ---- serialisation ---------------------------------------------------- #
    def to_dict(self) -> dict:
        return _jsonable({
            "target": self.target, "window": self.window, "baseline": self.baseline,
            "severity": {"score": self.severity_score, "label": self.severity_label, **self.severity_components},
            "verdict": self.verdict,
            "focus_hours": self.focus_hours, "focus_mode": self.focus_mode,
            "window_summary": self.window_summary, "baseline_summary": self.baseline_summary,
            "window_summary_all_hours": self.window_summary_all, "baseline_summary_all_hours": self.baseline_summary_all,
            "comparisons": [c.as_dict() for c in self.comparisons],
            "comparisons_all_hours": [c.as_dict() for c in self.comparisons_all],
            "hour_table": self.hour_table,
            "trends": [t.as_dict() for t in self.trends],
            "hour_pattern": self.hour_pattern,
            "ranked_locations": self.ranked_locations,
            "ranked_causes": self.ranked_causes,
            "evidence": [e.as_dict() for e in self.evidence],
            "impact": self.impact.as_dict() if self.impact else None,
            "recommendations": [r.as_dict() for r in self.recommendations],
            "coverage": self.coverage, "sources_used": self.sources_used, "caveats": self.caveats,
        })

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def to_markdown(self) -> str:
        t = self.target
        L: list[str] = []
        L.append(f"# Arrival analysis: {t['station_name']} ({t['stop_id']}) - routes {', '.join(t['routes'])}, {t['direction']}-bound")
        L.append("")
        L.append(f"**Window:** {self.window['start'][:10]} to {self.window['end'][:10]}  ")
        L.append(f"**Baseline:** {self.baseline['start'][:10]} to {self.baseline['end'][:10]}  ")
        if self.focus_hours:
            L.append(f"**Focus hours ({self.focus_mode}):** {', '.join(f'{h:02d}:00' for h in self.focus_hours)}  ")
        L.append(f"**Severity:** {self.severity_score}/100 ({self.severity_label})  ")
        if self.impact:
            L.append(f"**Rider impact:** ~{self.impact.passenger_hours_per_day:,.0f} passenger-hours/day of extra journey time "
                     f"(platform wait {self.impact.apt_passenger_minutes_per_day / 60:,.0f} h + lateness {self.impact.att_passenger_minutes_per_day / 60:,.0f} h; "
                     f"{self.impact.extra_wait_min_per_rider:.1f} min per exposed rider; "
                     f"{self.impact.riders_per_day_exposed:,.0f} riders/day exposed; source: {self.impact.ridership_source})  ")
        L.append("")
        if self.verdict:
            L.append(f"> {self.verdict}")
            L.append("")
        top_c = self.ranked_causes[0] if self.ranked_causes else None
        top_l = self.ranked_locations[0] if self.ranked_locations else None
        if top_l:
            L.append(f"**Where:** `{top_l['cause']}` (support {top_l['score']:.2f})  ")
        if top_c:
            L.append(f"**Most likely cause:** `{top_c['cause']}` (support {top_c['score']:.2f}; lenses: {', '.join(top_c['lenses'])})  ")
        L.append("")
        L.append("## What changed" + (" (focus hours)" if self.focus_mode == "detected" else ""))
        L.append("")
        L += _comparison_table(self.comparisons)
        if self.comparisons_all:
            L.append("")
            L.append("<details><summary>All hours</summary>")
            L.append("")
            L += _comparison_table(self.comparisons_all)
            L.append("")
            L.append("</details>")
        L.append("")
        if not self.hour_table.empty:
            L.append("## When (window vs baseline by hour)")
            L.append("")
            L.append("| hour | trains (w/b) | problem rate w | problem rate b | mean lateness w | mean lateness b | APT w | APT b | verdict |")
            L.append("|---:|---|---:|---:|---:|---:|---:|---:|---|")
            for r in self.hour_table.itertuples(index=False):
                mark = "**" if r.verdict == "worse" else ""
                L.append(f"| {mark}{int(r.hour):02d}{mark} | {r.arrivals_window}/{r.arrivals_baseline} | {_pct(r.problem_rate_window)} | {_pct(r.problem_rate_baseline)} | "
                         f"{_min(r.lateness_mean_window_sec)} | {_min(r.lateness_mean_baseline_sec)} | {_min(r.apt_window_sec)} | {_min(r.apt_baseline_sec)} | {mark}{r.verdict}{mark} |")
            L.append("")
        if self.trends:
            L.append("## Trend")
            L.append("")
            for tr_ in self.trends:
                cp = f"; change point {tr_.changepoint_date} (shift {tr_.changepoint_shift:+.2f})" if tr_.changepoint_date else ""
                L.append(f"- {tr_.metric}: {tr_.direction} over {tr_.n_days} days (slope {tr_.slope_per_day:+.3f}/day, "
                         f"Kendall tau {tr_.kendall_tau:+.2f}, p={_fmt_p(tr_.p_value)}){cp}")
            L.append("")
        L.append("## Where the delay originates")
        L.append("")
        if not self.ranked_locations:
            L.append("No upstream data available to locate the origin (collect upstream stops to enable this).")
        for i, rc_ in enumerate(self.ranked_locations, 1):
            L.append(f"{i}. **{rc_['cause']}** (support {rc_['score']:.2f}; {', '.join(rc_['lenses'])})")
            for ev in rc_["evidence"]:
                L.append(f"   - {ev}")
        L.append("")
        L.append("## Why (ranked causes)")
        L.append("")
        if not self.ranked_causes:
            L.append("No cause could be isolated from the available evidence.")
        for i, rc_ in enumerate(self.ranked_causes, 1):
            L.append(f"{i}. **{rc_['cause']}** (support {rc_['score']:.2f}; {', '.join(rc_['lenses'])})")
            for ev in rc_["evidence"]:
                L.append(f"   - {ev}")
        L.append("")
        L.append("## How to avoid / mitigate")
        L.append("")
        for aud in ("operator", "rider", "monitoring"):
            items = [r for r in self.recommendations if r.audience == aud]
            if not items:
                continue
            L.append(f"**{aud.title()}**")
            L.append("")
            for r in items:
                L.append(f"- (P{r.priority}) {r.action}  ")
                L.append(f"  _why:_ {r.rationale} _expected:_ {r.expected_effect}")
            L.append("")
        L.append("## Data coverage and caveats")
        L.append("")
        for k, v in self.coverage.items():
            L.append(f"- {k}: {v}")
        for c in self.caveats:
            L.append(f"- caveat: {c}")
        L.append("")
        L.append("Sources used: " + ", ".join(self.sources_used))
        return "\n".join(L)


def _comparison_table(comps: list[Comparison]) -> list[str]:
    out = ["| metric | window | baseline | diff | 95% CI | p | effect | verdict |",
           "|---|---:|---:|---:|---|---:|---:|---|"]
    for c in comps:
        mark = "**" if c.direction == "worse" else ""
        out.append(f"| {mark}{c.metric}{mark} | {_fmt_metric(c.metric, c.window_value)} | {_fmt_metric(c.metric, c.baseline_value)} | "
                   f"{_fmt_metric(c.metric, c.diff, signed=True)} | [{_fmt_metric(c.metric, c.ci_lo)}, {_fmt_metric(c.metric, c.ci_hi)}] | "
                   f"{_fmt_p(c.p_value)} | {c.effect_size:+.2f} | {mark}{c.direction}{mark} |")
    return out


def _pct(v) -> str:
    return "n/a" if v is None or not np.isfinite(v) else f"{v:.0%}"


def _min(v) -> str:
    return "n/a" if v is None or not np.isfinite(v) else f"{v / 60:.1f}m"


def _fmt_metric(metric: str, v: float, signed: bool = False) -> str:
    if v is None or not np.isfinite(v):
        return "n/a"
    if metric.endswith("_share") or metric in ("service_delivered",):
        return f"{v:+.1%}" if signed else f"{v:.1%}"
    if metric.endswith("_sec"):
        return f"{v / 60:+.2f} min" if signed else f"{v / 60:.2f} min"
    if metric == "headway_cv":
        return f"{v:+.2f}" if signed else f"{v:.2f}"
    return f"{v:+.3g}" if signed else f"{v:.3g}"


def _fmt_p(p: float) -> str:
    if p is None or not np.isfinite(p):
        return "n/a"
    return "<0.001" if p < 0.001 else f"{p:.3f}"
