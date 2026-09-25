"""Close the loop on the live forecasts: record what each snapshot predicted for the monitored
platforms and score it against the arrivals observed afterwards.

Three predictions are scored per (train, platform): the MTA feed's ETA, the model ETA shown on the
Live page (look-back calibration or the learned model), and the forward simulation's baseline
projection (position-corrected, no overtaking). The corroboration flag is scored too: when the
train's position said the feed was optimistic, did the train really arrive later than the feed said?
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .propagation import HORIZON_BUCKETS, horizon_bucket

PROJECTION_COLUMNS = ["made_ts", "target_id", "stop_id", "trip_id", "route_id", "horizon_sec", "feed_eta_ts", "model_eta_ts", "model_source",
                      "sim_eta_ts", "hold_eta_ts", "corroboration", "holding", "stalled", "stops_away"]
FORECAST_EVAL_COLUMNS = PROJECTION_COLUMNS + ["arrival_ts", "feed_err_sec", "model_err_sec", "sim_err_sec"]


def projections_from_live(live: dict) -> list[dict]:
    """One row per predicted arrival at a monitored platform in a live snapshot."""
    made = live.get("generated_ts")
    if not made:
        return []
    sim_at: dict[tuple[str, str], float] = {}          # (trip_id, stop_id) -> baseline projected arrival
    hold_at: dict[tuple[str, str], float] = {}
    for entry in live.get("simulation") or []:
        for name, store in (("baseline", sim_at), ("hold_persists", hold_at)):
            sc = (entry.get("scenarios") or {}).get(name)
            if not sc:
                continue
            stops = [s["stop_id"] for s in sc["stops"]]
            for t in sc["trains"]:
                for i, ts in t["points"]:
                    if 0 <= i < len(stops):
                        store[(t["trip_id"], stops[i])] = float(ts)
    rows = []
    for st in live.get("stations") or []:
        sid = st.get("stop_id")
        for a in st.get("arrivals") or []:
            if a.get("started") is False or not a.get("feed_eta_ts"):
                continue
            pos = a.get("position") or {}
            rows.append({"made_ts": float(made), "target_id": st.get("id"), "stop_id": sid, "trip_id": a["trip_id"], "route_id": a.get("route_id"),
                         "horizon_sec": float(a["feed_eta_ts"]) - float(made), "feed_eta_ts": float(a["feed_eta_ts"]),
                         "model_eta_ts": float(a["model_eta_ts"]) if a.get("model_eta_ts") else None, "model_source": a.get("model_source"),
                         "sim_eta_ts": sim_at.get((a["trip_id"], sid)), "hold_eta_ts": hold_at.get((a["trip_id"], sid)),
                         "corroboration": pos.get("corroboration"), "holding": bool(pos.get("holding")), "stalled": bool(pos.get("stalled")),
                         "stops_away": a.get("stops_away")})
    return rows


def evaluate_projections(projections: pd.DataFrame, arrivals: pd.DataFrame) -> pd.DataFrame:
    """Join projections to the observed arrival of the same train at the same platform; errors are prediction − actual."""
    if projections is None or projections.empty or arrivals is None or arrivals.empty:
        return pd.DataFrame(columns=FORECAST_EVAL_COLUMNS)
    obs = arrivals[["trip_id", "stop_id", "arrival_ts"]].dropna().sort_values("arrival_ts").drop_duplicates(["trip_id", "stop_id"], keep="last")
    df = projections.reindex(columns=PROJECTION_COLUMNS).merge(obs, on=["trip_id", "stop_id"], how="inner")
    # an "arrival" observed before the forecast was made is a different (earlier) visit or a dropout artefact
    df = df[df["arrival_ts"] >= df["made_ts"] - 120]
    for src in ("feed", "model", "sim"):
        df[f"{src}_err_sec"] = df[f"{src}_eta_ts"] - df["arrival_ts"]
    return df.reindex(columns=FORECAST_EVAL_COLUMNS)


def _score(g: pd.DataFrame, col: str) -> dict:
    v = g[col].dropna()
    if v.empty:
        return {"n": 0, "mae_sec": None, "bias_sec": None, "p90_abs_sec": None}
    a = v.abs()
    return {"n": int(len(v)), "mae_sec": round(float(a.mean()), 1), "bias_sec": round(float(v.mean()), 1), "p90_abs_sec": round(float(a.quantile(0.9)), 1)}


def summarize_forecast_eval(df: pd.DataFrame) -> dict:
    """Accuracy by horizon bucket for the feed, the model and the simulation, plus the corroboration check."""
    if df is None or df.empty:
        return {"n": 0, "by_horizon": [], "overall": {}, "corroboration": [], "days": 0}
    df = df.copy()
    df["bucket"] = df["horizon_sec"].map(horizon_bucket)
    by_h = []
    for lo, hi in HORIZON_BUCKETS:
        g = df[df["bucket"] == f"{lo}-{hi}"]
        if g.empty:
            continue
        by_h.append({"horizon_lo_sec": lo, "horizon_hi_sec": None if hi >= 10 ** 8 else hi, "n": int(len(g)),
                     "feed": _score(g, "feed_err_sec"), "model": _score(g, "model_err_sec"), "sim": _score(g, "sim_err_sec")})
    overall = {"feed": _score(df, "feed_err_sec"), "model": _score(df, "model_err_sec"), "sim": _score(df, "sim_err_sec")}
    # paired comparison on rows where all three exist
    both = df.dropna(subset=["feed_err_sec", "model_err_sec"])
    overall["model_beats_feed_share"] = round(float((both["model_err_sec"].abs() < both["feed_err_sec"].abs()).mean()), 3) if len(both) else None
    trio = df.dropna(subset=["feed_err_sec", "sim_err_sec"])
    overall["sim_beats_feed_share"] = round(float((trio["sim_err_sec"].abs() < trio["feed_err_sec"].abs()).mean()), 3) if len(trio) else None
    corr = []
    for label, g in df.groupby(df["corroboration"].fillna("no_position")):
        corr.append({"corroboration": label, "n": int(len(g)), "feed_bias_sec": _score(g, "feed_err_sec")["bias_sec"], "feed_mae_sec": _score(g, "feed_err_sec")["mae_sec"],
                     "model_mae_sec": _score(g, "model_err_sec")["mae_sec"], "sim_mae_sec": _score(g, "sim_err_sec")["mae_sec"],
                     "share_arrived_later_than_feed": round(float((g["feed_err_sec"] < -60).mean()), 3)})
    held = df[df["holding"] | df["stalled"]]
    days = int(pd.to_datetime(df["made_ts"], unit="s").dt.date.nunique())
    return {"n": int(len(df)), "days": days, "by_horizon": by_h, "overall": overall, "corroboration": corr,
            "held": {"n": int(len(held)), "feed": _score(held, "feed_err_sec"), "sim": _score(held, "sim_err_sec"),
                     "hold_persists": _score(held.assign(h=held["hold_eta_ts"] - held["arrival_ts"]), "h") if len(held) else None},
            "n_snapshots": int(df["made_ts"].nunique())}
