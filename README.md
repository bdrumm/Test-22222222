# mta-delay-insights

A framework for answering one question about a transit station:

> **What is causing the arrival problems for these lines at this station, how
> much does it matter, and what would avoid it?**

It ingests the MTA's realtime and static feeds plus NY Open Data performance
datasets and weather, derives *observed* arrivals from GTFS-Realtime, and runs
a station × line diagnosis: what changed versus a baseline, when it happens,
where the delay originates, why, how many riders it costs, and what to do.

```
$ mta-insights demo --scenario signal
# Arrival analysis: Grand Central-42 St (631N) - routes 6, 4, N-bound
**Focus hours (detected):** 09:00
**Severity:** 43.2/100 (moderate)
**Rider impact:** ~16 passenger-hours/day of extra journey time (platform wait 1 h + lateness 16 h; ...)

> Service in the focus hours is significantly worse than the baseline on late_share.

**Where:** upstream_propagation
   - [6] 100% of late trains were already late at 33 St (inherited delay). Origins: at 33 St: 13.
     Largest time loss on segment 28 St -> 33 St (median +359s vs schedule).
**Most likely cause:** signal (lenses: alerts, incidents)
   - 71% of problem arrivals happened while an MTA 'signal' alert was active for the line
     (problem rate 60% with an alert vs 2% without, lift 38.5x).
   - [6] 99% of the run-time loss sits on one segment (28 St -> 33 St): a signal timer,
     speed restriction, or failure on that segment.

## How to avoid / mitigate
- (P1) Prioritise signal maintenance / CBTC inspection on the approach segment 28 St -> 33 St
- (P1) Focus the investigation upstream: at 33 St (13). Re-run this analysis with that station as the target
- (P2) During 09:00-10:00, check alerts for '6, 4' before entering; use the alternate routes ...
```

The injected truth for that run was a signal failure between 28 St and 33 St on
northbound 6 trains in the morning peak on 70% of weekdays.

## Review site and live pipeline (GitHub Pages)

A browsable app lives in `site/` and is published to GitHub Pages by the
`pipeline` workflow: **https://bdrumm.github.io/Test-22222222/**

One-time setup (the workflow token cannot do this): in the repository go to
**Settings → Pages → Build and deployment** and set *Source* to **Deploy from a
branch**, branch **gh-pages**, folder **/ (root)**, then save. (Choosing
**GitHub Actions** as the source also works; the workflow publishes both ways.)
Every run then refreshes the site; the "Verify published site" step in the run
log reports the HTTP status of the live URL.

