"""Gradient-boosted downstream arrival model with calibrated p10/p50/p90 ranges.

Three histogram gradient-boosting regressors (quantile losses 0.1, 0.5, 0.9)
predict the excess run time from the train's current stop to a stop ``k``
stops ahead. Capacity matters on this data: the sweep in docs/model_report.md
went from 31 leaves / 400 rounds to 255 leaves / 1500 rounds and gained 2.7 s
of MAE on 2 million rows, so the defaults here are the pipeline-sized middle
(127 leaves, 800 rounds, learning rate 0.1) and ``make model`` passes the
larger setting. Evaluation is strictly time-ordered: the last share of the history
(or everything after ``split_ts``) is held out. Baselines reported alongside:
the schedule (excess = 0), the segment's recent excess (persistence), the
per-route lateness carry the clients use, and the feed's own ETA where sampled.

Day patterns: before fitting, :class:`Profiles` learns from the training span
the typical excess of every segment by service day type and hour and the
typical lateness of every route then, with hierarchical shrinkage (segment →
route/k → zero). Those two numbers join the feature row, so the trees see
"how this segment usually runs at this time of this kind of day" directly and
the held-out span never leaks into them.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from ..sources.gtfs_static import NY_TZ
from .context import BANDS
from .features import CATEGORICAL, FEATURES, GROUPS, NUMERIC, PROFILE_FEATURES, ROUTES, build_training_rows

QUANTILES = (0.1, 0.5, 0.9)
PROFILE_PRIOR_N = 20.0
PROFILE_FOLDS = 5


@dataclass
class Profiles:
    """Typical excess per segment and typical route lateness by (day type, hour), fitted on the training span."""
    seg: pd.DataFrame | None = None          # (u, d, daytype, hour) -> prof_seg_excess
    seg_route: pd.DataFrame | None = None    # (route_id, direction, k, daytype, hour) -> fallback
    seg_route_day: pd.DataFrame | None = None  # (route_id, direction, k, daytype) -> fallback
    route_lat: pd.DataFrame | None = None    # (route_id, direction, daytype, hour) -> prof_route_lateness
    route_lat_day: pd.DataFrame | None = None
    n_rows: int = 0

    @property
    def ready(self) -> bool:
        return self.seg is not None

    @staticmethod
    def _keyed(rows: pd.DataFrame) -> pd.DataFrame:
        r = rows[["u", "d", "route_id", "direction", "k", "daytype", "hour", "lateness_u", "delta_sec"]].copy()
        r["hour"] = np.floor(r["hour"].astype(float)).clip(0, 23).astype(int)
        r["daytype"] = r["daytype"].astype(int); r["k"] = r["k"].astype(int)
        r["route_id"] = r["route_id"].astype(str); r["direction"] = r["direction"].astype(str)
        return r

    @staticmethod
    def _shrink(df: pd.DataFrame, keys: list[str], value: str, prior: pd.Series | float, name: str, n: float = PROFILE_PRIOR_N) -> pd.DataFrame:
        g = df.groupby(keys, sort=False)[value].agg(["mean", "count"]).reset_index()
        if isinstance(prior, pd.Series):
            p = g.merge(prior.rename("_prior").reset_index(), on=list(prior.index.names), how="left")["_prior"].fillna(0.0).values
        else:
            p = float(prior)
        g[name] = (g["count"] * g["mean"] + n * p) / (g["count"] + n)
        return g[keys + [name]]

    def fit(self, rows: pd.DataFrame) -> "Profiles":
        if rows is None or rows.empty:
            return self
        r = self._keyed(rows)
        self.n_rows = int(len(r))
        day = self._shrink(r, ["route_id", "direction", "k", "daytype"], "delta_sec", 0.0, "v")
        self.seg_route_day = day
        day_s = day.set_index(["route_id", "direction", "k", "daytype"])["v"]
        hr = self._shrink(r, ["route_id", "direction", "k", "daytype", "hour"], "delta_sec", day_s, "v")
        self.seg_route = hr
        # the segment level needs the route/k level as its prior: attach it per row first
        rk = r.merge(hr.rename(columns={"v": "_p"}), on=["route_id", "direction", "k", "daytype", "hour"], how="left")
        g = rk.groupby(["u", "d", "daytype", "hour"], sort=False).agg(mean=("delta_sec", "mean"), count=("delta_sec", "size"), p=("_p", "first")).reset_index()
        g["v"] = (g["count"] * g["mean"] + PROFILE_PRIOR_N * g["p"].fillna(0.0)) / (g["count"] + PROFILE_PRIOR_N)
        self.seg = g[["u", "d", "daytype", "hour", "v"]]
        # route lateness: one observation per (trip, u), not one per k
        one = r.drop_duplicates(["route_id", "direction", "u", "daytype", "hour", "lateness_u"]) if "trip_key" not in rows else \
            rows.drop_duplicates(["trip_key", "u"]).pipe(self._keyed)
        rd = self._shrink(one, ["route_id", "direction", "daytype"], "lateness_u", 0.0, "v")
        self.route_lat_day = rd
        self.route_lat = self._shrink(one, ["route_id", "direction", "daytype", "hour"], "lateness_u", rd.set_index(["route_id", "direction", "daytype"])["v"], "v")
        return self

    def __getstate__(self):
        d = dict(self.__dict__)
        d.pop("_dicts", None)           # lookup dicts are rebuilt lazily after loading
        return d

    def _lookup(self) -> dict:
        """Tuple-keyed dicts of the tables, built once: a merge against 600 000 segment rows costs ~0.1 s per call,
        which the live server cannot afford for every (train, stop) pair."""
        d = self.__dict__.get("_dicts")
        if d is None:
            def to_dict(df, keys):
                return dict(zip(zip(*(df[k].values.tolist() for k in keys)), df["v"].values.tolist()))
            d = {"seg": to_dict(self.seg, ["u", "d", "daytype", "hour"]), "seg_route": to_dict(self.seg_route, ["route_id", "direction", "k", "daytype", "hour"]),
                 "seg_route_day": to_dict(self.seg_route_day, ["route_id", "direction", "k", "daytype"]),
                 "route_lat": to_dict(self.route_lat, ["route_id", "direction", "daytype", "hour"]), "route_lat_day": to_dict(self.route_lat_day, ["route_id", "direction", "daytype"])}
            self.__dict__["_dicts"] = d
        return d

    def apply(self, rows: pd.DataFrame) -> pd.DataFrame:
        """prof_seg_excess / prof_route_lateness aligned to ``rows.index`` (NaN when the model has no profiles)."""
        out = pd.DataFrame({c: np.full(len(rows), np.nan) for c in PROFILE_FEATURES}, index=rows.index)
        if not self.ready or rows.empty:
            return out
        if len(rows) <= 5000:
            d = self._lookup()
            r = self._keyed(rows.assign(delta_sec=0.0, lateness_u=rows.get("lateness_u", 0.0)))
            seg = np.full(len(r), np.nan); lat = np.full(len(r), np.nan)
            for i, (u, dd, rt, dr, k, dt, hr) in enumerate(zip(r["u"], r["d"], r["route_id"], r["direction"], r["k"], r["daytype"], r["hour"])):
                v = d["seg"].get((u, dd, dt, hr))
                if v is None:
                    v = d["seg_route"].get((rt, dr, k, dt, hr))
                if v is None:
                    v = d["seg_route_day"].get((rt, dr, k, dt))
                seg[i] = np.nan if v is None else v
                w = d["route_lat"].get((rt, dr, dt, hr))
                if w is None:
                    w = d["route_lat_day"].get((rt, dr, dt))
                lat[i] = np.nan if w is None else w
            out["prof_seg_excess"] = seg; out["prof_route_lateness"] = lat
            return out
        r = self._keyed(rows.assign(delta_sec=0.0, lateness_u=rows.get("lateness_u", 0.0)))
        r["_i"] = np.arange(len(r))
        m = r.merge(self.seg.rename(columns={"v": "s2"}), on=["u", "d", "daytype", "hour"], how="left")
        m = m.merge(self.seg_route.rename(columns={"v": "s1"}), on=["route_id", "direction", "k", "daytype", "hour"], how="left")
        m = m.merge(self.seg_route_day.rename(columns={"v": "s0"}), on=["route_id", "direction", "k", "daytype"], how="left")
        m = m.merge(self.route_lat.rename(columns={"v": "l1"}), on=["route_id", "direction", "daytype", "hour"], how="left")
        m = m.merge(self.route_lat_day.rename(columns={"v": "l0"}), on=["route_id", "direction", "daytype"], how="left")
        m = m.sort_values("_i")
        out["prof_seg_excess"] = m["s2"].fillna(m["s1"]).fillna(m["s0"]).values
        out["prof_route_lateness"] = m["l1"].fillna(m["l0"]).values
        return out


@dataclass
class ArrivalModel:
    quantile_models: dict = field(default_factory=dict)     # q -> fitted HistGradientBoostingRegressor
    features: list[str] = field(default_factory=lambda: list(FEATURES))
    card: dict = field(default_factory=dict)
    n_train: int = 0
    range_scale: float = 1.0        # conformal factor so the p10-p90 band covers 80% on held-out rows
    profiles: Profiles | None = None
    residual: bool = False          # the trees predict delta minus the segment's day-pattern profile

    @property
    def ready(self) -> bool:
        return bool(self.quantile_models)

    def _frame(self, rows: pd.DataFrame) -> pd.DataFrame:
        rows = self.with_profiles(rows)
        return rows.reindex(columns=self.features).astype(np.float32)

    def with_profiles(self, rows: pd.DataFrame) -> pd.DataFrame:
        """Fill the day-pattern features from the fitted profiles where the rows do not carry them yet."""
        need = [c for c in PROFILE_FEATURES if c in self.features]
        if not need or self.profiles is None or not self.profiles.ready or rows.empty:
            return rows
        have = all(c in rows.columns and rows[c].notna().all() for c in need)
        if have:
            return rows
        if not {"u", "d", "route_id", "direction", "k", "daytype", "hour"} <= set(rows.columns):
            return rows
        prof = self.profiles.apply(rows)
        rows = rows.copy()
        for c in need:
            cur = rows[c] if c in rows.columns else pd.Series(np.nan, index=rows.index)
            rows[c] = cur.where(cur.notna(), prof[c])
        return rows

    def predict(self, rows: pd.DataFrame) -> pd.DataFrame:
        """Returns p10/p50/p90 excess seconds per row (monotone-fixed)."""
        if rows is None or len(rows) == 0:
            return pd.DataFrame(columns=["p10", "p50", "p90"])
        rows = self.with_profiles(rows)
        X = rows.reindex(columns=self.features).astype(np.float32)
        base = rows["prof_seg_excess"].fillna(0.0).values.astype(float) if self.residual and "prof_seg_excess" in rows.columns else 0.0
        out = pd.DataFrame(index=rows.index)
        # single-row serving calls gain nothing from OpenMP threads and stall badly when another process (a training
        # run on the same machine) already keeps every core busy, so small batches predict single-threaded
        if len(X) < 2000:
            from threadpoolctl import threadpool_limits
            with threadpool_limits(limits=1):
                for q, mdl in self.quantile_models.items():
                    out[f"p{int(round(q * 100))}"] = mdl.predict(X) + base
        else:
            for q, mdl in self.quantile_models.items():
                out[f"p{int(round(q * 100))}"] = mdl.predict(X) + base
        if {"p10", "p50", "p90"} <= set(out.columns):
            out["p10"] = out["p50"] + self.range_scale * np.minimum(out["p10"] - out["p50"], 0)
            out["p90"] = out["p50"] + self.range_scale * np.maximum(out["p90"] - out["p50"], 0)
        return out

    def save(self, path: str | Path) -> None:
        import joblib
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"quantile_models": self.quantile_models, "features": self.features, "card": self.card, "n_train": self.n_train,
                     "range_scale": self.range_scale, "profiles": self.profiles, "residual": self.residual}, path, compress=3)
        path.with_suffix(".card.json").write_text(json.dumps(self.card, default=str, indent=1))

    @classmethod
    def load(cls, path: str | Path) -> "ArrivalModel":
        import joblib
        d = joblib.load(path)
        return cls(d["quantile_models"], d["features"], d.get("card", {}), d.get("n_train", 0), d.get("range_scale", 1.0), d.get("profiles"),
                   bool(d.get("residual", False)))


def _fit_one(X: pd.DataFrame, y: np.ndarray, q: float, max_iter: int, seed: int, learning_rate: float = 0.1, max_leaf_nodes: int = 127,
             min_samples_leaf: int = 100):
    from sklearn.ensemble import HistGradientBoostingRegressor
    cat = [X.columns.get_loc(c) for c in CATEGORICAL if c in X.columns]
    mdl = HistGradientBoostingRegressor(loss="quantile", quantile=q, max_iter=max_iter, learning_rate=learning_rate, max_leaf_nodes=max_leaf_nodes,
                                        min_samples_leaf=min_samples_leaf, l2_regularization=1.0, categorical_features=cat or None,
                                        early_stopping=True, validation_fraction=0.15, n_iter_no_change=25, random_state=seed)
    mdl.fit(X, y)
    return mdl


def _mae(a, b):
    a = np.asarray(a, float); b = np.asarray(b, float); m = np.isfinite(a) & np.isfinite(b)
    return float(np.mean(np.abs(a[m] - b[m]))) if m.any() else float("nan")


def fit_carry_baseline(train: pd.DataFrame, min_n: int = 30) -> dict:
    """What the clients' lateness-carry tables predict: delta = a + b * lateness_u per (route, k), shrunk to the all-routes fit."""
    out = {"all": {}, "by_route": {}}
    if train is None or train.empty:
        return out

    def fit(g: pd.DataFrame) -> tuple[float, float]:
        x = g["lateness_u"].values.astype(float); y = g["delta_sec"].values.astype(float)
        if len(g) < 3 or x.std() < 1e-6:
            return 0.0, float(y.mean()) if len(g) else 0.0
        b, a = np.polyfit(x, y, 1)
        return float(np.clip(b, -1.0, 0.5)), float(a)
    for k, g in train.groupby("k"):
        out["all"][int(k)] = fit(g)
    for (r, k), g in train.groupby(["route_id", "k"]):
        if len(g) >= min_n:
            out["by_route"][(str(r), int(k))] = fit(g)
    return out


