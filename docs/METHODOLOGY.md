# Methodology

This document explains how `mta-delay-insights` turns raw feeds into a
diagnosis of *what is going wrong with arrivals at one station, how much it
matters, and what to do about it*.

## 1. From feeds to observed arrivals

The MTA does not publish historical arrival times. The GTFS-Realtime trip
update feed lists, for every train, the predicted arrival at every stop it has
**not yet left**. When a stop disappears from a trip's list between two polls,
the train has served it; the last prediction we saw is the best estimate of the
actual arrival (predictions converge as the train approaches). The collector
(`collect/arrivals.py`) implements exactly this and records, per (trip, stop):

| field | meaning |
|---|---|
| `arrival_ts` | last predicted arrival before the stop dropped off (clamped to the poll time) |
| `source` | `rt_dropoff` (normal) or `trip_vanished` (trip left the feed; lower confidence) |
| `n_predictions`, `pred_drift_sec` | how many polls tracked the stop and how far the ETA moved: a proxy for holds |
| `confidence` | 1.0 minus penalties for stale/unconverged predictions |

Raw protobuf snapshots can be archived (`--raw-dir`) and replayed, so the
derivation can be improved later without re-collecting.

## 2. Schedule matching

Each observed arrival is matched to the static GTFS schedule:

1. **trip-id match**: the realtime id `007800_6..N01R` is the suffix of the
   static id `ASP26GEN-…_007800_6..N01R`;
2. otherwise the **nearest scheduled arrival** of the same route at the same
   stop within a tolerance (default 15 minutes).

The match yields `lateness_sec` and the scheduled headway *of that trip*
(`sched_headway_sec`), which is the reference for gap/bunching flags. Using the
trip's own scheduled headway means service transitions (6 → 10 minute headways
at 22:00) are not counted as gaps.

## 3. Station × line metrics

Computed per platform, **per route** (a local and an express sharing a platform
are not interchangeable for riders), per service date and hour:

| metric | definition |
|---|---|
| `lateness_median/mean/p90_sec`, `late_share` | lateness vs schedule; late = ≥ 5 min |
| `headway_sec`, `headway_cv` | observed headway within the route and service day |
| `gap_share` | headway ≥ 1.5 × scheduled headway |
| `bunching_share` | headway ≤ 0.5 × scheduled headway |
| `expected_wait_sec` | E[h²] / (2 E[h]) — mean wait for a rider arriving at random |
| `apt_sec` | *additional platform time*: expected wait − scheduled headway / 2 |
| `service_delivered` | observed trains / scheduled trains |
| `problem` (per arrival) | `is_late` OR `is_gap` |

`apt_sec` is the same construct the MTA reports monthly in its Customer
Journey-Focused Metrics, so window values can be sanity-checked against Open Data.

## 4. Baseline comparison and focus hours

The user names a **window** (the period with the suspected issue) and a
**baseline** (default: the same length immediately before). For every metric:

* bootstrap 95% CI of the difference in means (2,000 resamples),
* Mann-Whitney U test,
* Cliff's delta effect size,
* a **practical-magnitude threshold** (`MIN_EFFECT`), so a statistically
  significant +3 s change is still reported as *flat*.

A metric is *worse* only when it is significant **and** material. Samples are
hour-buckets (day × hour) for share/APT metrics, and individual arrivals for
lateness and headway.

**Focus hours.** Unless the caller fixes hours, the engine tests each hour of
the day separately (problem share, APT, late share, median and mean lateness)
with stricter thresholds and keeps the hours that worsened. The headline
comparison, the attribution lenses and the impact estimate then run on those
hours, which stops a 08:00–09:00 issue being diluted by 22 quiet hours.

**Trends.** Daily series of APT and problem share are tested with Kendall's tau
(monotonic trend) and a single change-point search (binary segmentation with a
minimum gain and a shift of at least one pooled standard deviation).

## 5. Attribution lenses

Each lens returns `Evidence(lens, cause, share_explained, lift, confidence, summary, details)`.
`share_explained` is the fraction of problem arrivals covered by the
explanation; `lift` is P(problem | explanation) / P(problem | no explanation).

**Location lenses (where does the delay originate?)**

* *upstream*: for each late train, lateness at up to six upstream stops is
  inspected. If the train was already late at the nearest upstream stop the
  delay is *inherited*; otherwise it was *accumulated on the approach*. The
  first stop where lateness crosses the threshold is the *origin*. Per-segment
  run-time excess (actual − scheduled run time) shows where time is lost.
* *gap_inheritance*: did headway gaps already exist at the nearest upstream stop?

**Cause lenses (why?)**

* *alerts*: unplanned MTA alerts (Mercury `alert_type` + regex cause tagging:
  signal, track, switch, rolling stock, police, medical, person on track,
  fire/smoke, power, weather…) active for the route at each arrival; share and
  lift per category. *planned_work* is the same test for planned alerts.
* *run_time_pattern*: one segment carrying ≥ 60% of the run-time loss ⇒
  `segment_restriction` (timer, speed restriction, failure); loss spread across
  ≥ 3 segments ⇒ `dwell_time`.
* *merge*: at each upstream stop shared with another route, a train's
  **unimpeded** arrival is projected (scheduled arrival + lateness at the previous
  stop). If another route's train arrived within 180 s before that, the train is
  in *conflict*. Extra lateness gained at the merge stop by conflict trains vs.
  free trains (Mann-Whitney) ⇒ `interlining_merge`.
* *terminal*: late trains that were already late leaving the terminal ⇒
  `terminal_dispatch` (crew / rolling stock / turnaround).
* *service_delivered*: problem hours running < 90% of scheduled trains ⇒ `missing_service`.
* *temporal*: concentration of problems in peak or late-night hours, or on
  weekends (weak, contextual evidence).