| page | what it shows |
|---|---|
| Plan a trip | trip-time planner (with *which way right now*: alternatives for the same origin and destination ranked by predicted arrival, with a confidence call when ranges overlap, and *when should I leave*: platform-by times for arriving by each of the next hours with 90% confidence) for the configured journeys (e.g. 4 Av-9 St → 14 St-8 Av): leave-now arrival time with a range, every catchable option in the next hour with wait / walk / ride breakdown, typical time at this hour vs right now, and a time-distance (stringline) chart of the trains on the corridor with the recommended itinerary drawn on it |
| Line view | every train on a line as a time-distance (Marey) chart: observed arrivals over the last two hours coloured by lateness, the feed's projections for trains under way, and the timetable; where the line loses time (stop × hour heatmap of lateness change); how far ahead the countdown clock can be trusted (ETA error by stops ahead, from the sampled ETAs) |
| Disruptions | disruption climatology from the MTA service-alert archive (since 2020): unplanned events per week by line, by hour and weekday, a day × hour heatmap per line, durations by cause |
| Model | the learned arrival model's card: error by horizon and route vs the schedule, persistence and the MTA countdown ETA, range coverage and calibration, feature importance |
| Routes | cross-line effects and full-route analysis for each configured journey: where the time goes by hour (origin wait, each ride, each transfer), connection waits vs schedule and missed-connection rates at each transfer station, the cost of a late feeder train, whether the two lines' lateness moves together (and which leads), and shared-track interaction (time lost behind another line's train) |
| Live | holistic status now (plus *developing incidents*: consecutive trains losing time between the same two stops, flagged before an alert exists; trains on a different track than scheduled; learned-model ETAs): every route/direction with trains in service, lateness, the largest gap forming and where, active unplanned alerts; for each monitored platform the next arrivals with feed ETA, look-back-calibrated ETA and range, predicted headways, and the *downstream effects* (gaps forming, late trains inbound with their expected lateness here, alert effects) |
| Stations | one card per monitored platform: severity, verdict, focus hours, where / why, rider impact |
| Station report | dwell time at the platform by hour with its crowding link; what changed (with CIs), problem rate and lateness by hour, day × hour heatmap, daily trend, ranked locations and causes with evidence, recommendations |
| Lines | *terminal recovery* (are delays absorbed at the terminal or carried into the next trip, per line and terminal); *network scorecard* from observed trains (per line and direction: trips/day, mean and p90 lateness, share ≥5 min late, running-time loss per trip, headway regularity in and off peak, share of trips whose lateness grew, worst segment, hourly sparkline); monthly trains delayed by reported cause per line (MTA Open Data), month-over-month / year-over-year change, cause mix vs system, customer journey metrics, major incidents |
| Alerts | alerts seen in the last 24 h with cause tags; the *event study*: mean lateness of the route's trains from 60 min before to 120 min after an unplanned alert is posted, detection lag, peak excess and recovery time, overall and by cause |
| Data | collection coverage, pipeline runs, source catalog |

**How the data gets there.** `.github/workflows/pipeline.yml` runs hourly (and
on push, briefly) on GitHub Actions, where the MTA and Open Data hosts are reachable:

1. `pipeline/collect.py` polls the realtime feeds for the platforms in
   `pipeline/targets.json` (plus their upstream stops and terminals) for ~50
   minutes, derives observed arrivals and stores them as per-day CSVs on the
   `data` branch, together with the alerts seen.
2. `pipeline/context.py` pulls trains delayed, incidents, customer journey
   metrics, the stations table, hourly ridership for the target complexes and
   recent weather.
3. `pipeline/build_site.py` loads everything, runs `analyze_station` for each
   target (window = most recent half of the coverage, baseline = the rest),
   builds line insights, and writes `site/` + JSON into `_site/`, which is
   deployed to Pages.

Reports say "collecting" until a platform has about two days of arrivals; the
Lines and Alerts pages are populated from the first run. To monitor other
platforms, edit `pipeline/targets.json`; monitored today: Grand Central
(uptown 4/5/6), Times Sq (downtown 1/2/3), Jay St-MetroTech (Manhattan-bound
A/C), **4 Av-9 St** (F/G and R/N/D/W, both directions) and **14 St-8 Av** (A/C/E
both directions, L both directions). To preview offline:

```bash
python -m pipeline.build_site --synthetic --out _site && python -m http.server -d _site 8000
```

The scheduled collector is a convenience for review; for production, run
`mta-insights collect` continuously on a small VM and point `build_site` at its store.

## Running your own instance (beyond GitHub Pages)

Pages refreshes only while the hourly Actions run collects. For continuous
30-second updates, a growing local history, the line views and a JSON API,
run the server yourself: `docker compose up -d --build` (or the systemd unit
in `deploy/`). `mta-insights serve` is then a collector *and* a server: every
poll of every feed is ingested into a persistent SQLite store, the
propagation, journey and learned arrival models are refitted from that store
every 30 minutes, the browser-side live mode's timetable extract is rewritten
for each new service date, and `/api/live`, `/api/plan?journey=`,
`/api/routes`, `/api/station?id=`, `/api/incidents`, `/api/forecast_eval` and
`/api/health` expose the snapshot. See [deploy/README.md](deploy/README.md).

## Data sources

