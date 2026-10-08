"""Vectorised context lookups for the arrival model: time patterns, hourly weather, alerts, events.

Every helper takes an array of epoch timestamps (and, where it matters, the route of each row) and
returns one value per row, so the feature builder can label millions of rows without a Python loop
per row. The same objects serve single rows at prediction time.

* :func:`time_features` — hour (cyclic and plain), weekday, service day type (weekday / Saturday /
  Sunday-or-holiday), time band (night, AM peak, midday, PM peak, evening; weekend day / night).
* :class:`WeatherHourly` — Open-Meteo hourly rows (temperature, precipitation this hour and over the
  last three, snow, wind, a coarse weather-code group); NaN where no hour is within reach.
* :class:`AlertTimeline` — unplanned and planned service alerts as [start, end] intervals per route:
  how many are active, the cause and age of the newest one, how many are active network-wide. Built
  from the live alert schema or from the data.ny.gov archive. NaN outside the period the alert data
  covers, so "no data" is never mistaken for "no alert".
* :class:`EventTimeline` — holiday flag and weighted counts of venue, street and news events around
  the moment (±2 h), route-specific events only counting for their routes.
* :class:`NWSTimeline` — any / severe National Weather Service alert active.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from ..sources import alerts as alerts_src
from ..sources import events as events_src
from ..sources.gtfs_static import NY_TZ

PEAK_HOURS = {7, 8, 9, 16, 17, 18, 19}
BANDS = ["night", "am_peak", "midday", "pm_peak", "evening", "weekend_day", "weekend_night"]
CAUSES = ["none", "signal", "track", "police", "medical", "mechanical", "crowding", "weather", "other"]
CAUSE_CODE = {c: i for i, c in enumerate(CAUSES)}
# live classifier categories -> coarse cause code
_CAUSE_ALIAS = {"rolling_stock": "mechanical", "switch": "track", "person_on_track": "police", "fire_smoke": "other",
                "obstruction": "track", "signal": "signal", "track": "track", "police": "police", "medical": "medical",
                "mechanical": "mechanical", "crowding": "crowding", "weather": "weather"}
WCODE_GROUPS = [(0, 0, 0), (1, 3, 1), (45, 48, 2), (51, 57, 3), (61, 67, 4), (71, 77, 5), (80, 82, 4), (85, 86, 5), (95, 99, 6)]


def cause_code(c) -> int:
    if not isinstance(c, str):
        return 0
    c = c.lower()
    for name, alias in _CAUSE_ALIAS.items():
        if name in c:
            return CAUSE_CODE[alias]
    return CAUSE_CODE["other"] if c and c != "none" and c != "unknown" else 0


def _local(ts: np.ndarray) -> pd.DatetimeIndex:
    return pd.to_datetime(np.asarray(ts, dtype=float), unit="s", utc=True).tz_convert(NY_TZ)


def holiday_dates(years) -> set[date]:
    out: set[date] = set()
    for y in set(int(y) for y in years):
        out |= set(events_src.us_federal_holidays(y).keys())
    return out


def band_of(hour: int, weekday: int) -> int:
    if weekday >= 5:
        return 5 if 7 <= hour <= 21 else 6
    if hour < 6:
        return 0
    if hour <= 9:
        return 1
    if hour <= 15:
        return 2
    if hour <= 19:
        return 3
    return 4


def time_features(ts: np.ndarray) -> pd.DataFrame:
    """Calendar features per timestamp (NY local time)."""
    loc = _local(ts)
    hour = loc.hour.values; minute = loc.minute.values; wd = loc.weekday.values
    years = set(loc.year.unique().tolist()) if len(loc) else set()
    hol = holiday_dates(years)
    dates = loc.date
    is_hol = np.array([d in hol for d in dates], dtype=bool) if hol else np.zeros(len(loc), dtype=bool)
    h = hour + minute / 60.0
    daytype = np.where(wd == 5, 1, np.where((wd == 6) | is_hol, 2, 0))
    band = np.array([band_of(int(hh), 6 if ho else int(w)) for hh, w, ho in zip(hour, wd, is_hol)], dtype=int) if len(loc) else np.zeros(0, int)
    return pd.DataFrame({"hour_sin": np.sin(2 * np.pi * h / 24), "hour_cos": np.cos(2 * np.pi * h / 24), "hour": h,
                         "dow": wd.astype(float), "daytype": daytype.astype(float), "band": band.astype(float),
                         "weekend": (wd >= 5).astype(float), "holiday": is_hol.astype(float),
                         "peak": (np.isin(hour, list(PEAK_HOURS)) & (wd < 5) & ~is_hol).astype(float)})


def _active_count(starts: np.ndarray, ends: np.ndarray, ts: np.ndarray, weights: np.ndarray | None = None) -> np.ndarray:
    """Number (or weight) of intervals [s, e] that contain each t: #{s <= t} - #{e < t} (valid since e >= s)."""
    if len(starts) == 0:
        return np.zeros(len(ts))
    if weights is None:
        return (np.searchsorted(np.sort(starts), ts, side="right") - np.searchsorted(np.sort(ends), ts, side="left")).astype(float)
    os_ = np.argsort(starts); oe = np.argsort(ends)
    cs = np.concatenate([[0.0], np.cumsum(weights[os_])]); ce = np.concatenate([[0.0], np.cumsum(weights[oe])])
    return cs[np.searchsorted(starts[os_], ts, side="right")] - ce[np.searchsorted(ends[oe], ts, side="left")]


