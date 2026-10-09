# Arrival model report

_Generated 2026-10-08 22:56 EDT from `pipeline/train_model.py`; tables are produced by `pipeline/model_report.py`._

## October 8 refresh: the same model on three more weeks of the Action's data

_Added 2026-10-08. The tables below this note are from this run; the October 5
run's discussion follows it, for the record._

**What this run is.** The hourly GitHub Action collects the feeds for 50
minutes of every hour it runs and backfills the subwaydata.nyc archive into
the data branch, pruned to a rolling 21 days (`network_retention_days` in
`pipeline/targets.json`). So the training set is now the archive for
**Sep 18 – Oct 4** (17 weekdays and weekends, 8.85 M rows built, 6 M used for
the fit) instead of Sep 13 – 27, and the hold-out is **Oct 5 – 7**, three full
days that neither this model nor the October 5 one had seen (Oct 6 had 404'd
for the Action's backfill; it was fetched by hand first). Same features, same
capacity (255 leaves, 1 500 rounds), no ablation this time.

**Did more data help?** A little, and in the right places. Scored on the
same 1.83 M held-out rows (`scripts/model_compare.py`, which runs an older
model over the rows a later run cached):

| model | trained on | MAE s | bias s | schedule | carry table | 80% coverage |
|---|---|---|---|---|---|---|
| October 5 | Sep 13 – 27 | 71.1 | −15.5 | 84.3 | 87.2 | 0.81 |
| **October 8** | Sep 18 – Oct 4 | **70.1** | −13.6 | 84.3 | 87.2 | 0.80 |

By horizon the two are identical to the second at k ≤ 6; the gains are at
k = 7 (81 → 75 s), k = 11 (108 → 103) and k = 16/20 (134/153 → 132/152),
which are the horizons of the rider's own legs (Hoyt – 14 St is 7 stops,
W 4 St – 7 Av is 11), added to the k set by `legs.json`. The one-second
headline gain is small against a ~14 s negative bias on both models: on
these three days trains ran a little later than either model expects, which
the day-pattern features are meant to absorb and only partly do with three
weeks of history. On the collector's own rows for the same days (4.7 M, the
data the live server actually has) the model is at 77.1 s against 89.5 s for
the schedule and 90.1 s for the feed's own ETA, which it improves to 59.9 s
on the rows where the feed had one.

**The rider's legs.** The commute-leg table now comes from the rider's real
legs (`data/trips/legs.json` from the trip review) besides the configured
journeys. On Oct 5 – 7 the model beats the schedule on every leg but the
4-stop F ride 4 Av-9 St → Jay St, where the schedule is already within
43 s; the largest gains are on the longer legs (Hoyt – 14 St 102 vs 116 s,
W 4 St – 7 Av 100 vs 112, 4 Av – W 4 St 95 vs 109). The per-leg biases are the
thing to watch: −33 s on Hoyt – 14 St and −32 s on Jay St – 4 Av-9 St
(the model runs early), +36 s on W 4 St – 7 Av and +22 s on 4 Av – W 4 St
(late). With 115 – 400 rows per leg over three days these are suggestive,
not settled; a steady sign over a few more refreshes would justify a
per-leg correction in the app's carry table.

**What the real rides changed, and what they cannot.** Thirteen recorded
trips cannot retrain a gradient-boosted model of the whole network, and this
run does not try. What they did show, through the trip review's own
tables, is where the end-to-end forecast loses time *around* the model: the
planner booked 3:00 for the change at Jay St-MetroTech and the rider made
it in 0:17 (0:56 at W 4 St against 3:00), because the MTA's transfers.txt
gives 180 s even between lines that share the same stop. The schedule
export now books no walk between lines at the same stop, and the phone's
learned change times replace the timetable's either way; see the README.
After the platform-timing fix of Oct 7 the boarding forecast landed within
±45 s of the felt pull-away on both Oct 8 rides. The model's own error on the
rider's legs is the table above; the rest of the door-to-door error was
the change.

