"""NY State Open Data (Socrata) client for the MTA performance datasets.

All fetchers return DataFrames with lower snake-case column names, parsed month
dates and numeric measures, so fixtures and live pulls look identical downstream.
"""
from __future__ import annotations

from typing import Iterable

import pandas as pd
import requests

from .. import config

MONTH_ALIASES = ("month", "date", "period_start")
COUNT_ALIASES = {
    "delays": ("delays", "count", "delay_count", "number_of_delays"),
    "incidents": ("incidents", "count", "incident_count", "number_of_incidents", "delays"),
    "major_incidents": ("count", "incidents", "major_incidents"),
}


class SocrataClient:
    def __init__(self, domain: str | None = None, app_token: str | None = None,
                 session: requests.Session | None = None, timeout: int | None = None):
        self.domain = domain or config.SOCRATA_DOMAIN
        self.app_token = app_token or config.socrata_app_token()
        self.session = session or requests.Session()
        self.timeout = timeout or config.DEFAULTS.request_timeout_sec

    def url(self, dataset_id: str) -> str:
        return f"https://{self.domain}/resource/{dataset_id}.json"

    def fetch(self, dataset_id: str, where: str | None = None, select: str | None = None,
              order: str | None = None, group: str | None = None, page_size: int = 50000,
              max_rows: int | None = None) -> pd.DataFrame:
        """Page through a SoQL query. ``group`` is required whenever ``select`` aggregates."""
        headers = {"X-App-Token": self.app_token} if self.app_token else {}
        frames = []
        offset = 0
        while True:
            params = {"$limit": page_size, "$offset": offset}
            if where:
                params["$where"] = where
            if select:
                params["$select"] = select
            if group:
                params["$group"] = group
            if order:
                params["$order"] = order
            resp = self.session.get(self.url(dataset_id), params=params, headers=headers, timeout=self.timeout)
            if resp.status_code >= 400:
                raise requests.HTTPError(f"{resp.status_code} for {resp.url}: {resp.text[:200]}", response=resp)
            batch = resp.json()
            if not batch:
                break
            frames.append(pd.DataFrame(batch))
            offset += len(batch)
            if len(batch) < page_size or (max_rows and offset >= max_rows):
                break
        if not frames:
            return pd.DataFrame()
        return normalize_columns(pd.concat(frames, ignore_index=True))

    # ---- dataset-specific helpers ---------------------------------------- #
    def trains_delayed(self, lines: Iterable[str] | None = None, since: str | None = None) -> pd.DataFrame:
        return normalize_trains_delayed(self.fetch(config.OPEN_DATASETS["trains_delayed"]["id"],
                                                   where=_where(lines, since)))

    def delay_causing_incidents(self, lines: Iterable[str] | None = None, since: str | None = None) -> pd.DataFrame:
        return normalize_incidents(self.fetch(config.OPEN_DATASETS["delay_causing_incidents"]["id"],
                                              where=_where(lines, since)))

    def _fetch_series(self, keys: tuple[str, ...], lines: Iterable[str] | None, since: str | None,
                      required: tuple[str, ...] = ("month", "line")) -> pd.DataFrame:
        """Fetch and concatenate a dataset series (e.g. 2020-2024 + beginning 2025).

        Each dataset is tried with the month/line filter first and then unfiltered
        (older series sometimes name the month column differently). A dataset whose
        columns do not include ``required`` is rejected with its actual columns in the
        error, so a wrong dataset id shows up in the run log instead of as empty output.
        """
        frames, errors = [], []
        for key in keys:
            ds = config.OPEN_DATASETS[key]["id"]
            for where in (_where(lines, since), None):
                try:
                    df = self.fetch(ds, where=where)
                except requests.HTTPError as exc:
                    errors.append(f"{key}({ds}): {str(exc)[:160]}")
                    if exc.response is not None and exc.response.status_code in (401, 403, 404):
                        break  # unknown or restricted dataset: retrying without the filter will not help
                    continue
                if df.empty:
                    errors.append(f"{key}({ds}): empty" + (" with filter" if where else ""))
                    continue
                missing = [c for c in required if c not in df.columns]
                if missing:
                    errors.append(f"{key}({ds}): missing {missing}; columns={list(df.columns)[:12]}")
                    break
                frames.append(df)
                break
        if not frames:
            raise ValueError("; ".join(errors) or "no data")
        return pd.concat(frames, ignore_index=True)

    def major_incidents(self, lines: Iterable[str] | None = None, since: str | None = None) -> pd.DataFrame:
        return normalize_major_incidents(self._fetch_series(
            ("major_incidents_2025", "major_incidents_2020", "major_incidents_2015"), lines, since))

    def customer_journey(self, lines: Iterable[str] | None = None, since: str | None = None) -> pd.DataFrame:
        return normalize_customer_journey(self._fetch_series(
            ("customer_journey_2015", "customer_journey_2020", "customer_journey_2025"), lines, since))

    def hourly_ridership(self, station_complex_ids: Iterable[str] | None, start: str, end: str) -> pd.DataFrame:
        clauses = [f"transit_timestamp >= '{start}T00:00:00'", f"transit_timestamp < '{end}T00:00:00'"]
        if station_complex_ids:
            ids = ",".join(f"'{s}'" for s in station_complex_ids)
            clauses.append(f"station_complex_id in ({ids})")
        key = "hourly_ridership_2025" if start >= "2025-01-01" else "hourly_ridership_2020"
        # Aggregate over payment method / fare class server-side (SoQL needs $group with aggregates).
        df = self.fetch(config.OPEN_DATASETS[key]["id"], where=" AND ".join(clauses),
                        select="transit_timestamp,station_complex_id,station_complex,sum(ridership) as ridership,sum(transfers) as transfers",
                        group="transit_timestamp,station_complex_id,station_complex", order="transit_timestamp")
        return normalize_hourly_ridership(df)

    def stations(self) -> pd.DataFrame:
        return normalize_stations(self.fetch(config.OPEN_DATASETS["stations"]["id"]))


