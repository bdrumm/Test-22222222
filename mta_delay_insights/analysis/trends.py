"""Trend and pattern detection on the daily / hourly profile."""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd
from scipy import stats


@dataclass
class TrendResult:
    metric: str
    n_days: int
    slope_per_day: float
    kendall_tau: float
    p_value: float
    direction: str          # "worsening" | "improving" | "flat"
    changepoint_date: str | None
    changepoint_shift: float | None

    def as_dict(self) -> dict:
        return asdict(self)


def daily_series(profile: pd.DataFrame, metric: str, hours: list[int] | None = None) -> pd.Series:
    p = profile if not hours else profile[profile["hour"].isin(hours)]
    if p.empty:
        return pd.Series(dtype=float)
    w = p["n_actual"].astype(float)
    v = p[metric].astype(float)
    tmp = pd.DataFrame({"d": p["service_date"], "wv": v * w, "w": w}).dropna()
    g = tmp.groupby("d").sum()
    return (g["wv"] / g["w"]).sort_index()


def trend_test(profile: pd.DataFrame, metric: str, hours: list[int] | None = None,
               higher_is_worse: bool = True, alpha: float = 0.05) -> TrendResult:
    s = daily_series(profile, metric, hours).dropna()
    n = len(s)
    if n < 5:
        return TrendResult(metric, n, float("nan"), float("nan"), float("nan"), "flat", None, None)
    x = np.arange(n, dtype=float)
    slope = float(np.polyfit(x, s.values, 1)[0])
    tau, p = stats.kendalltau(x, s.values)
    direction = "flat"
    if np.isfinite(p) and p < alpha:
        direction = "worsening" if (slope > 0) == higher_is_worse else "improving"
    cp_date, cp_shift = changepoint(s)
    return TrendResult(metric, n, slope, float(tau), float(p), direction, cp_date, cp_shift)


def changepoint(s: pd.Series, min_segment: int = 3) -> tuple[str | None, float | None]:
    """Single most likely mean shift (binary segmentation on squared error)."""
    v = s.values.astype(float)
    n = len(v)
    if n < 2 * min_segment:
        return None, None
    total = ((v - v.mean()) ** 2).sum()
    best, best_i = 0.0, None
    for i in range(min_segment, n - min_segment + 1):
        a, b = v[:i], v[i:]
        gain = total - ((a - a.mean()) ** 2).sum() - ((b - b.mean()) ** 2).sum()
        if gain > best:
            best, best_i = gain, i
    if best_i is None or total <= 0 or best / total < 0.25:
        return None, None
    a, b = v[:best_i], v[best_i:]
    shift = float(b.mean() - a.mean())
    pooled = float(np.sqrt((a.var(ddof=0) * len(a) + b.var(ddof=0) * len(b)) / n))
    if pooled > 0 and abs(shift) < 1.0 * pooled:
        return None, None
    return str(s.index[best_i]), shift


def hour_pattern(flagged: pd.DataFrame) -> pd.DataFrame:
    """Problem rate and arrival counts by hour of day, plus the share of all problems per hour."""
    if flagged.empty:
        return pd.DataFrame(columns=["hour", "arrivals", "problems", "problem_rate", "problem_share"])
    g = flagged.groupby("hour").agg(arrivals=("problem", "size"), problems=("problem", "sum")).reset_index()
    g["problem_rate"] = g["problems"] / g["arrivals"]
    g["problem_share"] = g["problems"] / max(g["problems"].sum(), 1)
    return g


def dow_pattern(flagged: pd.DataFrame) -> pd.DataFrame:
    if flagged.empty:
        return pd.DataFrame(columns=["dow", "arrivals", "problems", "problem_rate"])
    f = flagged.copy()
    f["dow"] = pd.to_datetime(f["service_date"]).dt.dayofweek
    g = f.groupby("dow").agg(arrivals=("problem", "size"), problems=("problem", "sum")).reset_index()
    g["problem_rate"] = g["problems"] / g["arrivals"]
    return g


def concentration(pattern: pd.DataFrame, top_n: int = 2) -> dict:
    """How concentrated problems are in a few hours: top-n share of problems vs share of arrivals."""
    if pattern.empty or pattern["problems"].sum() == 0:
        return {"top_hours": [], "problem_share": 0.0, "arrival_share": 0.0, "concentrated": False}
    top = pattern.sort_values("problems", ascending=False).head(top_n)
    ps = float(top["problems"].sum() / pattern["problems"].sum())
    as_ = float(top["arrivals"].sum() / pattern["arrivals"].sum())
    return {"top_hours": [int(h) for h in top["hour"]], "problem_share": ps, "arrival_share": as_,
            "concentrated": ps >= 0.5 and ps >= 1.8 * as_}
