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
2. **stem match** when the realtime id carries no path code (the L feed and
   some G and 7 trips publish `020300_L..N`): origin time + route + direction
   (`020300_L..N`) against the static ids' stems;
3. otherwise the **nearest scheduled arrival** of the same route at the same
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

## 9. Journey-time model and trip planning

A journey is a list of legs; a leg is a ride on one of a set of routes from
platform *a* to platform *b* (with an optional transfer walk before boarding).
For each leg the training set is every trip observed at both *a* and *b*:

    ride = t_b − t_a,   excess = ride − scheduled ride for the matched trip

Features at boarding (all known in realtime): the train's lateness at *a*
(clipped to −5..30 min), an active unplanned alert on the route, holiday,
weekend, weekday peak, permitted street-event weight and venue-event weight
within ±2 h on the route (events.py), news weight (RSS items mentioning the
route on that day, 0.6 if the item talks about delays / suspensions), daily
precipitation (mm, capped) and a heat flag.

The mean model is a ridge regression `excess ~ features` (λ = 25, intercept
unpenalised) whose prediction is shrunk to zero with weight n/(n+15), so a leg
with little history simply follows the schedule. The range is the p10–p90 of
the residuals, grouped by route and period (weekday peak, off-peak, weekend),
also shrunk towards ±1–2 minutes with little data. Typical waits are the
expected wait E[h²]/2E[h] from observed headways per route and hour, falling
back to half the scheduled headway.

Planning at time *t* uses the live snapshot. For the first leg, every train
serving the route set with a feed ETA at *a* in the next hour is a candidate.
Its ride is the feed's own ETA difference *b − a* when the feed publishes *b*,
averaged with `schedule + predicted excess` (the feed's long-horizon ETAs are
optimistic; the model corrects with what history says about trains in this
state). At a transfer, the earliest boarding is arrival plus the walk time,
and the first train of the next leg after that becomes the next candidate; if
the feed lists none, the typical wait for the hour is used and marked as such.
Options are ordered by arrival; the best is compared with the *typical* total
for the hour (typical waits + scheduled rides + walks) so the page can say
"about normal" or "+6 min slower than usual". Range = sum of leg ranges.

The time-distance (stringline) chart shows every train on each leg's stop
sequence as a line through its feed ETAs; the recommended itinerary is drawn
as a dashed path (wait at *a*, ride, walk, ride). Flat segments are dwells or
holds; a fan of lines converging is bunching; a wide empty band is a gap.

The whole training table (`context/journeys_training.csv.gz` on the `data`
branch) has one row per observed ride with these features, so gradient-boosted
or sequence models can be trained offline and dropped in through
`JourneyModel.from_dict`.

## 9b. Cross-line effects at transfer stations and full-route analysis

`analysis/transfers.py` treats every leg boundary of a configured journey as a
transfer (feeder platform and routes → connecting platform and routes, walk
time) and answers three questions from the collected history:

**Connections.** For each feeder arrival, *ready* = arrival + walk; the
observed connection is the first connecting-route arrival at or after *ready*
(wait = its arrival − ready). The *planned* connection is the first scheduled
connecting trip after the feeder's *scheduled* arrival + walk; the rider
"missed" it when that trip's observed arrival is before *ready* (matched by
trip id). Feeder arrivals whose connection falls outside the same polling
interval are dropped as censored. Summaries: median / p90 wait vs the
scheduled wait, missed-connection rate, planned-train-never-observed rate, all
by hour and by feeder lateness bucket (on time < 2 min, 2–5, 5+). The **cost
of a late feeder** is the bootstrap difference in excess wait (observed −
scheduled) for feeders ≥ 3 min late vs on time, with Mann-Whitney p and
Cliff's delta, plus the missed-rate lift and a per-minute slope.

**Co-movement.** Mean lateness of each line in 15-minute bins at the station;
Spearman correlation (and at lags −30…+30 min: the lag with the strongest
correlation says which line leads), and the *joint disruption lift*: how much
more often both lines are ≥ 4 min late in the same bin than if independent.
High co-movement points to shared causes (incidents at the station, crowding,
holds for connections); independence means one line's problems are its own.

