"""Train and evaluate the arrival model on the pooled history with the full context.

    python -m pipeline.train_model --data-dir data-branch --db data/mta.sqlite --out data/models --test-from 2026-09-28

Steps: assemble each day's arrivals (the subwaydata archive when it has the day, otherwise our collector),
build the training rows day by day with the shared context (hourly weather, alerts, events, NWS, climatology),
cache them, run the feature-group ablation (p50 only), fit the final three-quantile model on everything,
score it on the held-out span (archive rows and, separately, collector rows that look like serving),
check the configured commutes' legs (destination arrival), and write ``experiments.json`` + the model card.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from mta_delay_insights.models.arrival import ArrivalModel, evaluate, fit_carry_baseline, train_arrival_model
from mta_delay_insights.models.context import FeatureContext
from mta_delay_insights.models.features import FEATURES, GROUPS, K_SET, LEGACY_FEATURES, build_training_rows
from mta_delay_insights.realtime.journey import resolve_journeys
from mta_delay_insights.sources import alerts_archive as aa
from mta_delay_insights.sources import weather
from mta_delay_insights.sources.gtfs_static import NY_TZ, StaticGTFS
from mta_delay_insights.storage.db import Store

from . import lib

log = logging.getLogger("train_model")
FLOAT_COLS = [c for c in FEATURES if c not in ("route_code", "direction_code", "cause_code")]
VARIANTS = [
    ("legacy", "the first deployed feature set (state + cyclic hour, daily weather, alert flag)", LEGACY_FEATURES),
    ("state", "train / traffic / segment state only", GROUPS["state"]),
    ("state+time", "+ hour, weekday, day type, time band, holiday", GROUPS["state"] + GROUPS["time"]),
    ("state+time+patterns", "+ day-pattern profiles, line and network state, climatology", GROUPS["state"] + GROUPS["time"] + GROUPS["patterns"]),
    ("state+time+patterns+weather", "+ hourly weather, NWS", GROUPS["state"] + GROUPS["time"] + GROUPS["patterns"] + GROUPS["weather"]),
    ("all", "+ alerts (count, cause, age, network-wide), events", FEATURES),
]


# ----------------------------------------------------------------------------- loading

def ensure_weather_hourly(data_dir: Path, start: date, end: date) -> pd.DataFrame:
    """Hourly weather covering [start, end]; fetched from Open-Meteo (archive + recent) and cached in context/."""
    f = data_dir / "context" / "weather_hourly.csv.gz"
    df = pd.read_csv(f) if f.exists() else pd.DataFrame(columns=weather.WEATHER_COLUMNS)
    have = pd.to_datetime(df["ts"]) if len(df) else pd.Series(dtype="datetime64[ns]")
    need_from = start - timedelta(days=1)
    stale = len(df) == 0 or have.min().date() > need_from or have.max() < pd.Timestamp(end) + pd.Timedelta(hours=20)
    if stale:
        try:
            frames = [df]
            arch_end = min(end, date.today() - timedelta(days=6))
            if arch_end >= need_from:
                frames.append(weather.fetch_hourly(need_from.isoformat(), arch_end.isoformat()))
            frames.append(weather.fetch_recent_hourly(min(92, (date.today() - need_from).days + 1)))
            df = pd.concat([x for x in frames if x is not None and len(x)], ignore_index=True)
            df["ts"] = pd.to_datetime(df["ts"])
            df = df.drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)
            f.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(f, index=False, compression="gzip")
            log.info("weather hourly refreshed: %d hours %s..%s", len(df), df["ts"].min(), df["ts"].max())
        except Exception as exc:
            log.warning("weather fetch failed (%s); using cached %d hours", exc, len(df))
    return df


def load_frames(data_dir: Path, db: Path | None, start: date, end: date) -> dict:
    ctx: dict = {}
    ctx["weather_hourly"] = ensure_weather_hourly(data_dir, start, end)
    wd = lib.load_context(data_dir, "weather_daily")
    if ctx["weather_hourly"] is not None and len(ctx["weather_hourly"]):
        derived = weather.daily_summary(weather.add_flags(ctx["weather_hourly"].assign(ts=pd.to_datetime(ctx["weather_hourly"]["ts"]))))
        derived["date"] = derived["date"].astype(str)
        wd = pd.concat([wd, derived], ignore_index=True).drop_duplicates("date", keep="last") if wd is not None and len(wd) else derived
    ctx["weather_daily"] = wd
    ctx["events"] = lib.load_events(data_dir)
    ctx["nws_alerts"] = lib.load_context(data_dir, "nws_alerts")
    live = lib.load_alerts(data_dir)
    store = Store(db) if db and Path(db).exists() else None
    if store is not None:
        sa = store.alerts()
        if sa is not None and len(sa):
            live = pd.concat([live, sa], ignore_index=True).drop_duplicates(["alert_id", "active_start"], keep="last")
    ctx["alerts"] = live
    seen = pd.to_numeric(live.get("last_seen_ts"), errors="coerce") if len(live) else pd.Series(dtype=float)
    ctx["alerts_coverage"] = (float(seen.min()) - 600, float(seen.max())) if seen.notna().any() else None
    arch = lib.load_alerts_archive(data_dir)
    ctx["alerts_archive"] = arch
    try:
        ctx["climatology"] = aa.climatology(aa.events(arch)) if arch is not None and len(arch) else None
    except Exception as exc:
        log.warning("climatology failed: %s", exc); ctx["climatology"] = None
    es = lib.load_eta_samples(data_dir)
    if store is not None:
        se = store.eta_samples(None, None, None)
        if se is not None and len(se):
            es = pd.concat([es, se], ignore_index=True)
    ctx["eta_samples"] = es.drop_duplicates(["trip_key", "stop_id", "at_stop"], keep="first") if es is not None and len(es) else None
    ctx["_store"] = store
    return ctx


def feature_context(ctx: dict) -> FeatureContext:
    fc = FeatureContext.build(alerts_df=ctx["alerts"], weather_daily=ctx["weather_daily"], events_df=ctx["events"], nws_df=ctx["nws_alerts"],
                              climatology=ctx["climatology"], weather_hourly=ctx["weather_hourly"], alerts_archive=None)
    if ctx.get("alerts_coverage"):
        fc.alerts.coverage = ctx["alerts_coverage"]
    return fc


def _day_bounds(day: date) -> tuple[float, float]:
    start = datetime(day.year, day.month, day.day, tzinfo=NY_TZ).timestamp()
    return start, start + 86400.0


def day_arrivals(data_dir: Path, store: Store | None, day: date, prefer_archive: bool = True) -> tuple[pd.DataFrame, str]:
    """The day's arrivals from the archive when present (one convention per day), else the collector files + store."""
    f = data_dir / "arrivals_all" / f"{day.isoformat()}.csv.gz"
    df = pd.read_csv(f, low_memory=False, dtype={"start_date": str, "sched_track": str, "actual_track": str}) if f.exists() else pd.DataFrame()
    source = "none"
    if len(df) and prefer_archive and (df["source"] == "subwaydata").sum() > 50_000:
        df = df[df["source"] == "subwaydata"]
        source = "archive"
    else:
        if len(df):
            df = df[df["source"] != "subwaydata"]
            source = "collector"
        if store is not None:
            lo, hi = _day_bounds(day)
            sa = store.arrivals(None, lo, hi)
            if sa is not None and len(sa):
                df = pd.concat([df, sa], ignore_index=True) if len(df) else sa
                source = "collector"
    if len(df):
        df = df.drop_duplicates(["trip_key", "stop_id"], keep="first")
    return df, source


