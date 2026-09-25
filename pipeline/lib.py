"""Shared helpers: data-branch layout, arrivals/alerts persistence, target resolution."""
from __future__ import annotations

import gzip
import json
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from mta_delay_insights import config
from mta_delay_insights.sources.gtfs_static import NY_TZ, StaticGTFS
from mta_delay_insights.storage.db import ARRIVAL_COLUMNS

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / "data-branch"


def load_targets(path: str | Path | None = None) -> dict:
    p = Path(path) if path else ROOT / "pipeline" / "targets.json"
    return json.loads(p.read_text())


def load_static(path: str | Path = "data/gtfs_subway.zip") -> StaticGTFS:
    p = Path(path)
    if p.exists():
        return StaticGTFS.load(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    return StaticGTFS.download("subway", p)


def resolve_target(static: StaticGTFS, t: dict) -> dict:
    """Platform stop id, routes, upstream stops and terminals for a target definition."""
    from mta_delay_insights.analysis.engine import AnalysisRequest, resolve_target as _rt
    req = AnalysisRequest(station=t["station"], direction=t["direction"], routes=list(t["routes"]))
    r = _rt(static, req)
    r["upstream"] = {route: static.upstream_stops(route, t["direction"], r["stop_id"], t.get("upstream_stops", 6))
                     for route in r["routes"]}
    r["terminals"] = {route: static.terminal_stop(route, t["direction"]) for route in r["routes"]}
    return r


def stops_and_feeds(static: StaticGTFS, targets: dict) -> tuple[set[str], list[str], list[dict]]:
    stops: set[str] = set()
    feeds: set[str] = set()
    resolved = []
    for t in targets["targets"]:
        r = resolve_target(static, {**t, "upstream_stops": targets.get("upstream_stops", 6)})
        stops.add(r["stop_id"])
        for ups in r["upstream"].values():
            stops |= set(ups)
        stops |= {s for s in r["terminals"].values() if s}
        for route in r["routes"]:
            try:
                feeds.add(config.feed_for_route(route))
            except KeyError:
                pass
        resolved.append({**t, **{k: r[k] for k in ("station_id", "station_name", "stop_id", "routes")}})
    # Journey legs: both endpoints and every intermediate stop, so ride times can be learned.
    try:
        from mta_delay_insights.realtime.journey import resolve_journeys
        for j in resolve_journeys(static, targets):
            for leg in j.legs:
                stops |= set(leg.stops) | {leg.from_stop, leg.to_stop}
                for route in leg.routes:
                    try:
                        feeds.add(config.feed_for_route(route))
                    except KeyError:
                        pass
    except Exception as exc:  # a bad journey definition must not stop collection
        import logging
        logging.getLogger(__name__).warning("journeys not resolved: %s", exc)
    return stops, sorted(feeds), resolved


# ---- arrivals -------------------------------------------------------------- #
def local_date(ts: float) -> str:
    return datetime.fromtimestamp(ts, NY_TZ).strftime("%Y-%m-%d")


def arrivals_dir(data_dir: Path) -> Path:
    d = Path(data_dir) / "arrivals"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_arrivals(data_dir: Path, df: pd.DataFrame) -> dict[str, int]:
    """Merge new arrivals into per-day gzipped CSVs (dedupe on trip_key + stop_id)."""
    if df.empty:
        return {}
    d = arrivals_dir(data_dir)
    df = df.reindex(columns=ARRIVAL_COLUMNS)
    df["_date"] = df["arrival_ts"].map(local_date)
    written = {}
    for date, g in df.groupby("_date"):
        f = d / f"{date}.csv.gz"
        g = g.drop(columns=["_date"])
        if f.exists():
            old = pd.read_csv(f, dtype={"start_date": str})
            g = pd.concat([old, g], ignore_index=True)
        g = g.sort_values(["arrival_ts", "confidence"]).drop_duplicates(["trip_key", "stop_id"], keep="last")
        g.to_csv(f, index=False, compression="gzip")
        written[date] = int(len(g))
    return written


def load_arrivals(data_dir: Path, since: str | None = None) -> pd.DataFrame:
    d = Path(data_dir) / "arrivals"
    frames = []
    if d.exists():
        for f in sorted(d.glob("*.csv.gz")):
            if since and f.stem.split(".")[0] < since:
                continue
            frames.append(pd.read_csv(f, dtype={"start_date": str}))
    if not frames:
        return pd.DataFrame(columns=ARRIVAL_COLUMNS)
    return pd.concat(frames, ignore_index=True)


# ---- alerts ---------------------------------------------------------------- #
def save_alerts(data_dir: Path, df: pd.DataFrame, seen_ts: float) -> int:
    if df.empty:
        return 0
    d = Path(data_dir) / "alerts"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{local_date(seen_ts)}.json.gz"
    existing = {}
    if f.exists():
        with gzip.open(f, "rt") as fh:
            for row in json.load(fh):
                existing[(row["alert_id"], row.get("active_start"))] = row
    for row in df.to_dict(orient="records"):
        row = {k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in row.items()}
        row["routes"] = list(row.get("routes") or [])
        row["stops"] = list(row.get("stops") or [])
        row["last_seen_ts"] = seen_ts
        existing[(row["alert_id"], row.get("active_start"))] = row
    with gzip.open(f, "wt") as fh:
        json.dump(list(existing.values()), fh)
    return len(existing)


def load_alerts(data_dir: Path, days: int | None = None) -> pd.DataFrame:
    d = Path(data_dir) / "alerts"
    rows = []
    if d.exists():
        files = sorted(d.glob("*.json.gz"))
        if days:
            files = files[-days:]
        for f in files:
            with gzip.open(f, "rt") as fh:
                rows.extend(json.load(fh))
    if not rows:
        return pd.DataFrame(columns=["alert_id", "alert_type", "planned", "cause_category", "created_at", "updated_at",
                                     "active_start", "active_end", "routes", "stops", "header", "description", "last_seen_ts"])
    df = pd.DataFrame(rows)
    df["planned"] = df["planned"].astype(bool)
    return df.drop_duplicates(["alert_id", "active_start"], keep="last").reset_index(drop=True)


# ---- context and run log --------------------------------------------------- #
def save_context(data_dir: Path, name: str, df: pd.DataFrame) -> None:
    d = Path(data_dir) / "context"
    d.mkdir(parents=True, exist_ok=True)
    df.to_csv(d / f"{name}.csv.gz", index=False, compression="gzip")


def load_context(data_dir: Path, name: str) -> pd.DataFrame | None:
    f = Path(data_dir) / "context" / f"{name}.csv.gz"
    if not f.exists():
        return None
    return pd.read_csv(f)


def append_run(data_dir: Path, record: dict) -> None:
    f = Path(data_dir) / "runs.json"
    runs = json.loads(f.read_text()) if f.exists() else []
    runs.append({"ts": time.time(), "iso": datetime.now(NY_TZ).isoformat(), **record})
    f.write_text(json.dumps(runs[-500:], indent=0))


def load_runs(data_dir: Path) -> list[dict]:
    f = Path(data_dir) / "runs.json"
    return json.loads(f.read_text()) if f.exists() else []


def load_events(data_dir: Path) -> pd.DataFrame | None:
    ev = load_context(data_dir, "events")
    if ev is None or ev.empty:
        return ev
    ev = ev.copy()
    ev["routes"] = ev["routes"].map(lambda v: json.loads(v) if isinstance(v, str) and v.startswith("[") else [])
    return ev
