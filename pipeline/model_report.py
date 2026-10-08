"""Render ``docs/model_report.md`` from the training run's ``experiments.json`` (and the model card inside it).

    python -m pipeline.model_report --experiments data/models/experiments.json --notes docs/model_report_notes.md --out docs/model_report.md

The notes file (hand-written discussion) is inserted after the title; every table below it is generated.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from mta_delay_insights.sources.gtfs_static import NY_TZ


def _f(v, nd=1, suffix=""):
    if v is None:
        return "–"
    try:
        if isinstance(v, float) and v != v:
            return "–"
        return f"{float(v):.{nd}f}{suffix}"
    except (TypeError, ValueError):
        return str(v)


def _pct(a, b):
    """Relative improvement of a over b (positive = a is better) as a percent string."""
    try:
        if a is None or b is None or float(b) == 0:
            return "–"
        return f"{(1 - float(a) / float(b)) * 100:+.0f}%"
    except (TypeError, ValueError):
        return "–"


def table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)


def _block_rows(blocks: list[dict], key: str, with_feed: bool = False) -> list[list]:
    rows = []
    for b in blocks:
        r = [b.get(key), f"{b.get('n', 0):,}", _f(b.get("mae_model")), _f(b.get("mae_schedule")), _f(b.get("mae_carry")), _f(b.get("mae_persistence")),
             _pct(b.get("mae_model"), b.get("mae_schedule")), _pct(b.get("mae_model"), b.get("mae_carry")), _f(b.get("coverage"), 2)]
        if with_feed:
            r.append(_f(b.get("mae_feed")))
            r.append(_f(b.get("mae_model_on_feed_rows")))
        rows.append(r)
    return rows


BLOCK_HEADERS = ["", "rows", "model MAE s", "schedule", "carry table", "persistence", "vs schedule", "vs carry", "80% coverage"]


def render(exp: dict, notes: str | None = None) -> str:
    data = exp.get("data", {}); abl = exp.get("ablation", []); fin = exp.get("final", {})
    ev = fin.get("evaluation", {}); evc = fin.get("evaluation_collector", {})
    L = ["# Arrival model report", ""]
    if fin.get("trained_at"):
        L.append(f"_Generated {datetime.fromtimestamp(float(fin['trained_at']), NY_TZ).strftime('%Y-%m-%d %H:%M %Z')} from `pipeline/train_model.py`; "
                 f"tables are produced by `pipeline/model_report.py`._")
        L.append("")
    if notes:
        L += [notes.strip(), ""]
    # ---- data
    L += ["## Data", ""]
    days = data.get("days", [])
    L.append(f"Days {days[0]['day'] if days else '?'} to {days[-1]['day'] if days else '?'}; held out from **{data.get('test_from')}**. "
             f"{data.get('n_rows', 0):,} rows ({data.get('n_train', 0):,} train / {data.get('n_test', 0):,} test) with {int(float(data.get('sample', 1)) * 100)}% of each day's trips kept "
             f"(the stream features — leaders, segment and line state — are computed from every arrival first). Horizons k = {data.get('k_set')}.")
    L.append("")
    ctx = data.get("context", {})
    L.append(f"Context: {ctx.get('weather_hours', 0):,} hourly weather rows, {ctx.get('alerts', 0):,} live alerts "
             f"(observed {_span(ctx.get('alerts_coverage'))}), {ctx.get('events', 0):,} events, {ctx.get('nws', 0)} NWS alerts, "
             f"{ctx.get('eta_samples', 0):,} feed ETA samples, climatology from {ctx.get('climatology_events', 0):,} archived disruptions.")
    L.append("")
    L.append(table(["day", "source", "arrivals", "rows (all k)", "rows kept"],
                   [[d["day"], d.get("source") or "–", f"{d.get('arrivals', 0):,}", f"{d.get('rows_full', 0):,}", f"{d.get('rows', 0):,}" + (" (skipped)" if d.get("skipped") else "")] for d in days]))
    L.append("")
    # ---- ablation
    if abl:
        L += ["## Feature-group ablation (p50 only, held-out rows)", ""]
        L.append("Each variant adds a feature group to the previous one; MAE is on the excess run time in seconds, so the schedule baseline is the "
                 "error of assuming the train keeps its current lateness.")
        L.append("")
        L.append(table(["variant", "features", "MAE s", "bias s", "schedule", "carry table", "vs schedule", "vs carry", "fit s"],
                       [[a["variant"], a["n_features"], _f(a["mae_model"], 2), _f(a.get("bias_model"), 1), _f(a["mae_schedule"], 2), _f(a.get("mae_carry"), 2),
                         _pct(a["mae_model"], a["mae_schedule"]), _pct(a["mae_model"], a.get("mae_carry")), _f(a.get("fit_seconds"), 0)] for a in abl]))
        L.append("")
        ks = sorted({b["k"] for a in abl for b in a.get("by_k", [])})
        if ks:
            L += ["By horizon (MAE s):", ""]
            hdr = ["k", "rows", "schedule", "carry"] + [a["variant"] for a in abl]
            rows = []
            for k in ks:
                ref = next((b for b in abl[-1].get("by_k", []) if b["k"] == k), {})
                rows.append([k, f"{ref.get('n', 0):,}", _f(ref.get("mae_schedule")), _f(ref.get("mae_carry"))] +
                            [_f(next((b["mae_model"] for b in a.get("by_k", []) if b["k"] == k), None)) for a in abl])
            L.append(table(hdr, rows)); L.append("")
        for key, title in (("by_band", "By time band"), ("by_daytype", "By day type"), ("by_weather", "Wet vs dry (rain in the last three hours)"), ("by_alert", "Unplanned alert on the route")):
            labels = []
            for a in abl:
                for b in a.get(key, []):
                    lab = b[key[3:]] if key[3:] in b else b.get(key.replace("by_", ""))
                    if lab not in labels:
                        labels.append(lab)
            if not labels:
                continue
            L += [f"{title} (MAE s):", ""]
            hdr = [key[3:], "rows", "schedule", "carry"] + [a["variant"] for a in abl]
            rows = []
            for lab in labels:
                ref = next((b for b in abl[-1].get(key, []) if b.get(key[3:]) == lab), {})
                rows.append([lab, f"{ref.get('n', 0):,}", _f(ref.get("mae_schedule")), _f(ref.get("mae_carry"))] +
                            [_f(next((b["mae_model"] for b in a.get(key, []) if b.get(key[3:]) == lab), None)) for a in abl])
            L.append(table(hdr, rows)); L.append("")
    # ---- final
    if fin:
        L += ["## Final model (p10 / p50 / p90)", ""]
        L.append(f"{fin.get('n_train', 0):,} training rows, {fin.get('n_test', 0):,} held-out rows, {len(fin.get('features', []))} features "
                 f"(dropped as constant: {', '.join(fin.get('dropped_features', [])) or 'none'}); iterations {fin.get('iterations')}; "
                 f"fit {_f(fin.get('fit_seconds'), 0)} s; profiles from {fin.get('profiles', {}).get('n_rows', 0):,} rows over {fin.get('profiles', {}).get('n_segments', 0):,} segment-hours; "
                 f"range scale {_f(ev.get('range_scale'), 3)}.")
        L.append("")
        L.append(table(["", "MAE s", "note"], [
            ["model p50", _f(ev.get("mae_model"), 2), f"bias {_f(ev.get('bias_model'))} s; 80% window covers {_f(ev.get('coverage_p10_p90'), 3)} of targets, median width {_f(ev.get('range_width_median_sec'), 0)} s"],
            ["schedule", _f(ev.get("mae_schedule"), 2), "the train keeps its current lateness"],
            ["persistence", _f(ev.get("mae_persistence"), 2), "the segment's last three trains"],
            ["carry table", _f(ev.get("mae_carry"), 2), "the clients' per-route lateness carry"],
            ["feed", _f(ev.get("mae_feed"), 2), f"MTA countdown ETA, on the {ev.get('n_with_feed', 0):,} rows with a sample (model there: {_f(ev.get('mae_model_on_feed_rows'), 2)})"]]))
        L.append("")
        for key, title in (("by_k", "By horizon"), ("by_band", "By time band"), ("by_daytype", "By day type"), ("by_weather", "By weather"), ("by_alert", "By alert state"), ("by_source", "By data source")):
            if ev.get(key):
                L += [f"### {title}", "", table(BLOCK_HEADERS, _block_rows(ev[key], key[3:] if key != "by_k" else "k")), ""]
        if ev.get("by_route"):
            L += ["### By route (held-out rows, largest first)", "", table(BLOCK_HEADERS, _block_rows(ev["by_route"][:16], "route")), ""]
        if ev.get("calibration_by_width"):
            L += ["### Does a wide range mean a genuinely uncertain ride?", "",
                  table(["width bucket", "rows", "median width s", "MAE s"], [[c["bucket"], f"{c['n']:,}", _f(c["width_median"], 0), _f(c["mae"])] for c in ev["calibration_by_width"]]), ""]
        if fin.get("group_importance"):
            L += ["### Permutation importance by feature group (MAE increase, s)", "",
                  table(["group", "features", "MAE increase s"], [[g["group"], g["n_features"], _f(g["mae_increase"], 2)] for g in fin["group_importance"]]), ""]
        if fin.get("importance"):
            L += ["### Permutation importance, top features (MAE increase, s)", "",
                  table(["feature", "MAE increase s"], [[i["feature"], _f(i["mae_increase"], 2)] for i in fin["importance"][:20]]), ""]
    # ---- collector
    if evc:
        L += ["## Held-out days on the collector's own rows (what the live server sees)", ""]
        L.append(f"{evc.get('n', 0):,} rows from {', '.join(d['day'] for d in evc.get('days', []) if not d.get('skipped'))}.")
        L.append("")
        L.append(table(["", "MAE s"], [["model p50", _f(evc.get("mae_model"), 2) + f" (bias {_f(evc.get('bias_model'))})"], ["schedule", _f(evc.get("mae_schedule"), 2)],
                                       ["persistence", _f(evc.get("mae_persistence"), 2)], ["carry table", _f(evc.get("mae_carry"), 2)],
                                       ["feed", _f(evc.get("mae_feed"), 2) + f" on {evc.get('n_with_feed', 0):,} rows (model there {_f(evc.get('mae_model_on_feed_rows'), 2)})"],
                                       ["80% coverage", _f(evc.get("coverage_p10_p90"), 3)]]))
        L.append("")
        if evc.get("by_k"):
            L += ["### By horizon", "", table(BLOCK_HEADERS + ["feed", "model on feed rows"], _block_rows(evc["by_k"], "k", with_feed=True)), ""]
    # ---- legs
    for key, title in (("legs", "## Destination arrival on the configured commutes (held-out archive rows)"), ("legs_collector", "## Destination arrival on the configured commutes (collector rows)")):
        legs = [l for l in fin.get(key, []) if l.get("n")]
        if not legs:
            continue
        L += [title, "", "Boarding at the leg's first platform, alighting at its last: error of the predicted excess over the scheduled ride.", ""]
        L.append(table(["leg", "stops", "rides", "model MAE s", "schedule", "carry", "bias s", "80% cov.", "actual excess p50 / p90 s"],
                       [[f"{l['from_name']} → {l['to_name']} ({'/'.join(l['routes'])})", l["k"], l["n"], _f(l.get("mae_model_sec")), _f(l.get("mae_schedule_sec")), _f(l.get("mae_carry_sec")),
                         _f(l.get("bias_model_sec")), _f(l.get("coverage_p10_p90"), 2), f"{_f(l.get('actual_excess_median_sec'), 0)} / {_f(l.get('actual_excess_p90_sec'), 0)}"]
                        for l in legs]))
        L.append("")
    for name, lst in (exp.get("extra") or {}).items():
        if not lst:
            continue
        L += [f"## {name}", ""]
        key = "variant" if "variant" in lst[0] else "experiment"
        ks = sorted({int(k) for r in lst for k in (r.get("by_k") or {})})
        hdr = [key, "MAE s", "bias s", "iterations", "fit s"] + [f"k={k}" for k in ks]
        rows = []
        for r in lst:
            it = r.get("iters") or r.get("iterations") or {}
            rows.append([r[key], _f(r.get("mae"), 2), _f(r.get("bias"), 1), ", ".join(str(v) for v in it.values()) if isinstance(it, dict) else it, _f(r.get("sec"), 0)] +
                        [_f((r.get("by_k") or {}).get(str(k), (r.get("by_k") or {}).get(k)), 1) for k in ks])
        L.append(table(hdr, rows)); L.append("")
    L += ["## Reading the tables", "",
          "* The target is the change in lateness between the train's current stop and the stop k ahead (seconds). "
          "MAE in seconds is on that quantity, so it is directly the error of the predicted arrival time at the stop ahead.",
          "* *schedule* assumes the train keeps its current lateness; *persistence* repeats the segment's last three trains; *carry table* is the "
          "per-route linear lateness carry the browser and the phone apply (fitted on the training span); *feed* is the MTA countdown ETA sampled "
          "at the last poll before the train reached its current stop.",
          "* Coverage is the share of held-out targets inside the p10–p90 band after conformal scaling on the held-out span (target 0.80).",
          "* Alert features are unknown (NaN) before the alert feed was observed; the model treats unknown and \"no alert\" differently.", ""]
    return "\n".join(L)


def _span(cov):
    if not cov:
        return "never"
    try:
        a, b = (datetime.fromtimestamp(float(x), NY_TZ) for x in cov)
        return f"{a:%b %d %H:%M} – {b:%b %d %H:%M}"
    except Exception:
        return str(cov)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiments", default="data/models/experiments.json")
    ap.add_argument("--notes", default=None)
    ap.add_argument("--out", default="docs/model_report.md")
    ap.add_argument("--extra", action="append", default=[], help="title=path.json of an extra experiment list to tabulate")
    args = ap.parse_args(argv)
    exp = json.loads(Path(args.experiments).read_text())
    exp["extra"] = {}
    for item in args.extra:
        title, _, path = item.partition("=")
        if path and Path(path).exists():
            exp["extra"][title] = json.loads(Path(path).read_text())
    notes = Path(args.notes).read_text() if args.notes and Path(args.notes).exists() else None
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(exp, notes))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