def collector_arrivals(data_dir: Path, store: Store | None, day: date) -> pd.DataFrame:
    df, _ = day_arrivals(data_dir, store, day, prefer_archive=False)
    return df


def build_rows_for_days(days: list[date], loader, static: StaticGTFS, fc: FeatureContext, eta_samples, k_set, sample: float, seed: int = 1,
                        min_arrivals: int = 5000) -> tuple[pd.DataFrame, list[dict]]:
    """Rows for each day, built with two hours of context either side so the stream features see the neighbours."""
    frames, manifest = [], []
    cache: dict[date, pd.DataFrame] = {}

    def get(d: date) -> pd.DataFrame:
        if d not in cache:
            cache[d] = loader(d)
        return cache[d]
    for d in days:
        t0 = time.time()
        cur = get(d)
        if cur is None or len(cur) < min_arrivals:
            manifest.append({"day": d.isoformat(), "arrivals": 0 if cur is None else int(len(cur)), "rows": 0, "skipped": True})
            continue
        lo, hi = _day_bounds(d)
        prev = get(d - timedelta(days=1)); nxt = get(d + timedelta(days=1))
        parts = [cur]
        if prev is not None and len(prev):
            parts.insert(0, prev[prev["arrival_ts"] >= lo - 7200])
        if nxt is not None and len(nxt):
            parts.append(nxt[nxt["arrival_ts"] < hi + 7200])
        arr = pd.concat(parts, ignore_index=True).drop_duplicates(["trip_key", "stop_id"], keep="first")
        rows = build_training_rows(arr, static, eta_samples=eta_samples, k_set=k_set, ctx=fc)
        rows = rows[(rows["t"] >= lo) & (rows["t"] < hi)]
        n_full = len(rows)
        if sample < 1.0 and len(rows):
            h = pd.util.hash_pandas_object(rows["trip_key"], index=False).values % 10_000
            rows = rows[h < int(sample * 10_000)]
        for c in FLOAT_COLS:
            rows[c] = rows[c].astype(np.float32)
        rows["day"] = d.isoformat()
        frames.append(rows.reset_index(drop=True))
        manifest.append({"day": d.isoformat(), "arrivals": int(len(cur)), "rows_full": int(n_full), "rows": int(len(rows)), "seconds": round(time.time() - t0, 1),
                         "source": str(cur["source"].iloc[0]) if "source" in cur and len(cur) else None})
        log.info("rows %s: %d arrivals -> %d rows (kept %d) in %.0fs", d, len(cur), n_full, len(rows), time.time() - t0)
        for k in [x for x in cache if x < d - timedelta(days=1)]:
            cache.pop(k, None)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["trip_key"] + FEATURES)
    return out, manifest