**How to repeat it.** `make model` trains straight into `data/`; for a
comparison, train to a side folder with a row cache and a fixed hold-out,
then score the previous model on the same rows:

    .venv/bin/python -m pipeline.train_model --data-dir data-branch --db data/mta.sqlite \
        --out data/models_oct8 --cache data/models_oct8/rows.pkl --end 2026-10-07 --test-from 2026-10-05 \
        --skip-ablation --max-iter 1500 --max-leaf-nodes 255 --min-samples-leaf 200
    .venv/bin/python scripts/model_compare.py --rows data/models_oct8/rows.pkl --test-from 2026-10-05 \
        --model "Oct 5=data/models/arrival_oct5.joblib" --model "Oct 8=data/models_oct8/arrival.joblib"

The rows take about ten minutes to build and the fit about 35 on this Mac.
Give `--end` explicitly: the default takes today's partial day.

## October 5 run


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

Days 2026-09-18 to 2026-10-07; held out from **2026-10-05**. 10,678,600 rows (8,849,374 train / 1,829,226 test) with 30% of each day's trips kept (the stream features — leaders, segment and line state — are computed from every arrival first). Horizons k = [1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 16, 20].

Context: 1,079 hourly weather rows, 1,980 live alerts (observed Sep 24 17:16 – Oct 08 22:10), 6,350 events, 53 NWS alerts, 1,071,850 feed ETA samples, climatology from 19,657 archived disruptions.

| day | source | arrivals | rows (all k) | rows kept |
|---|---|---|---|---|
| 2026-09-18 | subwaydata | 234,641 | 2,078,441 | 631,501 |
| 2026-09-19 | subwaydata | 150,474 | 1,156,854 | 357,260 |
| 2026-09-20 | subwaydata | 141,576 | 1,088,505 | 317,631 |
| 2026-09-21 | subwaydata | 233,470 | 2,062,497 | 627,233 |
| 2026-09-22 | subwaydata | 237,864 | 2,092,017 | 632,595 |
| 2026-09-23 | subwaydata | 234,677 | 2,046,810 | 605,678 |
| 2026-09-24 | subwaydata | 232,438 | 2,026,687 | 600,010 |
| 2026-09-25 | subwaydata | 236,184 | 2,078,319 | 623,184 |
| 2026-09-26 | subwaydata | 158,769 | 1,244,130 | 381,375 |
| 2026-09-27 | subwaydata | 148,167 | 1,153,995 | 335,246 |
| 2026-09-28 | subwaydata | 235,690 | 2,072,212 | 603,040 |
| 2026-09-29 | subwaydata | 237,187 | 2,077,485 | 632,589 |
| 2026-09-30 | subwaydata | 236,272 | 2,068,618 | 612,361 |
| 2026-10-01 | subwaydata | 232,310 | 2,025,311 | 589,409 |
| 2026-10-02 | subwaydata | 232,938 | 2,040,561 | 606,348 |
| 2026-10-03 | subwaydata | 156,339 | 1,246,839 | 359,318 |
| 2026-10-04 | subwaydata | 144,502 | 1,144,320 | 334,596 |
| 2026-10-05 | subwaydata | 234,608 | 2,061,698 | 621,642 |
| 2026-10-06 | subwaydata | 229,799 | 2,005,833 | 592,593 |
| 2026-10-07 | subwaydata | 226,971 | 1,994,945 | 614,991 |

## Final model (p10 / p50 / p90)

6,000,000 training rows, 1,829,226 held-out rows, 50 features (dropped as constant: track_changed, holiday, heat, snow_cm, venue_event_w); iterations {'0.1': 1500, '0.5': 1500, '0.9': 1500}; fit 2027 s; profiles from 6,000,000 rows over 712,115 segment-hours; range scale 1.202.

|  | MAE s | note |
|---|---|---|
| model p50 | 70.09 | bias -13.6 s; 80% window covers 0.800 of targets, median width 179 s |
| schedule | 84.29 | the train keeps its current lateness |
| persistence | 91.43 | the segment's last three trains |
| carry table | 87.15 | the clients' per-route lateness carry |
| feed | 96.59 | MTA countdown ETA, on the 144,510 rows with a sample (model there: 55.37) |

