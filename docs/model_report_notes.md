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
