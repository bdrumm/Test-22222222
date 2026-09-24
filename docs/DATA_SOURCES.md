# Data sources

All feeds are public. Run `mta-insights sources` for the live catalog
(`mta_delay_insights/sources/registry.py`).

## Realtime (poll every ~30 s)

| feed | URL | notes |
|---|---|---|
| Subway GTFS-RT, 1-7 + S | `https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs` | trip updates + vehicle positions; NYCT extension (train id, track) is ignored by the base parser |
| A C E (+H, FS) | `…/nyct%2Fgtfs-ace` | |
| B D F M | `…/nyct%2Fgtfs-bdfm` | |
| G | `…/nyct%2Fgtfs-g` | |
| J Z | `…/nyct%2Fgtfs-jz` | |
| N Q R W | `…/nyct%2Fgtfs-nqrw` | |
| L | `…/nyct%2Fgtfs-l` | |
| SIR | `…/nyct%2Fgtfs-si` | |
| LIRR / Metro-North | `…/lirr%2Fgtfs-lirr`, `…/mnr%2Fgtfs-mnr` | same pipeline; use the matching static feed |
| Subway alerts (JSON) | `…/camsys%2Fsubway-alerts.json` | GTFS-RT JSON with Mercury extension: `alert_type`, `created_at`, `updated_at` |
| All / LIRR / MNR alerts | `…/camsys%2Fall-alerts.json`, `…/camsys%2Flirr-alerts.json`, `…/camsys%2Fmnr-alerts.json` | |
| Elevator & escalator outages | `…/nyct%2Fnyct_ene.json` | accessibility context |

No API key is required for the subway feeds. If `MTA_API_KEY` is set it is sent as `x-api-key`.

## Static schedules

| feed | URL |
|---|---|
| Subway | `https://rrgtfsfeeds.s3.amazonaws.com/gtfs_subway.zip` |
| Subway with planned service changes | `https://rrgtfsfeeds.s3.amazonaws.com/gtfs_supplemented.zip` |
| LIRR / Metro-North | `…/gtfslirr.zip`, `…/gtfsmnr.zip` |

Used for: station names and platforms (`stop_id` + N/S), scheduled stop times
and headways, stop sequences (upstream stops, terminals), routes sharing a
platform (interlining). Direction: `direction_id` 0 = North/uptown, 1 = South.

## NY State Open Data (Socrata, `https://data.ny.gov/resource/<id>.json`)

| key | id | title | grain |
|---|---|---|---|
| trains_delayed | `9zbp-wz3y` | MTA Subway Trains Delayed: Beginning 2020 | month × line × day type × category × subcategory |
| delay_causing_incidents | `g937-7k7c` | MTA Subway Delay-Causing Incidents: Beginning 2020 | month × line × category |
| major_incidents_2020 | `j6d2-s8m2` | MTA Subway Major Incidents: 2020-2024 | month × line × category (50+ trains delayed) |
| major_incidents_2025 | `f462-ka72` | MTA Subway Major Incidents: Beginning 2025 | |
| customer_journey_2015 | `r7qk-6tcy` | Customer Journey-Focused Metrics: Beginning 2015 | month × line × period: APT, ATT, CJTP (public series) |
| customer_journey_2020 | `4apg-4kt9` | Customer Journey-Focused Metrics: 2020-2024 | returned 403 (login required) in Sept 2026; kept as fallback |
| customer_journey_2025 | `s4u6-t435` | Customer Journey-Focused Metrics: Beginning 2025 | |
| wait_assessment | `s666-h6b7` | Wait Assessment: Beginning 2015 | month × line × period |
| terminal_otp_2020 | `vtvh-gimj` | Terminal On-Time Performance: 2020-2024 | month × line |
| hourly_ridership_2020 | `wujg-7c2s` | Subway Hourly Ridership: 2020-2024 | hour × station complex × fare class |
| hourly_ridership_2025 | `5wq4-mkjj` | Subway Hourly Ridership: Beginning 2025 | |
| daily_ridership | `vxuj-8kew` | MTA Daily Ridership: 2020-2025 | day × agency |
| stations | `39hk-dx4f` | MTA Subway Stations | station: GTFS stop id ↔ complex id, ADA, daytime routes |

An app token (`SOCRATA_APP_TOKEN`) is optional but raises the rate limit.
Column names vary slightly between dataset versions; the normalisers in
`sources/open_data.py` accept the known aliases. Verify a dataset id on
data.ny.gov if a fetch returns 404 (ids change when a series is re-published).

## Weather

Open-Meteo archive/forecast API (no key): hourly temperature, precipitation,
rain, snowfall, wind speed and weather code for Central Park. Flags: heavy rain
≥ 5 mm/h, snow, heat ≥ 32 °C, cold ≤ −5 °C, wind ≥ 50 km/h.

## Other feeds worth adding

* **MTA Bus Time / bus GTFS-RT** for stations where bus connections matter.
* **NYC Open Data 311 / NYPD** incident feeds for police-activity context.
* **MTA elevator/escalator outages** (already in the catalog) for accessibility-related dwell.
* **Community arrival-history archives** (e.g. subwaydata.nyc) to backfill
  history before you started collecting.
