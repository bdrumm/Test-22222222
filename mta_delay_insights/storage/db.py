"""SQLite store for realtime snapshots, observed arrivals, alerts and context data.

SQLite keeps the framework dependency-free and portable; the schema is small and
every analysis query is a range scan on ``(stop_id, arrival_ts)``.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    feed TEXT NOT NULL, fetched_at REAL NOT NULL, feed_ts REAL,
    n_trip_updates INTEGER, n_vehicles INTEGER, n_arrivals_emitted INTEGER
);
CREATE TABLE IF NOT EXISTS predictions (
    snapshot_ts REAL NOT NULL, feed TEXT, trip_id TEXT NOT NULL, route_id TEXT, start_date TEXT,
    direction TEXT, stop_id TEXT NOT NULL, stop_sequence INTEGER, arrival_ts REAL, departure_ts REAL
);
CREATE INDEX IF NOT EXISTS ix_pred_stop_ts ON predictions(stop_id, arrival_ts);
CREATE TABLE IF NOT EXISTS arrivals (
    trip_key TEXT NOT NULL, trip_id TEXT NOT NULL, route_id TEXT, start_date TEXT, direction TEXT,
    stop_id TEXT NOT NULL, arrival_ts REAL NOT NULL, departure_ts REAL, source TEXT,
    first_seen_ts REAL, last_seen_ts REAL, first_pred_ts REAL, n_predictions INTEGER,
    pred_drift_sec REAL, confidence REAL,
    UNIQUE(trip_key, stop_id) ON CONFLICT REPLACE
);
CREATE INDEX IF NOT EXISTS ix_arr_stop_ts ON arrivals(stop_id, arrival_ts);
CREATE INDEX IF NOT EXISTS ix_arr_trip ON arrivals(trip_key);
CREATE TABLE IF NOT EXISTS alerts (
    alert_id TEXT NOT NULL, alert_type TEXT, planned INTEGER, cause_category TEXT,
    created_at REAL, updated_at REAL, active_start REAL, active_end REAL,
    routes TEXT, stops TEXT, header TEXT, description TEXT, last_seen_ts REAL,
    UNIQUE(alert_id, active_start) ON CONFLICT REPLACE
);
CREATE INDEX IF NOT EXISTS ix_alerts_start ON alerts(active_start);
"""