# ----------------------------------------------------------------------------- experiments

def run_ablation(rows: pd.DataFrame, split_ts: float, out: Path, train_cap: int, max_iter: int, seed: int) -> list[dict]:
    results = []
    for name, desc, feats in VARIANTS:
        t0 = time.time()
        m = train_arrival_model(rows, split_ts=split_ts, features=feats, quantiles=(0.5,), max_iter=max_iter, seed=seed,
                                importance=False, train_cap=train_cap)
        ev = m.card.get("evaluation", {})
        res = {"variant": name, "description": desc, "n_features": len(m.features), "n_train": m.card.get("n_train"), "n_test": m.card.get("n_test"),
               "iterations": m.card.get("iterations"), "fit_seconds": m.card.get("fit_seconds"), "seconds": round(time.time() - t0, 1),
               "mae_model": ev.get("mae_model"), "bias_model": ev.get("bias_model"), "mae_schedule": ev.get("mae_schedule"),
               "mae_persistence": ev.get("mae_persistence"), "mae_carry": ev.get("mae_carry"),
               "by_k": [{"k": b["k"], "n": b["n"], "mae_model": b["mae_model"], "mae_schedule": b["mae_schedule"], "mae_carry": b.get("mae_carry")} for b in ev.get("by_k", [])],
               "by_band": [{"band": b["band"], "n": b["n"], "mae_model": b["mae_model"], "mae_schedule": b["mae_schedule"], "mae_carry": b.get("mae_carry")} for b in ev.get("by_band", [])],
               "by_weather": [{"weather": b["weather"], "n": b["n"], "mae_model": b["mae_model"], "mae_schedule": b["mae_schedule"], "mae_carry": b.get("mae_carry")} for b in ev.get("by_weather", [])],
               "by_daytype": [{"daytype": b["daytype"], "n": b["n"], "mae_model": b["mae_model"], "mae_schedule": b["mae_schedule"], "mae_carry": b.get("mae_carry")} for b in ev.get("by_daytype", [])],
               "by_alert": [{"alert": b["alert"], "n": b["n"], "mae_model": b["mae_model"], "mae_schedule": b["mae_schedule"], "mae_carry": b.get("mae_carry")} for b in ev.get("by_alert", [])],
               "dropped": m.card.get("dropped_features")}
        results.append(res)
        log.info("ablation %-32s mae=%.2f (schedule %.2f, carry %s) in %.0fs", name, res["mae_model"], res["mae_schedule"],
                 f"{res['mae_carry']:.2f}" if res.get("mae_carry") else "-", res["seconds"])
        (out / "experiments.json").write_text(json.dumps({"ablation": results}, default=str, indent=1))
    return results


def rider_legs(path: str | Path | None) -> list[dict]:
    """The legs the rider actually rides (data/trips/legs.json from pipeline.trip_review), when the file is there."""
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    try:
        legs = json.loads(p.read_text())
    except Exception as exc:
        log.warning("rider legs unreadable (%s): %s", p, exc)
        return []
    out = []
    for e in legs:
        if all(k in e for k in ("from", "to", "routes", "k")) and int(e["k"]) >= 1:
            out.append({"from": e["from"], "to": e["to"], "from_name": e.get("from_name", e["from"]), "to_name": e.get("to_name", e["to"]),
                        "routes": list(e["routes"]), "k": int(e["k"]), "journey": "rider"})
    return out