# ----------------------------------------------------------------------------- weather

@dataclass
class WeatherHourly:
    hour_ts: np.ndarray = field(default_factory=lambda: np.zeros(0))
    cols: dict = field(default_factory=dict)
    COLUMNS = ("temp_c", "precip_hr_mm", "precip_3h_mm", "snow_cm", "wind_kmh", "wcode_group")

    @classmethod
    def from_frame(cls, df: pd.DataFrame | None) -> "WeatherHourly":
        w = cls()
        if df is None or df.empty or "ts" not in df:
            return w
        d = df.copy()
        ts = pd.to_datetime(d["ts"])
        if ts.dt.tz is None:
            ts = ts.dt.tz_localize(NY_TZ, ambiguous="NaT", nonexistent="shift_forward")
        d["_ts"] = ts.map(lambda x: x.timestamp() if pd.notna(x) else np.nan)
        d = d.dropna(subset=["_ts"]).drop_duplicates("_ts", keep="last").sort_values("_ts")
        w.hour_ts = d["_ts"].values.astype(float)
        precip = pd.to_numeric(d.get("precip_mm"), errors="coerce").fillna(0.0).values.astype(float)
        code = pd.to_numeric(d.get("weather_code"), errors="coerce").fillna(0).values.astype(float)
        grp = np.zeros(len(code))
        for lo, hi, g in WCODE_GROUPS:
            grp[(code >= lo) & (code <= hi)] = g
        w.cols = {"temp_c": pd.to_numeric(d.get("temp_c"), errors="coerce").values.astype(float), "precip_hr_mm": precip,
                  "precip_3h_mm": pd.Series(precip).rolling(3, min_periods=1).sum().values,
                  "snow_cm": pd.to_numeric(d.get("snow_cm"), errors="coerce").fillna(0.0).values.astype(float),
                  "wind_kmh": pd.to_numeric(d.get("wind_kmh"), errors="coerce").values.astype(float), "wcode_group": grp}
        return w

    @property
    def ready(self) -> bool:
        return len(self.hour_ts) > 0

    def at(self, ts: np.ndarray) -> pd.DataFrame:
        ts = np.asarray(ts, dtype=float)
        if not self.ready:
            return pd.DataFrame({c: np.full(len(ts), np.nan) for c in self.COLUMNS})
        i = np.searchsorted(self.hour_ts, ts, side="right") - 1
        ok = (i >= 0) & (ts - self.hour_ts[np.clip(i, 0, len(self.hour_ts) - 1)] < 2 * 3600)
        i = np.clip(i, 0, len(self.hour_ts) - 1)
        return pd.DataFrame({c: np.where(ok, self.cols[c][i], np.nan) for c in self.COLUMNS})