### By horizon

|  | rows | model MAE s | schedule | carry table | persistence | vs schedule | vs carry | 80% coverage |
|---|---|---|---|---|---|---|---|---|
| 1 | 184,479 | 21.3 | 32.2 | 33.2 | 29.1 | +34% | +36% | 0.84 |
| 2 | 178,650 | 32.4 | 44.6 | 45.9 | 42.9 | +27% | +29% | 0.84 |
| 3 | 172,295 | 42.0 | 55.4 | 57.1 | 55.5 | +24% | +26% | 0.83 |
| 4 | 165,899 | 51.0 | 65.1 | 67.3 | 67.2 | +22% | +24% | 0.82 |
| 5 | 159,652 | 59.4 | 74.3 | 76.9 | 78.4 | +20% | +23% | 0.81 |
| 6 | 153,389 | 67.5 | 83.1 | 86.0 | 89.0 | +19% | +21% | 0.80 |
| 7 | 147,133 | 75.1 | 90.9 | 94.1 | 98.8 | +17% | +20% | 0.79 |
| 8 | 140,879 | 82.7 | 98.2 | 101.7 | 108.0 | +16% | +19% | 0.78 |
| 10 | 128,453 | 96.7 | 111.5 | 115.4 | 125.1 | +13% | +16% | 0.77 |
| 11 | 122,278 | 103.2 | 118.2 | 122.3 | 133.5 | +13% | +16% | 0.76 |
| 12 | 116,093 | 109.5 | 124.3 | 128.8 | 141.0 | +12% | +15% | 0.76 |
| 16 | 91,804 | 132.0 | 146.8 | 152.2 | 168.9 | +10% | +13% | 0.77 |
| 20 | 68,222 | 152.0 | 167.7 | 172.5 | 194.8 | +9% | +12% | 0.76 |

### By time band

|  | rows | model MAE s | schedule | carry table | persistence | vs schedule | vs carry | 80% coverage |
|---|---|---|---|---|---|---|---|---|
| night | 104,511 | 77.0 | 107.5 | 111.8 | 109.8 | +28% | +31% | 0.79 |
| am_peak | 407,324 | 69.6 | 81.6 | 83.9 | 88.8 | +15% | +17% | 0.82 |
| midday | 575,435 | 77.0 | 92.3 | 94.5 | 100.1 | +17% | +19% | 0.78 |
| pm_peak | 453,657 | 64.7 | 75.3 | 78.7 | 86.0 | +14% | +18% | 0.80 |
| evening | 288,299 | 63.1 | 77.7 | 81.4 | 79.8 | +19% | +23% | 0.81 |

### By day type

|  | rows | model MAE s | schedule | carry table | persistence | vs schedule | vs carry | 80% coverage |
|---|---|---|---|---|---|---|---|---|
| weekday | 1,829,226 | 70.1 | 84.3 | 87.2 | 91.4 | +17% | +20% | 0.80 |

### By weather

|  | rows | model MAE s | schedule | carry table | persistence | vs schedule | vs carry | 80% coverage |
|---|---|---|---|---|---|---|---|---|
| dry | 1,829,226 | 70.1 | 84.3 | 87.2 | 91.4 | +17% | +20% | 0.80 |

### By alert state

|  | rows | model MAE s | schedule | carry table | persistence | vs schedule | vs carry | 80% coverage |
|---|---|---|---|---|---|---|---|---|
| none | 1,409,672 | 62.9 | 76.6 | 79.3 | 80.2 | +18% | +21% | 0.80 |
| active | 419,554 | 94.1 | 110.0 | 113.5 | 129.0 | +14% | +17% | 0.79 |

### By route (held-out rows, largest first)