| kind | feed | what it contributes |
|---|---|---|
| realtime | NYCT subway GTFS-RT (8 line-group feeds), LIRR, Metro-North | trip updates → observed arrivals, headways, lateness, ETA drift |
| realtime | Subway service alerts (JSON, Mercury extension) | unplanned vs planned alerts, cause tagging (signal, track, police, medical, …) |
| static | Subway GTFS (`gtfs_subway.zip`, `gtfs_supplemented.zip`) | schedule, headways, upstream stops, terminals, routes sharing a platform |
| open data | Trains Delayed, Delay-Causing Incidents, Major Incidents | MTA-reported cause mix per line (over-index vs system) |
| open data | Customer Journey-Focused Metrics, Wait Assessment, Terminal OTP | line-level APT/ATT/CJTP baselines |
| open data | Hourly Ridership, Stations | riders exposed per hour → passenger-minutes lost |
| weather | Open-Meteo hourly (no key) | precipitation / snow / heat / wind correlation |
| events | NYC permitted events (Open Data), Ticketmaster venue events (optional key), transit news RSS, federal holidays | journey-time model features (crowding / disruption context) |
| history | subwaydata.nyc daily archives (whole network since 2021, with train ids and tracks) | backfill of arrival history for training and route analysis |
| history | MTA Service Alerts archive (data.ny.gov 7kct-peq7, 520k alerts since 2020) | disruption base rates by line and hour, alert lifecycles |
| context | NWS active alerts (api.weather.gov), MTA elevator/escalator outages | severe-weather and accessibility context |

`mta-insights sources` prints the full catalog with URLs; see
[docs/DATA_SOURCES.md](docs/DATA_SOURCES.md).

## Install

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q            # ~45 s; runs the synthetic scenarios end to end
```

Requires Python 3.10+. Dependencies: pandas, numpy, scipy, requests, protobuf,
gtfs-realtime-bindings.

## Quickstart against the live feeds

```bash
# 1. Static schedule (downloads gtfs_subway.zip to data/)
mta-insights static summary
mta-insights static stations --query "Grand Central"
mta-insights static platform --station 631 --direction N        # routes, upstream stops, terminal

# 2. Collect observed arrivals for the station, its upstream stops and terminals
#    (feeds are chosen from the routes; alerts polled alongside; raw protobuf archived)
mta-insights collect --station "Grand Central" --direction N --routes 4,5,6 \
    --interval 30 --duration 86400 --alerts --raw-dir data/raw --db data/mta.sqlite

# 3. Context: MTA-reported incidents, hourly ridership for the complex, weather
mta-insights context --what incidents,journey --routes 4,5,6 --since 2026-01-01 --db data/mta.sqlite
mta-insights context --what ridership --complex-id 610 --start 2026-08-01 --end 2026-09-24 --db data/mta.sqlite
mta-insights context --what weather --start 2026-08-01 --end 2026-09-24 --db data/mta.sqlite

# 4. Analyse a two-week window against the two weeks before it
mta-insights analyze --station "Grand Central" --direction N --routes 4,5,6 \
    --window-start 2026-09-10 --window-end 2026-09-24 --out reports/gc_n.md --json reports/gc_n.json