**Shared-track interaction.** For each stop that both routes serve, a route-A
train's lateness change from the previous stop is compared between trips
whose projected arrival (scheduled + lateness at the previous stop) was within
3 min behind a route-B train and free-running trips (bootstrap CI,
Mann-Whitney). Because merge conflicts concentrate in peaks, the test is also
run per period (AM peak, midday, PM peak, evening, weekend) and a clear
peak-only effect is reported with its period. The time lost when the leader
is itself ≥ 3 min late is reported separately; the expected loss per trip sums
extra × conflict rate over the significant stops.

**Where the time goes.** For each journey, mean excess over schedule by hour
for every component: origin wait (expected wait E[h²]/2E[h] from observed
headways minus the same from the schedule), each ride (actual − scheduled run
time from the journey training rows), each transfer (observed − scheduled
connection wait). Shares use the positive components; the transfer share is
the *cross-line share*. A conditional view gives the downstream cost of a
late feeder: extra transfer wait plus extra excess on the next ride, for
feeders ≥ 3 min late vs on time.

Findings are ranked high / medium / low / info. The Routes page shows them
with the stacked hourly decomposition, connection waits by hour against the
schedule, the feeder-lateness table, co-movement and the interaction table;
the trip planner shows the top findings for the journey being planned and
flags tight connections (< 90 s margin) and trains whose feed stop list omits
the destination (reroutes / skip-stop service).

## 9c. Learned arrival model

**Rows.** For every observed trip and every pair of its observed stops (*u*,
*d*) with *d* exactly *k* ∈ {1, 2, 3, 5, 8, 12} stops later, a row records what
was knowable when the train served *u* at time *t*: route and direction, *k*
and the scheduled run time *u → d*, lateness at *u* and its change over the
previous 1 and 3 stops, whether the train is on a track other than scheduled,
the gap to the previous train at *u* and that train's lateness and route,
the scheduled headway, the mean excess of the last three trains that completed
*u → d* before *t* (segment state), the mean lateness at *d* over the previous
15 minutes, the feed's ETA for *d* at that moment when an ETA sample exists
(expressed as excess over schedule + current lateness), and context: hour
(sine/cosine), weekend, peak, unplanned alert on the route and its cause,
planned work, daily precipitation and heat, holiday, venue / street events and
news weights. Target: lateness at *d* minus lateness at *u*, clipped to
[−15, +60] minutes. Every feature is computed with merge-as-of joins on time
so nothing from after *t* leaks in.

**Model.** Three `HistGradientBoostingRegressor`s with quantile loss (0.1, 0.5,
0.9), early stopping, native handling of missing values and categorical
codes. The p10–p90 band is conformally scaled by the factor that gives 80%
coverage on the held-out rows. Columns that are constant or entirely missing
in the training window are dropped and listed on the card.

