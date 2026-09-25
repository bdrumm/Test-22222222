"""Gradient-boosted downstream arrival model with calibrated p10/p50/p90 ranges.

Three histogram gradient-boosting regressors (quantile losses 0.1, 0.5, 0.9)
predict the excess run time from the train's current stop to a stop ``k``
stops ahead. Evaluation is strictly time-ordered: the last share of the history
is held out. Baselines reported alongside: the schedule (excess = 0), the
segment's recent excess (persistence), and the feed's own ETA where sampled.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .features import CATEGORICAL, FEATURES, NUMERIC, ROUTES, build_training_rows

QUANTILES = (0.1, 0.5, 0.9)


@dataclass
class ArrivalModel:
    quantile_models: dict = field(default_factory=dict)     # q -> fitted HistGradientBoostingRegressor
    features: list[str] = field(default_factory=lambda: list(FEATURES))
    card: dict = field(default_factory=dict)
    n_train: int = 0
    range_scale: float = 1.0        # conformal factor so the p10-p90 band covers 80% on held-out rows

    @property
    def ready(self) -> bool:
        return bool(self.quantile_models)

    def _frame(self, rows: pd.DataFrame) -> pd.DataFrame:
        X = rows.reindex(columns=self.features).astype(float)
        return X

    def predict(self, rows: pd.DataFrame) -> pd.DataFrame:
        """Returns p10/p50/p90 excess seconds per row (monotone-fixed)."""
        if rows is None or len(rows) == 0:
            return pd.DataFrame(columns=["p10", "p50", "p90"])
        X = self._frame(rows)
        out = pd.DataFrame(index=rows.index)
        for q, mdl in self.quantile_models.items():
            out[f"p{int(q * 100)}"] = mdl.predict(X)
        if {"p10", "p50", "p90"} <= set(out.columns):
            out["p10"] = out["p50"] + self.range_scale * np.minimum(out["p10"] - out["p50"], 0)
            out["p90"] = out["p50"] + self.range_scale * np.maximum(out["p90"] - out["p50"], 0)
        return out

    def save(self, path: str | Path) -> None:
        import joblib
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"quantile_models": self.quantile_models, "features": self.features, "card": self.card, "n_train": self.n_train,
                     "range_scale": self.range_scale}, path, compress=3)
        path.with_suffix(".card.json").write_text(json.dumps(self.card, default=str, indent=1))

    @classmethod
    def load(cls, path: str | Path) -> "ArrivalModel":
        import joblib
        d = joblib.load(path)
        return cls(d["quantile_models"], d["features"], d.get("card", {}), d.get("n_train", 0), d.get("range_scale", 1.0))


def _fit_one(X: pd.DataFrame, y: np.ndarray, q: float, max_iter: int, seed: int):
    from sklearn.ensemble import HistGradientBoostingRegressor
    cat = [X.columns.get_loc(c) for c in CATEGORICAL if c in X.columns]
    mdl = HistGradientBoostingRegressor(loss="quantile", quantile=q, max_iter=max_iter, learning_rate=0.06, max_leaf_nodes=31,
                                        min_samples_leaf=40, l2_regularization=1.0, categorical_features=cat or None,
                                        early_stopping=True, validation_fraction=0.15, n_iter_no_change=25, random_state=seed)
    mdl.fit(X, y)
    return mdl


def _mae(a, b):
    a = np.asarray(a, float); b = np.asarray(b, float); m = np.isfinite(a) & np.isfinite(b)
    return float(np.mean(np.abs(a[m] - b[m]))) if m.any() else float("nan")


def evaluate(model: ArrivalModel, test: pd.DataFrame) -> dict:
    """MAE by horizon and route, range coverage, and the baselines on the same rows."""
    if test.empty or not model.ready:
        return {"n": 0}
    pred = model.predict(test)
    y = test["delta_sec"].values
    out = {"n": int(len(test)), "mae_model": _mae(y, pred["p50"]), "mae_schedule": _mae(y, np.zeros(len(y))),
           "mae_persistence": _mae(y, test["seg_recent_excess"].fillna(0).values),
           "coverage_p10_p90": float(np.mean((y >= pred["p10"]) & (y <= pred["p90"]))),
           "range_width_median_sec": float(np.median(pred["p90"] - pred["p10"]))}
    fe = test["feed_excess"]
    has = fe.notna().values
    if has.sum() >= 30:
        out["n_with_feed"] = int(has.sum())
        out["mae_feed"] = _mae(y[has], fe.values[has])
        out["mae_model_on_feed_rows"] = _mae(y[has], pred["p50"].values[has])
        out["improvement_vs_feed"] = 1 - out["mae_model_on_feed_rows"] / out["mae_feed"] if out["mae_feed"] > 0 else None
    by_k = []
    for k, g in test.groupby("k"):
        p = pred.loc[g.index]
        by_k.append({"k": int(k), "n": int(len(g)), "mae_model": _mae(g["delta_sec"], p["p50"]), "mae_schedule": _mae(g["delta_sec"], 0 * g["delta_sec"]),
                     "mae_persistence": _mae(g["delta_sec"], g["seg_recent_excess"].fillna(0)),
                     "mae_feed": _mae(g["delta_sec"][g["feed_excess"].notna()], g["feed_excess"].dropna()) if g["feed_excess"].notna().sum() >= 20 else None,
                     "coverage": float(np.mean((g["delta_sec"] >= p["p10"]) & (g["delta_sec"] <= p["p90"])))})
    out["by_k"] = by_k
    by_route = []
    for r, g in test.groupby("route_id"):
        if len(g) < 50:
            continue
        p = pred.loc[g.index]
        by_route.append({"route": str(r), "n": int(len(g)), "mae_model": _mae(g["delta_sec"], p["p50"]), "mae_schedule": _mae(g["delta_sec"], 0 * g["delta_sec"]),
                         "coverage": float(np.mean((g["delta_sec"] >= p["p10"]) & (g["delta_sec"] <= p["p90"])))})
    out["by_route"] = sorted(by_route, key=lambda x: -x["n"])
    # calibration by predicted-range bucket: does a wide range mean a genuinely uncertain ride?
    width = (pred["p90"] - pred["p10"]).values
    if len(width) >= 200:
        qs = np.quantile(width, [0.25, 0.5, 0.75])
        bucket = np.digitize(width, qs)
        out["calibration_by_width"] = [{"bucket": int(b), "n": int((bucket == b).sum()), "width_median": float(np.median(width[bucket == b])),
                                        "mae": _mae(y[bucket == b], pred["p50"].values[bucket == b])} for b in range(4) if (bucket == b).sum() > 0]
    return out


def permutation_importance(model: ArrivalModel, test: pd.DataFrame, n_rows: int = 4000, seed: int = 3) -> list[dict]:
    if test.empty or not model.ready:
        return []
    rng = np.random.default_rng(seed)
    sample = test.sample(min(n_rows, len(test)), random_state=seed)
    base = _mae(sample["delta_sec"], model.predict(sample)["p50"])
    out = []
    for f in model.features:
        if sample[f].isna().all():
            continue
        s2 = sample.copy(); s2[f] = rng.permutation(s2[f].values)
        out.append({"feature": f, "mae_increase": _mae(s2["delta_sec"], model.predict(s2)["p50"]) - base})
    return sorted(out, key=lambda x: -x["mae_increase"])


def train_arrival_model(rows: pd.DataFrame, test_share: float = 0.2, max_iter: int = 400, seed: int = 7,
                        min_rows: int = 500) -> ArrivalModel:
    """Time-ordered split, fit the three quantile models, evaluate, build the model card."""
    model = ArrivalModel()
    if rows is None or len(rows) < min_rows:
        model.card = {"status": "insufficient_data", "n_rows": 0 if rows is None else int(len(rows)), "min_rows": min_rows}
        return model
    rows = rows.sort_values("t").reset_index(drop=True)
    cut = int(len(rows) * (1 - test_share))
    train, test = rows.iloc[:cut], rows.iloc[cut:]
    # constant or all-missing columns carry no information (and break histogram binning)
    usable = [f for f in FEATURES if train[f].nunique(dropna=True) >= 2]
    dropped = [f for f in FEATURES if f not in usable]
    model.features = usable
    X = train.reindex(columns=usable).astype(float); y = train["delta_sec"].values.astype(float)
    t0 = time.time()
    for q in QUANTILES:
        model.quantile_models[q] = _fit_one(X, y, q, max_iter, seed)
    model.n_train = int(len(train))
    # conformal calibration: scale the band so 80% of held-out targets fall inside it
    raw = model.predict(test)
    y = test["delta_sec"].values
    lo, hi = (raw["p10"] - raw["p50"]).values, (raw["p90"] - raw["p50"]).values
    resid = y - raw["p50"].values
    ratio = np.where(resid < 0, resid / np.minimum(lo, -1e-6), resid / np.maximum(hi, 1e-6))
    ratio = ratio[np.isfinite(ratio)]
    model.range_scale = float(np.clip(np.quantile(ratio, 0.8), 0.5, 3.0)) if ratio.size else 1.0
    ev = evaluate(model, test)
    ev["range_scale"] = model.range_scale
    imp = permutation_importance(model, test)
    model.card = {"status": "ok", "trained_at": time.time(), "n_train": int(len(train)), "n_test": int(len(test)),
                  "train_span": [float(train["t"].min()), float(train["t"].max())], "test_span": [float(test["t"].min()), float(test["t"].max())],
                  "fit_seconds": round(time.time() - t0, 1), "features": usable, "dropped_features": dropped, "evaluation": ev, "importance": imp[:15],
                  "iterations": {str(q): int(m.n_iter_) for q, m in model.quantile_models.items()},
                  "routes_seen": sorted(rows["route_id"].astype(str).unique().tolist())}
    return model
