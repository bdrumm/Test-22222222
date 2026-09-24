"""Hourly weather for NYC from Open-Meteo (no API key).

Weather is a *context* feed: the attribution lens correlates daily delay
metrics with precipitation, snow, extreme temperature and wind.
"""
from __future__ import annotations

import pandas as pd
import requests

from .. import config

HOURLY_VARS = ["temperature_2m", "precipitation", "rain", "snowfall", "wind_speed_10m", "weather_code"]
WEATHER_COLUMNS = ["ts", "temp_c", "precip_mm", "rain_mm", "snow_cm", "wind_kmh", "weather_code"]


def fetch_hourly(start_date: str, end_date: str, lat: float = config.NYC_LAT, lon: float = config.NYC_LON,
                 timeout: int | None = None, archive: bool = True) -> pd.DataFrame:
    url = config.OPEN_METEO_ARCHIVE if archive else config.OPEN_METEO_FORECAST
    params = {
        "latitude": lat, "longitude": lon, "start_date": start_date, "end_date": end_date,
        "hourly": ",".join(HOURLY_VARS), "timezone": "America/New_York",
    }
    resp = requests.get(url, params=params, timeout=timeout or config.DEFAULTS.request_timeout_sec)
    resp.raise_for_status()
    return weather_frame(resp.json())


def fetch_recent_hourly(past_days: int = 60, lat: float = config.NYC_LAT, lon: float = config.NYC_LON,
                        timeout: int | None = None) -> pd.DataFrame:
    """Recent hourly weather from the forecast endpoint (the archive lags ~5 days)."""
    params = {"latitude": lat, "longitude": lon, "past_days": min(int(past_days), 92), "forecast_days": 1,
              "hourly": ",".join(HOURLY_VARS), "timezone": "America/New_York"}
    resp = requests.get(config.OPEN_METEO_FORECAST, params=params, timeout=timeout or config.DEFAULTS.request_timeout_sec)
    resp.raise_for_status()
    df = weather_frame(resp.json())
    return df[df["ts"] <= pd.Timestamp.now()]


def weather_frame(payload: dict) -> pd.DataFrame:
    h = payload.get("hourly", {})
    if not h:
        return pd.DataFrame(columns=WEATHER_COLUMNS)
    df = pd.DataFrame({
        "ts": pd.to_datetime(h.get("time", [])),
        "temp_c": h.get("temperature_2m"),
        "precip_mm": h.get("precipitation"),
        "rain_mm": h.get("rain"),
        "snow_cm": h.get("snowfall"),
        "wind_kmh": h.get("wind_speed_10m"),
        "weather_code": h.get("weather_code"),
    })
    for c in WEATHER_COLUMNS[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return add_flags(df)


def add_flags(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["heavy_rain"] = df["precip_mm"].fillna(0) >= 5.0
    df["snow"] = df["snow_cm"].fillna(0) > 0.0
    df["extreme_heat"] = df["temp_c"].fillna(20) >= 32.0
    df["extreme_cold"] = df["temp_c"].fillna(20) <= -5.0
    df["high_wind"] = df["wind_kmh"].fillna(0) >= 50.0
    df["adverse"] = df[["heavy_rain", "snow", "extreme_heat", "extreme_cold", "high_wind"]].any(axis=1)
    return df


def daily_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["date", "precip_mm", "snow_cm", "temp_max_c", "temp_min_c", "wind_max_kmh", "adverse_hours"])
    d = df.copy()
    d["date"] = pd.to_datetime(d["ts"]).dt.date
    out = d.groupby("date").agg(
        precip_mm=("precip_mm", "sum"), snow_cm=("snow_cm", "sum"), temp_max_c=("temp_c", "max"),
        temp_min_c=("temp_c", "min"), wind_max_kmh=("wind_kmh", "max"), adverse_hours=("adverse", "sum"),
    ).reset_index()
    return out