```

`--hours 7,8,9` pins the analysis to specific hours; otherwise the engine
detects the hours that worsened. `--route-share` is the share of station entries
assumed to board the analysed routes/direction (default 0.5) for the impact estimate.

Run for a while before analysing: the upstream, merge and terminal lenses need
arrivals at the stops before the target, which `collect --station` includes by
default (`--all-stops` records everything on the chosen feeds).

## Realtime mode

```bash
mta-insights live --station "Grand Central" --direction N --routes 4,5,6 --db data/mta.sqlite
mta-insights serve --site _site --db data/mta.sqlite --port 8000      # Live page refreshes every 30 s
```

`live` prints the system status (per route/direction) and, for the target, the
next arrivals with the look-back model's calibrated ETAs and the downstream
effects of what is happening upstream right now. `serve` hosts the site locally
and recomputes `data/live.json` from the feeds every 30 seconds, refitting the
model from the store every 30 minutes. On GitHub Pages the Live page shows the
snapshot the collector published (every ~6 minutes while the hourly run is
active) and states its age.

The look-back model is fitted from the collected history: how much feed ETAs
slip by lead time, how lateness upstream carries to the platform, how often
gaps persist, and how much each alert cause adds. See
[docs/METHODOLOGY.md](docs/METHODOLOGY.md#8-realtime-mode-and-the-look-back-propagation-model).

### Train positions, corroboration and what-if scenarios

Every live snapshot fuses the feeds' **vehicle positions** with the trip
updates. A train reported `STOPPED_AT` a station for 2.5 minutes or more is
*holding*; one `IN_TRANSIT_TO` its next stop for longer than the scheduled run
plus 2 minutes is *stalled*. The position also proves a lower bound on the
train's lateness (it is still at a stop it should have left), so when that
exceeds what the feed's ETA implies the train is flagged *feed optimistic* and
every downstream ETA is raised accordingly. Holding or stalled trains show up
under "Developing right now" before any alert is posted. Every hold anywhere
on the polled feeds is also logged (`holds/` on the data branch) and the
Disruptions page reports where trains get held, by hour and by line, and how
long after a long hold the MTA's alert followed, if at all.

From the fused state the snapshot runs a **forward simulation** for each line
that serves a monitored platform or journey: every train's trajectory over the
next hour (learned-model ETAs where available, position-corrected, no train
within 90 s of the one ahead), the knock-on delay this adds to followers, and
the largest projected gap. Where a train is holding, two more scenarios are
simulated, *hold persists 10 more minutes* and *clears now*, and each
monitored platform states what they mean for its next arrivals ("if the hold
on the 6 persists, the next trains arrive 0/10/7 min later and the gap grows to
13 min"). The Line view draws the simulated trajectories (dotted) on the Marey
chart next to the observed and feed-projected ones.

### Travel mode

The Travel tab is an origin-to-destination planner over the transfer graph,
live. Pick where you board and where you are going (the destination list
offers only stations reachable directly or with one change from the origin,
grouped that way). Every viable path is enumerated from the exported lines
and station complexes: direct on a shared line, or one transfer at any
station downstream; parallel routes over the same stops merge into one option
("F → A/C/E at W 4 St", "G → A/C at Hoyt-Schermerhorn Sts"), and paths that
ride through the destination or change off a line that already goes there
are dropped. Each path gets three numbers: scheduled (canonical running
times plus the transfer walk), expected (adds half the scheduled headway as
the wait at the origin and at the change, the time trains typically lose on
each stretch at this hour from the line views, and a hold risk from the hold
log) and live (the arrival of the next itinerary from the feeds, once they
arrive). Paths are ranked by the live arrival, else by the expected time,
with proportional bars broken into wait, ride and walk and a note on why.

The selected path is drawn as a horizontal track diagram, one track per leg,
left to right in the direction of travel, with the transfer link between the
tracks. Every train on those lines is a route-badged marker that glides
between the 30-second feed polls along the scheduled running time (green
moving, amber stopped, red holding or stalled), the recommended itinerary's
trains ringed (black: your first train, purple: the connection), monitored
platforms marked, and bars under the stops for typical time lost and holds
per day. Below it: the next itineraries with both trains, boarding and
arrival ETAs, the connection's walk, wait and margin, lateness corrected
from positions and flags; what our data says about the stretches (worst
stops at this hour, where trains get held, alerts, the simulation's projected
gaps, monitored-platform effects, the disruption base rate); and a Marey
chart of the path over the next 45 minutes with the recommended itinerary
drawn through the traffic.

### Train position and speed between stations

The subway feeds carry no GPS and no speed: a vehicle reports only its state
(stopped at, in transit to, or incoming at a stop), the stop, and the moment
it entered that state. Two things follow from that. A train's progress on its
current segment is dead-reckoned from the elapsed time over the scheduled
running time (the markers above), and its speed on that segment is only known
once it arrives. But the state timestamps are exact: "in transit to X" is
stamped at departure and "stopped at X" at arrival, so seeing the two gives
the segment's run time at feed precision. With track distances between
consecutive stops projected from the static GTFS shapes (great-circle
distance where a feed has no shapes) that is a realized speed per segment.
The browser times every segment it watches a train complete (the label shows
the last one), and the collector logs every completed segment on the network
(`segment_runs/` on the data branch). The build turns them into a speed
profile per segment (median realized run against the scheduled run, by hour,
`data/segments.json`), which the Travel diagram draws as a km/h layer and
uses to name the slowest measured segment on a path.

### 30-second live mode in the browser

The MTA feed endpoint allows cross-origin requests, so the published site can
poll the feeds itself. The "live feeds" toggle on the Live page fetches the
relevant line-group feeds every 30 seconds, decodes the protobuf in the
browser (`site/rt-client.js`, no dependencies), and computes the board for
every monitored platform: next arrivals, lateness against today's timetable
(shipped by the build as `data/client_schedule.json`), positions, holds,
stalls, feed-optimistic corrections, gaps and bunching, and the "if the hold
persists" ETAs with the same rules the Python side uses, plus the active
service alerts (the alerts document allows browser requests too, polled every
two minutes). The pipeline snapshot
below it still carries the model forecasts and downstream effects. The same
toggle on the Line view draws every train of the line from the feeds every
30 seconds: reported positions as dots on the Marey chart (green moving, amber
stopped, red holding or stalled), the feed's projections, and a table with
each train's position, lateness and flags. On the trip planner it chains the
configured journeys straight from the feeds (next train at the origin, its
own ETA at the leg's destination, the transfer walk, the next train there)
and flags legs whose train is holding or stalled right now.

## Learned arrival model

`mta_delay_insights/models/` turns the collected history into a prediction
model for train times: for a train that just served stop *u*, how much will
its lateness change by a stop *k* stops ahead? Three gradient-boosted quantile
regressors (p10 / p50 / p90, scikit-learn HistGradientBoosting) learn from the
train's state (lateness, momentum, track change), the traffic ahead (gap to and
lateness of the leader), the segment's last few trains, the destination's
recent lateness, the feed's own ETA when sampled, and context (time, alerts and
their cause, planned work, precipitation, heat, events, news, holidays). The
range is conformally scaled so 80% of held-out targets fall inside it, and the
model card (`data/models/arrival.card.json`) reports MAE by horizon and route
against the schedule, persistence and the MTA feed's ETA at the same moments.
Live forecasts and the trip planner use the model as soon as it is ready
(`model_source: "learned"`), blending with the feed by inverse variance when
the feed feature is unavailable.

The live forecasts are scored too. Every snapshot's predicted arrivals at the
monitored platforms (the feed's ETA, the model ETA and the forward
simulation's projection) are kept, matched to the arrival observed afterwards
and saved to the data branch (`forecast_eval/`); the Model page reports the
resulting error by horizon, how often the model and the simulation beat the
feed, and the corroboration check (when a train's position said the feed was
optimistic, did it really arrive later?). `mta-insights serve` does the same
continuously and exposes it at `/api/forecast_eval`.

Backfilled history is not only for the model: rows at the monitored platforms
and journey stops are loaded into the analysis store too (with full-day
coverage), so station reports, journeys and transfer analyses have weeks of
baseline from the first run instead of waiting days for our own collection.

The training set is fed by three streams: the hourly collection at every stop
of every feed (`collect.all_stops` in `pipeline/targets.json`, files under
`arrivals_all/` with a rolling retention), ETA samples recorded when a train
is 1/2/3/5/8/12 stops from a monitored platform (`eta_samples/`, the benchmark
for the feed's own predictions), and a **backfill from subwaydata.nyc**
(`pipeline/backfill.py`, daily archives of the whole network since 2021, a few
days fetched per run). Dwell lower bounds from vehicle positions land in
`dwells/`. The NYCT feed extension (train id, scheduled and actual track) is
parsed without generated code (`sources/nyct_ext.py`), so track changes and
physical train runs are available to every analysis.

## Trip planner and the journey-time model

Journeys are chains of legs declared in `pipeline/targets.json`:

```json
"journeys": [{"id": "4av9st-to-14st8av-via-f", "label": "4 Av-9 St → 14 St-8 Av (F, then A/C/E)",
  "legs": [{"from": {"station": "4 Av-9 St", "direction": "N", "routes": ["F"]}, "to": {"station": "W 4 St-Wash Sq"}},
           {"transfer_min": 2, "from": {"station": "W 4 St-Wash Sq", "direction": "N", "routes": ["A","C","E"]}, "to": {"station": "14 St"}}]}]
