"""Local realtime server: serves the built site and recomputes ``/data/live.json`` every N seconds.

    mta-insights serve --site _site --db data/mta.sqlite --interval 30

Runs where the MTA feeds are reachable (a laptop, a small VM). The Live page of
the site polls ``data/live.json``; everything else is served from ``--site``.
"""
from __future__ import annotations

import http.server
import json
import urllib.parse
import logging
import threading
import time
from functools import partial
from pathlib import Path

from .. import config
from ..sources import alerts as alerts_src
from ..sources import gtfs_realtime as rt
from ..sources.gtfs_static import StaticGTFS
from ..storage.db import Store
from .propagation import PropagationModel, fit_model
from .status import build_live

log = logging.getLogger(__name__)


class LiveState:
    def __init__(self, static: StaticGTFS, targets: list[dict], feeds: list[str],
                 store: Store | None = None, refit_every_sec: float = 1800.0, journeys: list | None = None, site_dir: str | Path | None = None,
                 collect: bool = True, sample_stops: set[str] | None = None, poll_interval_sec: float = 30.0,
                 learned_path: str | Path | None = None):
        self.static, self.targets, self.feeds = static, targets, feeds
        self.store = store
        self.refit_every = refit_every_sec
        self.models: dict[str, PropagationModel] = {}
        self.journeys = journeys or []
        self.journey_models: dict = {}
        self.learned = None
        self.learned_path = Path(learned_path) if learned_path else None
        self.hold_model: dict | None = None      # hold survival from the store's dwells (refit every 6 h)
        self._hold_model_at: float | None = None
        self.last_live: dict = {}
        self.started_at = time.time()
        self.polls = 0
        # In continuous mode the server is also the collector: every poll is ingested into the store
        # (all stops), so history, models and line views grow on the machine that serves them.
        self.collector = None
        if collect and store is not None:
            from ..collect.collector import Collector
            self.collector = Collector(store, feeds, None, poll_interval_sec=poll_interval_sec, sample_stops=sample_stops)
        self.payload: bytes = json.dumps({"status": "starting"}).encode()
        self.alerts_df = None
        self._last_fit = 0.0
        self.lock = threading.Lock()
        # forecasts made by each snapshot, scored against the arrivals the collector observes later
        self.projections: list[dict] = []
        self.forecast_eval = None
        self._last_eval = 0.0
        # the browser-side live mode needs today's timetable extract next to the site
        self.site_dir = Path(site_dir) if site_dir else None
        self._sched_date = None

    def refresh_client_schedule(self, now: float) -> bool:
        """(Re)write data/client_schedule.json and client_lines.json when the service date changes."""
        if self.site_dir is None:
            return False
        from datetime import datetime, timedelta
        from ..sources.gtfs_static import NY_TZ
        from .client_export import export_client_schedule
        dt = datetime.fromtimestamp(now, NY_TZ)
        sd = (dt - timedelta(hours=3)).date()
        if sd == self._sched_date:
            return False
        out = self.site_dir / "data"
        out.mkdir(parents=True, exist_ok=True)
        export_client_schedule(self.static, self.targets, self.journeys, out, dt, self.feeds)
        self._sched_date = sd
        log.info("client schedule exported for %s", sd)
        return True

    def refit(self, now: float) -> None:
        try:
            self.refresh_client_schedule(now)
        except Exception as exc:
            log.warning("client schedule export failed: %s", exc)
        if self.store is None:
            return
        for t in self.targets:
            try:
                self.models[t["id"]] = fit_model(self.store, self.static, t, now)
            except Exception as exc:
                log.warning("fit %s failed: %s", t["id"], exc)
        if self.journeys:
            from .journey import fit_journey
            weather = self.store.get_frame("weather_daily") if self.store is not None else None
            for spec in self.journeys:
                try:
                    self.journey_models[spec.id], _ = fit_journey(self.store, self.static, spec, self.store.alerts(), weather, None, now)
                except Exception as exc:
                    log.warning("journey fit %s failed: %s", spec.id, exc)
        # learned arrival model: load a published one, or (re)train from the store when there is enough history
        try:
            from ..models import ArrivalModel, build_training_rows, train_arrival_model
            if self.learned_path and self.learned_path.exists() and self.learned is None:
                self.learned = ArrivalModel.load(self.learned_path)
            elif self.store is not None:
                arr = self.store.arrivals(None, now - 21 * 86400, now)
                if len(arr) >= 20000:
                    rows = build_training_rows(arr, self.static, self.store.alerts(), None, None, self.store.eta_samples(None, now - 21 * 86400, now))
                    m = train_arrival_model(rows)
                    if m.ready:
                        self.learned = m
                        if self.learned_path:
                            m.save(self.learned_path)
                        log.info("learned model refitted: %s", {k: m.card.get(k) for k in ("n_train", "n_test")})
        except Exception as exc:
            log.warning("learned model fit failed: %s", exc)
        self._last_fit = now

    def tick(self) -> None:
        now = time.time()
        if now - self._last_fit > self.refit_every:
            self.refit(now)
        feed_bytes = {}
        for key in self.feeds:
            try:
                data = rt.fetch_feed_bytes(config.rt_feed_url(key))
                feed_bytes[key] = data
                if self.collector is not None:
                    self.collector.ingest(key, data, now)
            except Exception as exc:
                log.warning("feed %s failed: %s", key, exc)
        if self.polls % 4 == 0:
            try:
                payload = alerts_src.fetch_alerts_json()
                self.alerts_df = alerts_src.alerts_frame(payload)
                if self.collector is not None:
                    self.collector.ingest_alerts(payload, now)
            except Exception as exc:
                log.warning("alerts failed: %s", exc)
        self.polls += 1
        if self._hold_model_at is None or now - self._hold_model_at > 6 * 3600:
            try:
                from .client_model import fit_hold_survival
                self.hold_model = fit_hold_survival(self.store.dwells(start_ts=now - 30 * 86400), self.static)
            except Exception as exc:
                log.warning("hold survival refit failed: %s", exc)
            self._hold_model_at = now
        live = build_live(feed_bytes, self.alerts_df, self.static, self.targets, self.models, now, source="local-realtime",
                          journeys=self.journeys, journey_models=self.journey_models, learned=self.learned, store=self.store, hold_model=self.hold_model)
        self._record_forecasts(live, now)
        with self.lock:
            self.last_live = live
            self.payload = json.dumps(live, default=str).encode()

    def _record_forecasts(self, live: dict, now: float, every_sec: float = 600.0) -> None:
        """Keep every snapshot's predicted arrivals; every 10 minutes score the ones whose train has since arrived."""
        try:
            import pandas as pd
            from .evaluate import evaluate_projections, projections_from_live
            self.projections.extend(projections_from_live(live))
            if self.store is None or now - self._last_eval < every_sec or not self.projections:
                return
            ev = evaluate_projections(pd.DataFrame(self.projections), self.store.arrivals(None, now - 3 * 3600, now))
            scored = set(zip(ev["made_ts"], ev["trip_id"], ev["stop_id"])) if not ev.empty else set()
            # unscored projections stay until their train is two hours overdue
            self.projections = [x for x in self.projections if (x["made_ts"], x["trip_id"], x["stop_id"]) not in scored and x["feed_eta_ts"] > now - 7200]
            with self.lock:
                if not ev.empty:
                    self.forecast_eval = ev if self.forecast_eval is None else pd.concat([self.forecast_eval, ev], ignore_index=True).tail(50000)
            self._last_eval = now
        except Exception as exc:
            log.warning("forecast evaluation failed: %s", exc)

    def forecast_eval_summary(self) -> dict:
        from .evaluate import summarize_forecast_eval
        with self.lock:
            df = self.forecast_eval
        return summarize_forecast_eval(df)

    def loop(self, interval: float, stop: threading.Event) -> None:
        while not stop.is_set():
            t0 = time.time()
            try:
                self.tick()
            except Exception as exc:
                log.warning("tick failed: %s", exc)
            stop.wait(max(1.0, interval - (time.time() - t0)))


