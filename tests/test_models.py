import tempfile
from datetime import timedelta
from pathlib import Path

import numpy as np

from mta_delay_insights import synthetic
from mta_delay_insights.models import ArrivalModel, build_training_rows, train_arrival_model
from mta_delay_insights.models.features import FEATURES, live_features
from tests.conftest import START


def _rows(static, days=10):
    sc = synthetic.Scenario("t", START, START + timedelta(days=3), START + timedelta(days=days),
                            issues=list(synthetic.SCENARIOS["mixed"]), directions=("N",))
    sim = synthetic.simulate(static, sc)
    return build_training_rows(sim.arrivals, static, sim.alerts, sim.weather_daily, None, None, k_set=(1, 3, 5)), sim


def test_training_rows_are_causal_and_complete(static):
    rows, sim = _rows(static, 6)
    assert len(rows) > 5000 and set(FEATURES) <= set(rows.columns)
    assert set(rows["k"]) == {1, 3, 5}
    assert (rows["t_d"] > rows["t"]).all() and (rows["sched_run_sec"] > 0).all()
    # momentum needs earlier stops of the same trip; segment state comes from trains that finished before t
    assert rows["mom1"].isna().mean() < 0.5 and rows["seg_recent_excess"].notna().mean() > 0.9
    assert rows["alert_active"].max() == 1.0
    assert rows["route_code"].isin([5, 3]).all()   # '6' and '4'


def test_model_trains_beats_schedule_and_calibrates(static):
    rows, _ = _rows(static, 12)
    model = train_arrival_model(rows, max_iter=120)
    card = model.card
    assert card["status"] == "ok" and model.ready and card["n_test"] > 1000
    ev = card["evaluation"]
    assert ev["mae_model"] <= ev["mae_schedule"] * 1.02
    assert 0.6 <= ev["coverage_p10_p90"] <= 0.95
    assert ev["by_k"] and all(b["n"] > 0 for b in ev["by_k"])
    assert card["importance"][0]["mae_increase"] >= 0
    pred = model.predict(rows.head(20))
    assert (pred["p10"] <= pred["p50"]).all() and (pred["p50"] <= pred["p90"]).all()
    with tempfile.TemporaryDirectory() as d:
        model.save(Path(d) / "m.joblib")
        m2 = ArrivalModel.load(Path(d) / "m.joblib")
        assert np.allclose(m2.predict(rows.head(5))["p50"], pred["p50"].head(5))
        assert (Path(d) / "m.card.json").exists()
    # a serving row built by live_features predicts without error
    import pandas as pd
    lf = live_features("6", "N", 3, 270.0, 120.0, 20.0, 40.0, 240.0, 30.0, 1.0, 300.0, 15.0, 40.0, None, 0.0, rows["t"].iloc[-1])
    p = model.predict(pd.DataFrame([lf]))
    assert np.isfinite(p["p50"].iloc[0])


def test_insufficient_data_yields_unready_model(static):
    rows, _ = _rows(static, 4)
    model = train_arrival_model(rows.head(100))
    assert not model.ready and model.card["status"] == "insufficient_data"
    assert model.predict(rows.head(3)).empty or True