# --------------------------------------------------------------------------- #
# Normalisers (pure functions; used on fixtures in tests)
# --------------------------------------------------------------------------- #
def _where(lines: Iterable[str] | None, since: str | None) -> str | None:
    clauses = []
    if lines:
        ls = ",".join(f"'{l}'" for l in lines)
        clauses.append(f"line in ({ls})")
    if since:
        clauses.append(f"month >= '{since}'")
    return " AND ".join(clauses) or None


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_").replace("-", "_") for c in df.columns]
    return df


def _month(df: pd.DataFrame) -> pd.DataFrame:
    for c in MONTH_ALIASES:
        if c in df.columns:
            df["month"] = pd.to_datetime(df[c], errors="coerce").dt.to_period("M").dt.to_timestamp()
            break
    return df


def _count(df: pd.DataFrame, kind: str, target: str) -> pd.DataFrame:
    for c in COUNT_ALIASES[kind]:
        if c in df.columns:
            df[target] = pd.to_numeric(df[c], errors="coerce").fillna(0)
            return df
    df[target] = 0.0
    return df


def _text_cols(df: pd.DataFrame, cols: Iterable[str]) -> pd.DataFrame:
    for c in cols:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()
    return df


def normalize_trains_delayed(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["month", "division", "line", "day_type", "reporting_category", "subcategory", "delays"])
    df = normalize_columns(df)
    df = _month(df)
    df = _count(df, "delays", "delays")
    if "category" in df.columns and "reporting_category" not in df.columns:
        df["reporting_category"] = df["category"]
    df = _text_cols(df, ["division", "line", "day_type", "reporting_category", "subcategory"])
    keep = [c for c in ["month", "division", "line", "day_type", "reporting_category", "subcategory", "delays"] if c in df.columns]
    return df[keep]


def normalize_incidents(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["month", "division", "line", "day_type", "reporting_category", "subcategory", "incidents"])
    df = normalize_columns(df)
    df = _month(df)
    df = _count(df, "incidents", "incidents")
    if "category" in df.columns and "reporting_category" not in df.columns:
        df["reporting_category"] = df["category"]
    df = _text_cols(df, ["division", "line", "day_type", "reporting_category", "subcategory"])
    keep = [c for c in ["month", "division", "line", "day_type", "reporting_category", "subcategory", "incidents"] if c in df.columns]
    return df[keep]


def normalize_major_incidents(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["month", "division", "line", "day_type", "category", "count"])
    df = normalize_columns(df)
    df = _month(df)
    df = _count(df, "major_incidents", "count")
    df = _text_cols(df, ["division", "line", "day_type", "category"])
    keep = [c for c in ["month", "division", "line", "day_type", "category", "count"] if c in df.columns]
    return df[keep]


def normalize_customer_journey(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["month", "division", "line", "period", "num_passengers", "additional_platform_time",
            "additional_train_time", "over_five_mins", "over_five_mins_perc", "customer_journey_time_performance"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    df = normalize_columns(df)
    df = _month(df)
    for c in cols[4:]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = _text_cols(df, ["division", "line", "period"])
    return df[[c for c in cols if c in df.columns]]


def normalize_hourly_ridership(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["ts", "hour", "station_complex_id", "station_complex", "ridership", "transfers"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    df = normalize_columns(df)
    df["ts"] = pd.to_datetime(df["transit_timestamp"], errors="coerce")
    df["ridership"] = pd.to_numeric(df.get("ridership", 0), errors="coerce").fillna(0)
    df["transfers"] = pd.to_numeric(df.get("transfers", 0), errors="coerce").fillna(0)
    df["hour"] = df["ts"].dt.hour
    grp = df.groupby(["ts", "station_complex_id", "station_complex"], as_index=False)[["ridership", "transfers"]].sum()
    grp["hour"] = grp["ts"].dt.hour
    return grp[cols]


def normalize_stations(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = normalize_columns(df)
    for c in ("gtfs_latitude", "gtfs_longitude"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def ridership_profile(hourly: pd.DataFrame) -> pd.DataFrame:
    """Average riders entering per hour-of-day x weekday/weekend for a complex."""
    if hourly.empty:
        return pd.DataFrame(columns=["day_type", "hour", "riders_per_hour"])
    h = hourly.copy()
    h["day_type"] = h["ts"].dt.dayofweek.map(lambda d: "weekend" if d >= 5 else "weekday")
    h["date"] = h["ts"].dt.date
    daily = h.groupby(["day_type", "date", "hour"], as_index=False)["ridership"].sum()
    return daily.groupby(["day_type", "hour"], as_index=False)["ridership"].mean().rename(columns={"ridership": "riders_per_hour"})