class Handler(http.server.SimpleHTTPRequestHandler):
    state: LiveState = None  # set by serve()

    def _json(self, obj, status: int = 200) -> None:
        body = obj if isinstance(obj, bytes) else json.dumps(obj, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = dict(urllib.parse.parse_qsl(query))
        if path in ("/data/live.json", "/live.json", "/api/live"):
            with self.state.lock:
                body = self.state.payload
            return self._json(body)
        if path in ("/api/forecast_eval", "/data/forecast_eval.json"):
            summary = self.state.forecast_eval_summary()
            if summary["n"] or path == "/api/forecast_eval":
                return self._json(summary)
            # nothing scored yet in this process: fall through to the site's published summary, if any
        if path.startswith("/api/"):
            with self.state.lock:
                live = self.state.last_live
            if path == "/api/health":
                st = self.state
                return self._json({"ok": bool(live), "polls": st.polls, "uptime_sec": time.time() - st.started_at,
                                   "last_poll_age_sec": (time.time() - live["generated_ts"]) if live.get("generated_ts") else None,
                                   "learned_model_ready": bool(st.learned and st.learned.ready), "feeds": st.feeds,
                                   "store_arrivals": int(len(st.store.arrivals(None, time.time() - 86400, time.time()))) if st.store is not None else None})
            if not live:
                return self._json({"error": "no snapshot yet"}, 503)
            if path == "/api/routes":
                return self._json({"generated_ts": live["generated_ts"], "routes": live.get("routes", [])})
            if path == "/api/incidents":
                return self._json({"generated_ts": live["generated_ts"], "incidents": live.get("incidents_developing", []), "track_changes": live.get("track_changes", [])})
            if path == "/api/station":
                sid = params.get("id")
                st = next((x for x in live.get("stations", []) if x.get("id") == sid), None)
                return self._json(st or {"error": f"unknown station id {sid!r}", "ids": [x.get("id") for x in live.get("stations", [])]}, 200 if st else 404)
            if path == "/api/plan":
                jid = params.get("journey")
                plan = next((x for x in live.get("journeys", []) if x.get("id") == jid), None)
                if plan is None:
                    return self._json({"error": f"unknown journey {jid!r}", "ids": [x.get("id") for x in live.get("journeys", [])],
                                       "route_choice": live.get("route_choice", [])}, 404)
                lb = next((x for x in live.get("leave_by", []) if x.get("id") == jid), None)
                return self._json({"generated_ts": live["generated_ts"], "plan": plan, "leave_by": lb})
            return self._json({"error": "unknown endpoint", "endpoints": ["/api/live", "/api/routes", "/api/station?id=", "/api/plan?journey=", "/api/incidents", "/api/health"]}, 404)
        if self.path.startswith("/data/models/"):
            tid = self.path.rsplit("/", 1)[-1].replace(".json", "")
            m = self.state.models.get(tid)
            if m:
                body = json.dumps(m.to_dict()).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                return
        return super().do_GET()

    def log_message(self, fmt, *args):  # quieter
        log.debug(fmt, *args)


def serve(site_dir: str | Path, static: StaticGTFS, targets: list[dict], feeds: list[str], store: Store | None, journeys: list | None = None,
          port: int = 8000, interval: float = 30.0, collect: bool = True, sample_stops: set[str] | None = None,
          learned_path: str | Path | None = None) -> None:
    state = LiveState(static, targets, feeds, store, journeys=journeys, collect=collect, sample_stops=sample_stops,
                      poll_interval_sec=interval, learned_path=learned_path, site_dir=site_dir)
    stop = threading.Event()
    th = threading.Thread(target=state.loop, args=(interval, stop), daemon=True)
    th.start()
    Handler.state = state
    handler = partial(Handler, directory=str(site_dir))
    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), handler)
    log.info("serving %s on http://localhost:%d (live every %.0fs)", site_dir, port, interval)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()