# ----------------------------------------------------------------------------- alerts

@dataclass
class AlertTimeline:
    start: np.ndarray = field(default_factory=lambda: np.zeros(0))
    end: np.ndarray = field(default_factory=lambda: np.zeros(0))
    routes: list = field(default_factory=list)          # set of routes or None (all)
    cause: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))
    planned: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))
    coverage: tuple[float, float] | None = None
    COLUMNS = ("alert_active", "alert_n", "planned_active", "cause_code", "alert_age_min", "net_alert_n")

    @classmethod
    def from_live(cls, alerts_df: pd.DataFrame | None, coverage: tuple[float, float] | None = None) -> "AlertTimeline":
        t = cls()
        if alerts_df is None or alerts_df.empty:
            return t
        a = alerts_df
        kinds = np.array([alerts_src.alert_kind(x, h) for x, h in zip(a["alert_type"], a["header"])])
        start = pd.to_numeric(a["active_start"], errors="coerce")
        upd = pd.to_numeric(a.get("updated_at", pd.Series(index=a.index, dtype=float)), errors="coerce")
        seen = pd.to_numeric(a.get("last_seen_ts", pd.Series(index=a.index, dtype=float)), errors="coerce")
        start = start.fillna(pd.to_numeric(a.get("created_at"), errors="coerce")).fillna(upd)
        # an alert without an explicit end is treated as over 20 minutes after it was last seen or updated
        end = pd.to_numeric(a["active_end"], errors="coerce").fillna(seen.fillna(upd).fillna(start) + 1200)
        keep = (kinds != "notice") & start.notna().values & end.notna().values
        t.start = start.values[keep].astype(float); t.end = np.maximum(end.values[keep].astype(float), t.start)
        t.routes = [set(map(str, rs)) if isinstance(rs, (list, tuple, set)) and len(rs) else None for rs in a["routes"].values[keep]]
        causes = a.get("cause_category", pd.Series([None] * len(a))).values[keep]
        t.cause = np.array([cause_code(c) for c in causes], dtype=int)
        t.planned = (kinds[keep] == "planned")
        if coverage is None and len(t.start):
            # the data covers the span in which alerts were observed (first poll to last), not since the oldest
            # planned alert began: an empty stretch before the first poll is "unknown", not "no alerts"
            seen_v = seen.values[keep].astype(float); upd_v = upd.values[keep].astype(float)
            obs = np.concatenate([seen_v, upd_v])
            seen_max = float(np.nanmax(obs)) if np.isfinite(obs).any() else float(np.nanmax(t.end))
            first = float(np.nanmin(seen_v)) - 600 if np.isfinite(seen_v).any() else float(np.nanmin(t.start))
            coverage = (first, seen_max)
        t.coverage = coverage
        return t

    @classmethod
    def from_archive(cls, archive: pd.DataFrame | None, coverage: tuple[float, float] | None = None) -> "AlertTimeline":
        """data.ny.gov alert archive rows (one per update) -> one interval per event (first to last update + 20 min)."""
        t = cls()
        if archive is None or archive.empty:
            return t
        from ..sources.alerts_archive import events as archive_events
        a = archive.copy()
        if a["routes"].map(lambda v: isinstance(v, str)).any():
            import json
            a["routes"] = a["routes"].map(lambda v: json.loads(v) if isinstance(v, str) else v)
        ev = archive_events(a)
        ev = ev[~ev["planned"].astype(bool)] if "planned" in ev else ev
        if ev.empty:
            return t
        t.start = ev["start_ts"].values.astype(float); t.end = ev["end_ts"].values.astype(float) + 1200
        t.routes = [set(map(str, rs)) if isinstance(rs, (list, tuple, set)) and len(rs) else None for rs in ev["routes"].values]
        t.cause = np.array([cause_code(c) for c in ev["cause_category"].values], dtype=int)
        t.planned = np.zeros(len(ev), dtype=bool)
        t.coverage = coverage or (float(t.start.min()), float(archive["ts"].max()))
        return t

    def merge(self, other: "AlertTimeline") -> "AlertTimeline":
        if not len(other.start):
            return self
        if not len(self.start):
            return other
        t = AlertTimeline(np.concatenate([self.start, other.start]), np.concatenate([self.end, other.end]), self.routes + other.routes,
                          np.concatenate([self.cause, other.cause]), np.concatenate([self.planned, other.planned]))
        covs = [c for c in (self.coverage, other.coverage) if c]
        t.coverage = (min(c[0] for c in covs), max(c[1] for c in covs)) if covs else None
        return t

    def features(self, ts: np.ndarray, route: np.ndarray) -> pd.DataFrame:
        ts = np.asarray(ts, dtype=float); route = np.asarray(route).astype(str)
        n = len(ts)
        out = {c: np.full(n, np.nan) for c in self.COLUMNS}
        if not len(self.start):
            return pd.DataFrame(out)
        delay = ~self.planned
        net = _active_count(self.start[delay], self.end[delay], ts)
        active = np.zeros(n); planned_on = np.zeros(n); cause = np.zeros(n); age = np.full(n, np.nan)
        order = np.argsort(ts, kind="stable")
        ts_sorted = ts[order]; route_sorted = route[order]
        for r in np.unique(route_sorted):
            rows = np.flatnonzero(route_sorted == r)
            rts = ts_sorted[rows]
            sel = np.array([rs is None or r in rs for rs in self.routes], dtype=bool)
            if not sel.any():
                continue
            d_sel = sel & delay; p_sel = sel & self.planned
            active[order[rows]] = _active_count(self.start[d_sel], self.end[d_sel], rts)
            planned_on[order[rows]] = _active_count(self.start[p_sel], self.end[p_sel], rts)
            # the newest active unplanned alert gives the cause and the age (intervals in start order overwrite)
            idx = np.flatnonzero(d_sel)
            idx = idx[np.argsort(self.start[idx])]
            c_loc = np.zeros(len(rows)); a_loc = np.full(len(rows), np.nan)
            for i in idx:
                lo = np.searchsorted(rts, self.start[i], side="left"); hi = np.searchsorted(rts, self.end[i], side="right")
                if hi > lo:
                    c_loc[lo:hi] = self.cause[i]
                    a_loc[lo:hi] = (rts[lo:hi] - self.start[i]) / 60.0
            cause[order[rows]] = c_loc; age[order[rows]] = a_loc
        out["alert_active"] = (active > 0).astype(float); out["alert_n"] = active; out["planned_active"] = (planned_on > 0).astype(float)
        out["cause_code"] = cause; out["alert_age_min"] = age; out["net_alert_n"] = net
        df = pd.DataFrame(out)
        if self.coverage is not None:
            outside = (ts < self.coverage[0]) | (ts > self.coverage[1])
            df.loc[outside, list(self.COLUMNS)] = np.nan
        return df