def leg_specs(static: StaticGTFS, targets: dict, extra: list[dict] | None = None) -> list[dict]:
    """Every distinct (from, to, routes, k) among the configured journeys' legs, and the rider's own legs."""
    out, seen = [], set()
    for e in extra or []:
        key = (e["from"], e["to"])
        if key not in seen:
            seen.add(key)
            out.append(dict(e))
    try:
        specs = resolve_journeys(static, targets)
    except Exception as exc:
        log.warning("journeys unavailable: %s", exc)
        return out
    for js in specs:
        for lg in js.legs:
            k = len(lg.stops) - 1
            key = (lg.from_stop, lg.to_stop)
            if k < 1 or key in seen:
                continue
            seen.add(key)
            out.append({"from": lg.from_stop, "to": lg.to_stop, "from_name": lg.from_name, "to_name": lg.to_name, "routes": list(lg.routes), "k": k, "journey": js.id})
    return out


def evaluate_legs(model: ArrivalModel, rows: pd.DataFrame, legs: list[dict], carry: dict) -> list[dict]:
    """Destination arrival on the configured commute legs: predicted vs actual excess from boarding to alighting."""
    out = []
    for lg in legs:
        g = rows[(rows["u"] == lg["from"]) & (rows["d"] == lg["to"])]
        if len(g) < 20:
            out.append({**lg, "n": int(len(g)), "note": "too few held-out rides"})
            continue
        ev = evaluate(model, g, carry)
        pred = model.predict(g)
        out.append({**lg, "n": int(len(g)), "mae_model_sec": ev["mae_model"], "mae_schedule_sec": ev["mae_schedule"], "mae_carry_sec": ev.get("mae_carry"),
                    "bias_model_sec": ev["bias_model"], "coverage_p10_p90": ev.get("coverage_p10_p90"), "range_width_median_sec": ev.get("range_width_median_sec"),
                    "actual_excess_median_sec": float(g["delta_sec"].median()), "actual_excess_p90_sec": float(g["delta_sec"].quantile(0.9)),
                    "model_p50_median_sec": float(pred["p50"].median()),
                    "by_band": [{"band": b["band"], "n": b["n"], "mae_model": b["mae_model"], "mae_schedule": b["mae_schedule"], "mae_carry": b.get("mae_carry")} for b in ev.get("by_band", [])]})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(lib.DEFAULT_DATA_DIR))
    ap.add_argument("--db", default="data/mta.sqlite")
    ap.add_argument("--gtfs", default="data/gtfs_subway.zip")
    ap.add_argument("--targets", default=None)
    ap.add_argument("--legs", default="data/trips/legs.json", help="the rider's own legs from pipeline.trip_review (evaluated besides the journeys' legs)")
    ap.add_argument("--out", default="data/models")
    ap.add_argument("--cache", default=None, help="pickle of the built rows (reused when present)")
    ap.add_argument("--start", default=None, help="first day (default: first archive day)")
    ap.add_argument("--end", default=None, help="last day (default: yesterday)")
    ap.add_argument("--test-from", default=None, help="first held-out day (default: last 30%% of the span)")
    ap.add_argument("--sample", type=float, default=0.3, help="share of trips kept per day (stream features use every arrival)")
    ap.add_argument("--train-cap", type=int, default=2_000_000, help="training rows per ablation variant")
    ap.add_argument("--final-cap", type=int, default=6_000_000)
    ap.add_argument("--max-iter", type=int, default=800)
    ap.add_argument("--learning-rate", type=float, default=0.1)
    ap.add_argument("--max-leaf-nodes", type=int, default=127)
    ap.add_argument("--min-samples-leaf", type=int, default=100)
    ap.add_argument("--skip-ablation", action="store_true")
    ap.add_argument("--skip-final", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    data_dir = Path(args.data_dir); out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    static = lib.load_static(args.gtfs)
    targets = lib.load_targets(args.targets)
    files = sorted((data_dir / "arrivals_all").glob("20*.csv.gz"))
    first = date.fromisoformat(args.start) if args.start else date.fromisoformat(files[0].stem[:10])
    last = date.fromisoformat(args.end) if args.end else min(date.fromisoformat(files[-1].stem[:10]), date.today())
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    test_from = date.fromisoformat(args.test_from) if args.test_from else days[int(len(days) * 0.7)]
    split_ts = _day_bounds(test_from)[0]
    log.info("days %s..%s, held out from %s", first, last, test_from)
    ctx = load_frames(data_dir, Path(args.db) if args.db else None, first, last)
    fc = feature_context(ctx)
    legs = leg_specs(static, targets, rider_legs(args.legs))
    k_set = tuple(sorted(set(K_SET) | {lg["k"] for lg in legs}))
    log.info("k set %s (legs: %s)", k_set, [(lg["from_name"], lg["to_name"], lg["k"]) for lg in legs])
    cache = Path(args.cache) if args.cache else None
    if cache and cache.exists():
        rows = pd.read_pickle(cache)
        manifest = json.loads(cache.with_suffix(".manifest.json").read_text()) if cache.with_suffix(".manifest.json").exists() else []
        log.info("rows from cache: %d", len(rows))
    else:
        rows, manifest = build_rows_for_days(days, lambda d: day_arrivals(data_dir, ctx["_store"], d)[0], static, fc, ctx["eta_samples"], k_set, args.sample, args.seed)
        if cache:
            rows.to_pickle(cache)
            cache.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=1))
    log.info("rows total %d (%d train, %d test)", len(rows), int((rows["t"] < split_ts).sum()), int((rows["t"] >= split_ts).sum()))
    summary = {"days": manifest, "k_set": list(k_set), "test_from": test_from.isoformat(), "n_rows": int(len(rows)), "sample": args.sample,
               "n_train": int((rows["t"] < split_ts).sum()), "n_test": int((rows["t"] >= split_ts).sum()),
               "context": {"weather_hours": int(len(ctx["weather_hourly"])) if ctx["weather_hourly"] is not None else 0,
                           "alerts": int(len(ctx["alerts"])) if ctx["alerts"] is not None else 0, "alerts_coverage": ctx.get("alerts_coverage"),
                           "events": int(len(ctx["events"])) if ctx["events"] is not None else 0,
                           "nws": int(len(ctx["nws_alerts"])) if ctx["nws_alerts"] is not None else 0,
                           "eta_samples": int(len(ctx["eta_samples"])) if ctx["eta_samples"] is not None else 0,
                           "climatology_events": (ctx["climatology"] or {}).get("n_events", 0)}}
    exp_path = out / "experiments.json"
    existing = json.loads(exp_path.read_text()) if exp_path.exists() else {}
    existing["data"] = summary
    exp_path.write_text(json.dumps(existing, default=str, indent=1))
    if not args.skip_ablation:
        existing["ablation"] = run_ablation(rows, split_ts, out, args.train_cap, args.max_iter, args.seed)
        existing["data"] = summary
        exp_path.write_text(json.dumps(existing, default=str, indent=1))
    if args.skip_final:
        return 0
    t0 = time.time()
    model = train_arrival_model(rows, split_ts=split_ts, max_iter=args.max_iter, seed=args.seed, train_cap=args.final_cap,
                                learning_rate=args.learning_rate, max_leaf_nodes=args.max_leaf_nodes, min_samples_leaf=args.min_samples_leaf)
    log.info("final model: mae %.2f (schedule %.2f) coverage %.3f in %.0fs", model.card["evaluation"]["mae_model"], model.card["evaluation"]["mae_schedule"],
             model.card["evaluation"].get("coverage_p10_p90", float("nan")), time.time() - t0)
    train_rows = rows[rows["t"] < split_ts]
    carry = fit_carry_baseline(train_rows.sample(min(len(train_rows), 2_000_000), random_state=1))
    test_rows = rows[rows["t"] >= split_ts]
    model.card["legs"] = evaluate_legs(model, test_rows, legs, carry)
    # collector rows for the held-out days: the data the live server actually has
    coll_days = [d for d in days if d >= test_from]
    crow, cman = build_rows_for_days(coll_days, lambda d: collector_arrivals(data_dir, ctx["_store"], d), static, fc, ctx["eta_samples"], k_set, 1.0, args.seed, min_arrivals=3000)
    if len(crow):
        model.card["evaluation_collector"] = evaluate(model, crow, carry)
        model.card["evaluation_collector"]["days"] = cman
        model.card["legs_collector"] = evaluate_legs(model, crow, legs, carry)
        log.info("collector rows %d: mae %.2f (schedule %.2f, feed %s)", len(crow), model.card["evaluation_collector"]["mae_model"],
                 model.card["evaluation_collector"]["mae_schedule"], model.card["evaluation_collector"].get("mae_feed"))
    model.card["data"] = summary
    model.save(out / "arrival.joblib")
    existing["final"] = {k: v for k, v in model.card.items() if k not in ("routes_seen",)}
    exp_path.write_text(json.dumps(existing, default=str, indent=1))
    log.info("saved %s", out / "arrival.joblib")
    return 0


if __name__ == "__main__":
    sys.exit(main())