def apply_carry_baseline(carry: dict, rows: pd.DataFrame) -> np.ndarray:
    pred = np.zeros(len(rows))
    if not carry or rows.empty:
        return pred
    ks = rows["k"].values.astype(int); rs = rows["route_id"].astype(str).values; x = rows["lateness_u"].values.astype(float)
    for i in range(len(rows)):
        ab = carry["by_route"].get((rs[i], ks[i])) or carry["all"].get(ks[i])
        if ab:
            pred[i] = ab[1] + ab[0] * x[i]
    return pred


def evaluate(model: ArrivalModel, test: pd.DataFrame, carry: dict | None = None) -> dict:
    """MAE by horizon, route, time band, weather and source; range coverage; and the baselines on the same rows."""
    if test.empty or not model.ready:
        return {"n": 0}
    pred = model.predict(test)
    y = test["delta_sec"].values
    has_band = {"p10", "p90"} <= set(pred.columns)
    out = {"n": int(len(test)), "mae_model": _mae(y, pred["p50"]), "bias_model": float(np.nanmean(pred["p50"].values - y)),
           "mae_schedule": _mae(y, np.zeros(len(y))), "mae_persistence": _mae(y, test["seg_recent_excess"].fillna(0).values)}
    if has_band:
        out["coverage_p10_p90"] = float(np.mean((y >= pred["p10"]) & (y <= pred["p90"])))
        out["range_width_median_sec"] = float(np.median(pred["p90"] - pred["p10"]))
    cb = apply_carry_baseline(carry, test) if carry else None
    pos = pd.Series(np.arange(len(test)), index=test.index)
    if cb is not None:
        out["mae_carry"] = _mae(y, cb)
    fe = test["feed_excess"]
    has = fe.notna().values
    if has.sum() >= 30:
        out["n_with_feed"] = int(has.sum())
        out["mae_feed"] = _mae(y[has], fe.values[has])
        out["mae_model_on_feed_rows"] = _mae(y[has], pred["p50"].values[has])
        out["improvement_vs_feed"] = 1 - out["mae_model_on_feed_rows"] / out["mae_feed"] if out["mae_feed"] > 0 else None

    def block(g: pd.DataFrame, p: pd.DataFrame) -> dict:
        d = {"n": int(len(g)), "mae_model": _mae(g["delta_sec"], p["p50"]), "mae_schedule": _mae(g["delta_sec"], 0 * g["delta_sec"]),
             "mae_persistence": _mae(g["delta_sec"], g["seg_recent_excess"].fillna(0))}
        if cb is not None:
            d["mae_carry"] = _mae(g["delta_sec"], cb[pos.loc[g.index].values])
        if has_band:
            d["coverage"] = float(np.mean((g["delta_sec"] >= p["p10"]) & (g["delta_sec"] <= p["p90"])))
        fe_g = g["feed_excess"]
        if fe_g.notna().sum() >= 20:
            d["mae_feed"] = _mae(g["delta_sec"][fe_g.notna()], fe_g.dropna())
            d["mae_model_on_feed_rows"] = _mae(g["delta_sec"][fe_g.notna()], p["p50"][fe_g.notna()])
        return d
    out["by_k"] = [dict(k=int(k), **block(g, pred.loc[g.index])) for k, g in test.groupby("k")]
    out["by_route"] = sorted([dict(route=str(r), **block(g, pred.loc[g.index])) for r, g in test.groupby("route_id") if len(g) >= 50], key=lambda x: -x["n"])
    if "band" in test.columns:
        out["by_band"] = [dict(band=BANDS[int(b)] if 0 <= int(b) < len(BANDS) else str(b), **block(g, pred.loc[g.index])) for b, g in test.groupby("band") if len(g) >= 50]
    if "daytype" in test.columns:
        out["by_daytype"] = [dict(daytype=["weekday", "saturday", "sunday_holiday"][int(d)], **block(g, pred.loc[g.index])) for d, g in test.groupby("daytype") if len(g) >= 50]
    if "precip_hr_mm" in test.columns and test["precip_hr_mm"].notna().any():
        wet = test["precip_3h_mm"].fillna(0) > 0.2
        out["by_weather"] = [dict(weather=lab, **block(g, pred.loc[g.index])) for lab, g in (("dry", test[~wet]), ("wet_last_3h", test[wet])) if len(g) >= 50]
    if "alert_active" in test.columns and test["alert_active"].notna().any():
        known = test["alert_active"].notna()
        out["by_alert"] = [dict(alert=lab, **block(g, pred.loc[g.index])) for lab, g in (("none", test[known & (test["alert_active"] == 0)]), ("active", test[known & (test["alert_active"] == 1)])) if len(g) >= 50]
    if "source" in test.columns and test["source"].nunique() > 1:
        out["by_source"] = [dict(source=str(s), **block(g, pred.loc[g.index])) for s, g in test.groupby("source") if len(g) >= 50]
    # calibration by predicted-range bucket: does a wide range mean a genuinely uncertain ride?
    if has_band:
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
    sample = model.with_profiles(test.sample(min(n_rows, len(test)), random_state=seed))
    base = _mae(sample["delta_sec"], model.predict(sample)["p50"])
    out = []
    for f in model.features:
        if f not in sample.columns or sample[f].isna().all():
            continue
        s2 = sample.copy(); s2[f] = rng.permutation(s2[f].values)
        out.append({"feature": f, "mae_increase": _mae(s2["delta_sec"], model.predict(s2)["p50"]) - base})
    return sorted(out, key=lambda x: -x["mae_increase"])


