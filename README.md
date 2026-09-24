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
| Stations | one card per monitored platform: severity, verdict, focus hours, where / why, rider impact |
| Station report | what changed (with CIs), problem rate and lateness by hour, day × hour heatmap, daily trend, ranked locations and causes with evidence, recommendations |
| Lines | monthly trains delayed by reported cause per line (MTA Open Data), month-over-month / year-over-year change, cause mix vs system, customer journey metrics, major incidents |
| Alerts | alerts seen in the last 24 h with cause tags |
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
platforms, edit `pipeline/targets.json`. To preview offline:

```bash
python -m pipeline.build_site --synthetic --out _site && python -m http.server -d _site 8000
```

The scheduled collector is a convenience for review; for production, run
`mta-insights collect` continuously on a small VM and point `build_site` at its store.

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
  cli.py                  mta-insights sources | static | collect | replay | context | analyze | demo
pipeline/                 GitHub Actions data pipeline: collect, context, build_site, data-branch helper
site/                     static review app (vanilla JS + SVG charts) published to GitHub Pages
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