|  | rows | model MAE s | schedule | carry table | persistence | vs schedule | vs carry | 80% coverage |
|---|---|---|---|---|---|---|---|---|
| 1 | 142,862 | 32.8 | 38.3 | 40.9 | 42.5 | +14% | +20% | 0.82 |
| F | 138,182 | 97.0 | 116.5 | 118.6 | 126.9 | +17% | +18% | 0.80 |
| 2 | 134,929 | 62.6 | 68.8 | 74.2 | 78.1 | +9% | +16% | 0.79 |
| 6 | 118,700 | 47.9 | 64.5 | 62.6 | 66.8 | +26% | +23% | 0.80 |
| R | 110,302 | 72.3 | 82.0 | 84.4 | 88.3 | +12% | +14% | 0.78 |
| D | 107,612 | 83.6 | 93.7 | 98.4 | 119.5 | +11% | +15% | 0.81 |
| A | 103,126 | 109.3 | 126.6 | 132.1 | 137.5 | +14% | +17% | 0.79 |
| L | 90,994 | 39.6 | 55.2 | 54.8 | 52.0 | +28% | +28% | 0.83 |
| C | 83,848 | 62.6 | 70.5 | 76.7 | 81.9 | +11% | +18% | 0.80 |
| 4 | 80,281 | 63.1 | 66.4 | 93.5 | 77.2 | +5% | +33% | 0.81 |
| 5 | 76,862 | 89.0 | 90.3 | 95.1 | 103.6 | +1% | +6% | 0.78 |
| 3 | 74,642 | 57.6 | 63.5 | 66.2 | 73.0 | +9% | +13% | 0.82 |
| M | 71,033 | 75.2 | 114.5 | 113.6 | 121.6 | +34% | +34% | 0.82 |
| 7 | 67,427 | 43.9 | 66.9 | 67.2 | 57.9 | +34% | +35% | 0.83 |
| Q | 63,720 | 86.3 | 102.0 | 100.0 | 105.8 | +15% | +14% | 0.80 |
| N | 62,803 | 99.5 | 120.3 | 118.7 | 131.2 | +17% | +16% | 0.78 |

### Does a wide range mean a genuinely uncertain ride?

| width bucket | rows | median width s | MAE s |
|---|---|---|---|
| 0 | 457,307 | 60 | 20.3 |
| 1 | 457,306 | 136 | 45.3 |
| 2 | 457,306 | 231 | 77.0 |
| 3 | 457,307 | 405 | 137.7 |

### Permutation importance by feature group (MAE increase, s)

| group | features | MAE increase s |
|---|---|---|
| state | 20 | 29.01 |
| patterns | 6 | 3.06 |
| time | 8 | 1.17 |
| events | 8 | 0.36 |
| weather | 8 | -0.21 |

### Permutation importance, top features (MAE increase, s)

| feature | MAE increase s |
|---|---|
| sched_run_sec | 136.78 |
| k | 123.24 |
| route_code | 12.74 |
| lateness_u | 4.18 |
| seg_recent_excess | 3.15 |
| seg_last_excess | 2.56 |
| prof_seg_excess | 2.54 |
| pos_u | 2.34 |
| mom1 | 1.68 |
| d_is_last | 1.61 |
| stops_to_end | 1.39 |
| gap_ahead_sec | 1.15 |
| mom3 | 1.05 |
| route_recent_lateness | 0.76 |
| direction_code | 0.58 |
| sched_headway_sec | 0.42 |
| hour_sin | 0.31 |
| dest_recent_lateness | 0.30 |
| prof_route_lateness | 0.26 |
| leader_same_route | 0.26 |

## Held-out days on the collector's own rows (what the live server sees)

4,707,024 rows from 2026-10-05, 2026-10-06, 2026-10-07.

|  | MAE s |
|---|---|
| model p50 | 77.14 (bias -18.0) |
| schedule | 89.53 |
| persistence | 94.60 |
| carry table | 91.00 |
| feed | 90.11 on 422,185 rows (model there 59.91) |
| 80% coverage | 0.784 |

### By horizon

