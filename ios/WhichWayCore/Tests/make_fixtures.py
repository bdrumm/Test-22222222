"""Regenerate the Swift predictor fixture from the Python reference (run from the repository root).

    .venv/bin/python ios/WhichWayCore/Tests/make_fixtures.py
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mta_delay_insights import synthetic  # noqa: E402
from mta_delay_insights.realtime import client_model as cm  # noqa: E402
from mta_delay_insights.sources.gtfs_static import StaticGTFS  # noqa: E402
from tests.conftest import END, START  # noqa: E402
from tests.test_client_model import _arrivals, _line, _train  # noqa: E402


def main() -> None:
    import tempfile
    d = Path(tempfile.mkdtemp()) / "gtfs"
    static = StaticGTFS.load(synthetic.build_mini_gtfs(d, START - timedelta(days=1), END + timedelta(days=1)))
    a, now = _arrivals(static)
    rng = np.random.default_rng(3)
    es = pd.DataFrame([{"trip_key": r["trip_key"], "route_id": r["route_id"], "stop_id": r["stop_id"], "at_stop": "x", "at_ts": r["arrival_ts"] - 35 - h,
                        "stops_ahead": 2, "eta_ts": r["arrival_ts"] - 35 + float(rng.normal(0, 20))} for _, r in a.head(800).iterrows() for h in (100, 700, 2000)])
    dw = pd.DataFrame({"trip_key": [f"t{i}" for i in range(120)], "route_id": "6", "direction": "N", "stop_id": "633N", "stopped_from_ts": 1.0, "stopped_to_ts": 2.0,
                       "dwell_sec": np.r_[np.full(60, 220.0), np.full(40, 500.0), np.full(20, 1300.0)], "polls": 3})
    model = cm.fit_client_model(es, a, dw, static, "2026-01-01")
    line = _line()
    trains = [_train("held", 3, now, lateness=60.0, eff=250.0, sched=now + 40, pos={"status": "STOPPED_AT", "since_sec": 330, "holding": True, "stalled": False}),
              _train("follower", 1, now, lateness=-20.0, sched=now + 80), _train("leader", 6, now, lateness=400.0, eff=400.0, sched=now + 30, step=80.0),
              _train("nosched", 2, now, lateness=None, eff=None), _train("twin", 1, now, lateness=30.0, sched=now + 95, step=110.0)]
    trains[3]["effective_lateness_sec"] = None
    scenarios = list(cm.SCENARIOS)
    elapsed = [100, 200, 330, 1000, 5000]
    horizons = [-10, 0, 150, 700, 9000]
    fixture = {"input": {"trains": trains, "line": line, "model": model, "now": now, "scenarios": scenarios, "elapsed": elapsed, "horizons": horizons},
               "expected": {sc: cm.predict_line(trains, line, model, now, sc) for sc in scenarios},
               "remaining": [cm.remaining_hold(model["hold_survival"], e) for e in elapsed],
               "calibration": [cm.calibration_at(model, "6", h) for h in horizons]}
    out = Path(__file__).resolve().parent / "WhichWayCoreTests" / "Fixtures" / "predictor_fixture.json"
    out.write_text(json.dumps(fixture, indent=0, sort_keys=True))
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
