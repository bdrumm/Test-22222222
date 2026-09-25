import http.server
import json
import threading
import urllib.request
from functools import partial

from mta_delay_insights.realtime import server as srv


class _Stub:
    def __init__(self):
        self.lock = threading.Lock()
        live = {"generated_ts": 1790000000.0, "routes": [{"route_id": "6", "direction": "N", "status": "good"}],
                "stations": [{"id": "gc-n", "arrivals": []}], "journeys": [{"id": "j1", "options": [], "best": None}],
                "leave_by": [{"id": "j1", "hours": []}], "incidents_developing": [], "track_changes": [], "route_choice": []}
        self.last_live = live
        self.payload = json.dumps(live).encode()
        self.polls = 3; self.started_at = 1.0; self.learned = None; self.feeds = ["1234567S"]; self.store = None; self.models = {}

    def forecast_eval_summary(self):
        from mta_delay_insights.realtime.evaluate import summarize_forecast_eval
        return summarize_forecast_eval(None)


def _get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
        return r.status, json.loads(r.read())


def test_api_endpoints(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>")
    srv.Handler.state = _Stub()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(srv.Handler, directory=str(tmp_path)))
    port = httpd.server_address[1]
    th = threading.Thread(target=httpd.serve_forever, daemon=True); th.start()
    try:
        assert _get(port, "/api/live")[1]["generated_ts"] == 1790000000.0
        assert _get(port, "/data/live.json")[1]["routes"][0]["route_id"] == "6"
        st, h = _get(port, "/api/health"); assert st == 200 and h["polls"] == 3 and h["ok"] is True
        assert _get(port, "/api/routes")[1]["routes"][0]["status"] == "good"
        assert _get(port, "/api/station?id=gc-n")[1]["id"] == "gc-n"
        assert _get(port, "/api/plan?journey=j1")[1]["plan"]["id"] == "j1"
        assert _get(port, "/api/incidents")[1]["incidents"] == []
        fe = _get(port, "/api/forecast_eval"); assert fe[0] == 200 and fe[1]["n"] == 0 and "by_horizon" in fe[1]
        for bad in ("/api/plan?journey=nope", "/api/station?id=nope", "/api/whatever"):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}{bad}")
                assert False, bad
            except urllib.error.HTTPError as e:
                assert e.code == 404 and "error" in json.loads(e.read())
    finally:
        httpd.shutdown(); httpd.server_close()