def group_importance(model: ArrivalModel, test: pd.DataFrame, n_rows: int = 4000, seed: int = 3) -> list[dict]:
    """Permute whole feature groups at once (correlated features share their importance otherwise)."""
    if test.empty or not model.ready:
        return []
    rng = np.random.default_rng(seed)
    sample = model.with_profiles(test.sample(min(n_rows, len(test)), random_state=seed))
    base = _mae(sample["delta_sec"], model.predict(sample)["p50"])
    out = []
    for name, feats in GROUPS.items():
        cols = [f for f in feats if f in model.features and f in sample.columns and not sample[f].isna().all()]
        if not cols:
            continue
        s2 = sample.copy()
        perm = rng.permutation(len(s2))
        for c in cols:
            s2[c] = s2[c].values[perm]
        out.append({"group": name, "n_features": len(cols), "mae_increase": _mae(s2["delta_sec"], model.predict(s2)["p50"]) - base})
    return sorted(out, key=lambda x: -x["mae_increase"])


def train_arrival_model(rows: pd.DataFrame, test_share: float = 0.2, max_iter: int = 800, seed: int = 7,
                        min_rows: int = 500, *, features: list[str] | None = None, quantiles=QUANTILES, split_ts: float | None = None,
                        fit_profiles: bool = True, importance: bool = True, learning_rate: float = 0.1, max_leaf_nodes: int = 127,
                        min_samples_leaf: int = 100, train_cap: int | None = None, residual: bool = False) -> ArrivalModel:
    """Time-ordered split, fit the quantile models, evaluate, build the model card."""
    model = ArrivalModel()
    if rows is None or len(rows) < min_rows:
        model.card = {"status": "insufficient_data", "n_rows": 0 if rows is None else int(len(rows)), "min_rows": min_rows}
        return model
    candidates = list(features or FEATURES)
    rows = rows.sort_values("t").reset_index(drop=True)
    if split_ts is not None:
        cut = int(np.searchsorted(rows["t"].values, float(split_ts)))
    else:
        cut = int(len(rows) * (1 - test_share))
    train, test = rows.iloc[:cut].copy(), rows.iloc[cut:].copy()
    if train_cap and len(train) > train_cap:
        train = train.sample(train_cap, random_state=seed).sort_values("t")
    if fit_profiles and any(c in candidates for c in PROFILE_FEATURES):
        model.profiles = Profiles().fit(train)
        # The training rows get out-of-fold profiles, fitted on the other days: a profile that already contains a
        # row's own target looks far more reliable than it will be on a new day, and the trees would over-trust it.
        # Held-out rows and serving use the profiles from the whole training span.
        days = pd.to_datetime(train["t"], unit="s", utc=True).dt.tz_convert(NY_TZ).dt.date
        uniq = sorted(days.unique())
        n_folds = min(PROFILE_FOLDS, len(uniq))
        if n_folds >= 2:
            fold_of = {d: i % n_folds for i, d in enumerate(uniq)}
            fold = days.map(fold_of).values
            for c in PROFILE_FEATURES:
                train[c] = np.nan
            for f in range(n_folds):
                m = fold == f
                pf = Profiles().fit(train[~m]).apply(train[m])
                for c in PROFILE_FEATURES:
                    train.loc[m, c] = pf[c].values
        else:
            pf = model.profiles.apply(train)
            for c in PROFILE_FEATURES:
                train[c] = pf[c].values
        prof = model.profiles.apply(test)
        for c in PROFILE_FEATURES:
            test[c] = prof[c].values
    # constant or all-missing columns carry no information (and break histogram binning)
    usable = [f for f in candidates if f in train.columns and train[f].nunique(dropna=True) >= 2]
    dropped = [f for f in candidates if f not in usable]
    model.features = usable
    X = train.reindex(columns=usable).astype(np.float32); y = train["delta_sec"].values.astype(float)
    if residual and "prof_seg_excess" in train.columns and model.profiles is not None:
        model.residual = True
        y = y - train["prof_seg_excess"].fillna(0.0).values.astype(float)
    t0 = time.time()
    for q in quantiles:
        model.quantile_models[q] = _fit_one(X, y, q, max_iter, seed, learning_rate, max_leaf_nodes, min_samples_leaf)
    model.n_train = int(len(train))
    fit_seconds = round(time.time() - t0, 1)
    carry = fit_carry_baseline(train)
    if {0.1, 0.5, 0.9} <= set(quantiles) and len(test):
        # conformal calibration: scale the band so 80% of held-out targets fall inside it
        raw = model.predict(test)
        yt = test["delta_sec"].values
        lo, hi = (raw["p10"] - raw["p50"]).values, (raw["p90"] - raw["p50"]).values
        resid = yt - raw["p50"].values
        ratio = np.where(resid < 0, resid / np.minimum(lo, -1e-6), resid / np.maximum(hi, 1e-6))
        ratio = ratio[np.isfinite(ratio)]
        model.range_scale = float(np.clip(np.quantile(ratio, 0.8), 0.5, 3.0)) if ratio.size else 1.0
    ev = evaluate(model, test, carry) if len(test) else {"n": 0}
    ev["range_scale"] = model.range_scale
    imp = permutation_importance(model, test) if importance and len(test) else []
    gimp = group_importance(model, test) if importance and len(test) else []
    model.card = {"status": "ok", "trained_at": time.time(), "n_train": int(len(train)), "n_test": int(len(test)),
                  "train_span": [float(train["t"].min()), float(train["t"].max())], "test_span": [float(test["t"].min()), float(test["t"].max())] if len(test) else None,
                  "fit_seconds": fit_seconds, "features": usable, "dropped_features": dropped, "evaluation": ev, "importance": imp[:25],
                  "group_importance": gimp, "quantiles": list(quantiles),
                  "iterations": {str(q): int(m.n_iter_) for q, m in model.quantile_models.items()},
                  "hyperparameters": {"max_iter": max_iter, "learning_rate": learning_rate, "max_leaf_nodes": max_leaf_nodes, "min_samples_leaf": min_samples_leaf, "residual": model.residual},
                  "profiles": {"ready": bool(model.profiles and model.profiles.ready), "n_rows": model.profiles.n_rows if model.profiles else 0,
                               "n_segments": int(len(model.profiles.seg)) if model.profiles and model.profiles.ready else 0},
                  "routes_seen": sorted(rows["route_id"].astype(str).unique().tolist())}
    return model