|  | rows | model MAE s | schedule | carry table | persistence | vs schedule | vs carry | 80% coverage | feed | model on feed rows |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 497,792 | 26.3 | 33.6 | 34.8 | 31.7 | +22% | +24% | 0.80 | 56.5 | 26.7 |
| 2 | 476,718 | 38.1 | 47.8 | 48.9 | 46.0 | +20% | +22% | 0.81 | 67.8 | 38.4 |
| 3 | 456,003 | 48.5 | 59.4 | 60.8 | 59.0 | +18% | +20% | 0.81 | 76.1 | 47.4 |
| 4 | 436,164 | 58.6 | 70.1 | 71.6 | 71.2 | +16% | +18% | 0.80 | 116.3 | 97.3 |
| 5 | 416,757 | 67.7 | 80.0 | 81.6 | 82.5 | +15% | +17% | 0.80 | 95.3 | 65.6 |
| 6 | 397,623 | 76.4 | 89.4 | 91.0 | 93.4 | +15% | +16% | 0.79 | 186.8 | 162.0 |
| 7 | 378,572 | 84.4 | 98.1 | 99.8 | 103.5 | +14% | +15% | 0.78 | 132.0 | 99.1 |
| 8 | 359,740 | 92.0 | 106.1 | 107.9 | 113.2 | +13% | +15% | 0.77 | 122.2 | 91.3 |
| 10 | 322,680 | 106.6 | 120.8 | 122.4 | 131.4 | +12% | +13% | 0.76 | 207.6 | 180.5 |
| 11 | 304,448 | 113.4 | 128.2 | 130.1 | 139.8 | +12% | +13% | 0.75 | 161.9 | 117.6 |
| 12 | 286,540 | 120.0 | 135.1 | 136.7 | 148.0 | +11% | +12% | 0.75 | 152.4 | 118.2 |
| 16 | 218,124 | 143.7 | 159.6 | 161.1 | 177.8 | +10% | +11% | 0.75 | 619.2 | 469.9 |
| 20 | 155,863 | 165.7 | 182.4 | 183.0 | 205.6 | +9% | +9% | 0.75 | – | – |

## Destination arrival on the configured commutes (held-out archive rows)

Boarding at the leg's first platform, alighting at its last: error of the predicted excess over the scheduled ride.

| leg | stops | rides | model MAE s | schedule | carry | bias s | 80% cov. | actual excess p50 / p90 s |
|---|---|---|---|---|---|---|---|---|
| Jay St-MetroTech → 14 St (A) | 6 | 258 | 87.5 | 99.4 | 103.0 | -24.7 | 0.81 | 5 / 158 |
| W 4 St-Wash Sq → 4 Av-9 St (F) | 10 | 115 | 93.3 | 97.9 | 111.6 | 13.6 | 0.74 | -25 / 141 |
| 6 Av → 8 Av (L) | 1 | 240 | 8.8 | 91.5 | 90.7 | 3.1 | 0.87 | 90 / 90 |
| 4 Av-9 St → Hoyt-Schermerhorn Sts (G) | 4 | 121 | 40.1 | 48.5 | 49.0 | -17.4 | 0.74 | 12 / 105 |
| Hoyt-Schermerhorn Sts → 14 St (A) | 7 | 258 | 102.2 | 116.0 | 120.3 | -33.3 | 0.79 | 5 / 185 |
| 14 St → W 4 St-Wash Sq (C) | 1 | 388 | 19.4 | 22.3 | 23.5 | -7.5 | 0.85 | 0 / 30 |
| W 4 St-Wash Sq → 7 Av (F) | 11 | 143 | 99.5 | 111.8 | 134.6 | 35.7 | 0.78 | -35 / 131 |
| W 4 St-Wash Sq → 14 St (A) | 1 | 412 | 18.2 | 30.2 | 30.6 | -6.3 | 0.85 | -25 / 4 |
| 4 Av-9 St → Jay St-MetroTech (F) | 4 | 153 | 54.6 | 66.7 | 61.7 | 11.5 | 0.84 | 25 / 135 |
| 4 Av-9 St → W 4 St-Wash Sq (F) | 10 | 153 | 95.2 | 109.0 | 110.5 | 22.1 | 0.82 | 10 / 195 |
| 4 Av-9 St → Jay St-MetroTech (R) | 4 | 116 | 45.5 | 43.1 | 45.4 | -5.1 | 0.89 | -5 / 75 |
| 14 St → Jay St-MetroTech (A/C) | 6 | 242 | 81.9 | 89.1 | 88.2 | -10.4 | 0.75 | 10 / 186 |
| Jay St-MetroTech → 4 Av-9 St (F) | 4 | 117 | 59.0 | 107.1 | 114.3 | 13.2 | 0.80 | -81 / 17 |
| Jay St-MetroTech → 4 Av-9 St (R) | 4 | 133 | 61.8 | 70.6 | 68.0 | -32.4 | 0.75 | -30 / 132 |