# ----------------------------------------------------------------------------- events / NWS

@dataclass
class EventTimeline:
    start: np.ndarray = field(default_factory=lambda: np.zeros(0))
    end: np.ndarray = field(default_factory=lambda: np.zeros(0))
    kind: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=object))
    weight: np.ndarray = field(default_factory=lambda: np.zeros(0))
    routes: list = field(default_factory=list)
    COLUMNS = ("holiday_event", "venue_event_w", "street_event_w", "news_w")
    WINDOW = 7200.0

    @classmethod
    def from_frame(cls, events_df: pd.DataFrame | None) -> "EventTimeline":
        t = cls()
        if events_df is None or events_df.empty:
            return t
        e = events_df
        start = pd.to_numeric(e["ts_start"], errors="coerce")
        end = pd.to_numeric(e["ts_end"], errors="coerce").fillna(start + 3 * 3600)
        keep = start.notna().values
        t.start = start.values[keep].astype(float); t.end = np.maximum(end.values[keep].astype(float), t.start)
        t.kind = e["kind"].astype(str).values[keep]
        t.weight = pd.to_numeric(e["weight"], errors="coerce").fillna(0.0).values[keep].astype(float)
        rs = e["routes"].values[keep]
        parsed = []
        for v in rs:
            if isinstance(v, str):
                try:
                    import json
                    v = json.loads(v)
                except Exception:
                    v = []
            parsed.append(set(map(str, v)) if isinstance(v, (list, tuple, set)) and len(v) else None)
        t.routes = parsed
        return t

    def features(self, ts: np.ndarray, route: np.ndarray) -> pd.DataFrame:
        ts = np.asarray(ts, dtype=float); route = np.asarray(route).astype(str)
        n = len(ts)
        out = {c: np.zeros(n) for c in self.COLUMNS}
        if not len(self.start):
            return pd.DataFrame(out)
        s = self.start - self.WINDOW; e = self.end + self.WINDOW
        groups = {"holiday_event": self.kind == "holiday", "venue_event_w": self.kind == "venue_event", "news_w": self.kind == "news"}
        groups["street_event_w"] = ~(groups["holiday_event"] | groups["venue_event_w"] | groups["news_w"])
        glob = np.array([r is None for r in self.routes], dtype=bool)
        for col, g in groups.items():
            gg = g & glob
            if gg.any():
                out[col] += _active_count(s[gg], e[gg], ts, self.weight[gg])
            spec = g & ~glob
            if spec.any():
                for r in np.unique(route):
                    sel = spec & np.array([rs is not None and r in rs for rs in self.routes], dtype=bool)
                    if sel.any():
                        rows = route == r
                        out[col][rows] += _active_count(s[sel], e[sel], ts[rows], self.weight[sel])
        out["holiday_event"] = (out["holiday_event"] > 0).astype(float)
        return pd.DataFrame(out)


