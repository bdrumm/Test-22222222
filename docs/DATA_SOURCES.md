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
| major_incidents_2015 | `ereg-mcvp` | MTA Subway Major Incidents: Beginning 2015 | month × line × category (50+ trains delayed); public and current, used first |
| major_incidents_2020 | `j6d2-s8m2` | MTA Subway Major Incidents: Beginning 2020 | returned 403 (login required) in Sept 2026 |
| major_incidents_2025 | `f462-ka72` | (wrong id: resolves to the MTA open-data plan catalog) | rejected by the schema check; replace when the real id is known |
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

## Events, news and holidays (journey-time model context)

| source | endpoint | use |
|---|---|---|
| NYC permitted events | `https://data.cityofnewyork.us/resource/tvpp-9vvx.json` (no key; app token optional) | parades, races, street fairs, festivals → `street_event_w` (0.8 for parade / marathon / festival, 0.3 default, 0.2 for closures) within ±2 h of the event |
| Venue events | Ticketmaster Discovery API (`TICKETMASTER_API_KEY`), 15-mile radius of Midtown | events at Barclays Center, MSG, Yankee Stadium, Citi Field, USTA, Radio City, Beacon Theatre… mapped to the routes that serve the venue → `venue_event_w` |
| Local transit news | RSS: Gothamist, NY1 (no key) | items mentioning subway/MTA/train; route letters/numbers extracted ("F train", "L line"); weight 0.6 when the item mentions delays / suspensions / derailments / signal problems, else 0.2, for the publication day → `news_w` |
| Federal holidays | computed (`us_federal_holidays`) | `holiday` flag (schedule and demand differ) |

All are best-effort: a failing source is logged in `runs.json` and the model
simply sees zeros for that feature. Rows accumulate in `context/events.csv.gz`
for 120 days.

## Arrival history backfill

| source | endpoint | use |
|---|---|---|
| Subway Data NYC | `https://subwaydata.nyc/data/subwaydatanyc_YYYY-MM-DD_csv.tar.xz` (≈1.4 MB/day, published ~7 am for the previous day, since 2021-04-01) | `trips.csv` (trip_uid, trip_id, route_id, direction_id, start_time, vehicle_id = NYCT train id, …) and `stop_times.csv` (trip_uid, stop_id, track, arrival_time, departure_time, last_observed, marked_past) → normalised to the arrivals table with `source=subwaydata`, confidence 0.95 when `marked_past` is set |

The archive is derived from the same GTFS-Realtime feeds (stop drop-off
timing), so its rows and ours are comparable; our own rows win on the same
(trip, stop) when both exist. `pipeline/backfill.py` fetches only missing days
within the retention window, a few per run.

## MTA Service Alerts archive

`https://data.ny.gov/resource/7kct-peq7.json` (MTA Service Alerts: Beginning
April 2020; ~520k rows, refreshed monthly): alert_id, event_id, update_number,
date, agency, status_label (delays, some-delays, cancellations, planned-work,
part-suspended, local-to-express, stops-skipped, reroute, slow-speeds, …),
affected (routes, `A | H`), header, description. Grouping by `event_id` gives
each disruption's first and last update, hence its duration. Used for
disruption base rates by line, hour and weekday, and alert lifecycles.

## Other context feeds

* **NWS active alerts** `https://api.weather.gov/alerts/active?zone=NYZ072,NYZ073,NYZ074,NYZ075,NYZ176` (GeoJSON, no key): flood, heat, wind and winter advisories for the five boroughs.
* **MTA elevator / escalator outages** `https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fnyct_ene.json`: station, equipment, serving, outage and estimated return times, reason.

## Other feeds worth adding

* **MTA Bus Time / bus GTFS-RT** for stations where bus connections matter.
* **NYC Open Data 311 / NYPD** incident feeds for police-activity context.
* **MTA elevator/escalator outages** (already in the catalog) for accessibility-related dwell.
* **Community arrival-history archives** (e.g. subwaydata.nyc) to backfill
  history before you started collecting.