## Destination arrival on the configured commutes (collector rows)

Boarding at the leg's first platform, alighting at its last: error of the predicted excess over the scheduled ride.

| leg | stops | rides | model MAE s | schedule | carry | bias s | 80% cov. | actual excess p50 / p90 s |
|---|---|---|---|---|---|---|---|---|
| Jay St-MetroTech → 14 St (A) | 6 | 656 | 82.6 | 93.2 | 98.1 | -21.3 | 0.80 | 5 / 150 |
| W 4 St-Wash Sq → 4 Av-9 St (F) | 10 | 366 | 108.2 | 112.0 | 120.7 | 28.0 | 0.75 | -10 / 174 |
| 4 Av-9 St → Hoyt-Schermerhorn Sts (G) | 4 | 344 | 48.9 | 53.2 | 53.7 | -13.0 | 0.69 | 15 / 90 |
| Hoyt-Schermerhorn Sts → 14 St (A) | 7 | 664 | 88.4 | 106.0 | 110.8 | -21.7 | 0.81 | 10 / 174 |
| 14 St → W 4 St-Wash Sq (C) | 1 | 1113 | 23.3 | 24.9 | 26.6 | -6.8 | 0.77 | 0 / 34 |
| W 4 St-Wash Sq → 7 Av (F) | 11 | 376 | 105.0 | 108.0 | 123.8 | 22.2 | 0.76 | -25 / 144 |
| W 4 St-Wash Sq → 14 St (A) | 1 | 1103 | 23.7 | 32.9 | 33.3 | -3.4 | 0.79 | -25 / 10 |
| 4 Av-9 St → Jay St-MetroTech (F) | 4 | 415 | 70.0 | 80.5 | 75.6 | 1.2 | 0.79 | 28 / 177 |
| 4 Av-9 St → W 4 St-Wash Sq (F) | 10 | 390 | 124.1 | 133.8 | 133.7 | 18.0 | 0.74 | 15 / 215 |
| 4 Av-9 St → Jay St-MetroTech (R) | 4 | 363 | 46.7 | 48.1 | 49.9 | -7.3 | 0.83 | -2 / 80 |
| 14 St → Jay St-MetroTech (A/C) | 6 | 673 | 84.5 | 87.4 | 86.7 | -16.0 | 0.74 | 12 / 185 |
| Jay St-MetroTech → 4 Av-9 St (F) | 4 | 382 | 76.5 | 118.0 | 123.0 | 20.9 | 0.70 | -80 / 61 |
| Jay St-MetroTech → 4 Av-9 St (R) | 4 | 384 | 78.0 | 91.4 | 89.9 | -14.0 | 0.70 | -40 / 110 |

## Reading the tables

* The target is the change in lateness between the train's current stop and the stop k ahead (seconds). MAE in seconds is on that quantity, so it is directly the error of the predicted arrival time at the stop ahead.
* *schedule* assumes the train keeps its current lateness; *persistence* repeats the segment's last three trains; *carry table* is the per-route linear lateness carry the browser and the phone apply (fitted on the training span); *feed* is the MTA countdown ETA sampled at the last poll before the train reached its current stop.
* Coverage is the share of held-out targets inside the p10–p90 band after conformal scaling on the held-out span (target 0.80).
* Alert features are unknown (NaN) before the alert feed was observed; the model treats unknown and "no alert" differently.
