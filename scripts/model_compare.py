"""Score an earlier arrival model on the rows a later training run built, so two models are compared on the same
held-out days (the later run's test rows). Each model is evaluated with the carry-table baseline fitted on the
later run's training rows.

    .venv/bin/python scripts/model_compare.py --rows data/models_oct8/rows.pkl --test-from 2026-10-05 \
        --model "Oct 5=data/models/arrival.joblib" --model "Oct 8=data/models_oct8/arrival.joblib" \
        --out data/models_oct8/compare.json

Prints MAE (model, schedule, carry table), 80% coverage and the MAE by horizon for each model.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mta_delay_insights.models.arrival import ArrivalModel, evaluate, fit_carry_baseline  # noqa: E402
from pipeline.train_model import _day_bounds  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True, help="the rows pickle a training run wrote with --cache")
    ap.add_argument("--test-from", required=True)
    ap.add_argument("--model", action="append", default=[], help="title=path.joblib")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    rows = pd.read_pickle(args.rows)
    split_ts = _day_bounds(date.fromisoformat(args.test_from))[0]
    train = rows[rows["t"] < split_ts]
    test = rows[rows["t"] >= split_ts]
    carry = fit_carry_baseline(train.sample(min(len(train), 2_000_000), random_state=1))
    print(f"rows: {len(train):,} train, {len(test):,} test (from {args.test_from})")
    out = {}
    for item in args.model:
        title, _, path = item.partition("=")
        model = ArrivalModel.load(path)
        missing = [f for f in model.features if f not in test.columns]
        if missing:
            print(f"{title}: rows lack {missing}; skipped")
            continue
        ev = evaluate(model, test, carry)
        out[title] = ev
        print(f"\n== {title} ({path}; trained {model.card.get('n_train', '?'):,} rows)")
        print(f"   MAE {ev['mae_model']:.1f} s  (schedule {ev['mae_schedule']:.1f}, carry table {ev.get('mae_carry', float('nan')):.1f},"
              f" persistence {ev['mae_persistence']:.1f})  bias {ev['bias_model']:+.1f}  80% coverage {ev.get('coverage_p10_p90', float('nan')):.3f}")
        by_k = ev.get("by_horizon") or ev.get("by_k") or []
        if by_k:
            print("   by horizon: " + "  ".join(f"k{b.get('k', b.get('horizon'))}:{b['mae_model']:.0f}" for b in by_k))
    if args.out:
        Path(args.out).write_text(json.dumps(out, default=str, indent=1))
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
