# Subway reliability brief — Oct 10, 2026

## Lines losing the most time (16 days of observed trains)
- **F southbound**: 31% of stop arrivals ≥5 min late, 11.5 min lost per trip, worst at W 8 St-NY Aquarium (+104 s), peak headway CV 0.70
- **F northbound**: 27% of stop arrivals ≥5 min late, 11.6 min lost per trip, worst at 36 St (+56 s), peak headway CV 0.64
- **A northbound**: 27% of stop arrivals ≥5 min late, 9.9 min lost per trip, worst at Beach 60 St (+52 s), peak headway CV 0.68
- Most reliable: **GS N** (0% ≥5 min late, 0.0 min lost per trip)

## Transfers and routes
- *4 Av-9 St → 14 St-8 Av (F to W 4 St, then A/C/E)*: F and A/C/E lateness at W 4 St-Wash Sq move together (Spearman +0.20, p=0.000, 717 bins). Both lines are disrupted (mean lateness ≥4 min) in the same 15 min 1.4× more often than chance.
- *4 Av-9 St → 14 St-8 Av (F to Jay St, cross-platform to A/C)*: F and A/C lateness at Jay St-MetroTech move together (Spearman +0.16, p=0.000, 729 bins). Both lines are disrupted (mean lateness ≥4 min) in the same 15 min 1.2× more often than chance.
- *4 Av-9 St → 14 St-8 Av (R to Jay St, then A/C)*: R and A/C lateness at Jay St-MetroTech move together (Spearman +0.09, p=0.025, 680 bins). Both lines are disrupted (mean lateness ≥4 min) in the same 15 min 1.8× more often than chance. The correlation peaks when R leads by 15 min, so R delays precede A/C delays.
- *14 St-8 Av → 4 Av-9 St (A/C/E to W 4 St, then F)*: A/C/E and F lateness at W 4 St-Wash Sq move together (Spearman +0.55, p=0.000, 716 bins). Both lines are disrupted (mean lateness ≥4 min) in the same 15 min 1.8× more often than chance.
- *14 St-8 Av → 4 Av-9 St (A/C to Jay St, cross-platform to F)*: A/C and F lateness at Jay St-MetroTech move together (Spearman +0.41, p=0.000, 722 bins). Both lines are disrupted (mean lateness ≥4 min) in the same 15 min 1.5× more often than chance.
- *14 St-8 Av → 4 Av-9 St (A/C to Jay St, cross-platform to F)*: A trains that would reach Chambers St within 180 s behind a C in the weekend lose 379 s more there than free-running A trains (95% CI 7–1119 s, p=0.035); 18% of A trips are in that situation, and when that C is itself ≥3 min late the loss is 381 s.

## Terminals
- 37476 terminal turns matched: 21% of trips arrive ≥5 min late at the terminal and 16% of those leave late again; median 7.6 min recovered at the terminal

## Alerts
- 368 unplanned alerts studied: trains showed the problem 25 min before the alert was posted; peak excess 2.5 min; service back to normal 45 min after posting

## Disruption climatology
- Archive since 2020: 387 unplanned disruption events per week system-wide; most on A (54.4/wk), F (42.0/wk), 2 (40.1/wk); top cause rolling_stock (34%)

## Prediction model
- Arrival model: 67 s mean error vs 79 s for the schedule and 74 s for the MTA countdown ETA (33% better); 80% range covers 80% of outcomes; trained on 1,200,000 rows

## Holds
- 1986 holds per day (trains stopped ≥ 2.5 min at a station, origin terminals excluded) over 16 days; most held minutes at H19N (H), Franklin Av (FS), Canal St (N/Q/R). Of 8752 long holds (≥ 5 min), 46% had an unplanned alert for the line, posted a median 13 min after the hold began

## Live forecast accuracy
- Live forecasts scored against 24,232 observed arrivals (509 snapshots, 14 days): MTA feed 1.6 min mean error, model 1.5 min, simulation 3.1 min; the model was closer than the feed 52% of the time

---
Generated 2026-10-10T09:14-04:00 from the published analyses; details on each page of the app.