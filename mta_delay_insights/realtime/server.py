"""Local realtime server: serves the built site and recomputes ``/data/live.json`` every N seconds.

    mta-insights serve --site _site --db data/mta.sqlite --interval 30

Runs where the MTA feeds are reachable (a laptop, a small VM). The Live page of
the site polls ``data/live.json``; everything else is served from ``--site``.
"""
from __future__ import annotations

import http.server
import json
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
                 store: Store | None = None, refit_every_sec: float = 1800.0):
        self.static, self.targets, self.feeds = static, targets, feeds
        self.store = store
        self.refit_every = refit_every_sec
        self.models: dict[str, PropagationModel] = {}
        self.payload: bytes = json.dumps({"status": "starting"}).encode()
        self.alerts_df = None
        self._last_fit = 0.0
        self.lock = threading.Lock()

    def refit(self, now: float) -> None:
        if self.store is None:
            return
        for t in self.targets:
            try:
                self.models[t["id"]] = fit_model(self.store, self.static, t, now)
            except Exception as exc:
                log.warning("fit %s failed: %s", t["id"], exc)
        self._last_fit = now

    def tick(self) -> None:
        now = time.time()
        if now - self._last_fit > self.refit_every:
            self.refit(now)
        feed_bytes = {}
        for key in self.feeds:
            try:
                feed_bytes[key] = rt.fetch_feed_bytes(config.rt_feed_url(key))
            except Exception as exc:
                log.warning("feed %s failed: %s", key, exc)
        try:
            self.alerts_df = alerts_src.alerts_frame(alerts_src.fetch_alerts_json())
        except Exception as exc:
            log.warning("alerts failed: %s", exc)
        live = build_live(feed_bytes, self.alerts_df, self.static, self.targets, self.models, now, source="local-realtime")
        with self.lock:
            self.payload = json.dumps(live, default=str).encode()

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

    def do_GET(self):
        if self.path.split("?")[0] in ("/data/live.json", "/live.json"):
            with self.state.lock:
                body = self.state.payload
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
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


def serve(site_dir: str | Path, static: StaticGTFS, targets: list[dict], feeds: list[str], store: Store | None,
          port: int = 8000, interval: float = 30.0) -> None:
    state = LiveState(static, targets, feeds, store)
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
