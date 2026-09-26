# Subway reliability brief — Sep 26, 2026

## Lines losing the most time (9 days of observed trains)
- **F southbound**: 20% of stop arrivals ≥5 min late, 12.3 min lost per trip, worst at W 8 St-NY Aquarium (+120 s), peak headway CV 0.68
- **F northbound**: 21% of stop arrivals ≥5 min late, 11.1 min lost per trip, worst at 46 St (+48 s), peak headway CV 0.71
- **N southbound**: 22% of stop arrivals ≥5 min late, 8.9 min lost per trip, worst at 36 St (+80 s), peak headway CV 0.68
- Most reliable: **GS N** (0% ≥5 min late, 0.0 min lost per trip)

## Transfers and routes
- *4 Av-9 St → 14 St-8 Av (F to W 4 St, then A/C/E)*: F and A/C/E lateness at W 4 St-Wash Sq move together (Spearman +0.20, p=0.000, 567 bins). Both lines are disrupted (mean lateness ≥4 min) in the same 15 min 1.5× more often than chance.
- *4 Av-9 St → 14 St-8 Av (F to Jay St, cross-platform to A/C)*: F and A/C lateness at Jay St-MetroTech move together (Spearman +0.23, p=0.000, 584 bins). Both lines are disrupted (mean lateness ≥4 min) in the same 15 min 1.8× more often than chance.
- *4 Av-9 St → 14 St-8 Av (R to Jay St, then A/C)*: R and A/C lateness at Jay St-MetroTech move together (Spearman +0.05, p=0.232, 574 bins). Both lines are disrupted (mean lateness ≥4 min) in the same 15 min 2.7× more often than chance. The correlation peaks when A/C leads by 30 min, so A/C delays precede R delays.
- *14 St-8 Av → 4 Av-9 St (A/C/E to W 4 St, then F)*: A/C/E and F lateness at W 4 St-Wash Sq move together (Spearman +0.50, p=0.000, 547 bins). Both lines are disrupted (mean lateness ≥4 min) in the same 15 min 2.0× more often than chance.
- *14 St-8 Av → 4 Av-9 St (A/C to Jay St, cross-platform to F)*: A/C and F lateness at Jay St-MetroTech move together (Spearman +0.40, p=0.000, 564 bins). Both lines are disrupted (mean lateness ≥4 min) in the same 15 min 1.8× more often than chance.
- *14 St-8 Av → 4 Av-9 St (A/C to Jay St, then R)*: A/C and R lateness at Jay St-MetroTech move together (Spearman +0.31, p=0.000, 557 bins). Both lines are disrupted (mean lateness ≥4 min) in the same 15 min 2.0× more often than chance. The correlation peaks when R leads by 30 min, so R delays precede A/C delays.

## Terminals
- 33959 terminal turns matched: 17% of trips arrive ≥5 min late at the terminal and 19% of those leave late again; median 7.1 min recovered at the terminal

## Alerts
- 73 unplanned alerts studied: trains showed the problem 45 min before the alert was posted; peak excess 1.7 min; service back to normal 30 min after posting

## Disruption climatology
- Archive since 2020: 387 unplanned disruption events per week system-wide; most on A (54.4/wk), F (42.0/wk), 2 (40.1/wk); top cause rolling_stock (34%)

## Prediction model
- Arrival model: 63 s mean error vs 72 s for the schedule and 67 s for the MTA countdown ETA (6% better); 80% range covers 80% of outcomes; trained on 1,200,000 rows

## Holds
- 1565 holds per day (trains stopped ≥ 2.5 min at a station, origin terminals excluded) over 2 days; most held minutes at H19N (H), Franklin Av (FS), Atlantic Av-Barclays Ctr (D/N/R). Of 927 long holds (≥ 5 min), 45% had an unplanned alert for the line, posted a median 13 min after the hold began

## Live forecast accuracy
- Live forecasts scored against 3,180 observed arrivals (74 snapshots, 2 days): MTA feed 1.3 min mean error, model 1.3 min, simulation 2.4 min; the model was closer than the feed 48% of the time

---
Generated 2026-09-26T11:16-04:00 from the published analyses; details on each page of the app.