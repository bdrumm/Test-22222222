import http.server
import json

from pipeline import lib
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


def _post(port, path, payload):
    data = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_telemetry_post(tmp_path):
    from mta_delay_insights.storage.db import Store
    (tmp_path / "index.html").write_text("<html></html>")
    stub = _Stub(); stub.store = Store(tmp_path / "t.sqlite")
    srv.Handler.state = stub
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(srv.Handler, directory=str(tmp_path)))
    port = httpd.server_address[1]
    th = threading.Thread(target=httpd.serve_forever, daemon=True); th.start()
    try:
        obs = {"id": "trip-1", "installId": "phone-a", "createdTs": 1790000000.0, "routeLabel": "F → A/C/E at W 4 St-Wash Sq",
               "legs": [{"line": "F_N", "from": "F23N", "to": "D20N"}], "predictedBoardTs": 1790000600.0,
               "events": [{"kind": "departed", "ts": 1790000630.0}], "corroboratedDepartureTs": 1790000630.0}
        assert _post(port, "/api/telemetry", obs) == (200, {"ok": True, "stored": 1})
        assert _post(port, "/api/telemetry", [obs, dict(obs, id="trip-2")]) == (200, {"ok": True, "stored": 2})
        rows = stub.store.telemetry()
        assert [r["id"] for r in rows] == ["trip-1", "trip-2"], "a repeat of trip-1 replaced it"
        assert rows[0]["events"][0]["kind"] == "departed"
        assert _get(port, "/api/health")[1]["telemetry"] == 2
        assert _post(port, "/api/telemetry", "not json")[0] == 400
        assert _post(port, "/api/telemetry", {"no": "id"})[0] == 400
        assert _post(port, "/api/other", obs)[0] == 404
        stub.store = None
        assert _post(port, "/api/telemetry", obs)[0] == 503
    finally:
        httpd.shutdown(); httpd.server_close()


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


def test_serve_state_exports_the_client_schedule_daily(static, tmp_path):
    from datetime import timedelta
    from mta_delay_insights.realtime.server import LiveState
    from mta_delay_insights.sources.gtfs_static import service_midnight
    from tests.conftest import START
    targets = {"targets": [{"id": "gc", "station": "Grand Central", "direction": "N", "routes": ["6", "4"]}], "upstream_stops": 3}
    _, feeds, resolved = lib.stops_and_feeds(static, targets)
    st = LiveState(static, resolved, feeds, None, collect=False, site_dir=tmp_path)
    now = service_midnight(START + timedelta(days=2)).timestamp() + 9 * 3600
    assert st.refresh_client_schedule(now) and (tmp_path / "data" / "client_schedule.json").exists() and (tmp_path / "data" / "client_lines.json").exists()
    assert not st.refresh_client_schedule(now + 3600), "same service date: no rewrite"
    assert st.refresh_client_schedule(now + 86400), "next service date: rewritten"
    cs = json.loads((tmp_path / "data" / "client_schedule.json").read_text())
    assert cs["targets"]["gc"]["stop_id"] == "631N" and cs["target_feeds"] == ["1234567S"]
    # the geometry is refreshed with the schedule and matches it stop for stop (the app maps coordinates by index)
    geo = json.loads((tmp_path / "data" / "client_geometry.json").read_text())
    assert set(geo["lines"]) == set(cs["lines"])
    assert all(len(geo["lines"][k]["coords"]) == len(cs["lines"][k]["stops"]) for k in cs["lines"])
