"""goodservice.io: a community-run status engine built on the same GTFS-realtime feeds.

Its public JSON (``/api/routes``, ``/api/routes/<id>``) gives, per route and direction, a status word
(Good Service / Slow / Not Good / Delay / Service Change ...), delay and irregularity summaries ("longer
wait times between X and Y, up to 24 mins, normally every 10"), slow and long-headway sections, and the
actual routing against the scheduled one (reroutes). It publishes no history, so the only way to learn
whether it adds anything to our own engine is to log it alongside the feeds and score it later; the live
server samples it every few minutes into the store (``ctx_goodservice``).
"""
from __future__ import annotations

import time

import pandas as pd
import requests

URL = "https://goodservice.io/api/routes"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; mta-delay-insights/0.1)", "Accept": "application/json"}
COLUMNS = ["ts", "route", "status", "status_north", "status_south", "delay_north", "delay_south", "irregularity_north", "irregularity_south",
           "n_slow_sections", "n_long_headway_sections", "n_service_changes", "max_delay_sec", "rerouted"]


def fetch_routes(timeout: int = 30, session: requests.Session | None = None, details_for_degraded: int = 12) -> dict:
    """The routes summary; routes not in Good Service also get their detail (sections, routings), a few per sample."""
    s = session or requests
    resp = s.get(URL, params={"detailed": 1}, headers=HEADERS, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()
    payload = resp.json()
    routes = (payload or {}).get("routes") or {}
    degraded = [rid for rid, r in routes.items() if isinstance(r, dict) and r.get("status") not in (None, "Good Service", "No Service")][:details_for_degraded]
    for rid in degraded:
        try:
            d = s.get(f"{URL}/{rid}", headers=HEADERS, timeout=timeout, allow_redirects=True)
            d.raise_for_status()
            detail = d.json()
            if isinstance(detail, dict):
                routes[rid] = {**routes[rid], **detail}
        except Exception:
            continue
    return payload


def normalize(payload: dict, ts: float | None = None) -> pd.DataFrame:
    """One row per route from the routes payload (summary fields; detail endpoints add sections)."""
    ts = ts or time.time()
    routes = (payload or {}).get("routes") or {}
    rows = []
    for rid, r in routes.items():
        if not isinstance(r, dict):
            continue
        ds = r.get("direction_statuses") or {}
        dl = r.get("delay_summaries") or {}
        ir = r.get("service_irregularity_summaries") or {}
        sc = r.get("service_change_summaries") or {}
        slow = r.get("slow_sections") or {}
        lh = r.get("long_headway_sections") or {}
        actual = r.get("actual_routings") or {}
        sched = r.get("scheduled_routings") or {}
        rerouted = any(actual.get(d) and sched.get(d) and actual.get(d) != sched.get(d) for d in ("north", "south"))
        rows.append({"ts": ts, "route": str(r.get("id") or rid), "status": r.get("status"), "status_north": ds.get("north"), "status_south": ds.get("south"),
                     "delay_north": dl.get("north"), "delay_south": dl.get("south"), "irregularity_north": ir.get("north"), "irregularity_south": ir.get("south"),
                     "n_slow_sections": sum(len(v or []) for v in slow.values()) if isinstance(slow, dict) else 0,
                     "n_long_headway_sections": sum(len(v or []) for v in lh.values()) if isinstance(lh, dict) else 0,
                     "n_service_changes": sum(len(v or []) for v in sc.values()) if isinstance(sc, dict) else 0,
                     "max_delay_sec": r.get("max_delay"), "rerouted": bool(rerouted)})
    return pd.DataFrame(rows, columns=COLUMNS)


def route_flags(df: pd.DataFrame) -> dict[str, dict]:
    """route -> {'bad': bool, 'slow': bool, 'irregular': bool, 'text': str} for the latest sample of each route."""
    out: dict[str, dict] = {}
    if df is None or df.empty:
        return out
    latest = df.sort_values("ts").groupby("route").tail(1)

    def txt(v) -> str:
        return "" if v is None or (isinstance(v, float) and v != v) else str(v)

    def num(v) -> float:
        try:
            return 0.0 if v is None or (isinstance(v, float) and v != v) else float(v)
        except (TypeError, ValueError):
            return 0.0
    for r in latest.itertuples(index=False):
        status = txt(r.status)
        text = next((t for t in (txt(r.delay_north), txt(r.delay_south), txt(r.irregularity_north), txt(r.irregularity_south)) if t), "")
        out[str(r.route)] = {"bad": status in ("Not Good", "Delay", "Delays", "Service Change") or bool(r.rerouted), "slow": status == "Slow" or num(r.n_slow_sections) > 0,
                             "irregular": num(r.n_long_headway_sections) > 0 or bool(txt(r.irregularity_north)) or bool(txt(r.irregularity_south)),
                             "status": status, "text": text[:200]}
    return out