**Evaluation.** Strict time split (last 20% of rows). MAE by horizon and by
route against three baselines: the schedule (no change), persistence (the
segment's recent excess) and, on rows with an ETA sample, the feed's own
forecast; range coverage and median width; error by predicted-range quartile
(a wider band should mean a larger error); permutation importance.

**Serving.** For a live train the state is rebuilt from the store's recent
arrivals (last served stop, momentum, leader at that stop, segment and
destination state) and the feed's current ETA. When the feed feature was not
learnable, the model's and the feed's estimates are combined by inverse
variance, the feed's variance coming from the look-back calibration of its
errors by horizon. The trip planner applies the same model to the boarding
stop and the destination of each leg.

## 9d. Disruption climatology

The MTA service-alert archive (data.ny.gov, since April 2020) is grouped by
``event_id`` into disruption events: start = first update, end = last update,
duration = the difference (a lower bound on service impact), routes from the
``affected`` field, kind from ``status_label`` and cause from the header text
using the same classifier as live alerts. Planned kinds (planned work,
weekend/weekday service changes, station notices, boarding changes) are
excluded. Rates are events per week by route, by hour, by weekday and on a
7 × 24 grid per route; the grid cell for the current day and hour is the
expected number of new disruptions on that line in the coming hour, a prior
that the planner and the model can use before any live signal appears.

## 9e. Line views, developing incidents, ETA trust

**Marey chart.** For a route and direction, the canonical stop sequence is
the y axis and time the x axis. Observed arrivals (network-wide collection
plus backfill) over the last two hours are drawn per trip as solid lines
coloured by the train's latest lateness; trains under way are continued with
the feed's projected ETAs (dashed); the timetable's trips in the window are
drawn faintly. Parallel lines are regular service, converging lines are
bunching, a horizontal stretch is a hold, and a widening white band is a gap.

**Where the line loses time.** For every observed trip, the lateness change
between consecutive observed stops (clipped to −10…+15 min) is attributed to
the arrival stop; the mean by stop and hour of day (cells with ≥ 3 trips)
gives a stop × hour map of running-time loss; the five stops with the largest
mean loss are listed.

**Developing incidents.** Every few minutes the last 20 minutes of arrivals
are scanned per (route, direction, segment): when at least two trains and at
least 60% of the trains through the segment lost ≥ 2 minutes there, the
segment is reported with the mean loss and the first affected train's time,
marked "no alert yet" when no unplanned alert covers the route. This turns the
collection into an early-warning signal that precedes the MTA's own alerts.

**ETA trust.** ETA samples (the feed's prediction for a stop recorded when the
train was 1/2/3/5/8/12 stops away) are joined with the observed arrival: the
median and p90 absolute error and the signed bias by stops ahead, per route,
say how far ahead the countdown clock can be believed and whether it is
systematically optimistic.

## 9f. Route choice and leave-by

Journeys with the same origin and destination stations are alternatives.
Each is planned independently from the live snapshot; the alternative with
the earliest predicted arrival is recommended, with the margin to the next
option and a "close call" flag when the margin is smaller than half the best
option's p10–p90 range. The historically faster alternative at this hour
(typical waits + scheduled rides + learned excess) is named when it differs
from the live winner.

The leave-by budget for arriving at a target time with 90% confidence is the
sum over legs of the p90 wait at that hour (from observed headways, else the
scheduled headway), the transfer walk, the scheduled ride plus the journey
model's expected excess, and the p90 residual of that route and period. The
typical trip uses the expected wait and no residual margin.

## 9g. Alert event study

For every unplanned alert with routes, the mean lateness of that route's
observed arrivals is binned in 5-minute steps from 60 minutes before the
alert's creation to 120 minutes after. Per alert: the pre-alert baseline
(bins before −15 min), the onset (first bin before 0 at ≥ 2 min above the
baseline), the peak and the recovery (first bin after the peak within 1 min
of the baseline). Averaged per cause and overall, this gives the *detection
lag* (how long trains showed the problem before the MTA posted), the *peak
excess* and the *recovery time*, and a mean curve for the Alerts page.

## 9h. Network scorecard and context features

Per route and direction over the recent network-wide history: trips per day,
mean and p90 lateness over observed stop arrivals, the share ≥ 5 minutes late
and the share early, running-time loss per trip (sum of positive lateness
changes between consecutive observed stops), the share of trips whose
lateness grew by ≥ 3 minutes between first and last observed stop, headway
regularity as the coefficient of variation of headways at the line's busiest
observed stop in weekday peaks and otherwise, the worst segment (largest mean
lateness change at its arrival stop, ≥ 10 trips) and mean lateness by hour.

Two context features join the learned model: NWS severe-weather alerts for
the five boroughs (any active advisory, and any Severe/Extreme one, at the
moment of the row, from the accumulated alert history) and the disruption
climatology rate for the route at that weekday and hour (expected unplanned
disruptions per hour from the alert archive), a prior for the model before
any live symptom appears.

## 9i. Dwell times and terminal recovery

**Dwell.** Vehicle positions report `STOPPED_AT` a stop; the first and last
poll in that state bound the dwell from below (30-second polling). Per stop:
median and p90, share over 90 s, weekday-peak vs other medians, medians by
hour. For monitored platforms the hourly dwell profile is correlated with the
complex's hourly ridership: a strong positive correlation means dwell is
crowding-driven (a capacity problem); none means long dwells are holds,
dispatching or merges.

**Terminal recovery.** NYCT train ids change every trip, so physical runs are
chained by terminal turns: a trip ending at station Y is matched first-in-
first-out with the next trip of the same route leaving Y in the opposite
direction within 60 s – 40 min. Lateness at the end of the inbound trip vs the
start of the outbound one gives the carry-over slope, the share of late
arrivals that leave late again and the median time recovered, per route and
terminal, which says whether the timetable's recovery allowance matches the
delays that actually reach the terminal.

## 9j. Vehicle-position fusion, corroboration and forward simulation

**Fusion.** Each realtime trip is joined to its vehicle entity (same trip id
and start date). The vehicle carries `current_status` (INCOMING_AT,
STOPPED_AT, IN_TRANSIT_TO), the stop it refers to and a timestamp that the
NYCT feed sets to the time the train *entered* that state. Hence
`since_update_sec = now − vehicle.timestamp` is how long the train has been
stopped at, or running toward, that stop.

* *Holding*: STOPPED_AT for ≥ `HOLD_SEC` (150 s; a normal dwell is 30–60 s).
* *Stalled*: IN_TRANSIT_TO for longer than the scheduled run from the previous
  stop of the route's canonical sequence plus `STALL_SLACK_SEC` (120 s).
* *Position lateness*: the schedule says when the train should have been at
  the stop it is at (or heading to). If it is still stopped there,
  `now − sched(stop)` is a lower bound on its lateness; if it is in transit,
  `now + remaining_run − sched(stop)`. `effective_lateness` is the larger of the
  feed's lateness and the position lateness.
* *Corroboration*: `agree` when the feed's implied lateness is within 60 s of
  the position lateness, `feed_optimistic` when the position proves the train
  more than 60 s later than the feed says, `position_unknown` when the
  vehicle's stop cannot be matched to the schedule. Forecasts at a monitored
  platform raise every ETA of a feed-optimistic train by the difference, and
  the learned model receives the effective lateness as its state.

Holding trains stopped for ≥ 2 × HOLD_SEC and stalled trains are listed with
the developing incidents (kind `holding` / `stalled`), with a note when no
alert has been posted for the route yet.

**Forward simulation** (`realtime/simulate.py`). For each (route, direction)
that serves a monitored platform or a configured journey, the started trains
are ordered by progress along the canonical stop sequence. Each train's
unconstrained trajectory over the next hour is the learned model's prediction
per stop where available, else the feed's ETA; feed-optimistic trains are
shifted by their position correction; and, under the `hold_persists`
scenario, holding or stalled trains lose `hold_extra_sec` more (600 s), while
`clears_now` assumes they move immediately. Trains are then processed
front-to-back and no follower may arrive at a stop within `MIN_HEADWAY_SEC`
(90 s) of its leader; the delay this adds is the train's *knock-on*. Outputs:
projected `points` per train, per-stop headways, the worst projected gap
(where and when), the number of trains held back and the total knock-on. The
hold scenarios are only run where a train is holding or stalled. For each
monitored platform `station_scenarios` reports the next arrivals under each
scenario and the extra minutes the persisting hold would add. The
simulation is deliberately simple (no dwell model, no terminal turn, no
dispatcher interventions such as skipped stops or rerouting) and is meant to
answer "what does the current state imply if nothing changes", not to replace
the learned model.

**Browser-side live mode** (`site/rt-client.js`). The MTA endpoint answers
cross-origin GETs (`Access-Control-Allow-Origin: *`), so the site polls the
feeds directly every 30 s. A minimal protobuf wire decoder reads the subset we
need (feed timestamp, trip descriptor with the NYCT train id / assignment,
stop time updates with scheduled and actual track, vehicle positions). The
build ships `data/client_schedule.json`: for every monitored platform today's
and tomorrow's scheduled arrivals keyed by the realtime trip-id suffix, and
for every relevant line the canonical stop sequence with scheduled running
times. The browser then reproduces the server rules (lateness against the
timetable, holds and stalls with the same thresholds, an approximate position
lateness that places the train's scheduled time at its current stop using the
canonical running times, gaps and bunching against the scheduled headway, and
the `hold_persists` shift with the 90 s follower constraint). Trip ids are matched to the timetable by id, then by stem, then to the
nearest scheduled trip of the same route within 15 minutes (shown with a
`~`), as in section 2. The Line view's live overlay uses `data/client_lines.json`
(each trip's scheduled time at the last canonical stop it serves, per line) to
derive the schedule at any stop from the canonical running times, and draws
every started train of the line: its reported position as a dot (between
stops when in transit), the feed's projected trajectory, and holds, stalls
and feed-optimistic corrections. The trip planner's live section chains each configured journey from the
feeds alone: the next train of the leg's routes at the origin (started trains
only), its own ETA at the leg's destination, the transfer walk, then the next
train at the transfer stop; legs whose train is holding or stalled and
connections under two minutes are flagged. It carries no model calibration,
which the snapshot's planner adds. `tests/test_rt_client_js.py` runs the
decoder, both boards and the planner under Node against a synthetic snapshot
and checks they agree with the Python parser and position rules. The synthetic preview
ships its recorded feed next to the site so the mode can be exercised
offline; against real feeds (G and L, 3:48 AM) the decoder matched the Python
parser entity for entity.

## 9k. Scoring the live forecasts

Every live snapshot records, for each predicted arrival at a monitored
platform, the feed's ETA, the model ETA shown on the Live page, the forward
simulation's baseline projection (and the `hold_persists` one where a train
was held), the train's position state and corroboration, and the horizon
(`feed_eta − made`). At the end of a collection run (and every 10 minutes in
`serve` mode) each record is joined to the observed arrival of the same train
at the same platform; arrivals observed before the forecast was made are
discarded as a different visit. Errors are prediction − actual, so a negative
bias means trains arrived later than predicted. The published summary
(`data/forecast_eval.json`, last 14 days) reports MAE, bias and p90 by
horizon bucket for the three predictors, the share of paired rows where the
model / the simulation is closer than the feed, a breakdown by corroboration
verdict (the `feed_optimistic` rows should show a strongly negative feed bias
and a better model and simulation) and the held-train subset with the
`hold_persists` scenario's error.

