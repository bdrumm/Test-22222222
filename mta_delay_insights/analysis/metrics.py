"""Station x line service-quality metrics.

For a stream of arrivals at one platform we compute, per (service date, hour):

* lateness against the schedule (median, p90, share >= threshold),
* headway regularity **per route** (a local and an express service that share a
  platform are not interchangeable, so their headways are measured separately):
  mean, coefficient of variation, bunching and gap shares,
* expected platform wait for a randomly arriving rider ``E[h^2] / (2 E[h])``
  and the *additional platform time* (APT) versus the scheduled wait, which is
  the MTA's own customer-facing measure,
* service delivered (observed trains / scheduled trains),
* prediction volatility (how far ETAs drifted while the train approached).

Every arrival is also flagged (``is_late``, ``is_gap``, ``is_bunched``,
``problem``) so the attribution lenses can reason about individual events.
The reference headway for the flags is the *scheduled headway of the matched
trip*, so schedule transitions (e.g. 6 -> 10 minute headways at 22:00) do not
register as gaps.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config
from ..sources.gtfs_static import NY_TZ

PROFILE_COLUMNS = [
    "service_date", "hour", "n_actual", "n_sched", "service_delivered", "lateness_median_sec",
    "lateness_mean_sec", "lateness_p90_sec", "late_share", "headway_mean_sec", "headway_cv", "sched_headway_sec",
    "bunching_share", "gap_share", "max_gap_sec", "expected_wait_sec", "sched_expected_wait_sec",
    "apt_sec", "pred_drift_mean_sec", "problem_share", "bucket_start_ts",
]


def expected_wait(headways: pd.Series | np.ndarray) -> float:
    h = np.asarray(headways, dtype=float)
    h = h[np.isfinite(h) & (h > 0)]
    if h.size == 0:
        return float("nan")
    return float((h ** 2).sum() / (2.0 * h.sum()))


def local_hour(ts: pd.Series) -> pd.Series:
    return pd.to_datetime(ts, unit="s", utc=True).dt.tz_convert(NY_TZ).dt.hour


def flag_arrivals(arrivals: pd.DataFrame, sched_counts: pd.DataFrame | None = None,
                  defaults: config.AnalysisDefaults = config.DEFAULTS) -> pd.DataFrame:
    """Add per-route headway, headway ratio, leader info and problem flags to matched arrivals."""
    a = arrivals.sort_values("arrival_ts").copy()
    a["hour"] = local_hour(a["arrival_ts"])
    local = pd.to_datetime(a["arrival_ts"], unit="s", utc=True).dt.tz_convert(NY_TZ)
    a["bucket_start_ts"] = local.dt.floor("h").map(lambda t: t.timestamp())
    if "service_date" not in a:
        a["service_date"] = pd.to_datetime(a["arrival_ts"], unit="s", utc=True).dt.tz_convert(NY_TZ).dt.date
    a["route_id"] = a["route_id"].astype(str)
    # Headways are measured within a service day: the first train of the day has no headway
    # (the overnight gap is scheduled, not a service failure).
    a["headway_sec"] = a.groupby(["stop_id", "route_id", "service_date"])["arrival_ts"].diff()
    # Leader in the combined stream (any route): used by the merge lens.
    a["leader_route"] = a.groupby(["stop_id", "service_date"])["route_id"].shift(1)
    a["gap_to_leader_sec"] = a.groupby(["stop_id", "service_date"])["arrival_ts"].diff()
    if "sched_headway_sec" not in a:
        a["sched_headway_sec"] = np.nan
    fallback = a.groupby(["route_id", "hour"])["headway_sec"].transform("median")
    a["ref_headway_sec"] = a["sched_headway_sec"].astype(float).fillna(fallback)
    a["headway_ratio"] = a["headway_sec"] / a["ref_headway_sec"]
    a["is_late"] = a["lateness_sec"].fillna(0) >= defaults.late_threshold_sec
    a["is_gap"] = a["headway_ratio"].fillna(1.0) >= defaults.gap_ratio
    a["is_bunched"] = a["headway_ratio"].fillna(1.0) <= defaults.bunching_ratio
    a["problem"] = a["is_late"] | a["is_gap"]
    return a


def _route_bucket_stats(flagged: pd.DataFrame) -> pd.DataFrame:
    """Per (service_date, hour, route) headway statistics, later weighted by arrivals per route."""
    keys = ["service_date", "hour", "route_id"]
    g = flagged.groupby(keys, sort=False)
    hw = flagged["headway_sec"]
    tmp = flagged.assign(_hw=hw, _hw2=hw ** 2, _has=hw.notna().astype(int), _ref=flagged["ref_headway_sec"])
    agg = tmp.groupby(keys, sort=False).agg(n=("_has", "size"), n_hw=("_has", "sum"), hw_sum=("_hw", "sum"),
                                              hw2_sum=("_hw2", "sum"), hw_mean=("_hw", "mean"), hw_std=("_hw", lambda x: x.std(ddof=0)),
                                              ref_med=("_ref", "median")).reset_index()
    agg = agg[agg["n_hw"] > 0]
    agg["ew"] = agg["hw2_sum"] / (2.0 * agg["hw_sum"])
    agg["cv"] = np.where((agg["n_hw"] > 1) & (agg["hw_mean"] > 0), agg["hw_std"] / agg["hw_mean"], 0.0)
    agg = agg[np.isfinite(agg["ew"]) & np.isfinite(agg["ref_med"])]
    w = agg["n"].astype(float)
    out = pd.DataFrame({
        "service_date": agg["service_date"], "hour": agg["hour"],
        "_w": w, "_ew": w * agg["ew"], "_sew": w * agg["ref_med"] / 2.0, "_hm": w * agg["hw_mean"], "_cv": w * agg["cv"],
    }).groupby(["service_date", "hour"], sort=False).sum().reset_index()
    out["expected_wait_sec"] = out["_ew"] / out["_w"]
    out["sched_expected_wait_sec"] = out["_sew"] / out["_w"]
    out["headway_mean_sec"] = out["_hm"] / out["_w"]
    out["headway_cv"] = out["_cv"] / out["_w"]
    return out[["service_date", "hour", "expected_wait_sec", "sched_expected_wait_sec", "headway_mean_sec", "headway_cv"]]


def hourly_profile(flagged: pd.DataFrame, sched_counts: pd.DataFrame | None = None,
                   defaults: config.AnalysisDefaults = config.DEFAULTS) -> pd.DataFrame:
    """Aggregate flagged arrivals into the per (service_date, hour) profile (vectorised)."""
    if flagged.empty:
        return pd.DataFrame(columns=PROFILE_COLUMNS)
    f = flagged
    if "pred_drift_sec" not in f:
        f = f.assign(pred_drift_sec=np.nan)
    if "bucket_start_ts" not in f:
        f = f.assign(bucket_start_ts=np.nan)
    keys = ["service_date", "hour"]
    prof = f.groupby(keys, sort=False).agg(
        n_actual=("arrival_ts", "size"),
        lateness_median_sec=("lateness_sec", "median"),
        lateness_mean_sec=("lateness_sec", "mean"),
        lateness_p90_sec=("lateness_sec", lambda x: x.quantile(0.9) if x.notna().any() else np.nan),
        late_share=("is_late", "mean"),
        sched_headway_sec=("ref_headway_sec", "median"),
        bunching_share=("is_bunched", "mean"),
        gap_share=("is_gap", "mean"),
        max_gap_sec=("headway_sec", "max"),
        pred_drift_mean_sec=("pred_drift_sec", "mean"),
        problem_share=("problem", "mean"),
        bucket_start_ts=("bucket_start_ts", "min"),
    ).reset_index()
    prof = prof.merge(_route_bucket_stats(f), on=keys, how="left")
    if sched_counts is not None and not sched_counts.empty:
        prof = prof.merge(sched_counts[["service_date", "hour", "n_sched"]], on=keys, how="left")
    else:
        prof["n_sched"] = np.nan
    prof["hour"] = prof["hour"].astype(int)
    prof["n_actual"] = prof["n_actual"].astype(int)
    prof["service_delivered"] = prof["n_actual"] / prof["n_sched"]
    prof["apt_sec"] = prof["expected_wait_sec"] - prof["sched_expected_wait_sec"]
    return prof[PROFILE_COLUMNS].sort_values(["service_date", "hour"]).reset_index(drop=True)


def apply_coverage(prof: pd.DataFrame, intervals: list[tuple[float, float]] | None, min_fraction: float = 0.5) -> pd.DataFrame:
    """Scale scheduled counts by the share of each hour that was actually observed.

    Chunked collection (e.g. hourly jobs polling for 50 minutes) sees only part of
    each hour; without this, service_delivered under-counts and every partial hour
    looks like missing trains. Buckets covered less than ``min_fraction`` get NaN.
    """
    if prof.empty or not intervals:
        return prof
    iv = sorted((float(a), float(b)) for a, b in intervals if b > a)
    frac = []
    for start in prof["bucket_start_ts"]:
        if start is None or not np.isfinite(start):
            frac.append(1.0)
            continue
        end = start + 3600.0
        cov = sum(max(0.0, min(end, b) - max(start, a)) for a, b in iv)
        frac.append(min(1.0, cov / 3600.0))
    out = prof.copy()
    out["coverage_fraction"] = frac
    eff = out["n_sched"] * out["coverage_fraction"]
    out["service_delivered"] = np.where(out["coverage_fraction"] >= min_fraction, out["n_actual"] / eff.replace(0, np.nan), np.nan)
    return out


def summarize_profile(prof: pd.DataFrame) -> dict:
    """Sample-weighted headline numbers for a set of profile rows."""
    if prof.empty:
        return {}
    w = prof["n_actual"].astype(float)

    def wmean(col):
        v = prof[col].astype(float)
        m = v.notna() & w.notna()
        return float((v[m] * w[m]).sum() / w[m].sum()) if w[m].sum() > 0 else float("nan")

    return {
        "arrivals": int(w.sum()),
        "service_delivered": float(prof["n_actual"].sum() / prof["n_sched"].sum()) if prof["n_sched"].sum() > 0 else float("nan"),
        "late_share": wmean("late_share"),
        "lateness_median_sec": wmean("lateness_median_sec"),
        "lateness_mean_sec": wmean("lateness_mean_sec"),
        "gap_share": wmean("gap_share"),
        "bunching_share": wmean("bunching_share"),
        "headway_cv": wmean("headway_cv"),
        "sched_headway_sec": wmean("sched_headway_sec"),
        "expected_wait_sec": wmean("expected_wait_sec"),
        "apt_sec": wmean("apt_sec"),
        "problem_share": wmean("problem_share"),
        "pred_drift_mean_sec": wmean("pred_drift_mean_sec"),
    }
