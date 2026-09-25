"""Weekly digest: a Markdown brief for people who will not open the app.

Built from the JSON the site build already produced (index, scorecard,
climatology, routes, event study, train runs, model card): the lines that lost
the most time, the transfer findings that matter, how the alert lifecycle
looked, and how the arrival model is doing. Written to data/digest.md and
data/digest.json; the Home page links to it.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def _load(out_data: Path, name: str) -> dict:
    p = out_data / name
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except Exception:
        return {}


def _m(sec) -> str:
    return "–" if sec is None else f"{sec / 60:.1f} min"


def build_digest(out_data: Path, now: datetime) -> dict:
    idx, sc, clim = _load(out_data, "index.json"), _load(out_data, "scorecard.json"), _load(out_data, "climatology.json")
    routes, es, tr = _load(out_data, "routes.json"), _load(out_data, "event_study.json"), _load(out_data, "train_runs.json")
    card = _load(out_data / "models", "arrival.card.json")
    lines = [f"# Subway reliability brief — {now.strftime('%b %d, %Y')}", ""]
    items: list[dict] = []
    rows = sc.get("rows", [])
    if rows:
        worst = rows[:3]
        lines.append(f"## Lines losing the most time ({sc.get('days', 0):.0f} days of observed trains)")
        for r in worst:
            txt = (f"**{r['route']} {'northbound' if r['direction'] == 'N' else 'southbound'}**: {r['share_late_5min']:.0%} of stop arrivals ≥5 min late, "
                   f"{r['loss_per_trip_sec'] / 60:.1f} min lost per trip"
                   + (f", worst at {r['worst_segment_stop']} (+{r['worst_segment_loss_sec']:.0f} s)" if r.get("worst_segment_stop") and (r.get("worst_segment_loss_sec") or 0) > 0 else "")
                   + (f", peak headway CV {r['headway_cv_peak']:.2f}" if r.get("headway_cv_peak") is not None else ""))
            lines.append(f"- {txt}"); items.append({"kind": "line", "text": txt})
        best = min(rows, key=lambda r: r["share_late_5min"] * 100 + r["loss_per_trip_sec"] / 60)
        lines.append(f"- Most reliable: **{best['route']} {best['direction']}** ({best['share_late_5min']:.0%} ≥5 min late, {best['loss_per_trip_sec'] / 60:.1f} min lost per trip)")
        lines.append("")
    findings = [(r["label"], f) for r in routes.get("routes", []) for f in r.get("findings", []) if f.get("severity") in ("high", "medium")]
    if findings:
        lines.append("## Transfers and routes")
        for label, f in findings[:6]:
            lines.append(f"- *{label}*: {f['text']}"); items.append({"kind": "route", "text": f"{label}: {f['text']}"})
        lines.append("")
    if tr.get("overall"):
        o = tr["overall"]
        txt = (f"{tr['n_pairs']} terminal turns matched: {o['share_late_in']:.0%} of trips arrive ≥5 min late at the terminal"
               + (f" and {o['share_late_out_given_late_in']:.0%} of those leave late again" if o.get("share_late_out_given_late_in") is not None else "")
               + (f"; median {_m(o['median_recovered_sec'])} recovered at the terminal" if o.get("median_recovered_sec") is not None else ""))
        lines += ["## Terminals", f"- {txt}", ""]; items.append({"kind": "terminal", "text": txt})
    if es.get("n_alerts") and es.get("overall"):
        o = es["overall"]
        txt = (f"{es['n_alerts']} unplanned alerts studied: trains showed the problem "
               + (f"{o['detection_lag_min']:.0f} min before the alert was posted" if o.get("detection_lag_min") is not None else "around the time the alert was posted")
               + (f"; peak excess {_m(o['peak_excess_sec'])}" if o.get("peak_excess_sec") is not None else "")
               + (f"; service back to normal {o['recovery_min']:.0f} min after posting" if o.get("recovery_min") is not None else ""))
        lines += ["## Alerts", f"- {txt}", ""]; items.append({"kind": "alerts", "text": txt})
    if clim.get("n_events"):
        top = clim.get("by_route", [])[:3]
        txt = (f"Archive since 2020: {clim['n_events'] / clim['weeks']:.0f} unplanned disruption events per week system-wide; most on "
               + ", ".join(f"{x['route']} ({x['per_week']:.1f}/wk)" for x in top)
               + (f"; top cause {clim['by_cause'][0]['cause']} ({clim['by_cause'][0]['share']:.0%})" if clim.get("by_cause") else ""))
        lines += ["## Disruption climatology", f"- {txt}", ""]; items.append({"kind": "climatology", "text": txt})
    ev = card.get("evaluation", {})
    if card.get("status") == "ok":
        txt = (f"Arrival model: {ev.get('mae_model', 0):.0f} s mean error vs {ev.get('mae_schedule', 0):.0f} s for the schedule"
               + (f" and {ev['mae_feed']:.0f} s for the MTA countdown ETA ({(1 - ev['mae_model_on_feed_rows'] / ev['mae_feed']):.0%} better)" if ev.get("mae_feed") else "")
               + f"; 80% range covers {ev.get('coverage_p10_p90', 0):.0%} of outcomes; trained on {card.get('n_train', 0):,} rows")
        lines += ["## Prediction model", f"- {txt}", ""]; items.append({"kind": "model", "text": txt})
    hs = _load(out_data, "holds.json")
    if hs.get("n"):
        lg = hs.get("long") or {}
        top = hs.get("by_stop", [])[:3]
        txt = (f"{hs['per_day']:.0f} holds per day (trains stopped ≥ {hs['hold_sec'] / 60:.1f} min at a station, origin terminals excluded) over {hs['days']} days; most held minutes at "
               + ", ".join(f"{x['name']} ({x['routes'] and '/'.join(x['routes'])})" for x in top))
        if lg.get("n"):
            txt += (f". Of {lg['n']} long holds (≥ {hs['long_sec'] / 60:.0f} min), {lg['share_with_alert']:.0%} had an unplanned alert for the line"
                    + (f", posted a median {lg['median_latency_sec'] / 60:.0f} min after the hold began" if lg.get("median_latency_sec") is not None else ""))
        lines += ["## Holds", f"- {txt}", ""]; items.append({"kind": "holds", "text": txt})
    fe = _load(out_data, "forecast_eval.json")
    if fe.get("n"):
        o = fe.get("overall", {})
        txt = (f"Live forecasts scored against {fe['n']:,} observed arrivals ({fe.get('n_snapshots', 0)} snapshots, {fe.get('days', 0)} days): "
               f"MTA feed {_m(o.get('feed', {}).get('mae_sec'))} mean error, model {_m(o.get('model', {}).get('mae_sec'))}, simulation {_m(o.get('sim', {}).get('mae_sec'))}"
               + (f"; the model was closer than the feed {o['model_beats_feed_share']:.0%} of the time" if o.get("model_beats_feed_share") is not None else ""))
        lines += ["## Live forecast accuracy", f"- {txt}", ""]; items.append({"kind": "forecast_eval", "text": txt})
    lines += ["---", f"Generated {now.isoformat(timespec='minutes')} from the published analyses; details on each page of the app."]
    md = "\n".join(lines)
    (out_data / "digest.md").write_text(md)
    digest = {"generated_at": now.isoformat(), "items": items, "markdown": md}
    (out_data / "digest.json").write_text(json.dumps(digest))
    return digest
