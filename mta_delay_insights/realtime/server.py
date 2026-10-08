"""Local realtime server: serves the built site and recomputes ``/data/live.json`` every N seconds.

    mta-insights serve --site _site --db data/mta.sqlite --interval 30

Runs where the MTA feeds are reachable (a laptop, a small VM). The Live page of
the site polls ``data/live.json``; everything else is served from ``--site``.
"""
from __future__ import annotations

import http.server
import os
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
        self.weather_hourly = None               # recent + next-day hourly weather for the learned model's features
        self._weather_at: float | None = None
        self._goodservice_at: float | None = None  # community status (goodservice.io) sampled into the store every 5 min
        self._learned_fit_at: float | None = None
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
        self._review_timer: threading.Timer | None = None
        self._data_repo_at: float | None = None
        self._review_lock = threading.Lock()
        self._last_eval = 0.0
        # the browser-side live mode needs today's timetable extract next to the site
        self.site_dir = Path(site_dir) if site_dir else None
        self._sched_date = None

    def refresh_client_schedule(self, now: float) -> bool:
        """(Re)write data/client_schedule.json, client_lines.json and client_geometry.json when the service date changes."""
        if self.site_dir is None:
            return False
        from datetime import datetime, timedelta
        from ..sources.gtfs_static import NY_TZ
        from .client_export import export_client_geometry, export_client_schedule
        dt = datetime.fromtimestamp(now, NY_TZ)
        sd = (dt - timedelta(hours=3)).date()
        if sd == self._sched_date:
            return False
        out = self.site_dir / "data"
        out.mkdir(parents=True, exist_ok=True)
        cs = export_client_schedule(self.static, self.targets, self.journeys, out, dt, self.feeds)
        # the geometry must describe the same lines, stop for stop: the app maps coordinates onto them by index
        export_client_geometry(self.static, list(cs.get("lines", {})), out, dt)
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
        # hourly weather (Open-Meteo, past month + tomorrow) for the learned model's features, refreshed every 3 h
        if self._weather_at is None or now - self._weather_at > 3 * 3600:
            try:
                from ..sources import weather
                w = weather.fetch_recent_hourly(35)
                if w is not None and len(w):
                    self.weather_hourly = w
                    if self.store is not None:
                        self.store.put_frame("weather_hourly", w)
                        self.store.put_frame("weather_daily", weather.daily_summary(w))
            except Exception as exc:
                if self.weather_hourly is None and self.store is not None:
                    cached = self.store.get_frame("weather_hourly")
                    self.weather_hourly = cached if cached is not None and len(cached) else None
                log.warning("weather refresh failed (%s); cached hours: %s", exc, 0 if self.weather_hourly is None else len(self.weather_hourly))
            self._weather_at = now
        # learned arrival model: a published model (pipeline / `make model`, trained on the full history with
        # weather, alerts and day patterns) is loaded and kept; without one, refit from the store every 6 hours
        try:
            from ..models import ArrivalModel, build_training_rows, train_arrival_model
            if self.learned_path and self.learned_path.exists():
                mtime = self.learned_path.stat().st_mtime
                if self.learned is None or getattr(self, "_learned_mtime", None) != mtime:
                    self.learned = ArrivalModel.load(self.learned_path)
                    self._learned_mtime = mtime
                    log.info("learned model loaded: %s", {k: self.learned.card.get(k) for k in ("n_train", "n_test", "trained_at")})
            elif self.store is not None and (self._learned_fit_at is None or now - self._learned_fit_at > 6 * 3600):
                self._learned_fit_at = now
                arr = self.store.arrivals(None, now - 21 * 86400, now)
                if len(arr) >= 20000:
                    rows = build_training_rows(arr, self.static, self.store.alerts(), self.store.get_frame("weather_daily"), None,
                                               self.store.eta_samples(None, now - 21 * 86400, now), weather_hourly=self.weather_hourly)
                    m = train_arrival_model(rows)
                    if m.ready:
                        self.learned = m
                        log.info("learned model refitted from the store: %s", {k: m.card.get(k) for k in ("n_train", "n_test")})
        except Exception as exc:
            log.warning("learned model fit failed: %s", exc)
        self._last_fit = now

    def _sample_goodservice(self, now: float) -> None:
        """Off the poll thread: the community status takes a dozen requests and must not delay the feeds."""
        try:
            from ..sources import goodservice
            gsdf = goodservice.normalize(goodservice.fetch_routes(timeout=15), now)
            if len(gsdf) and self.store is not None:
                self.store.put_frame("goodservice", gsdf, replace=False)
        except Exception as exc:
            log.warning("goodservice sample failed: %s", exc)

    def _pull_data_repo(self) -> None:
        """Every five minutes: the trips the phone wrote to the GitHub data repository; new ones are reviewed. Only a
        repository `make trips` has cloned already (data/trips/github), or one named by WHICHWAY_DATA_REPO."""
        try:
            from ..trips import sync_data_repo
            if not (self.trips_dir() / "github" / ".git").exists() and "WHICHWAY_DATA_REPO" not in os.environ:
                return
            if sync_data_repo(self.trips_dir()):
                log.info("new trips in the data repository: reviewing")
                self.schedule_trip_review(delay=5)
        except Exception as exc:
            log.warning("data repository pull failed: %s", exc)

    def tick(self) -> None:
        now = time.time()
        if now - self._last_fit > self.refit_every:
            self.refit(now)
        if self.store is not None and (self._goodservice_at is None or now - self._goodservice_at >= 300):
            self._goodservice_at = now
            threading.Thread(target=self._sample_goodservice, args=(now,), name="goodservice", daemon=True).start()
        if self.store is not None and (self._data_repo_at is None or now - self._data_repo_at >= 300):
            self._data_repo_at = now
            threading.Thread(target=self._pull_data_repo, name="data-repo", daemon=True).start()
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
        t_build = time.time()
        live = build_live(feed_bytes, self.alerts_df, self.static, self.targets, self.models, now, source="local-realtime",
                          journeys=self.journeys, journey_models=self.journey_models, learned=self.learned, store=self.store, hold_model=self.hold_model,
                          weather_daily=self.store.get_frame("weather_daily") if self.store is not None else None, weather_hourly=self.weather_hourly)
        build_sec = time.time() - t_build
        if build_sec > 10:
            n_learned = sum(1 for st in live.get("stations") or [] for a in st.get("arrivals") or [] if a.get("model_source") == "learned")
            log.warning("snapshot took %.0fs (%d trains, %d learned platform ETAs, %d journeys)", build_sec, live.get("trains_total", 0), n_learned, len(live.get("journeys") or []))
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
                kept = self.forecast_eval
            self._last_eval = now
            if kept is not None and not kept.empty:
                # kept in the store too, so the trip review can score the rider's trains after the fact
                threading.Thread(target=self._persist_forecast_eval, args=(kept.copy(),), daemon=True).start()
        except Exception as exc:
            log.warning("forecast evaluation failed: %s", exc)

    def _persist_forecast_eval(self, df) -> None:
        try:
            self.store.put_frame("forecast_eval", df)
        except Exception as exc:
            log.warning("forecast evaluation not persisted: %s", exc)

    # ---- the rider's trips ------------------------------------------------ #

    def schedule_trip_review(self, delay: float | None = None) -> None:
        """A trip observation just arrived: review every trip against the trains once the last arrivals are in
        (two minutes by default, TRIP_REVIEW_DELAY_SEC), the latest arrival resetting the clock."""
        if self.store is None:
            return
        if delay is None:
            try:
                delay = float(os.environ.get("TRIP_REVIEW_DELAY_SEC", "120"))
            except ValueError:
                delay = 120.0
        with self._review_lock:
            if self._review_timer is not None:
                self._review_timer.cancel()
            self._review_timer = threading.Timer(delay, self.run_trip_review)
            self._review_timer.daemon = True
            self._review_timer.start()

    def trips_dir(self) -> Path:
        """Where the trip review lives: WHICHWAY_TRIPS_DIR, else trips/ next to the store, else data/trips."""
        env = os.environ.get("WHICHWAY_TRIPS_DIR")
        if env:
            return Path(env)
        p = getattr(self.store, "path", None) if self.store is not None else None
        return Path(p).parent / "trips" if p and p != ":memory:" else Path("data/trips")

    def run_trip_review(self) -> dict:
        """The review itself (after an upload, or on demand through POST /api/trips/review): data/trips/trip_review.md,
        .json and legs.json next to the store, from the uploads, the pulls and the store's arrivals."""
        if self.store is None:
            return {"error": "no store"}
        try:
            from ..trips import review_all
            with self.lock:
                feval = self.forecast_eval.copy() if self.forecast_eval is not None else None
            with self._review_lock:
                res = review_all(self.store, self.static, self.trips_dir(), feval=feval)
            log.info("trip review: %s", res)
            return res
        except Exception as exc:
            log.warning("trip review failed: %s", exc, exc_info=True)
            return {"error": f"{type(exc).__name__}: {exc}"}

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
                                   "store_arrivals": int(len(st.store.arrivals(None, time.time() - 86400, time.time()))) if st.store is not None else None,
                                   "telemetry": st.store.telemetry_count() if st.store is not None else None})
            if not live:
                return self._json({"error": "no snapshot yet"}, 503)
            if path == "/api/trips":
                p = self.state.trips_dir() / "trip_review.json"
                if not p.exists():
                    return self._json({"error": "no trip review yet", "hint": "POST /api/telemetry or `make trips`"}, 404)
                try:
                    doc = json.loads(p.read_text())
                except Exception as exc:
                    return self._json({"error": f"unreadable review: {exc}"}, 500)
                return self._json({"generated": doc.get("generated"), "summary": doc.get("summary"), "legs": doc.get("legs")})
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
            return self._json({"error": "unknown endpoint", "endpoints": ["/api/live", "/api/routes", "/api/station?id=", "/api/plan?journey=", "/api/incidents", "/api/health", "/api/trips"]}, 404)
        if self.path.startswith("/data/models/"):
            tid = self.path.rsplit("/", 1)[-1].replace(".json", "")
            m = self.state.models.get(tid)
            if m:
                body = json.dumps(m.to_dict()).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                return
        return super().do_GET()

    def do_POST(self):
        """POST /api/telemetry: one trip observation, or a list of them, from an opted-in phone."""
        path, _, _ = self.path.partition("?")
        if path == "/api/trips/review":
            run = getattr(self.state, "run_trip_review", None)
            if run is None:
                return self._json({"error": "no trip review here"}, 503)
            res = run()
            return self._json(res, 500 if res.get("error") else 200)
        if path != "/api/telemetry":
            return self._json({"error": "unknown endpoint", "endpoints": ["POST /api/telemetry", "POST /api/trips/review"]}, 404)
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > 1_000_000:
            return self._json({"error": "a JSON body of at most 1 MB is required"}, 400)
        try:
            body = json.loads(self.rfile.read(n))
        except Exception:
            return self._json({"error": "invalid JSON"}, 400)
        items = body if isinstance(body, list) else [body]
        if not items or not all(isinstance(x, dict) and isinstance(x.get("id"), str) and x["id"] for x in items):
            return self._json({"error": "each observation is an object with a string id"}, 400)
        store = self.state.store
        if store is None:
            return self._json({"error": "no store: run serve with --db"}, 503)
        stored = store.insert_telemetry(items, received_ts=time.time())
        resp = {"ok": True, "stored": stored}
        schedule = getattr(self.state, "schedule_trip_review", None)
        if schedule is not None:
            schedule()
            resp["review"] = "scheduled"
        return self._json(resp)

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
