"""External context signals for the journey model: events, news, holidays.

Each fetcher is best-effort and returns a DataFrame with a common shape so the
pipeline can persist it and the model can turn it into features:

    ts_start, ts_end (epoch seconds), kind, title, source, borough, lat, lon, routes (list), weight

* **NYC permitted events** (NYC Open Data, Socrata ``tvpn-ykxb``): street fairs,
  parades, races, festivals with start/end times and boroughs. Large street
  events raise ridership and hold buses; parades close streets near stations.
* **Ticketmaster Discovery** (optional, ``TICKETMASTER_API_KEY``): venue events
  (Barclays Center, Madison Square Garden, Yankee Stadium, Citi Field, ...)
  mapped to the routes serving the venue.
* **News RSS** (MTA / local transit press): items mentioning a route or station
  become ``news`` events for the day they were published.
* **Holidays**: US federal + NYC school-year markers as calendar features.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

import pandas as pd
import requests

from .. import config
from .gtfs_static import NY_TZ

EVENT_COLUMNS = ["ts_start", "ts_end", "kind", "title", "source", "borough", "lat", "lon", "routes", "weight"]

NYC_EVENTS_DATASET = "tvpn-ykxb"            # NYC Permitted Event Information (upcoming)
NYC_EVENTS_DOMAIN = "data.cityofnewyork.us"

# Major venues -> (lat, lon, routes that serve them, default crowd weight)
VENUES = {
    "Barclays Center": (40.6826, -73.9754, ["2", "3", "4", "5", "B", "D", "N", "Q", "R", "W", "G"], 1.0),
    "Madison Square Garden": (40.7505, -73.9934, ["1", "2", "3", "A", "C", "E"], 1.0),
    "Yankee Stadium": (40.8296, -73.9262, ["4", "B", "D"], 1.0),
    "Citi Field": (40.7571, -73.8458, ["7"], 1.0),
    "Arthur Ashe Stadium": (40.7500, -73.8458, ["7"], 0.8),
    "Radio City Music Hall": (40.7600, -73.9800, ["B", "D", "F", "M"], 0.4),
    "Javits Center": (40.7577, -74.0025, ["7"], 0.5),
    "USTA Billie Jean King National Tennis Center": (40.7500, -73.8458, ["7"], 0.8),
}

DEFAULT_NEWS_FEEDS = [
    "https://gothamist.com/feed",
    "https://ny1.com/nyc/all-boroughs/rss.xml",
]

ROUTE_PATTERN = re.compile(r"\b([1-7]|[ACEBDFMGJZLNQRW])\s*(?:train|line)s?\b", re.IGNORECASE)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ts(s) -> float | None:
    try:
        t = pd.to_datetime(s)
        if t.tzinfo is None:
            t = t.tz_localize(NY_TZ)
        return float(t.timestamp())
    except Exception:
        return None


# --------------------------------------------------------------------------- #
def fetch_nyc_permitted_events(days_ahead: int = 14, days_back: int = 30, timeout: int | None = None) -> pd.DataFrame:
    """Upcoming and recent permitted events (street activity, parades, races, festivals)."""
    start = (date.today() - timedelta(days=days_back)).isoformat()
    end = (date.today() + timedelta(days=days_ahead)).isoformat()
    params = {"$limit": 5000, "$where": f"start_date_time >= '{start}T00:00:00' AND start_date_time <= '{end}T23:59:59'",
              "$order": "start_date_time"}
    headers = {"X-App-Token": config.socrata_app_token()} if config.socrata_app_token() else {}
    resp = requests.get(f"https://{NYC_EVENTS_DOMAIN}/resource/{NYC_EVENTS_DATASET}.json", params=params, headers=headers,
                        timeout=timeout or config.DEFAULTS.request_timeout_sec)
    resp.raise_for_status()
    return normalize_nyc_events(pd.DataFrame(resp.json()))


def normalize_nyc_events(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    d = df.copy()
    d.columns = [c.lower() for c in d.columns]
    out = pd.DataFrame({
        "ts_start": d.get("start_date_time", pd.Series(dtype=object)).map(_ts),
        "ts_end": d.get("end_date_time", pd.Series(dtype=object)).map(_ts),
        "kind": d.get("event_type", pd.Series(dtype=object)).astype(str).str.lower().str.replace(" ", "_"),
        "title": d.get("event_name", pd.Series(dtype=object)).astype(str).str.slice(0, 120),
        "source": "nyc_permitted_events",
        "borough": d.get("event_borough", pd.Series(dtype=object)).astype(str),
        "lat": None, "lon": None, "routes": [[] for _ in range(len(d))],
    })
    kind = out["kind"].fillna("")
    out["weight"] = 0.3
    out.loc[kind.str.contains("parade|marathon|race|street_festival|fair|block_party|festival", regex=True), "weight"] = 0.8
    out.loc[kind.str.contains("closure|construction", regex=True), "weight"] = 0.2
    return out.dropna(subset=["ts_start"])[EVENT_COLUMNS].reset_index(drop=True)


def fetch_ticketmaster_events(days_ahead: int = 14, api_key: str | None = None, timeout: int | None = None) -> pd.DataFrame:
    """Venue events near the subway from the Ticketmaster Discovery API (needs TICKETMASTER_API_KEY)."""
    key = api_key or os.environ.get("TICKETMASTER_API_KEY")
    if not key:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    start = datetime.now(NY_TZ).strftime("%Y-%m-%dT00:00:00Z")
    end = (datetime.now(NY_TZ) + timedelta(days=days_ahead)).strftime("%Y-%m-%dT23:59:59Z")
    params = {"apikey": key, "latlong": "40.75,-73.95", "radius": "15", "unit": "miles", "size": 200,
              "startDateTime": start, "endDateTime": end, "sort": "date,asc"}
    resp = requests.get("https://app.ticketmaster.com/discovery/v2/events.json", params=params,
                        timeout=timeout or config.DEFAULTS.request_timeout_sec)
    resp.raise_for_status()
    rows = []
    for ev in resp.json().get("_embedded", {}).get("events", []):
        venue = (ev.get("_embedded", {}).get("venues") or [{}])[0]
        vname = venue.get("name", "")
        loc = venue.get("location", {})
        lat, lon = _f(loc.get("latitude")), _f(loc.get("longitude"))
        routes, weight = [], 0.5
        for name, (vlat, vlon, vroutes, vweight) in VENUES.items():
            if name.lower() in vname.lower() or (lat and lon and abs(lat - vlat) < 0.004 and abs(lon - vlon) < 0.004):
                routes, weight = vroutes, vweight
                break
        start_ts = _ts(ev.get("dates", {}).get("start", {}).get("dateTime"))
        if start_ts is None:
            continue
        rows.append({"ts_start": start_ts, "ts_end": start_ts + 3 * 3600, "kind": "venue_event", "title": ev.get("name", "")[:120],
                     "source": "ticketmaster", "borough": venue.get("city", {}).get("name"), "lat": lat, "lon": lon,
                     "routes": routes, "weight": weight})
    return pd.DataFrame(rows, columns=EVENT_COLUMNS)


def fetch_news(feeds: list[str] | None = None, timeout: int | None = None) -> pd.DataFrame:
    """RSS items that mention subway routes; each becomes a 'news' event for its publication day."""
    rows = []
    for url in feeds or DEFAULT_NEWS_FEEDS:
        try:
            resp = requests.get(url, timeout=timeout or config.DEFAULTS.request_timeout_sec, headers={"User-Agent": "mta-delay-insights/0.1"})
            resp.raise_for_status()
            rows.extend(parse_rss(resp.text, source=url))
        except Exception:
            continue
    return pd.DataFrame(rows, columns=EVENT_COLUMNS)


def parse_rss(xml_text: str, source: str = "rss") -> list[dict]:
    rows = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return rows
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item") or root.findall(".//atom:entry", ns)
    for it in items:
        title = (it.findtext("title") or it.findtext("atom:title", namespaces=ns) or "").strip()
        desc = (it.findtext("description") or it.findtext("atom:summary", namespaces=ns) or "")
        text = f"{title} {desc}"
        if not re.search(r"subway|mta|train|transit", text, re.IGNORECASE):
            continue
        routes = sorted({m.group(1).upper() for m in ROUTE_PATTERN.finditer(text)})
        when = it.findtext("pubDate") or it.findtext("atom:published", namespaces=ns) or it.findtext("atom:updated", namespaces=ns)
        try:
            ts = parsedate_to_datetime(when).timestamp() if when and "," in when else _ts(when)
        except Exception:
            ts = None
        if ts is None:
            continue
        weight = 0.6 if re.search(r"delay|suspend|derail|fire|signal|outage|shutdown|strike|closure|flood", text, re.IGNORECASE) else 0.2
        rows.append({"ts_start": float(ts), "ts_end": float(ts) + 24 * 3600, "kind": "news", "title": title[:120],
                     "source": source, "borough": None, "lat": None, "lon": None, "routes": routes, "weight": weight})
    return rows


# --------------------------------------------------------------------------- #
def us_federal_holidays(year: int) -> dict[date, str]:
    """Observed US federal holidays (rule-based, no dependency)."""
    def nth_weekday(month, weekday, n):
        d = date(year, month, 1)
        while d.weekday() != weekday:
            d += timedelta(days=1)
        return d + timedelta(weeks=n - 1)

    def last_weekday(month, weekday):
        d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
        while d.weekday() != weekday:
            d -= timedelta(days=1)
        return d

    def observed(d):
        if d.weekday() == 5:
            return d - timedelta(days=1)
        if d.weekday() == 6:
            return d + timedelta(days=1)
        return d

    h = {observed(date(year, 1, 1)): "New Year's Day", nth_weekday(1, 0, 3): "MLK Day", nth_weekday(2, 0, 3): "Presidents' Day",
         last_weekday(5, 0): "Memorial Day", observed(date(year, 6, 19)): "Juneteenth", observed(date(year, 7, 4)): "Independence Day",
         nth_weekday(9, 0, 1): "Labor Day", nth_weekday(10, 0, 2): "Columbus Day", observed(date(year, 11, 11)): "Veterans Day",
         nth_weekday(11, 3, 4): "Thanksgiving", observed(date(year, 12, 25)): "Christmas Day"}
    return h


def holiday_events(start: date, end: date) -> pd.DataFrame:
    rows = []
    for y in range(start.year, end.year + 1):
        for d, name in us_federal_holidays(y).items():
            if start <= d <= end:
                ts = datetime(d.year, d.month, d.day, tzinfo=NY_TZ).timestamp()
                rows.append({"ts_start": ts, "ts_end": ts + 86400, "kind": "holiday", "title": name, "source": "calendar",
                             "borough": None, "lat": None, "lon": None, "routes": [], "weight": 1.0})
    return pd.DataFrame(rows, columns=EVENT_COLUMNS)


def events_active(events: pd.DataFrame, ts: float, routes: list[str] | None = None, window_before_sec: float = 2 * 3600,
                  window_after_sec: float = 2 * 3600) -> pd.DataFrame:
    """Events overlapping [ts - before, ts + after]; route-tagged events are filtered to ``routes``."""
    if events is None or events.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    e = events.copy()
    m = (e["ts_start"] <= ts + window_after_sec) & (e["ts_end"].fillna(e["ts_start"] + 3 * 3600) >= ts - window_before_sec)
    e = e[m]
    if routes:
        want = set(map(str, routes))
        e = e[e["routes"].map(lambda rs: (not rs) or bool(want & set(map(str, rs))))]
    return e


def event_features(events: pd.DataFrame, ts: float, routes: list[str] | None = None) -> dict:
    """Numeric features for one moment: weighted counts by kind, plus holiday flag."""
    act = events_active(events, ts, routes)
    feats = {"holiday": 0.0, "venue_event_w": 0.0, "street_event_w": 0.0, "news_w": 0.0, "events_any": 0.0}
    if act.empty:
        return feats
    for r in act.itertuples(index=False):
        w = float(r.weight or 0.0)
        if r.kind == "holiday":
            feats["holiday"] = 1.0
        elif r.kind == "venue_event":
            feats["venue_event_w"] += w
        elif r.kind == "news":
            feats["news_w"] += w
        else:
            feats["street_event_w"] += w
    feats["events_any"] = float(min(1.0, feats["venue_event_w"] + feats["street_event_w"] + feats["news_w"]))
    return feats