```

The collector then records arrivals at every stop of every leg, and
`build_site` fits one **journey-time model** per journey
(`mta_delay_insights/realtime/journey.py`): for each leg, a ridge regression
of *actual ride minus scheduled ride* on the conditions at boarding (the
train's lateness, an unplanned alert on the route, holiday / weekend / peak,
permitted street events and venue events, transit news mentions, precipitation,
heat), residual quantiles per route and period for the range, and typical
waits from observed headways. The **Plan a trip** page enumerates the trains a
rider can actually catch from the live snapshot, chains legs through transfers,
and shows each option's arrival window and the stringline of trains on the
corridor. Models are published as `data/models/journey_<id>.json`; the full
training table (one row per observed ride with all features) is exported to
the `data` branch as `context/journeys_training.csv.gz` so you can train your
own model on it.

**Cross-line effects.** Every transfer in a journey is analysed
(`mta_delay_insights/analysis/transfers.py`, published as `data/routes.json`):
connection waits and missed connections, what a late feeder costs at the
transfer and on the next ride, lateness co-movement between the lines at the
station, and shared-track interaction where routes share stops. The planner
flags tight connections and trains whose feed stop list omits the destination.
See [docs/METHODOLOGY.md](docs/METHODOLOGY.md#9b-cross-line-effects-at-transfer-stations-and-full-route-analysis).

External signals come from `mta_delay_insights/sources/events.py`: NYC
permitted events (Open Data), venue events near major stations (Ticketmaster,
optional `TICKETMASTER_API_KEY` secret), local transit news via RSS, and
federal holidays. `pipeline/context.py` refreshes them hourly into
`context/events.csv.gz`.

## Try it offline

```bash
mta-insights demo --scenario signal     # also: dwell, merge, missing, terminal, weather, mixed, none
mta-insights demo --scenario mixed --out reports/mixed.md --json reports/mixed.json
```

The demo builds a small Lexington-Avenue-style corridor (6 local, 4 express),
injects a known cause for two weeks after a two-week clean baseline, and runs
the same engine used on live data. Ground truth is printed on stderr.

## How the analysis works

1. **Observed arrivals** — a stop disappearing from a trip's GTFS-RT update
   list means the train served it; the last prediction is the arrival. ETA
   drift and prediction counts are kept as a "holds" signal.
2. **Schedule match** — realtime trip id ↔ static trip id (suffix match), else
   nearest scheduled trip of the same route. Gives lateness and the trip's own
   scheduled headway.
3. **Metrics per route, per day-hour** — lateness, headway CV, gap share (≥ 1.5×
   scheduled), bunching share (≤ 0.5×), expected platform wait E[h²]/2E[h],
   *additional platform time* (the MTA's own customer metric), service delivered.
4. **What changed** — window vs baseline with bootstrap CIs, Mann-Whitney,
   Cliff's delta **and** a practical-magnitude threshold; per-hour scan finds
   the *focus hours*; Kendall trend and change-point on daily series.
5. **Where** — upstream lens (inherited vs accumulated on the approach, origin
   stop, per-segment run-time excess) and gap inheritance.
6. **Why** — alert coverage & lift by cause category, planned work, run-time
   pattern (one bad segment vs dwell everywhere), merge conflicts at
   interlining stops, late terminal departures, missing trains, MTA incident
   category over-index, weather correlation, ETA volatility.
7. **Significance & impact** — severity 0-100 (confidence, effect size,
   magnitude × rider exposure) and passenger-hours/day from hourly ridership.
8. **How to avoid** — a playbook keyed by cause, parameterised with the
   evidence (segment, merge stop, hours, terminal, alternates), for operators,
   riders and monitoring.

Details, formulas and limitations: [docs/METHODOLOGY.md](docs/METHODOLOGY.md).

## Layout

```
mta_delay_insights/
  config.py               feed URLs, dataset ids, thresholds (env-overridable)
  sources/                gtfs_static, gtfs_realtime, alerts, open_data (Socrata), weather, registry
  collect/                ArrivalTracker (snapshots → arrivals), Collector (poll / replay / archive)
  storage/db.py           SQLite: snapshots, predictions, arrivals, alerts, context frames
  analysis/
    schedule_match.py     lateness + scheduled headway per arrival
    metrics.py            per-route station metrics and hourly profile
    significance.py       bootstrap / Mann-Whitney / Cliff's delta, rider impact, severity
    trends.py             hour / day-of-week patterns, Kendall trend, change point
    attribution.py        lenses → Evidence, cause ranking
    recommendations.py    playbook
    report.py             InsightReport → Markdown / JSON
    engine.py             analyze_station(): orchestration
  synthetic.py            mini corridor + injected-cause scenarios + GTFS-RT re-encoding
  realtime/               status (holistic now + position fusion), propagation (look-back model + forecast), simulate (forward scenarios), server (local live mode)
  cli.py                  mta-insights sources | static | collect | replay | context | analyze | live | serve | demo