## 9l. Hold log and alert latency

The dwell tracker (section 9i) keeps every dwell at the monitored stops; it
now also keeps every **hold** anywhere on the polled feeds, a train reported
`STOPPED_AT` a station for ≥ `HOLD_SEC` (150 s), as `holds/` day files on the
data branch. The summary (`data/holds.json`, last 30 days) gives holds per
day, their distribution by hour and by line, the stops that accumulate the
most held minutes (with their worst hours), and, for long holds (≥ 5 min),
the match to unplanned delay alerts naming the same route: an alert counts if
it was active within 30 minutes before the hold began or started within an
hour after; *alert latency* is the alert's start minus the hold's start, and
the share of long holds that never got an alert is reported alongside.
Terminals and relay points hold trains by design, so the stop table is read
against the line's topology.

## 10. Validation

`synthetic.py` builds a mini Lexington-Avenue-style corridor (6 local, 4
express) and injects known causes: signal failure on a segment, peak dwell,
merge holds, missing trips, late terminal departures, weather sensitivity. The
test-suite checks that the engine names the injected cause, and that a null
scenario produces no finding. The collector is tested by encoding simulated
arrivals into GTFS-RT protobuf snapshots and replaying them. The realtime
model is tested by fitting on the simulated history, then holding one
approaching train 7 minutes in a synthetic snapshot: the forecast must flag the
late inbound train, the gap it creates and the active alert's effect. The
journey model is tested by fitting on the same simulated history (the
signal-failure scenario must yield a positive alert coefficient) and planning
from a synthetic snapshot: options must be catchable, ordered by arrival, and
chain through the transfer with the configured walk time. The cross-line
module is validated on the same corridor: the merge scenario (a 6 held when it
would arrive within 150 s behind a 4) must show a significant interaction at
the merge stop and a higher connection cost for late feeders; the weather
scenario (both lines slowed by rain) must show co-movement while the
single-line signal scenario must not.

## 11. Known limitations

* Observed arrivals inherit the feed's own errors (reassigned trains, trip-id
  changes mid-run, missing predictions). Confidence is tracked per arrival.
* The alerts lens attributes what the MTA *announced*, which is posted after
  delays start; the upstream / run-time lenses are the independent check.
* Interlining detection needs the other route's trains to be observed at the
  merge stop; collect with the target's upstream stops (the default in
  `collect --station`) or all stops.
* Monthly Open Data is line-level, not station-level; it contextualises rather
  than proves.