@dataclass
class NWSTimeline:
    on: np.ndarray = field(default_factory=lambda: np.zeros(0))
    off: np.ndarray = field(default_factory=lambda: np.zeros(0))
    severe: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))

    @classmethod
    def from_frame(cls, nws: pd.DataFrame | None) -> "NWSTimeline":
        t = cls()
        if nws is None or nws.empty:
            return t
        t.on = pd.to_numeric(nws["onset_ts"], errors="coerce").fillna(-1e12).values.astype(float)
        t.off = pd.to_numeric(nws["ends_ts"], errors="coerce").fillna(1e12).values.astype(float)
        t.severe = nws["severity"].isin(["Severe", "Extreme"]).values
        return t

    def features(self, ts: np.ndarray) -> pd.DataFrame:
        ts = np.asarray(ts, dtype=float)
        if not len(self.on):
            return pd.DataFrame({"nws_any": np.zeros(len(ts)), "nws_severe": np.zeros(len(ts))})
        return pd.DataFrame({"nws_any": (_active_count(self.on, self.off, ts) > 0).astype(float),
                             "nws_severe": (_active_count(self.on[self.severe], self.off[self.severe], ts) > 0).astype(float)})


def climatology_rate(clim: dict | None, route: np.ndarray, ts: np.ndarray) -> np.ndarray:
    """Expected unplanned disruptions per week for (route, weekday, hour) from the alert-archive climatology."""
    ts = np.asarray(ts, dtype=float); route = np.asarray(route).astype(str)
    out = np.full(len(ts), np.nan)
    if not clim or not clim.get("grid_by_route") or not len(ts):
        return out
    loc = _local(ts)
    wd = loc.weekday.values; hr = loc.hour.values
    for r in np.unique(route):
        grid = clim["grid_by_route"].get(str(r))
        if not grid:
            continue
        g = np.asarray(grid, dtype=float)
        rows = route == r
        out[rows] = g[wd[rows], hr[rows]]
    return out


