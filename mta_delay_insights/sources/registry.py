"""Catalog of every feed the framework knows about, with what it contributes.

``mta-insights sources`` prints this table. The catalog is also used by the
README generator and by the analysis layer to explain which lens used which feed.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

from .. import config


@dataclass(frozen=True)
class SourceSpec:
    key: str
    kind: str            # realtime | static | open_data | weather
    title: str
    url: str
    cadence: str
    auth: str
    contributes: str


def catalog() -> list[SourceSpec]:
    out: list[SourceSpec] = []
    for key in config.SUBWAY_RT_FEEDS:
        out.append(SourceSpec(
            key=f"rt:{key}", kind="realtime",
            title=f"NYCT subway GTFS-RT ({', '.join(config.SUBWAY_FEED_ROUTES[key])})",
            url=config.rt_feed_url(key), cadence="~30 s", auth="none (x-api-key optional)",
            contributes="Trip updates -> predicted and observed arrivals, headways, lateness; "
                        "vehicle positions -> where a train is when it is late",
        ))
    for key in config.COMMUTER_RT_FEEDS:
        out.append(SourceSpec(
            key=f"rt:{key}", kind="realtime", title=f"{key.upper()} GTFS-RT",
            url=config.rt_feed_url(key), cadence="~30 s", auth="none (x-api-key optional)",
            contributes="Commuter rail trip updates; same arrival pipeline as subway",
        ))
    for key in config.ALERT_FEEDS:
        out.append(SourceSpec(
            key=f"alerts:{key}", kind="realtime", title=key.replace("_", " "),
            url=config.rt_feed_url(key), cadence="~1 min", auth="none",
            contributes="Service alerts with Mercury extensions (alert type, affected routes/"
                        "stops, active periods) -> cause attribution and planned-work masking",
        ))
    for key, url in config.STATIC_GTFS_URLS.items():
        out.append(SourceSpec(
            key=f"static:{key}", kind="static", title=f"{key} static GTFS", url=url,
            cadence="weekly/as published", auth="none",
            contributes="Scheduled stop times, headways, stop sequences (upstream stops, "
                        "merge points), station names and coordinates",
        ))
    for key, meta in config.OPEN_DATASETS.items():
        out.append(SourceSpec(
            key=f"open:{key}", kind="open_data", title=meta["title"],
            url=f"https://{config.SOCRATA_DOMAIN}/resource/{meta['id']}.json",
            cadence="monthly (ridership: daily)", auth="none (SOCRATA_APP_TOKEN raises rate limit)",
            contributes=meta["use"],
        ))
    out.append(SourceSpec(
        key="weather:open_meteo", kind="weather", title="Open-Meteo hourly weather (NYC)",
        url=config.OPEN_METEO_ARCHIVE, cadence="hourly", auth="none",
        contributes="Precipitation, snow, temperature, wind -> weather correlation lens",
    ))
    return out


def as_records() -> list[dict]:
    return [asdict(s) for s in catalog()]


def format_table() -> str:
    rows = catalog()
    w_key = max(len(r.key) for r in rows)
    w_kind = max(len(r.kind) for r in rows)
    lines = [f"{'KEY'.ljust(w_key)}  {'KIND'.ljust(w_kind)}  TITLE / URL / CONTRIBUTES"]
    for r in rows:
        lines.append(f"{r.key.ljust(w_key)}  {r.kind.ljust(w_kind)}  {r.title}")
        lines.append(f"{'':{w_key}}  {'':{w_kind}}  {r.url}")
        lines.append(f"{'':{w_key}}  {'':{w_kind}}  cadence={r.cadence}; auth={r.auth}")
        lines.append(f"{'':{w_key}}  {'':{w_kind}}  -> {r.contributes}")
    return "\n".join(lines)