* *incidents*: Open Data monthly delay categories for the line vs. the system
  (over-index), mapped to the same cause vocabulary.
* *weather*: Spearman correlation of the daily problem rate with rain / snow /
  wind / heat (positive direction only) and adverse-day lift.
* *prediction_volatility*: ETAs drifting much more on problem arrivals ⇒ holds.

**Ranking.** Per cause, support = Σ share × confidence × lift-factor (lift only
counts for alert, planned-work, weather and incident lenses), capped at 1.
Location and cause findings are ranked separately.

## 6. Significance and rider impact

`severity_score` (0–100) = 35 % statistical confidence (1 − p of the strongest
*worse* metric) + 25 % effect size (|Cliff's delta|) + 40 % practical magnitude
(extra platform wait + extra lateness in minutes, saturating at 3 minutes,
scaled by rider exposure). Labels: ≥ 70 critical, ≥ 45 high, ≥ 25 moderate.

`RiderImpact` converts the per-hour deltas into passenger time, mirroring the
MTA's Additional Journey Time decomposition:

* APT component: Δ expected wait × riders entering in that hour × `route_share`
* ATT component: Δ mean lateness × the same riders

Riders per hour come from the Open Data hourly ridership dataset for the
station complex (`ridership_profile`); `route_share` (default 0.5) is the share
of entries assumed to board the analysed routes/direction and should be tuned
per station. Without ridership data a placeholder of 1,000 entries/hour is
used and flagged in the caveats.

## 7. Recommendations

`recommendations.py` holds a playbook keyed by cause (operator, rider and
monitoring actions) parameterised with the evidence: focus hours, the worst
segment, the merge stop and routes, the terminal, alternates at the station.
Metric-triggered rules add headway-management actions when bunching or gap
shares are high.

## 8. Realtime mode and the look-back propagation model

`mta_delay_insights.realtime` answers "what is the system doing right now, and
what will reach my platform next?"

**Holistic status (`status.build_live`).** Every train in the realtime feeds is
parsed into its remaining stops and ETAs; the trip id is matched to the static
schedule (suffix match on the service date) to get its lateness at its next stop.
Per route and direction: trains in service, median and p90 lateness of matched
trains, the largest headway forming anywhere on the line (largest difference
between consecutive ETAs at any stop in the next hour, versus the scheduled
headway at the busiest reference stop), bunching share, and active unplanned
alerts. Status: *disrupted* when a Delays / Suspended / Rerouted alert is
active, the largest gap is ≥ 2.5× the scheduled headway, or median lateness ≥ 8
min; *degraded* for any unplanned alert, gap ≥ 1.6× or lateness ≥ 4 min.

**Look-back model (`propagation.fit_model`).** Fitted per monitored platform
from the collected history (default 21 days):

| component | estimate | prior (used until data accumulates) |
|---|---|---|
| ETA calibration | median and p10/p90 of *actual − first predicted* arrival, by lead-time bucket (0–5, 5–10, 10–20, 20–40, 40–60, 60+ min) and route | bias 0; spread −45 s − 5 %·h … +60 s + 15 %·h |
| Lateness carry | least-squares slope/intercept of lateness at the target on lateness `k` stops upstream (k = 1…6), per route | slope 1, intercept 0 |
| Gap persistence | P(gap at target \| gap at nearest upstream stop) | 0.6 |
| Alert effect | median lateness with an unplanned alert of cause *c* active on the route minus without | 0 |

Estimates are shrunk toward the prior with weight n / (n + 20).

**Forecast (`propagation.forecast_station`).** For each live train that will
serve the platform within the hour: *model ETA* = feed ETA + calibrated bias for
that lead time + historical alert effect for active alerts; the range is the
p10–p90 error band. Predicted headways (model ETAs in sequence, per route) are
compared with the scheduled headway for the hour; a headway ≥ 1.5× is a *gap*,
≤ 0.5× is *bunched*. Trains already ≥ 3 min late upstream get an expected
lateness at the platform from the carry model for their distance. The
*downstream effects* list combines gaps forming, long current waits, late
inbound trains and active alerts, ranked by severity.

**Where it runs.** `mta-insights live` prints one snapshot; `mta-insights serve`
serves the site locally and refreshes `data/live.json` every 30 s (true
realtime); the GitHub Actions collector publishes a snapshot to Pages every ~6
minutes during its hourly run (GitHub Pages allows about ten builds per hour),
and the site build stores a final snapshot plus the fitted models
(`data/models/<target>.json`).

## 9. Validation

`synthetic.py` builds a mini Lexington-Avenue-style corridor (6 local, 4
express) and injects known causes: signal failure on a segment, peak dwell,
merge holds, missing trips, late terminal departures, weather sensitivity. The
test-suite checks that the engine names the injected cause, and that a null
scenario produces no finding. The collector is tested by encoding simulated
arrivals into GTFS-RT protobuf snapshots and replaying them. The realtime
model is tested by fitting on the simulated history, then holding one
approaching train 7 minutes in a synthetic snapshot: the forecast must flag the
late inbound train, the gap it creates and the active alert's effect.

## 10. Known limitations

* Observed arrivals inherit the feed's own errors (reassigned trains, trip-id
  changes mid-run, missing predictions). Confidence is tracked per arrival.
* The alerts lens attributes what the MTA *announced*, which is posted after
  delays start; the upstream / run-time lenses are the independent check.
* Interlining detection needs the other route's trains to be observed at the
  merge stop; collect with the target's upstream stops (the default in
  `collect --station`) or all stops.
* Monthly Open Data is line-level, not station-level; it contextualises rather
  than proves.