pipeline/                 GitHub Actions data pipeline: collect, context, build_site, data-branch helper
site/                     static review app (vanilla JS + SVG charts) published to GitHub Pages; rt-client.js polls the MTA feeds in the browser
tests/                    unit tests + end-to-end scenario tests
docs/                     METHODOLOGY.md, DATA_SOURCES.md
examples/                 programmatic use
```

## Programmatic use

```python
from datetime import datetime
from mta_delay_insights.sources.gtfs_static import StaticGTFS, NY_TZ
from mta_delay_insights.storage import Store
from mta_delay_insights.analysis import AnalysisRequest, analyze_station

static = StaticGTFS.load("data/gtfs_subway.zip")
store = Store("data/mta.sqlite")
req = AnalysisRequest(station="Grand Central", direction="N", routes=["4", "5", "6"],
                      window_start=datetime(2026, 9, 10, tzinfo=NY_TZ), window_end=datetime(2026, 9, 24, tzinfo=NY_TZ))
report = analyze_station(store, static, req)
print(report.verdict, report.ranked_causes[:2], report.impact.passenger_hours_per_day)
open("report.md", "w").write(report.to_markdown())
```

## Extending

* **New feed**: add a client in `sources/`, a `put_frame` in `cli.cmd_context`,
  and a lens in `analysis/attribution.py` returning `Evidence`.
* **New cause**: add the cause key to the `PLAYBOOK` in `recommendations.py`.
* **Other agencies**: any GTFS + GTFS-RT pair works; only the trip-id suffix
  matching and N/S platform conventions are MTA-specific (`gtfs_static.py`).

## Status

Validated end to end on synthetic scenarios (signal failure, dwell, merge
holds, missing trips, late terminal departures, weather; null control). The
live collectors are written against the documented MTA / Socrata / Open-Meteo
shapes and tested on fixture payloads; run them where the network allows.
