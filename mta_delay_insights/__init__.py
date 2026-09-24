"""MTA Delay Insights: a framework for diagnosing train arrival problems at a station.

The package is organised as a pipeline:

    sources/   -> fetch and normalise external feeds (GTFS static, GTFS-Realtime,
                  service alerts, NY Open Data performance datasets, weather)
    collect/   -> turn a stream of GTFS-Realtime snapshots into observed arrivals
    storage/   -> SQLite persistence for snapshots, arrivals, alerts, context data
    analysis/  -> station x line metrics, trend detection, cause attribution,
                  significance / rider-impact estimation, and recommendations
    cli.py     -> command line entry point (``mta-insights``)
"""

__version__ = "0.1.0"