ARRIVAL_COLUMNS = [
    "trip_key", "trip_id", "route_id", "start_date", "direction", "stop_id", "arrival_ts",
    "departure_ts", "source", "first_seen_ts", "last_seen_ts", "first_pred_ts", "n_predictions",
    "pred_drift_sec", "confidence",
]
ALERT_COLUMNS = [
    "alert_id", "alert_type", "planned", "cause_category", "created_at", "updated_at",
    "active_start", "active_end", "routes", "stops", "header", "description", "last_seen_ts",
]


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA journal_mode=WAL") if self.path != ":memory:" else None
        self.conn.executescript(SCHEMA)

    # ---- writes ----------------------------------------------------------- #
    def insert_snapshot(self, feed: str, fetched_at: float, feed_ts: float | None,
                        n_trip_updates: int, n_vehicles: int, n_arrivals: int) -> None:
        self.conn.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?)",
                          (feed, fetched_at, feed_ts, n_trip_updates, n_vehicles, n_arrivals))
        self.conn.commit()

    def insert_predictions(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        cols = ["snapshot_ts", "feed", "trip_id", "route_id", "start_date", "direction", "stop_id",
                "stop_sequence", "arrival_ts", "departure_ts"]
        rows = df[cols].astype(object).where(df[cols].notna(), None).values.tolist()
        self.conn.executemany(f"INSERT INTO predictions ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", rows)
        self.conn.commit()
        return len(rows)

    def insert_arrivals(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        d = df.reindex(columns=ARRIVAL_COLUMNS)
        rows = d.astype(object).where(d.notna(), None).values.tolist()
        self.conn.executemany(
            f"INSERT INTO arrivals ({','.join(ARRIVAL_COLUMNS)}) VALUES ({','.join('?' * len(ARRIVAL_COLUMNS))})", rows)
        self.conn.commit()
        return len(rows)

    def upsert_alerts(self, df: pd.DataFrame, seen_ts: float | None = None) -> int:
        if df.empty:
            return 0
        d = df.copy()
        d["routes"] = d["routes"].map(json.dumps)
        d["stops"] = d["stops"].map(json.dumps)
        d["planned"] = d["planned"].astype(int)
        d["last_seen_ts"] = seen_ts
        d = d.reindex(columns=ALERT_COLUMNS)
        rows = d.astype(object).where(d.notna(), None).values.tolist()
        self.conn.executemany(
            f"INSERT INTO alerts ({','.join(ALERT_COLUMNS)}) VALUES ({','.join('?' * len(ALERT_COLUMNS))})", rows)
        self.conn.commit()
        return len(rows)

    def put_frame(self, name: str, df: pd.DataFrame, replace: bool = True) -> None:
        """Persist a context DataFrame (incidents, ridership, weather...) as its own table."""
        d = df.copy()
        for c in d.columns:
            if d[c].dtype == object and d[c].map(lambda v: isinstance(v, (list, dict))).any():
                d[c] = d[c].map(json.dumps)
            elif str(d[c].dtype).startswith("datetime"):
                d[c] = d[c].astype(str)
            elif d[c].dtype == object:
                d[c] = d[c].map(lambda v: str(v) if v is not None and not isinstance(v, (str, float, int)) else v)
        d.to_sql(f"ctx_{name}", self.conn, if_exists="replace" if replace else "append", index=False)
        self.conn.commit()

    # ---- reads ------------------------------------------------------------ #
    def get_frame(self, name: str) -> pd.DataFrame:
        try:
            return pd.read_sql(f"SELECT * FROM ctx_{name}", self.conn)
        except Exception:
            return pd.DataFrame()

    def arrivals(self, stop_ids: Iterable[str] | str | None = None, start_ts: float | None = None,
                 end_ts: float | None = None, route_ids: Iterable[str] | None = None,
                 min_confidence: float = 0.0) -> pd.DataFrame:
        q = "SELECT * FROM arrivals WHERE confidence >= ?"
        args: list = [min_confidence]
        if stop_ids:
            ids = [stop_ids] if isinstance(stop_ids, str) else list(stop_ids)
            q += f" AND stop_id IN ({','.join('?' * len(ids))})"
            args += ids
        if start_ts is not None:
            q += " AND arrival_ts >= ?"
            args.append(start_ts)
        if end_ts is not None:
            q += " AND arrival_ts < ?"
            args.append(end_ts)
        if route_ids:
            rs = list(route_ids)
            q += f" AND route_id IN ({','.join('?' * len(rs))})"
            args += rs
        q += " ORDER BY arrival_ts"
        return pd.read_sql(q, self.conn, params=args)

    def arrivals_for_trips(self, trip_keys: Iterable[str]) -> pd.DataFrame:
        keys = list(trip_keys)
        if not keys:
            return pd.DataFrame(columns=ARRIVAL_COLUMNS)
        out = []
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            out.append(pd.read_sql(
                f"SELECT * FROM arrivals WHERE trip_key IN ({','.join('?' * len(chunk))}) ORDER BY arrival_ts",
                self.conn, params=chunk))
        return pd.concat(out, ignore_index=True)

    def alerts(self, start_ts: float | None = None, end_ts: float | None = None) -> pd.DataFrame:
        df = pd.read_sql("SELECT * FROM alerts", self.conn)
        if df.empty:
            return df
        df["routes"] = df["routes"].map(lambda s: json.loads(s) if s else [])
        df["stops"] = df["stops"].map(lambda s: json.loads(s) if s else [])
        df["planned"] = df["planned"].astype(bool)
        if start_ts is not None:
            end = df["active_end"].fillna(df["updated_at"].fillna(df["active_start"]) + 3 * 3600)
            df = df[end >= start_ts]
        if end_ts is not None:
            df = df[df["active_start"].fillna(0) <= end_ts]
        return df.reset_index(drop=True)

    def snapshot_stats(self) -> pd.DataFrame:
        return pd.read_sql("SELECT feed, COUNT(*) AS polls, MIN(fetched_at) AS first_ts, MAX(fetched_at) AS last_ts, "
                           "SUM(n_arrivals_emitted) AS arrivals FROM snapshots GROUP BY feed", self.conn)

    def close(self) -> None:
        self.conn.close()