@dataclass
class FeatureContext:
    """Everything the feature builder needs besides the arrivals themselves, built once and reused."""
    alerts: AlertTimeline = field(default_factory=AlertTimeline)
    weather: WeatherHourly = field(default_factory=WeatherHourly)
    weather_daily: dict = field(default_factory=dict)         # 'YYYY-MM-DD' -> (precip_mm, heat)
    events: EventTimeline = field(default_factory=EventTimeline)
    nws: NWSTimeline = field(default_factory=NWSTimeline)
    climatology: dict | None = None

    @classmethod
    def build(cls, alerts_df=None, weather_daily=None, events_df=None, nws_df=None, climatology=None, weather_hourly=None,
              alerts_archive=None) -> "FeatureContext":
        c = cls()
        live = AlertTimeline.from_live(alerts_df)
        arch = AlertTimeline.from_archive(alerts_archive)
        c.alerts = live.merge(arch) if len(arch.start) else live
        c.weather = WeatherHourly.from_frame(weather_hourly)
        if weather_daily is not None and not weather_daily.empty and "date" in weather_daily:
            for r in weather_daily.itertuples(index=False):
                precip = float(getattr(r, "precip_mm", 0) or 0); tmax = float(getattr(r, "temp_max_c", 0) or 0)
                c.weather_daily[str(r.date)[:10]] = (min(precip, 30.0), float(tmax >= 32))
        c.events = EventTimeline.from_frame(events_df)
        c.nws = NWSTimeline.from_frame(nws_df)
        c.climatology = climatology
        return c

    def frame(self, ts: np.ndarray, route: np.ndarray) -> pd.DataFrame:
        """All context columns for the given rows (same order)."""
        ts = np.asarray(ts, dtype=float); route = np.asarray(route).astype(str)
        parts = [time_features(ts).reset_index(drop=True), self.alerts.features(ts, route), self.events.features(ts, route),
                 self.nws.features(ts), self.weather.at(ts)]
        df = pd.concat(parts, axis=1)
        # the daily summary stays for compatibility with the journey model and older cards
        days = _local(ts).strftime("%Y-%m-%d") if len(ts) else []
        w = [self.weather_daily.get(d) for d in days]
        df["precip_mm"] = [x[0] if x else 0.0 for x in w]
        df["heat"] = [x[1] if x else 0.0 for x in w]
        df["clim_rate"] = climatology_rate(self.climatology, route, ts)
        # a federal holiday counts whether it came from the calendar or the events feed
        df["holiday"] = np.maximum(df["holiday"].values, df.pop("holiday_event").values)
        return df


_CACHE: dict[tuple, FeatureContext] = {}


def cached_context(alerts_df=None, weather_daily=None, events_df=None, nws_df=None, climatology=None, weather_hourly=None,
                   alerts_archive=None) -> FeatureContext:
    """Serving-side cache keyed on the identity and size of the input frames (one context per snapshot)."""
    def sig(df):
        return (id(df), 0 if df is None else len(df))
    key = (sig(alerts_df), sig(weather_daily), sig(events_df), sig(nws_df), id(climatology), sig(weather_hourly), sig(alerts_archive))
    ctx = _CACHE.get(key)
    if ctx is None:
        _CACHE.clear()
        ctx = _CACHE[key] = FeatureContext.build(alerts_df, weather_daily, events_df, nws_df, climatology, weather_hourly, alerts_archive)
    return ctx
