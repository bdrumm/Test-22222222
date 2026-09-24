"""Programmatic example: analyse a station from an existing collection database.

Run the collector first (see README), then:

    python examples/analyze_station.py --db data/mta.sqlite --gtfs data/gtfs_subway.zip \
        --station "Grand Central" --direction N --routes 4,5,6
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta

from mta_delay_insights.analysis import AnalysisRequest, analyze_station
from mta_delay_insights.sources.gtfs_static import NY_TZ, StaticGTFS
from mta_delay_insights.storage import Store


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/mta.sqlite")
    ap.add_argument("--gtfs", default="data/gtfs_subway.zip")
    ap.add_argument("--station", default="Grand Central")
    ap.add_argument("--direction", default="N")
    ap.add_argument("--routes", default="4,5,6")
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args()

    static = StaticGTFS.load(args.gtfs)
    store = Store(args.db)
    end = datetime.now(NY_TZ)
    req = AnalysisRequest(station=args.station, direction=args.direction, routes=args.routes.split(","),
                          window_start=end - timedelta(days=args.days), window_end=end)
    report = analyze_station(store, static, req)
    print(report.verdict)
    print("severity:", report.severity_score, report.severity_label)
    print("focus hours:", report.focus_hours)
    print("where:", [(r["cause"], r["score"]) for r in report.ranked_locations[:2]])
    print("why:", [(r["cause"], r["score"]) for r in report.ranked_causes[:3]])
    if report.impact:
        print(f"impact: {report.impact.passenger_hours_per_day:.0f} passenger-hours/day")
    for rec in report.recommendations[:5]:
        print(f"- P{rec.priority} [{rec.audience}] {rec.action}")


if __name__ == "__main__":
    main()
