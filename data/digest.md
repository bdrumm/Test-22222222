# Subway reliability brief — Oct 03, 2026

## Lines losing the most time (10 days of observed trains)
- **A northbound**: 30% of stop arrivals ≥5 min late, 5.0 min lost per trip, worst at Dyckman St (+51 s), peak headway CV 0.81
- **A southbound**: 28% of stop arrivals ≥5 min late, 5.0 min lost per trip, worst at 72 St (+138 s), peak headway CV 0.61
- **F southbound**: 24% of stop arrivals ≥5 min late, 6.1 min lost per trip, worst at W 8 St-NY Aquarium (+64 s), peak headway CV 0.70
- Most reliable: **GS N** (0% ≥5 min late, 0.0 min lost per trip)

## Transfers and routes
- *4 Av-9 St → 14 St-8 Av (F to W 4 St, then A/C/E)*: F and A/C/E lateness at W 4 St-Wash Sq move together (Spearman +0.40, p=0.000, 127 bins). Both lines are disrupted (mean lateness ≥4 min) in the same 15 min 2.4× more often than chance.
- *4 Av-9 St → 14 St-8 Av (F to W 4 St, then A/C/E)*: F trains that would reach Carroll St within 180 s behind a G in the evening/night lose 47 s more there than free-running F trains (95% CI 9–85 s, p=0.026); 22% of F trips are in that situation.
- *4 Av-9 St → 14 St-8 Av (F to Jay St, cross-platform to A/C)*: F and A/C lateness at Jay St-MetroTech move together (Spearman +0.35, p=0.000, 132 bins). Both lines are disrupted (mean lateness ≥4 min) in the same 15 min 1.6× more often than chance. The correlation peaks when A/C leads by 30 min, so A/C delays precede F delays.
- *4 Av-9 St → 14 St-8 Av (F to Jay St, cross-platform to A/C)*: F trains that would reach Carroll St within 180 s behind a G in the evening/night lose 47 s more there than free-running F trains (95% CI 9–85 s, p=0.026); 22% of F trips are in that situation.
- *4 Av-9 St → 14 St-8 Av (R to Jay St, then A/C)*: R and A/C lateness at Jay St-MetroTech move together (Spearman +0.23, p=0.018, 103 bins). Both lines are disrupted (mean lateness ≥4 min) in the same 15 min 3.4× more often than chance. The correlation peaks when A/C leads by 30 min, so A/C delays precede R delays.
- *4 Av-9 St → 14 St-8 Av (R to Jay St, then A/C)*: A R arriving ≥3 min late at Jay St-MetroTech (mean 4.5 min late, n=13) costs riders 2.6 min more downstream than an on-time arrival: 3.4 min at the transfer and -0.8 min on the A/C ride.

## Terminals
- 5460 terminal turns matched: 21% of trips arrive ≥5 min late at the terminal and 22% of those leave late again; median 7.3 min recovered at the terminal

## Alerts
- 251 unplanned alerts studied: trains showed the problem 15 min before the alert was posted; peak excess 1.4 min; service back to normal 20 min after posting

## Disruption climatology
- Archive since 2020: 387 unplanned disruption events per week system-wide; most on A (54.4/wk), F (42.0/wk), 2 (40.1/wk); top cause rolling_stock (34%)

## Prediction model
- Arrival model: 56 s mean error vs 66 s for the schedule and 79 s for the MTA countdown ETA (31% better); 80% range covers 80% of outcomes; trained on 859,340 rows

## Holds
- 1907 holds per day (trains stopped ≥ 2.5 min at a station, origin terminals excluded) over 9 days; most held minutes at H19N (H), Franklin Av (FS), Canal St (N/Q/R). Of 4885 long holds (≥ 5 min), 46% had an unplanned alert for the line, posted a median 13 min after the hold began

## Live forecast accuracy
- Live forecasts scored against 16,746 observed arrivals (368 snapshots, 9 days): MTA feed 1.5 min mean error, model 1.5 min, simulation 3.2 min; the model was closer than the feed 52% of the time

---
Generated 2026-10-03T18:01-04:00 from the published analyses; details on each page of the app.