"""Feed endpoints, dataset identifiers and tunable defaults.

Everything here can be overridden through environment variables so the same code
runs in a locked-down container (fixtures only) and against the live feeds.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# MTA GTFS-Realtime feeds. As of 2024 the subway feeds no longer require a key,
# but ``MTA_API_KEY`` is still forwarded in the ``x-api-key`` header when set.
# --------------------------------------------------------------------------- #
MTA_RT_BASE = os.environ.get(
    "MTA_RT_BASE", "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds"
)

SUBWAY_RT_FEEDS: dict[str, str] = {
    # feed key -> path segment.  Routes served by each feed are listed in
    # SUBWAY_FEED_ROUTES so callers can pick the feed for a route.
    "1234567S": "nyct%2Fgtfs",
    "ace": "nyct%2Fgtfs-ace",
    "bdfm": "nyct%2Fgtfs-bdfm",
    "g": "nyct%2Fgtfs-g",
    "jz": "nyct%2Fgtfs-jz",
    "nqrw": "nyct%2Fgtfs-nqrw",
    "l": "nyct%2Fgtfs-l",
    "si": "nyct%2Fgtfs-si",
}

SUBWAY_FEED_ROUTES: dict[str, list[str]] = {
    "1234567S": ["1", "2", "3", "4", "5", "6", "6X", "7", "7X", "GS"],
    "ace": ["A", "C", "E", "H", "FS"],
    "bdfm": ["B", "D", "F", "FX", "M"],
    "g": ["G"],
    "jz": ["J", "Z"],
    "nqrw": ["N", "Q", "R", "W"],
    "l": ["L"],
    "si": ["SI"],
}

COMMUTER_RT_FEEDS: dict[str, str] = {
    "lirr": "lirr%2Fgtfs-lirr",
    "mnr": "mnr%2Fgtfs-mnr",
}

ALERT_FEEDS: dict[str, str] = {
    "subway_alerts_pb": "camsys%2Fsubway-alerts",
    "subway_alerts_json": "camsys%2Fsubway-alerts.json",
    "all_alerts_json": "camsys%2Fall-alerts.json",
    "lirr_alerts_json": "camsys%2Flirr-alerts.json",
    "mnr_alerts_json": "camsys%2Fmnr-alerts.json",
    "elevator_escalator_json": "nyct%2Fnyct_ene.json",
}

# Static schedules (no key required).
STATIC_GTFS_URLS: dict[str, str] = {
    "subway": "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_subway.zip",
    # Includes planned service changes for the coming weeks.
    "subway_supplemented": "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_supplemented.zip",
    "lirr": "https://rrgtfsfeeds.s3.amazonaws.com/gtfslirr.zip",
    "mnr": "https://rrgtfsfeeds.s3.amazonaws.com/gtfsmnr.zip",
}

# --------------------------------------------------------------------------- #
# NY State Open Data (Socrata) performance datasets published by the MTA.
# Dataset ids are the 4x4 identifiers used by https://data.ny.gov/resource/<id>.json
# --------------------------------------------------------------------------- #
SOCRATA_DOMAIN = os.environ.get("SOCRATA_DOMAIN", "data.ny.gov")

OPEN_DATASETS: dict[str, dict[str, str]] = {
    "trains_delayed": {
        "id": "9zbp-wz3y",
        "title": "MTA Subway Trains Delayed: Beginning 2020",
        "grain": "month x line x day_type x reporting_category x subcategory",
        "use": "How many trains each line loses to each delay category per month.",
    },
    "delay_causing_incidents": {
        "id": "g937-7k7c",
        "title": "MTA Subway Delay-Causing Incidents: Beginning 2020",
        "grain": "month x line x day_type x category x subcategory",
        "use": "Counts of incidents by cause; used to over-index a line's cause mix.",
    },
    "major_incidents_2020": {
        "id": "j6d2-s8m2",
        "title": "MTA Subway Major Incidents: 2020-2024",
        "grain": "month x line x day_type x category",
        "use": "Incidents delaying 50+ trains (signals, track, persons on trackbed...).",
    },
    "major_incidents_2015": {
        "id": "ereg-mcvp",
        "title": "MTA Subway Major Incidents: Beginning 2015",
        "grain": "month x line x day_type x category",
        "use": "Longest-running public major incidents series.",
    },
    "major_incidents_2025": {
        # NOTE: the id below was wrong in Sept 2026 (it resolves to the MTA open-data plan
        # catalog); the fetcher rejects it by schema. Replace with the real id when known.
        "id": "f462-ka72",
        "title": "MTA Subway Major Incidents: Beginning 2025",
        "grain": "month x line x day_type x category",
        "use": "Continuation of the major incidents series (id to be confirmed).",
    },
    "customer_journey_2015": {
        "id": "r7qk-6tcy",
        "title": "MTA Subway Customer Journey-Focused Metrics: Beginning 2015",
        "grain": "month x line x period (peak/offpeak)",
        "use": "Public series of additional platform / train time and journey time performance per line.",
    },
    "customer_journey_2020": {
        "id": "4apg-4kt9",
        "title": "MTA Subway Customer Journey-Focused Metrics: 2020-2024",
        "grain": "month x line x period (peak/offpeak)",
        "use": "Additional platform / train time and journey time performance per line.",
    },
    "customer_journey_2025": {
        "id": "s4u6-t435",
        "title": "MTA Subway Customer Journey-Focused Metrics: Beginning 2025",
        "grain": "month x line x period",
        "use": "Continuation of the journey metrics series.",
    },
    "wait_assessment": {
        "id": "s666-h6b7",
        "title": "MTA Subway Wait Assessment: Beginning 2015",
        "grain": "month x line x period",
        "use": "Share of headways within +25% of schedule; line-level regularity baseline.",
    },
    "terminal_otp_2020": {
        "id": "vtvh-gimj",
        "title": "MTA Subway Terminal On-Time Performance: 2020-2024",
        "grain": "month x line x day_type",
        "use": "Share of trains reaching the terminal within 5 minutes of schedule.",
    },
    "hourly_ridership_2020": {
        "id": "wujg-7c2s",
        "title": "MTA Subway Hourly Ridership: 2020-2024",
        "grain": "hour x station_complex x payment method",
        "use": "Entries per hour per station; converts delay minutes into riders affected.",
    },
    "hourly_ridership_2025": {
        "id": "5wq4-mkjj",
        "title": "MTA Subway Hourly Ridership: Beginning 2025",
        "grain": "hour x station_complex x payment method",
        "use": "Continuation of hourly ridership.",
    },
    "daily_ridership": {
        "id": "vxuj-8kew",
        "title": "MTA Daily Ridership Data: 2020 - 2025",
        "grain": "day x agency",
        "use": "System-wide daily ridership for normalisation.",
    },
    "stations": {
        "id": "39hk-dx4f",
        "title": "MTA Subway Stations",
        "grain": "station",
        "use": "Maps GTFS stop ids to station complexes, ADA status and daytime routes.",
    },
}

# --------------------------------------------------------------------------- #
# Weather: Open-Meteo needs no key; NYC Central Park coordinates.
# --------------------------------------------------------------------------- #
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
NYC_LAT, NYC_LON = 40.78, -73.97


@dataclass
class AnalysisDefaults:
    """Tunable thresholds used by the analysis layer."""

    late_threshold_sec: int = 300           # "late" = 5+ minutes behind schedule
    bunching_ratio: float = 0.5             # headway < 50% of scheduled -> bunched
    gap_ratio: float = 1.5                  # headway > 150% of scheduled -> gap
    schedule_match_tolerance_sec: int = 900 # max distance to nearest scheduled trip
    min_samples: int = 8                    # minimum arrivals per bucket to test
    bootstrap_iterations: int = 2000
    alpha: float = 0.05
    upstream_stops_to_check: int = 6
    upstream_origin_share_threshold: float = 0.6
    alert_lift_threshold: float = 1.5
    weather_correlation_threshold: float = 0.3
    request_timeout_sec: int = 30
    extra: dict = field(default_factory=dict)


DEFAULTS = AnalysisDefaults()


def rt_feed_url(key: str) -> str:
    """Return the full URL of a subway/commuter realtime feed by key."""
    if key in SUBWAY_RT_FEEDS:
        return f"{MTA_RT_BASE}/{SUBWAY_RT_FEEDS[key]}"
    if key in COMMUTER_RT_FEEDS:
        return f"{MTA_RT_BASE}/{COMMUTER_RT_FEEDS[key]}"
    if key in ALERT_FEEDS:
        return f"{MTA_RT_BASE}/{ALERT_FEEDS[key]}"
    raise KeyError(f"unknown feed key {key!r}")


def feed_for_route(route_id: str) -> str:
    """Return the realtime feed key that carries a given subway route."""
    r = route_id.upper()
    for key, routes in SUBWAY_FEED_ROUTES.items():
        if r in routes:
            return key
    raise KeyError(f"no realtime feed known for route {route_id!r}")


def api_key() -> str | None:
    return os.environ.get("MTA_API_KEY") or None


def socrata_app_token() -> str | None:
    return os.environ.get("SOCRATA_APP_TOKEN") or None
