"""Small live context feeds: NWS weather alerts for the five boroughs and MTA elevator / escalator outages."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import requests

from .. import config
from .gtfs_static import NY_TZ

NWS_ZONES = "NYZ072,NYZ073,NYZ074,NYZ075,NYZ176"   # Manhattan, Bronx, Richmond, Kings, Northern Queens
NWS_URL = f"https://api.weather.gov/alerts/active?zone={NWS_ZONES}"
ENE_URL = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fnyct_ene.json"
NWS_COLUMNS = ["id", "event", "severity", "urgency", "onset_ts", "ends_ts", "area", "headline"]
ENE_COLUMNS = ["station", "routes", "equipment", "equipment_type", "serving", "ada", "outage_ts", "return_ts", "reason", "upcoming", "maintenance"]


def _ts(s) -> float | None:
    try:
        return datetime.fromisoformat(str(s)).timestamp()
    except Exception:
        return None


def fetch_nws_alerts(timeout: int | None = None) -> pd.DataFrame:
    resp = requests.get(NWS_URL, timeout=timeout or config.DEFAULTS.request_timeout_sec,
                        headers={"User-Agent": "mta-delay-insights/0.1 (github.com/bdrumm)", "Accept": "application/geo+json"})
    resp.raise_for_status()
    rows = []
    for f in resp.json().get("features", []):
        p = f.get("properties", {})
        rows.append({"id": p.get("id"), "event": p.get("event"), "severity": p.get("severity"), "urgency": p.get("urgency"),
                     "onset_ts": _ts(p.get("onset") or p.get("effective")), "ends_ts": _ts(p.get("ends") or p.get("expires")),
                     "area": p.get("areaDesc"), "headline": p.get("headline")})
    return pd.DataFrame(rows, columns=NWS_COLUMNS)


def nws_features(alerts: pd.DataFrame | None, ts: float) -> dict:
    """{'nws_any', 'nws_severe', 'nws_kinds'} at time ts."""
    out = {"nws_any": 0.0, "nws_severe": 0.0, "nws_kinds": []}
    if alerts is None or alerts.empty:
        return out
    a = alerts[(alerts["onset_ts"].fillna(-1e12) <= ts) & (alerts["ends_ts"].fillna(1e12) >= ts)]
    if a.empty:
        return out
    out["nws_any"] = 1.0
    out["nws_severe"] = float(a["severity"].isin(["Severe", "Extreme"]).any())
    out["nws_kinds"] = sorted(set(a["event"].dropna().astype(str)))
    return out


def fetch_elevator_outages(timeout: int | None = None) -> pd.DataFrame:
    resp = requests.get(ENE_URL, timeout=timeout or config.DEFAULTS.request_timeout_sec)
    resp.raise_for_status()
    rows = []
    for r in resp.json():
        def _t(v):
            try:
                return datetime.strptime(v, "%m/%d/%Y %I:%M:%S %p").replace(tzinfo=NY_TZ).timestamp()
            except Exception:
                return None
        rows.append({"station": r.get("station"), "routes": [x for x in str(r.get("trainno", "")).split("/") if x], "equipment": r.get("equipment"),
                     "equipment_type": r.get("equipmenttype"), "serving": r.get("serving"), "ada": r.get("ADA") == "Y",
                     "outage_ts": _t(r.get("outagedate")), "return_ts": _t(r.get("estimatedreturntoservice")), "reason": r.get("reason"),
                     "upcoming": r.get("isupcomingoutage") == "Y", "maintenance": r.get("ismaintenanceoutage") == "Y"})
    return pd.DataFrame(rows, columns=ENE_COLUMNS)
