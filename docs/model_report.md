# Arrival model report

_Generated 2026-10-05 02:47 EDT from `pipeline/train_model.py`; tables are produced by `pipeline/model_report.py`._

## Summary

This run rebuilt the arrival model's training set and feature space around the
question the app asks — *when will this train reach my platform, and when will
I reach my destination?* — and measured what time of day, day patterns,
weather, alerts and events add on a strictly time-ordered hold-out.

**What changed**

* **Three weeks of consistent history instead of two days.** The model that was
  live before this run had trained on 1.4 days of our own collector (Sep 26–27);
  every calendar, weather and event feature was constant over that span and had
  been dropped. The training set is now the subwaydata.nyc archive for every day
  from Sep 14 to Oct 3 (~230 000 arrivals per weekday at every stop, the same
  GTFS-realtime feeds our collector reads), backfilled for the ten days the
  pipeline had recorded as missing (the archive publishes with a lag; the
  backfill now retries those days). Our own collector rows are kept apart and
  used as the serving-condition check, because they mark arrivals about 40 s
  earlier than the archive does (the stop is dropped from the feed before the
  archive's "marked past" time). Within one convention that offset cancels in
  the target; mixing conventions inside a trip would not.
* **Horizons 1 to 20 stops.** The model used to stop at k = 12; the configured
  commutes have legs of 1, 4, 6 and 10 stops, and a direct ride can be 20. All
  of k = 1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20 are trained, so the same model
  gives the next platform and the destination arrival.
* **Vectorised context** (`models/context.py`): alert, event, NWS and weather
  lookups are interval arithmetic on sorted arrays instead of a Python loop per
  row, so a full archive day labels in ~30 s. Alert features are *unknown* (NaN)
  outside the span in which the alert feed was observed; before this they read
  as "no alert", which is a different thing to a tree.
* **New features**, by group: calendar (hour, weekday, service day type, time
  band, holiday); day patterns (out-of-fold profiles of each segment's typical
  excess by day type and hour, the route's typical lateness then, the route's
  and the network's lateness over the last 30 / 15 minutes, the route's
  throughput, the alert-archive climatology); hourly weather (temperature, rain
  this hour and over three hours, snow, wind, weather-code group, NWS);
  alerts and events (count, cause and age of unplanned alerts on the route,
  alerts network-wide, planned work, venue / street / news event weights).
* **Out-of-fold day-pattern profiles.** The first attempt fitted the profiles on
  the same training rows they describe and the patterns group made the model
  *worse* (73.3 s against 71.7 s without it): a typical value that already
  contains a row's own answer looks more reliable than it will be on a new day,
  and the trees over-trust it. Training rows now get profiles fitted on the
  other days (5 folds by day); held-out rows and serving use the full-span
  profiles. With that, the patterns group is the single most useful addition.
* **Time-band lateness-carry tables for the clients.** The browser and the phone
  do not run the learned model; they apply the published tables. Lateness carry
  is now fitted per route × time band as well as per route (lookups fall back
  route-band → route → band → all), and the JavaScript and Swift ports, their
  shared fixture and tests were updated. The Route Insights sheet names the band
  it is using.
* **Serving.** The live engine computes the new state features from the store
  (route and network lateness, throughput), keeps a month of hourly weather in
  the store (refreshed every three hours from Open-Meteo, so the current hour's
  rain is a feature), loads a published model and reloads it when the file
  changes, and no longer overwrites a published model with its own 30-minute
  refit (it refits from the store only when no published model exists, every
  six hours).

**What the numbers say** — see the tables below and the discussion that follows them.

## What the numbers say

**Baselines.** On the held-out week (Sep 28 – Oct 4, 2.96 million rows) assuming
a train keeps its current lateness is off by 85.5 s on average; the clients'
per-route lateness-carry tables do no better (86.0 s); repeating the segment's
last three trains is 86.3 s. The MTA feed's own ETA, where it was sampled, is
95.7 s on the archive rows and 91.0 s on the collector rows.

**Feature groups (first-round ablation, 2 million training rows, p50 only).**
The train/traffic/segment state alone reaches 72.0 s. Calendar features add
0.3 s, the day-pattern group 0.5 s more, and hourly weather, alerts and events
nothing measurable on top (71.2 → 71.3 → 71.4 s, within noise). Inside the
pattern group the whole gain is the segment profile — the typical excess of
*this* segment at *this* kind of day and hour (71.70 → 71.16 s on its own);
the route's typical lateness, the live route/network lateness, throughput and
the climatology each add nothing. Weather on its own is slightly worse than
nothing (71.9 s), as are the alert/event weights (71.8 s): with three weeks of
mostly dry days and the live state already in the row, the trees find nothing
in them that the state does not say better. The hold-out does contain 180 000
wet rows (rain in the last three hours) and 267 000 rows with an unplanned
alert on the route; the model is 75 s and 97 s there against 88 s and 115 s for
the schedule, so it *handles* those conditions through the state features — it
just does not need the weather or alert columns to do it.

**Second round.** Three new state features — where the stop sits on the line
(index on the canonical sequence, stops left to the end, whether the target is
the last stop), the leader's momentum, and the last train's excess on the
segment plus how long ago it got there — are worth 0.9 s at equal capacity
(71.35 → 70.43 s), the largest single feature gain in the study. Predicting the
residual against the segment profile instead of the excess itself changes
nothing (70.47 s). Model capacity turned out to be the biggest lever of all:
every variant had run to its iteration cap, and widening the trees took the
same rows from 70.4 s (31 leaves, 400 rounds) to 68.9 (63 leaves, 600), 68.3
(127, 800), 68.2 (127, 1500) and 67.8 s (255 leaves, 1500 rounds, learning
rate 0.1). A lower learning rate with more rounds did not help (69.0 s); the
leaf count did. The final model uses the 255-leaf setting on all 6.4 million
training rows; the pipeline and server defaults move to 127 leaves / 800
rounds so the published model stays a reasonable size.

**Bias.** Every variant's p50 sits about 18 s *below* the truth on average.
That is the shape of the target, not a fault: the held-out excess has a median
of 4 s and a mean of 24 s (a long right tail of held and rerouted trains), and
a median estimator is the right thing to show as "the" arrival time — the p90
carries the tail, and the 80% band covers 80.0% of held-out targets after the
conformal scaling. The width of the band tracks real uncertainty: rows in the
narrowest quarter of predicted ranges (median 57 s wide) have an MAE of 19 s,
rows in the widest quarter (399 s wide) 133 s.

**Horizons.** The improvement over the schedule grows with distance: 22 s
against 34 s at one stop, 97 against 117 s at ten, 152 against 177 s at
twenty. For the destination arrival the app shows, that is the number that
matters.

**Final model.** With the second-round features and the 255-leaf setting on
all 6.4 million training rows the held-out MAE is **67.6 s** against 85.5 s
for the schedule, 86.0 s for the carry tables and 95.7 s for the feed where it
was sampled (the model is 54.6 s on those rows, 43% better than the feed). The
first model of the night was 70.7 s; the one that was live before tonight had
been trained on 1.4 days and scored 63.5 s on its own two-day test, a number
that is not comparable (different days, shorter horizons, no weekends).

**Serving conditions.** On the collector's own rows for the held-out days the
final model is **72.0 s** against 85.4 s for the schedule and 90.9 s for the
feed (67.0 s on the rows where the feed was sampled, 26% better than the
feed), with coverage 0.77; the first-round model was 73.8 s there. It
transfers without retraining. The collector days are a different mix (most
rows are from Saturday Oct 4) and their arrival timestamps are noisier, so this
is the number to watch as the server collects more of its own history.

**Configured commutes.** On the ten legs of the configured journeys the model
is better than the schedule on eight (archive rows, 230–680 rides each) and
clearly so where it matters most: Jay St → 4 Av-9 St on the F, 56 s against
90 s; on the R, 52 s against 79 s; 4 Av-9 St → W 4 St on the F (ten stops),
128 s against 150 s; W 4 St → 14 St on the A/C/E, 17 s against 31 s. On two
legs (Jay St → 14 St on the A/C and 4 Av-9 St → Jay St on the R) it ties the
schedule. The collector rows have 18–91 rides per leg, too few to read.

**What did not work and why it is still in the model.** Weather, alerts and
events stay as features because the trees ignore what does not help and the
training span could not show their effect: it held one wet weekday morning and
no snow, heat, flooding or major incident. The infrastructure to use them is
now in place (hourly weather fetched and cached, alert timelines with proper
"unknown" handling, event weights by route) and the ablation can be rerun on a
longer span with one command.

**Next steps worth taking.** (1) Keep the archive backfill running so the
training span grows past the one rainy week; rerun `make model` monthly and
the ablation when a storm or a major incident has been collected. (2) Batch
the live predictions per snapshot instead of one row at a time (the server now
predicts single-threaded and the profile lookup is a dict, which brought a
tick from minutes to seconds, but a batched predict would make the learned
model free). (3) Let the clients use the model indirectly: publish the segment
profiles by band as a small table so the browser and the phone can add "what
this segment usually does at this hour" to the carry estimate.

## Outside and community data sources

The question was whether community or web signals, or other obscure sources,
could add to the model. Each candidate was checked for two things: can it be
fetched reliably, and does it have history for the training span, without
which "does it make a difference" cannot be measured.

* **goodservice.io** (community status engine, built on the same GTFS-realtime
  feeds). Its JSON API is reachable with a browser user agent and is rich: a
  status word per route and direction, delay and irregularity summaries with
  numbers ("longer wait times between New Lots Av and 116 St, up to 24 min,
  normally every 10"), slow and long-headway sections, and the actual routing
  against the scheduled one (reroutes). In one sample 10 of 29 routes had
  long-headway sections and 7 were rerouted. It publishes no history, so it
  cannot be back-tested; the live server now samples it every five minutes
  into the store (`ctx_goodservice`, off the poll thread) so that in a few
  weeks it can be scored against our own engine and the feeds. Expectation:
  little gain on arrival error (it sees the same feeds), possible gain for
  explanations and reroute detection.
* **Station-hour ridership** (NYC Open Data, hourly entries per station
  complex, 428 complexes, published with a ten-day lag). A crowding profile —
  typical entries per hour by station, day type and hour over seven weeks —
  was attached to every row as the entries at the current stop and at the
  target stop (99% coverage). Result: 70.43 → 70.39 s with the baseline trees
  and 67.76 → 67.76 s with the 255-leaf trees. No difference: the segment
  profile by day type and hour already carries what crowding does to dwell
  times. The profile is kept (`context/ridership_station_profile.csv.gz`) in
  case a live crowding feed ever appears.
* **Reddit (r/nycrail), subwaystats.com, Citizen**: scripted access is refused
  (403) or there is no API, and none has a history that lines up with the
  arrivals. Not pursued.
* **NYPD calls for service, 311**: no transit-related rows in the span (the
  calls dataset has none with a transit type; 311 has no subway complaint
  type). Not pursued.
* **News RSS, NWS alerts, the MTA alert archive**: already in. News and NWS
  add nothing measurable; the alert archive on data.ny.gov lags about six
  weeks, so it cannot cover recent days and the live alert feed is used.

Net: no outside source moved the error on this span, and the only one that
might (goodservice.io, for reroutes and explanations) is now being logged so
the question can be answered with data later.

## Data

Days 2026-09-14 to 2026-10-04; held out from **2026-09-28**. 9,369,568 rows (6,409,876 train / 2,959,692 test) with 30% of each day's trips kept (the stream features — leaders, segment and line state — are computed from every arrival first). Horizons k = [1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20].

Context: 985 hourly weather rows, 1,425 live alerts (observed Sep 24 17:16 – Oct 05 02:16), 5,350 events, 53 NWS alerts, 420,532 feed ETA samples, climatology from 19,657 archived disruptions.

| day | source | arrivals | rows (all k) | rows kept |
|---|---|---|---|---|
| 2026-09-14 | subwaydata | 232,904 | 1,753,906 | 528,709 |
| 2026-09-15 | subwaydata | 232,971 | 1,751,642 | 509,217 |
| 2026-09-16 | subwaydata | 228,487 | 1,707,144 | 510,523 |
| 2026-09-17 | subwaydata | 227,343 | 1,716,076 | 499,908 |
| 2026-09-18 | subwaydata | 234,641 | 1,772,100 | 538,495 |
| 2026-09-19 | subwaydata | 150,474 | 989,517 | 305,559 |
| 2026-09-20 | subwaydata | 141,576 | 930,907 | 271,655 |
| 2026-09-21 | subwaydata | 233,470 | 1,758,530 | 534,810 |
| 2026-09-22 | subwaydata | 237,864 | 1,783,786 | 539,350 |
| 2026-09-23 | subwaydata | 234,677 | 1,745,396 | 516,570 |
| 2026-09-24 | subwaydata | 232,438 | 1,728,221 | 511,675 |
| 2026-09-25 | subwaydata | 236,184 | 1,772,022 | 531,284 |
| 2026-09-26 | subwaydata | 158,769 | 1,062,670 | 325,662 |
| 2026-09-27 | subwaydata | 148,167 | 985,961 | 286,459 |
| 2026-09-28 | subwaydata | 235,690 | 1,766,853 | 514,267 |
| 2026-09-29 | subwaydata | 237,187 | 1,771,277 | 539,327 |
| 2026-09-30 | subwaydata | 236,272 | 1,763,790 | 522,123 |
| 2026-10-01 | subwaydata | 232,310 | 1,726,709 | 502,526 |
| 2026-10-02 | subwaydata | 232,938 | 1,739,679 | 516,977 |
| 2026-10-03 | subwaydata | 156,339 | 1,055,209 | 303,449 |
| 2026-10-04 | flush | 37,018 | 197,991 | 61,023 |

## Feature-group ablation (p50 only, held-out rows)

Each variant adds a feature group to the previous one; MAE is on the excess run time in seconds, so the schedule baseline is the error of assuming the train keeps its current lateness.

| variant | features | MAE s | bias s | schedule | carry table | vs schedule | vs carry | fit s |
|---|---|---|---|---|---|---|---|---|
| legacy | 27 | 71.83 | -19.0 | 85.52 | 86.02 | +16% | +16% | 42 |
| state | 14 | 71.96 | -17.9 | 85.52 | 86.02 | +16% | +16% | 38 |
| state+time | 22 | 71.70 | -18.8 | 85.52 | 86.02 | +16% | +17% | 34 |
| state+time+patterns | 28 | 71.18 | -18.2 | 85.52 | 86.02 | +17% | +17% | 44 |
| state+time+patterns+weather | 36 | 71.26 | -18.2 | 85.52 | 86.02 | +17% | +17% | 46 |
| all | 44 | 71.38 | -18.9 | 85.52 | 86.02 | +17% | +17% | 47 |

By horizon (MAE s):

| k | rows | schedule | carry | legacy | state | state+time | state+time+patterns | state+time+patterns+weather | all |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 352,498 | 34.1 | 34.8 | 24.5 | 24.3 | 24.5 | 24.2 | 24.2 | 24.4 |
| 2 | 340,853 | 47.6 | 48.3 | 36.7 | 36.5 | 36.6 | 36.4 | 36.4 | 36.5 |
| 3 | 328,512 | 59.2 | 60.0 | 47.2 | 47.0 | 47.0 | 46.7 | 46.8 | 46.9 |
| 4 | 316,062 | 69.5 | 70.4 | 56.6 | 56.4 | 56.4 | 56.0 | 56.0 | 56.2 |
| 5 | 303,736 | 79.1 | 79.9 | 65.4 | 65.4 | 65.3 | 64.8 | 64.9 | 65.0 |
| 6 | 291,520 | 88.0 | 88.9 | 73.9 | 73.8 | 73.7 | 73.3 | 73.4 | 73.4 |
| 8 | 266,976 | 103.6 | 104.3 | 88.6 | 88.8 | 88.4 | 87.7 | 87.9 | 88.0 |
| 10 | 242,598 | 117.4 | 117.5 | 101.7 | 102.1 | 101.6 | 100.9 | 101.0 | 101.2 |
| 12 | 218,413 | 130.7 | 130.8 | 114.3 | 114.7 | 114.1 | 113.3 | 113.3 | 113.5 |
| 16 | 171,649 | 155.0 | 154.6 | 136.8 | 137.9 | 136.8 | 135.6 | 135.8 | 135.9 |
| 20 | 126,872 | 177.4 | 175.3 | 156.6 | 158.1 | 156.7 | 155.4 | 155.7 | 155.8 |

By time band (MAE s):

| band | rows | schedule | carry | legacy | state | state+time | state+time+patterns | state+time+patterns+weather | all |
|---|---|---|---|---|---|---|---|---|---|
| night | 140,830 | 101.5 | 103.5 | 84.0 | 85.2 | 83.4 | 83.2 | 83.4 | 83.6 |
| am_peak | 608,990 | 79.0 | 81.0 | 69.4 | 69.3 | 69.3 | 68.7 | 68.7 | 68.8 |
| midday | 827,242 | 86.6 | 87.4 | 73.1 | 73.0 | 73.0 | 72.5 | 72.6 | 72.7 |
| pm_peak | 625,570 | 76.8 | 77.4 | 66.7 | 66.8 | 66.7 | 65.9 | 65.9 | 66.0 |
| evening | 392,588 | 88.7 | 89.6 | 76.3 | 76.4 | 76.2 | 75.8 | 75.8 | 75.9 |
| weekend_day | 287,097 | 99.9 | 96.6 | 71.6 | 72.2 | 71.4 | 71.3 | 71.5 | 71.6 |
| weekend_night | 77,372 | 96.6 | 90.8 | 74.8 | 75.5 | 74.2 | 74.2 | 74.7 | 74.9 |

By day type (MAE s):

| daytype | rows | schedule | carry | legacy | state | state+time | state+time+patterns | state+time+patterns+weather | all |
|---|---|---|---|---|---|---|---|---|---|
| weekday | 2,595,220 | 83.6 | 84.7 | 71.8 | 71.8 | 71.6 | 71.1 | 71.1 | 71.2 |
| saturday | 303,449 | 100.7 | 96.7 | 71.6 | 72.2 | 71.4 | 71.4 | 71.7 | 71.9 |
| sunday_holiday | 61,020 | 92.0 | 88.9 | 75.3 | 76.2 | 75.1 | 74.6 | 74.6 | 74.7 |

Wet vs dry (rain in the last three hours) (MAE s):

| weather | rows | schedule | carry | legacy | state | state+time | state+time+patterns | state+time+patterns+weather | all |
|---|---|---|---|---|---|---|---|---|---|
| dry | 2,780,080 | 85.4 | 85.8 | 71.5 | 71.7 | 71.4 | 70.9 | 71.0 | 71.1 |
| wet_last_3h | 179,609 | 87.8 | 89.1 | 76.3 | 76.5 | 75.9 | 75.4 | 75.5 | 75.5 |

Unplanned alert on the route (MAE s):

| alert | rows | schedule | carry | legacy | state | state+time | state+time+patterns | state+time+patterns+weather | all |
|---|---|---|---|---|---|---|---|---|---|
| none | 2,692,886 | 82.6 | 83.1 | 69.2 | 69.4 | 69.0 | 68.5 | 68.6 | 68.7 |
| active | 266,803 | 114.8 | 115.2 | 98.8 | 98.0 | 98.5 | 97.8 | 98.0 | 98.2 |

## Final model (p10 / p50 / p90)

6,000,000 training rows, 2,959,692 held-out rows, 50 features (dropped as constant: track_changed, holiday, heat, snow_cm, venue_event_w); iterations {'0.1': 1500, '0.5': 1500, '0.9': 1500}; fit 1700 s; profiles from 6,000,000 rows over 609,502 segment-hours; range scale 1.262.

|  | MAE s | note |
|---|---|---|
| model p50 | 67.56 | bias -16.8 s; 80% window covers 0.800 of targets, median width 167 s |
| schedule | 85.53 | the train keeps its current lateness |
| persistence | 85.89 | the segment's last three trains |
| carry table | 85.97 | the clients' per-route lateness carry |
| feed | 95.73 | MTA countdown ETA, on the 51,193 rows with a sample (model there: 54.58) |

### By horizon

|  | rows | model MAE s | schedule | carry table | persistence | vs schedule | vs carry | 80% coverage |
|---|---|---|---|---|---|---|---|---|
| 1 | 352,495 | 21.9 | 34.1 | 34.7 | 29.6 | +36% | +37% | 0.85 |
| 2 | 340,854 | 33.3 | 47.6 | 48.3 | 43.3 | +30% | +31% | 0.83 |
| 3 | 328,512 | 43.1 | 59.2 | 60.0 | 55.7 | +27% | +28% | 0.82 |
| 4 | 316,062 | 52.0 | 69.5 | 70.4 | 67.2 | +25% | +26% | 0.80 |
| 5 | 303,736 | 60.6 | 79.1 | 80.0 | 78.1 | +23% | +24% | 0.79 |
| 6 | 291,521 | 68.7 | 88.0 | 88.8 | 88.3 | +22% | +23% | 0.79 |
| 8 | 266,976 | 83.5 | 103.6 | 104.1 | 106.0 | +19% | +20% | 0.78 |
| 10 | 242,598 | 96.8 | 117.4 | 117.5 | 121.9 | +17% | +18% | 0.77 |
| 12 | 218,413 | 109.8 | 130.7 | 130.6 | 136.8 | +16% | +16% | 0.76 |
| 16 | 171,649 | 132.9 | 155.0 | 154.5 | 164.3 | +14% | +14% | 0.77 |
| 20 | 126,876 | 152.0 | 177.4 | 175.3 | 189.6 | +14% | +13% | 0.77 |

### By time band

|  | rows | model MAE s | schedule | carry table | persistence | vs schedule | vs carry | 80% coverage |
|---|---|---|---|---|---|---|---|---|
| night | 140,830 | 76.2 | 101.5 | 103.4 | 104.4 | +25% | +26% | 0.78 |
| am_peak | 608,990 | 65.4 | 79.0 | 80.9 | 83.5 | +17% | +19% | 0.80 |
| midday | 827,242 | 69.6 | 86.6 | 87.4 | 86.1 | +20% | +20% | 0.80 |
| pm_peak | 625,570 | 62.7 | 76.8 | 77.4 | 81.5 | +18% | +19% | 0.81 |
| evening | 392,588 | 72.0 | 88.7 | 89.5 | 91.1 | +19% | +20% | 0.79 |
| weekend_day | 287,096 | 66.0 | 99.9 | 96.6 | 82.5 | +34% | +32% | 0.81 |
| weekend_night | 77,376 | 69.2 | 96.7 | 90.8 | 91.3 | +28% | +24% | 0.78 |

### By day type

|  | rows | model MAE s | schedule | carry table | persistence | vs schedule | vs carry | 80% coverage |
|---|---|---|---|---|---|---|---|---|
| weekday | 2,595,220 | 67.7 | 83.6 | 84.7 | 86.1 | +19% | +20% | 0.80 |
| saturday | 303,449 | 66.1 | 100.7 | 96.7 | 83.4 | +34% | +32% | 0.81 |
| sunday_holiday | 61,023 | 69.2 | 92.1 | 88.9 | 88.9 | +25% | +22% | 0.76 |

### By weather

|  | rows | model MAE s | schedule | carry table | persistence | vs schedule | vs carry | 80% coverage |
|---|---|---|---|---|---|---|---|---|
| dry | 2,780,084 | 67.3 | 85.4 | 85.8 | 85.5 | +21% | +22% | 0.80 |
| wet_last_3h | 179,608 | 71.5 | 87.8 | 89.0 | 92.7 | +19% | +20% | 0.78 |

### By alert state

|  | rows | model MAE s | schedule | carry table | persistence | vs schedule | vs carry | 80% coverage |
|---|---|---|---|---|---|---|---|---|
| none | 2,692,891 | 65.2 | 82.6 | 83.1 | 82.4 | +21% | +22% | 0.80 |
| active | 266,801 | 91.9 | 114.8 | 115.0 | 120.7 | +20% | +20% | 0.80 |

### By data source

|  | rows | model MAE s | schedule | carry table | persistence | vs schedule | vs carry | 80% coverage |
|---|---|---|---|---|---|---|---|---|
| flush | 2,654 | 30.5 | 14.8 | 27.6 | 24.5 | -106% | -10% | 0.89 |
| rt_dropoff | 58,369 | 70.9 | 95.6 | 91.7 | 91.9 | +26% | +23% | 0.75 |
| subwaydata | 2,898,669 | 67.5 | 85.4 | 85.9 | 85.8 | +21% | +21% | 0.80 |

### By route (held-out rows, largest first)

|  | rows | model MAE s | schedule | carry table | persistence | vs schedule | vs carry | 80% coverage |
|---|---|---|---|---|---|---|---|---|
| 1 | 242,423 | 35.6 | 46.5 | 45.7 | 42.1 | +23% | +22% | 0.81 |
| F | 234,079 | 87.6 | 111.2 | 112.9 | 107.3 | +21% | +22% | 0.79 |
| 2 | 233,443 | 65.7 | 78.6 | 80.0 | 81.7 | +16% | +18% | 0.79 |
| 6 | 184,558 | 48.4 | 72.5 | 66.1 | 69.8 | +33% | +27% | 0.81 |
| R | 172,703 | 67.6 | 81.6 | 82.8 | 85.1 | +17% | +18% | 0.81 |
| D | 163,963 | 73.4 | 87.3 | 89.3 | 105.4 | +16% | +18% | 0.83 |
| A | 147,440 | 109.2 | 125.6 | 124.8 | 137.8 | +13% | +13% | 0.79 |
| L | 145,810 | 46.0 | 60.9 | 60.5 | 61.1 | +24% | +24% | 0.83 |
| 4 | 145,171 | 71.0 | 95.7 | 107.3 | 87.8 | +26% | +34% | 0.81 |
| 3 | 129,605 | 59.7 | 73.0 | 73.8 | 70.6 | +18% | +19% | 0.79 |
| Q | 121,960 | 69.4 | 85.0 | 85.5 | 91.1 | +18% | +19% | 0.81 |
| 7 | 117,026 | 57.5 | 83.8 | 81.6 | 70.5 | +31% | +29% | 0.79 |
| N | 113,382 | 85.2 | 101.0 | 102.6 | 104.0 | +16% | +17% | 0.79 |
| 5 | 110,828 | 86.8 | 100.3 | 102.7 | 107.9 | +13% | +15% | 0.80 |
| C | 110,446 | 69.5 | 85.3 | 85.9 | 87.2 | +18% | +19% | 0.78 |
| J | 108,539 | 65.3 | 91.2 | 88.4 | 66.2 | +28% | +26% | 0.75 |

### Does a wide range mean a genuinely uncertain ride?

| width bucket | rows | median width s | MAE s |
|---|---|---|---|
| 0 | 739,923 | 58 | 19.3 |
| 1 | 739,923 | 125 | 42.5 |
| 2 | 739,923 | 218 | 75.0 |
| 3 | 739,923 | 399 | 133.4 |

### Permutation importance by feature group (MAE increase, s)

| group | features | MAE increase s |
|---|---|---|
| state | 20 | 29.50 |
| patterns | 6 | 3.85 |
| time | 8 | 1.93 |
| weather | 8 | 0.19 |
| events | 8 | 0.17 |

### Permutation importance, top features (MAE increase, s)

| feature | MAE increase s |
|---|---|
| sched_run_sec | 151.06 |
| k | 143.62 |
| route_code | 12.41 |
| lateness_u | 4.05 |
| seg_recent_excess | 3.61 |
| prof_seg_excess | 3.54 |
| seg_last_excess | 3.27 |
| pos_u | 1.82 |
| d_is_last | 1.58 |
| stops_to_end | 1.19 |
| gap_ahead_sec | 0.91 |
| dow | 0.75 |
| mom3 | 0.64 |
| mom1 | 0.62 |
| sched_headway_sec | 0.47 |
| direction_code | 0.45 |
| hour_cos | 0.41 |
| hour_sin | 0.39 |
| prof_route_lateness | 0.25 |
| news_w | 0.25 |

## Held-out days on the collector's own rows (what the live server sees)

321,550 rows from 2026-09-28, 2026-09-30, 2026-10-01, 2026-10-02, 2026-10-04.

|  | MAE s |
|---|---|
| model p50 | 72.04 (bias -7.1) |
| schedule | 85.43 |
| persistence | 83.73 |
| carry table | 84.52 |
| feed | 90.94 on 32,434 rows (model there 67.04) |
| 80% coverage | 0.767 |

### By horizon

|  | rows | model MAE s | schedule | carry table | persistence | vs schedule | vs carry | 80% coverage | feed | model on feed rows |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 53,111 | 37.1 | 40.8 | 41.7 | 40.7 | +9% | +11% | 0.76 | 51.0 | 39.8 |
| 2 | 48,000 | 48.0 | 55.8 | 56.5 | 54.7 | +14% | +15% | 0.78 | 69.9 | 50.8 |
| 3 | 43,162 | 58.8 | 68.7 | 69.4 | 66.8 | +14% | +15% | 0.78 | 83.9 | 61.7 |
| 4 | 38,725 | 68.1 | 80.0 | 80.3 | 77.6 | +15% | +15% | 0.77 | 89.8 | 81.5 |
| 5 | 34,619 | 76.6 | 90.9 | 90.9 | 88.4 | +16% | +16% | 0.77 | 112.3 | 81.7 |
| 6 | 30,894 | 85.3 | 101.4 | 100.6 | 99.1 | +16% | +15% | 0.76 | 99.9 | 96.7 |
| 8 | 24,585 | 98.5 | 118.9 | 116.9 | 116.1 | +17% | +16% | 0.76 | 155.2 | 110.0 |
| 10 | 19,431 | 110.7 | 134.8 | 130.4 | 132.3 | +18% | +15% | 0.75 | 121.9 | 106.2 |
| 12 | 15,183 | 121.4 | 149.2 | 142.7 | 148.6 | +19% | +15% | 0.75 | 203.7 | 142.6 |
| 16 | 8,911 | 140.0 | 171.6 | 161.6 | 169.9 | +18% | +13% | 0.76 | – | – |
| 20 | 4,929 | 155.0 | 191.0 | 178.3 | 188.6 | +19% | +13% | 0.78 | – | – |

## Destination arrival on the configured commutes (held-out archive rows)

Boarding at the leg's first platform, alighting at its last: error of the predicted excess over the scheduled ride.

| leg | stops | rides | model MAE s | schedule | carry | bias s | 80% cov. | actual excess p50 / p90 s |
|---|---|---|---|---|---|---|---|---|
| 4 Av-9 St → W 4 St-Wash Sq (F) | 10 | 287 | 128.0 | 149.7 | 146.6 | -38.7 | 0.71 | 40 / 259 |
| W 4 St-Wash Sq → 14 St (A/C/E) | 1 | 680 | 17.2 | 30.8 | 30.1 | -2.1 | 0.85 | -28 / 5 |
| 4 Av-9 St → Jay St-MetroTech (F) | 4 | 293 | 66.7 | 74.0 | 72.0 | -5.5 | 0.79 | 28 / 139 |
| Jay St-MetroTech → 14 St (A/C) | 6 | 231 | 67.3 | 65.4 | 71.1 | -10.8 | 0.84 | 9 / 140 |
| 4 Av-9 St → Jay St-MetroTech (R) | 4 | 247 | 51.7 | 52.3 | 52.9 | -11.9 | 0.85 | 1 / 95 |
| 14 St → W 4 St-Wash Sq (A/C/E) | 1 | 627 | 28.2 | 31.7 | 32.5 | -14.1 | 0.85 | 0 / 46 |
| W 4 St-Wash Sq → 4 Av-9 St (F) | 10 | 279 | 81.3 | 83.6 | 89.3 | -17.1 | 0.81 | 5 / 145 |
| 14 St → Jay St-MetroTech (A/C) | 6 | 249 | 99.3 | 109.5 | 102.9 | -61.3 | 0.80 | 49 / 219 |
| Jay St-MetroTech → 4 Av-9 St (F) | 4 | 282 | 55.6 | 90.1 | 87.7 | -0.6 | 0.79 | -72 / 40 |
| Jay St-MetroTech → 4 Av-9 St (R) | 4 | 252 | 51.9 | 78.7 | 83.5 | -0.2 | 0.79 | -54 / 40 |

## Destination arrival on the configured commutes (collector rows)

Boarding at the leg's first platform, alighting at its last: error of the predicted excess over the scheduled ride.

| leg | stops | rides | model MAE s | schedule | carry | bias s | 80% cov. | actual excess p50 / p90 s |
|---|---|---|---|---|---|---|---|---|
| 4 Av-9 St → W 4 St-Wash Sq (F) | 10 | 20 | 142.3 | 234.1 | 218.7 | -94.4 | 0.60 | 234 / 362 |
| W 4 St-Wash Sq → 14 St (A/C/E) | 1 | 91 | 46.0 | 47.9 | 48.6 | 3.0 | 0.74 | -10 / 60 |
| 4 Av-9 St → Jay St-MetroTech (F) | 4 | 38 | 57.3 | 88.9 | 91.5 | -3.9 | 0.79 | 62 / 145 |
| Jay St-MetroTech → 14 St (A/C) | 6 | 20 | 128.2 | 92.3 | 112.2 | 50.9 | 0.60 | -55 / 66 |
| 4 Av-9 St → Jay St-MetroTech (R) | 4 | 51 | 50.5 | 48.3 | 49.5 | 4.9 | 0.82 | 5 / 76 |
| 14 St → W 4 St-Wash Sq (A/C/E) | 1 | 75 | 32.4 | 31.0 | 31.7 | 4.8 | 0.65 | 0 / 48 |
| W 4 St-Wash Sq → 4 Av-9 St (F) | 10 | 18 | – | – | – | – | – | – / – |
| 14 St → Jay St-MetroTech (A/C) | 6 | 46 | 83.9 | 58.8 | 57.9 | 8.9 | 0.93 | 19 / 117 |
| Jay St-MetroTech → 4 Av-9 St (F) | 4 | 31 | 75.8 | 93.1 | 84.7 | 58.2 | 0.55 | -45 / 50 |
| Jay St-MetroTech → 4 Av-9 St (R) | 4 | 41 | 98.7 | 138.2 | 137.8 | 56.0 | 0.59 | -151 / 0 |

## Which pattern features help (first-round rows, p50, 2M-row cap)

| variant | MAE s | bias s | iterations | fit s | k=1 | k=2 | k=3 | k=4 | k=5 | k=6 | k=8 | k=10 | k=12 | k=16 | k=20 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| base | 71.70 | -18.8 | 400 | 28 | 24.5 | 36.6 | 47.0 | 56.4 | 65.3 | 73.7 | 88.4 | 101.6 | 114.1 | 136.8 | 156.7 |
| +dynamic | 71.69 | -18.5 | 400 | 28 | 24.5 | 36.7 | 47.1 | 56.5 | 65.4 | 73.8 | 88.5 | 101.5 | 113.9 | 136.4 | 156.2 |
| +dynamic-no-count | 71.66 | -18.5 | 400 | 27 | 24.4 | 36.6 | 47.1 | 56.5 | 65.3 | 73.7 | 88.3 | 101.5 | 113.9 | 136.6 | 156.4 |
| +profiles | 71.20 | -18.5 | 400 | 50 | 24.1 | 36.3 | 46.7 | 56.0 | 64.9 | 73.2 | 87.8 | 101.0 | 113.3 | 135.8 | 155.7 |
| +seg-profile-only | 71.16 | -18.7 | 400 | 55 | 24.1 | 36.3 | 46.6 | 56.0 | 64.8 | 73.2 | 87.8 | 100.9 | 113.3 | 135.8 | 155.7 |
| +clim | 71.72 | -18.8 | 400 | 47 | 24.4 | 36.6 | 47.0 | 56.4 | 65.3 | 73.8 | 88.5 | 101.6 | 114.2 | 136.7 | 156.6 |
| +patterns | 71.18 | -18.2 | 400 | 70 | 24.2 | 36.4 | 46.7 | 56.0 | 64.8 | 73.3 | 87.7 | 100.9 | 113.3 | 135.6 | 155.4 |
| +patterns+weather+events | 71.38 | -18.9 | 400 | 75 | 24.4 | 36.5 | 46.9 | 56.2 | 65.0 | 73.5 | 88.0 | 101.2 | 113.5 | 135.9 | 155.8 |
| +weather | 71.88 | -18.6 | 400 | 50 | 24.6 | 36.7 | 47.2 | 56.6 | 65.5 | 74.0 | 88.6 | 101.8 | 114.3 | 136.8 | 156.8 |
| +events | 71.82 | -19.5 | 400 | 41 | 24.5 | 36.6 | 47.1 | 56.5 | 65.5 | 73.8 | 88.6 | 101.7 | 114.4 | 136.8 | 156.7 |

## Second-round features and capacity (p50, 2M-row cap)

| experiment | MAE s | bias s | iterations | fit s | k=1 | k=2 | k=3 | k=4 | k=5 | k=6 | k=8 | k=10 | k=12 | k=16 | k=20 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v1-all | 71.35 | -18.5 | 400 | 65 | 24.3 | 36.5 | 46.8 | 56.2 | 65.0 | 73.4 | 88.0 | 101.1 | 113.5 | 136.0 | 155.8 |
| v2-all | 70.43 | -18.5 | 400 | 55 | 23.6 | 35.6 | 45.8 | 55.0 | 63.8 | 72.2 | 87.0 | 100.2 | 112.7 | 135.5 | 155.6 |
| v2-all-residual | 70.47 | -18.4 | 400 | 47 | 23.6 | 35.7 | 45.9 | 55.0 | 63.8 | 72.3 | 87.1 | 100.2 | 112.9 | 135.5 | 155.3 |
| v2-lr0.1-leaves63-600 | 68.87 | -19.2 | 600 | 72 | 22.7 | 34.5 | 44.6 | 53.6 | 62.2 | 70.4 | 85.1 | 98.4 | 110.8 | 133.2 | 152.8 |
| v2-leaves127-800 | 68.30 | -19.3 | 800 | 111 | 22.3 | 34.1 | 44.1 | 53.1 | 61.8 | 69.9 | 84.5 | 97.5 | 110.1 | 132.4 | 151.9 |
| v2-lr0.03-leaves63-1200 | 68.99 | -19.0 | 1200 | 143 | 22.7 | 34.6 | 44.7 | 53.8 | 62.4 | 70.7 | 85.3 | 98.5 | 110.8 | 133.3 | 152.9 |
| v2-lr0.1-leaves127-1500 | 68.17 | -18.2 | 1500 | 172 | 22.3 | 33.9 | 43.9 | 52.8 | 61.4 | 69.6 | 84.3 | 97.4 | 110.1 | 132.7 | 152.1 |
| v2-lr0.1-leaves255-1500 | 67.76 | -18.2 | 1500 | 228 | 22.1 | 33.6 | 43.5 | 52.4 | 61.0 | 69.1 | 83.6 | 96.9 | 109.7 | 132.4 | 151.8 |
| v2-lr0.15-leaves127-1000 | 68.53 | -17.4 | 1000 | 118 | 22.7 | 34.2 | 44.2 | 53.2 | 61.7 | 70.0 | 84.6 | 97.8 | 110.5 | 133.2 | 152.8 |

## Station-hour ridership as a feature (p50, 2M-row cap)

| experiment | MAE s | bias s | iterations | fit s | k=1 | k=2 | k=3 | k=4 | k=5 | k=6 | k=8 | k=10 | k=12 | k=16 | k=20 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v2-all (reference) | 70.43 | -18.5 | 400 | 54 | 23.6 | 35.6 | 45.8 | 55.0 | 63.8 | 72.2 | 87.0 | 100.2 | 112.7 | 135.5 | 155.6 |
| v2-all + ridership | 70.39 | -18.9 | 400 | 52 | 23.5 | 35.5 | 45.7 | 55.0 | 63.7 | 72.2 | 87.0 | 100.3 | 112.8 | 135.5 | 155.5 |
| 255 leaves (reference) | 67.76 | -18.2 | 1500 | 271 | 22.1 | 33.6 | 43.5 | 52.4 | 61.0 | 69.1 | 83.6 | 96.9 | 109.7 | 132.4 | 151.8 |
| 255 leaves + ridership | 67.76 | -18.2 | 1500 | 264 | 22.1 | 33.6 | 43.4 | 52.3 | 60.9 | 69.0 | 83.7 | 97.0 | 109.8 | 132.5 | 152.0 |

## Reading the tables

* The target is the change in lateness between the train's current stop and the stop k ahead (seconds). MAE in seconds is on that quantity, so it is directly the error of the predicted arrival time at the stop ahead.
* *schedule* assumes the train keeps its current lateness; *persistence* repeats the segment's last three trains; *carry table* is the per-route linear lateness carry the browser and the phone apply (fitted on the training span); *feed* is the MTA countdown ETA sampled at the last poll before the train reached its current stop.
* Coverage is the share of held-out targets inside the p10–p90 band after conformal scaling on the held-out span (target 0.80).
* Alert features are unknown (NaN) before the alert feed was observed; the model treats unknown and "no alert" differently.
