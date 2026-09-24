"""Statistical significance and rider-impact estimation.

An issue is *significant* when the window differs from the baseline in a way
that is unlikely to be noise (bootstrap CI excludes zero, Mann-Whitney p below
alpha), *material* (effect size, additional platform time in minutes) and
*costly* (riders exposed x extra minutes). The severity score combines the
three on a 0-100 scale so issues at different stations can be ranked.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd
from scipy import stats

from .. import config


# Smallest difference that matters in practice; a statistically significant change below
# this is reported as "flat". Keys are metric names, values in the metric's own unit.
MIN_EFFECT: dict[str, float] = {
    "lateness_sec": 20.0, "headway_sec": 15.0, "apt_sec": 15.0, "late_share": 0.02, "gap_share": 0.02,
    "bunching_share": 0.02, "headway_cv": 0.03, "problem_share": 0.02, "service_delivered": 0.02,
    "lateness_median_sec": 20.0, "lateness_mean_sec": 20.0,
}
# Stricter thresholds used when scanning hours of the day (24 x 3 tests invite false positives).
MIN_EFFECT_FOCUS: dict[str, float] = {"apt_sec": 30.0, "late_share": 0.02, "problem_share": 0.03,
                                      "lateness_median_sec": 45.0, "lateness_mean_sec": 45.0}


@dataclass
class Comparison:
    metric: str
    window_value: float
    baseline_value: float
    diff: float
    ci_lo: float
    ci_hi: float
    p_value: float
    effect_size: float          # Cliff's delta, -1..1
    n_window: int
    n_baseline: int
    significant: bool           # statistically (CI excludes 0 and p < alpha)
    direction: str              # "worse" | "better" | "flat" (requires significant AND material)
    material: bool = True       # |diff| >= MIN_EFFECT[metric]

    def as_dict(self) -> dict:
        return asdict(self)


def bootstrap_mean_diff(a: np.ndarray, b: np.ndarray, n_iter: int = 2000, seed: int = 7,
                        alpha: float = 0.05) -> tuple[float, float, float]:
    rng = np.random.default_rng(seed)
    a = np.asarray(a, float); b = np.asarray(b, float)
    a = a[np.isfinite(a)]; b = b[np.isfinite(b)]
    if a.size == 0 or b.size == 0:
        return float("nan"), float("nan"), float("nan")
    diffs = rng.choice(a, (n_iter, a.size)).mean(axis=1) - rng.choice(b, (n_iter, b.size)).mean(axis=1)
    return float(a.mean() - b.mean()), float(np.quantile(diffs, alpha / 2)), float(np.quantile(diffs, 1 - alpha / 2))


def cliffs_delta(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, float); b = np.asarray(b, float)
    a = a[np.isfinite(a)]; b = b[np.isfinite(b)]
    if a.size == 0 or b.size == 0:
        return float("nan")
    # Vectorised for moderate sizes; subsample very large inputs.
    if a.size * b.size > 4_000_000:
        rng = np.random.default_rng(1)
        a = rng.choice(a, min(a.size, 2000), replace=False)
        b = rng.choice(b, min(b.size, 2000), replace=False)
    gt = (a[:, None] > b[None, :]).mean()
    lt = (a[:, None] < b[None, :]).mean()
    return float(gt - lt)


def mann_whitney_p(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, float); b = np.asarray(b, float)
    a = a[np.isfinite(a)]; b = b[np.isfinite(b)]
    if a.size < 2 or b.size < 2:
        return float("nan")
    if np.all(a == a[0]) and np.all(b == b[0]) and a[0] == b[0]:
        return 1.0
    return float(stats.mannwhitneyu(a, b, alternative="two-sided").pvalue)


def compare_metric(metric: str, window: np.ndarray, baseline: np.ndarray, higher_is_worse: bool = True,
                   defaults: config.AnalysisDefaults = config.DEFAULTS, min_effect: dict[str, float] | None = None) -> Comparison:
    w = np.asarray(window, float); b = np.asarray(baseline, float)
    w = w[np.isfinite(w)]; b = b[np.isfinite(b)]
    diff, lo, hi = bootstrap_mean_diff(w, b, defaults.bootstrap_iterations, alpha=defaults.alpha)
    p = mann_whitney_p(w, b)
    d = cliffs_delta(w, b)
    sig = bool(np.isfinite(lo) and np.isfinite(hi) and (lo > 0 or hi < 0) and (np.isnan(p) or p < defaults.alpha)
               and w.size >= defaults.min_samples and b.size >= defaults.min_samples)
    thresholds = min_effect if min_effect is not None else MIN_EFFECT
    material = bool(np.isfinite(diff) and abs(diff) >= thresholds.get(metric, 0.0))
    if not sig or not material or not np.isfinite(diff) or diff == 0:
        direction = "flat"
    else:
        direction = "worse" if (diff > 0) == higher_is_worse else "better"
    return Comparison(metric=metric, window_value=float(w.mean()) if w.size else float("nan"),
                      baseline_value=float(b.mean()) if b.size else float("nan"), diff=diff, ci_lo=lo, ci_hi=hi,
                      p_value=p, effect_size=d, n_window=int(w.size), n_baseline=int(b.size),
                      significant=sig, direction=direction, material=material)


@dataclass
class RiderImpact:
    """Extra time riders spend because of the issue, per day, in the hours considered.

    ``passenger_minutes_per_day`` = additional platform time (APT, from headway
    irregularity) + additional train time (ATT, from lateness), each multiplied
    by the riders exposed in that hour. This mirrors the MTA's Additional
    Journey Time decomposition.
    """
    riders_per_day_exposed: float
    extra_wait_min_per_rider: float
    passenger_minutes_per_day: float
    passenger_hours_per_day: float
    hours_considered: list[int]
    ridership_source: str
    apt_passenger_minutes_per_day: float = 0.0
    att_passenger_minutes_per_day: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


def rider_impact(window_profile: pd.DataFrame, baseline_profile: pd.DataFrame,
                 riders_per_hour: pd.DataFrame | None, hours: list[int] | None = None,
                 route_share: float = 1.0) -> RiderImpact:
    """Convert the APT change (window - baseline) per hour into passenger-minutes per day.

    ``riders_per_hour``: columns hour, riders_per_hour (entries at the station complex).
    ``route_share``: fraction of entries boarding the analysed routes/direction.
    """
    if window_profile.empty:
        return RiderImpact(0, 0, 0, 0, [], "none")
    hrs = hours or sorted(window_profile["hour"].unique().tolist())
    wp = window_profile[window_profile["hour"].isin(hrs)]
    bp = baseline_profile[baseline_profile["hour"].isin(hrs)] if not baseline_profile.empty else baseline_profile

    def delta_of(metric: str) -> pd.Series:
        w = wp.groupby("hour")[metric].mean()
        b = bp.groupby("hour")[metric].mean() if not bp.empty else pd.Series(dtype=float)
        return (w - b.reindex(w.index).fillna(0)).clip(lower=0).fillna(0)

    d_apt = delta_of("apt_sec")
    d_att = delta_of("lateness_mean_sec")
    if riders_per_hour is not None and not riders_per_hour.empty:
        rph = riders_per_hour.groupby("hour")["riders_per_hour"].mean().reindex(d_apt.index).fillna(0) * route_share
        src = "hourly_ridership"
    else:
        rph = pd.Series(1000.0 * route_share, index=d_apt.index)  # conservative placeholder
        src = "placeholder_1000_per_hour"
    apt_min = float((d_apt / 60.0 * rph).sum())
    att_min = float((d_att / 60.0 * rph).sum())
    pax_min = apt_min + att_min
    riders = float(rph.sum())
    return RiderImpact(
        riders_per_day_exposed=riders,
        extra_wait_min_per_rider=float(pax_min / riders) if riders > 0 else 0.0,
        passenger_minutes_per_day=pax_min, passenger_hours_per_day=pax_min / 60.0,
        hours_considered=[int(h) for h in hrs], ridership_source=src,
        apt_passenger_minutes_per_day=apt_min, att_passenger_minutes_per_day=att_min)


def severity_score(comparisons: list[Comparison], impact: RiderImpact | None) -> tuple[float, dict]:
    """0-100 severity: 35% statistical confidence, 25% effect size, 40% practical magnitude
    (extra platform wait + extra lateness in minutes, saturating at 3 min, scaled by rider exposure)."""
    if not comparisons:
        return 0.0, {}
    worse = [c for c in comparisons if c.direction == "worse"]
    conf = max((1 - c.p_value) for c in worse if np.isfinite(c.p_value)) if worse else 0.0
    eff = max(min(abs(c.effect_size), 1.0) for c in worse if np.isfinite(c.effect_size)) if worse else 0.0
    apt = next((c for c in comparisons if c.metric == "apt_sec"), None)
    lat = next((c for c in comparisons if c.metric == "lateness_sec"), None)
    apt_min = max(apt.diff, 0) / 60.0 if apt and np.isfinite(apt.diff) else 0.0
    lat_min = max(lat.diff, 0) / 60.0 if lat and np.isfinite(lat.diff) else 0.0
    magnitude = min((apt_min + lat_min) / 3.0, 1.0)
    exposure = 1.0
    if impact and impact.riders_per_day_exposed > 0 and impact.ridership_source == "hourly_ridership":
        exposure = min(1.0, 0.5 + impact.riders_per_day_exposed / 40000.0)
    score = 100.0 * (0.35 * conf + 0.25 * eff + 0.40 * magnitude * exposure)
    return round(float(score), 1), {"confidence": round(conf, 3), "effect": round(eff, 3),
                                    "magnitude": round(magnitude, 3), "exposure": round(exposure, 3)}


def severity_label(score: float) -> str:
    if score >= 70:
        return "critical"
    if score >= 45:
        return "high"
    if score >= 25:
        return "moderate"
    if score > 0:
        return "low"
    return "none"
