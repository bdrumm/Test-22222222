"""Line-level and system-level insights from the MTA's monthly Open Data series.

These complement the station diagnosis with the "what is trending across the
system" view: delays by cause category per line, month-over-month and
year-over-year change, category over-index versus the system, customer journey
metrics (additional platform / train time, journey time performance) and major
incidents.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .attribution import _jsonable, map_mta_category


def _prep(df: pd.DataFrame | None, count_col_options: tuple[str, ...]) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    d = df.copy()
    month_col = next((c for c in ("month", "date", "period_start") if c in d), None)
    if month_col is None or "line" not in d:
        return None
    d["month"] = pd.to_datetime(d[month_col], errors="coerce").dt.to_period("M").dt.to_timestamp()
    d = d.dropna(subset=["month"])
    if d.empty:
        return None
    d["line"] = d["line"].astype(str).str.strip()
    cat_col = "reporting_category" if "reporting_category" in d else "category"
    if cat_col not in d:
        return None
    d["category"] = d[cat_col].astype(str).str.strip()
    count_col = next((c for c in count_col_options if c in d), None)
    if count_col is None:
        return None
    d["n"] = pd.to_numeric(d[count_col], errors="coerce").fillna(0)
    return d[["month", "line", "category", "n"]]


def _pct_change(cur: float, prev: float) -> float | None:
    if prev is None or not np.isfinite(prev) or prev == 0 or cur is None or not np.isfinite(cur):
        return None
    return float((cur - prev) / prev)


def line_insights(trains_delayed: pd.DataFrame | None, customer_journey: pd.DataFrame | None = None,
                  major_incidents: pd.DataFrame | None = None, months: int = 24, top_categories: int = 6) -> dict:
    out: dict = {"months": [], "lines": {}, "system": {}, "categories": []}
    td = _prep(trains_delayed, ("delays", "count", "incidents"))
    if td is None:
        return out
    last = td["month"].max()
    first = last - pd.DateOffset(months=months - 1)
    td = td[td["month"] >= first]
    month_index = sorted(td["month"].unique())
    out["months"] = [m.strftime("%Y-%m") for m in month_index]
    # System-wide category ranking decides the fixed category order (top N + Other).
    sys_cat = td.groupby("category")["n"].sum().sort_values(ascending=False)
    cats = list(sys_cat.index[:top_categories])
    out["categories"] = cats + (["Other"] if len(sys_cat) > top_categories else [])
    td["cat_slot"] = np.where(td["category"].isin(cats), td["category"], "Other")

    sys_monthly = td.groupby(["month", "cat_slot"])["n"].sum().unstack(fill_value=0).reindex(month_index, fill_value=0)
    sys_total = sys_monthly.sum(axis=1)
    out["system"] = {
        "monthly_total": [float(v) for v in sys_total.reindex(month_index, fill_value=0)],
        "monthly_by_category": {c: [float(v) for v in sys_monthly.get(c, pd.Series(0, index=month_index)).reindex(month_index, fill_value=0)]
                                for c in out["categories"]},
        "latest_month": last.strftime("%Y-%m"),
        "latest_total": float(sys_total.iloc[-1]) if len(sys_total) else 0.0,
        "mom_change": _pct_change(sys_total.iloc[-1], sys_total.iloc[-2]) if len(sys_total) >= 2 else None,
        "yoy_change": _pct_change(sys_total.iloc[-1], sys_total.iloc[-13]) if len(sys_total) >= 13 else None,
    }
    recent3 = [m for m in month_index[-3:]]
    sys_share3 = td[td["month"].isin(recent3)].groupby("category")["n"].sum()
    sys_share3 = sys_share3 / max(sys_share3.sum(), 1)

    cj = None
    if customer_journey is not None and not customer_journey.empty and "month" in customer_journey and "line" in customer_journey:
        cj = customer_journey.copy()
        cj["month"] = pd.to_datetime(cj["month"], errors="coerce").dt.to_period("M").dt.to_timestamp()
        cj = cj.dropna(subset=["month"])
        cj["line"] = cj["line"].astype(str).str.strip()
        cj = cj[cj["month"] >= first]
        if cj.empty:
            cj = None
    mi = _prep(major_incidents, ("count", "incidents", "delays"))
    if mi is not None:
        mi = mi[mi["month"] >= first]

    ranking = []
    for line, g in td.groupby("line"):
        monthly = g.groupby(["month", "cat_slot"])["n"].sum().unstack(fill_value=0).reindex(month_index, fill_value=0)
        total = monthly.sum(axis=1)
        share3 = g[g["month"].isin(recent3)].groupby("category")["n"].sum()
        share3 = share3 / max(share3.sum(), 1)
        over = pd.DataFrame({"line_share": share3, "system_share": sys_share3.reindex(share3.index)}).fillna(0)
        over["over_index"] = over["line_share"] / over["system_share"].replace(0, np.nan)
        over = over.sort_values("line_share", ascending=False)
        entry = {
            "line": line,
            "monthly_total": [float(v) for v in total],
            "monthly_by_category": {c: [float(v) for v in monthly.get(c, pd.Series(0, index=month_index))] for c in out["categories"]},
            "latest_total": float(total.iloc[-1]) if len(total) else 0.0,
            "avg_3m": float(total.iloc[-3:].mean()) if len(total) else 0.0,
            "mom_change": _pct_change(total.iloc[-1], total.iloc[-2]) if len(total) >= 2 else None,
            "yoy_change": _pct_change(total.iloc[-1], total.iloc[-13]) if len(total) >= 13 else None,
            "category_mix_3m": [{"category": c, "cause": map_mta_category(c), "line_share": float(r.line_share),
                                 "system_share": float(r.system_share),
                                 "over_index": float(r.over_index) if np.isfinite(r.over_index) else None}
                                for c, r in over.iterrows()],
        }
        if cj is not None:
            cjl = cj[cj["line"] == line].sort_values("month")
            entry["journey"] = [{"month": r.month.strftime("%Y-%m"), "period": getattr(r, "period", None),
                                 "apt_min": _f(getattr(r, "additional_platform_time", None)),
                                 "att_min": _f(getattr(r, "additional_train_time", None)),
                                 "cjtp": _f(getattr(r, "customer_journey_time_performance", None)),
                                 "passengers": _f(getattr(r, "num_passengers", None))}
                                for r in cjl.itertuples(index=False)]
        if mi is not None:
            mil = mi[mi["line"] == line]
            if not mil.empty:
                mm = mil.groupby(["month", "category"])["n"].sum().unstack(fill_value=0).reindex(month_index, fill_value=0)
                entry["major_incidents"] = {c: [float(v) for v in mm[c]] for c in mm.columns}
                entry["major_incidents_total_12m"] = float(mm.iloc[-12:].values.sum())
        out["lines"][line] = entry
        ranking.append({"line": line, "avg_3m": entry["avg_3m"], "latest_total": entry["latest_total"],
                        "mom_change": entry["mom_change"], "yoy_change": entry["yoy_change"],
                        "top_category": entry["category_mix_3m"][0]["category"] if entry["category_mix_3m"] else None})
    ranking.sort(key=lambda r: -r["avg_3m"])
    out["system"]["ranking"] = ranking
    return _jsonable(out)


def _f(v):
    try:
        f = float(v)
        return None if np.isnan(f) else f
    except (TypeError, ValueError):
        return None
