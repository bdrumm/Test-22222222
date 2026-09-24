"""MTA service alerts (GTFS-RT JSON with the Mercury extension) and cause tagging.

The JSON alerts feed is preferred over protobuf because the Mercury extension
carries ``alert_type`` (e.g. "Delays", "Planned - Part Suspended"), creation and
update times, and a human readable active period. Those fields let the analysis
separate *planned* service changes from *unplanned* incidents, and map free-text
headers onto cause categories that line up with the MTA's own incident taxonomy.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

import pandas as pd
import requests

from .. import config

MERCURY_KEY = "transit_realtime.mercury_alert"

ALERT_COLUMNS = [
    "alert_id", "alert_type", "planned", "cause_category", "created_at", "updated_at",
    "active_start", "active_end", "routes", "stops", "header", "description",
]

# Ordered: first match wins, so more specific patterns go first.
CAUSE_PATTERNS: list[tuple[str, str]] = [
    ("person_on_track", r"person (on|struck|in) the (track|roadbed)|unauthorized person|someone (on|struck by) (the )?(track|train)|struck by a train"),
    ("police", r"\bnypd\b|police (activity|investigation)|criminal|assault|unruly|disruptive"),
    ("medical", r"\bems\b|medical|sick (customer|passenger)|injured"),
    ("fire_smoke", r"\bfire\b|smoke|\bfdny\b"),
    ("signal", r"signal(s|ling)? (problem|malfunction|failure|trouble|issue|work|maintenance)|signal(s)?\b"),
    ("switch", r"switch (problem|trouble|malfunction|failure)"),
    ("track", r"rail condition|track (condition|problem|fire|maintenance|work|replacement|inspection|defect)|broken rail|switch"),
    ("rolling_stock", r"mechanical (problem|issue)|door (problem|issue)|brakes?|disabled train|train with mechanical"),
    ("power", r"power (problem|loss|outage)|third rail|electrical|con ?ed(ison)?"),
    ("obstruction", r"debris|obstruction|object on the track|tree"),
    ("water_weather", r"flood|water condition|\bweather\b|\bsnow\b|\bice\b|\bheat\b|\bstorm\b|\bwind\b|hurricane|lightning"),
    ("crowding_dwell", r"crowd|overcrowd|customer volume|holding (the )?doors|door holding|heavy ridership"),
    ("crew", r"crew (availability|shortage)|operator availability|staffing"),
    ("reduced_service", r"runs every \d+ minutes|reduced service|fewer trains"),
    ("planned_work", r"planned work|scheduled maintenance|capital work|construction|track work|station work|maintenance"),
    ("investigation", r"investigation"),
]

PLANNED_TYPE_PREFIX = ("planned", "weekend service", "buses replace trains", "no midday service",
                       "no weekend service", "special schedule")
# Informational alert types that are not delay conditions and must not feed attribution.
NOTICE_TYPES = ("boarding change", "station notice", "extra service", "elevator", "escalator", "accessibility",
                "service reminder", "shuttle bus")


def alert_kind(alert_type: str | None, header: str = "") -> str:
    """'planned' (advance service change), 'notice' (informational), or 'delay' (unplanned condition)."""
    at = (alert_type or "").lower()
    if at.startswith(PLANNED_TYPE_PREFIX) or re.search(r"planned work|scheduled maintenance", (header or "").lower()):
        return "planned"
    if at.startswith(NOTICE_TYPES):
        return "notice"
    return "delay"


def classify_cause(text: str) -> str:
    t = (text or "").lower()
    for cat, pat in CAUSE_PATTERNS:
        if re.search(pat, t):
            return cat
    return "unknown"


def is_planned(alert_type: str | None, header: str = "") -> bool:
    return alert_kind(alert_type, header) == "planned"


def _text(field: dict | None, lang: str = "en") -> str:
    if not field:
        return ""
    trs = field.get("translation", [])
    for tr in trs:
        if tr.get("language") == lang:
            return tr.get("text", "")
    return trs[0].get("text", "") if trs else ""


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def fetch_alerts_json(feed_key: str = "subway_alerts_json", api_key: str | None = None,
                      timeout: int | None = None) -> dict:
    headers = {}
    key = api_key or config.api_key()
    if key:
        headers["x-api-key"] = key
    resp = requests.get(config.rt_feed_url(feed_key), headers=headers,
                        timeout=timeout or config.DEFAULTS.request_timeout_sec)
    resp.raise_for_status()
    return resp.json()


def alerts_frame(feed_json: dict | str | bytes) -> pd.DataFrame:
    """Normalise a GTFS-RT JSON alerts document. One row per alert x active period.

    ``active_end`` is ``None`` for open-ended alerts; the analysis treats those as
    active until ``updated_at`` + a grace period or until they disappear from the feed.
    """
    if isinstance(feed_json, (str, bytes)):
        feed_json = json.loads(feed_json)
    rows = []
    for ent in feed_json.get("entity", []):
        a = ent.get("alert")
        if not a:
            continue
        merc = a.get(MERCURY_KEY, {}) or {}
        header = _text(a.get("header_text"))
        desc = _text(a.get("description_text"))
        alert_type = merc.get("alert_type")
        routes = sorted({ie.get("route_id") for ie in a.get("informed_entity", []) if ie.get("route_id")})
        stops = sorted({ie.get("stop_id") for ie in a.get("informed_entity", []) if ie.get("stop_id")})
        periods = a.get("active_period") or [{}]
        for p in periods:
            rows.append({
                "alert_id": ent.get("id"),
                "alert_type": alert_type,
                "planned": is_planned(alert_type, header),
                "cause_category": classify_cause(header + " " + desc),
                "created_at": _f(merc.get("created_at")),
                "updated_at": _f(merc.get("updated_at")),
                "active_start": _f(p.get("start")),
                "active_end": _f(p.get("end")),
                "routes": routes,
                "stops": stops,
                "header": header,
                "description": desc,
            })
    return pd.DataFrame(rows, columns=ALERT_COLUMNS)


def alerts_active_at(alerts: pd.DataFrame, ts: float, route_id: str | None = None,
                     stop_ids: list[str] | None = None, open_end_grace_sec: float = 3 * 3600) -> pd.DataFrame:
    """Alerts whose active period covers ``ts`` and that mention the route or stops."""
    if alerts.empty:
        return alerts
    start_ok = alerts["active_start"].isna() | (alerts["active_start"] <= ts)
    end = alerts["active_end"].fillna(alerts["updated_at"].fillna(alerts["active_start"]) + open_end_grace_sec)
    end_ok = end.isna() | (end >= ts)
    m = start_ok & end_ok
    if route_id is not None:
        m &= alerts["routes"].map(lambda rs: route_id in rs or len(rs) == 0)
    if stop_ids:
        wanted = set(stop_ids)
        m &= alerts["stops"].map(lambda ss: len(ss) == 0 or bool(wanted & set(ss)))
    return alerts[m]


@dataclass
class AlertWindow:
    alert_id: str
    start: float
    end: float
    cause_category: str
    planned: bool
    routes: list[str]
    header: str
