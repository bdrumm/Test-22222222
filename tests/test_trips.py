"""The rider's trips reviewed against the trains: the ledger merge, one trip's scores, the summary and the legs."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from mta_delay_insights import trips


def _line(static):
    for r in sorted(static.routes["route_id"].unique()):
        seq = static.canonical_stop_sequence(r, "N")
        if len(seq) >= 6:
            return r, seq
    pytest.skip("no northbound line with six stops in the mini GTFS")


def _arrivals(route, seq, trip_id, t0, dwell=20.0, run=120.0, confidence=1.0):
    rows = []
    for i, s in enumerate(seq):
        a = t0 + i * run
        rows.append({"trip_key": f"20260901|{trip_id}", "trip_id": trip_id, "route_id": route, "stop_id": s, "arrival_ts": a, "departure_ts": a + dwell,
                     "confidence": confidence})
    return rows


def test_merge_keeps_the_fullest_copy_of_a_trip():
    open_copy = {"id": "t1", "createdTs": 10, "events": []}
    ended = {"id": "t1", "createdTs": 10, "endedTs": 900, "events": [{"kind": "departed", "ts": 100}]}
    out = trips.merge_observations([[ended], [open_copy], [{"id": "t0", "createdTs": 5}]])
    assert [o["id"] for o in out] == ["t0", "t1"]
    assert out[1]["endedTs"] == 900


def test_a_trip_is_scored_against_the_train_the_phone_named(static):
    route, seq = _line(static)
    a, b = seq[0], seq[3]
    t0 = 1_700_000_000.0
    me = _arrivals(route, seq, "0600_X..N1", t0)                       # the train taken: at the platform at t0, off at t0+20
    other = _arrivals(route, seq, "0605_X..N2", t0 + 300)              # the one behind it
    arr = pd.DataFrame(me + other)
    obs = {"id": "t1", "installId": "p", "createdTs": t0 - 400, "endedTs": t0 + 3 * 120 + 90, "endedBy": "alighted", "startedBy": "gps",
           "routeLabel": f"{route} direct", "legs": [{"line": f"{route}_N", "from": a, "to": b}], "expectedSec": 900, "schedSec": 360,
           "predictedArriveTs": t0 + 3 * 120 - 45,                     # the app said 45 s earlier than the train made it
           "events": [{"kind": "departed", "ts": t0 + 50}, {"kind": "alighted", "ts": t0 + 3 * 120 + 25}],
           "boarded": [{"leg": 0, "key": f"{route}_N", "trainId": f"{route}_N|0600_X..N1", "confidence": 0.9, "verdict": "onPlan", "evidence": ["departure", "ride"]}],
           "rideStops": [3]}
    m = trips.TrainMatcher(static, arr)
    r = trips.review_trip(obs, static, m)
    leg = r["legs"][0]
    assert leg["matchedBy"] == "phone" and leg["train"]["trip_id"] == "0600_X..N1"
    # felt against the platform moments: the store's time put back by the line's lag (plus the dwell to pull away)
    assert leg["departureErrSec"] == pytest.approx((t0 + 50) - trips.pulls_away(t0 + 20, route))
    assert leg["alightingErrSec"] == pytest.approx((t0 + 3 * 120 + 25) - trips.at_platform(t0 + 360, route))
    assert leg["stopsActual"] == 3 and leg["stopsFeltErr"] == 0
    assert leg["boardedAgrees"] is True
    assert r["actualArriveTs"] == pytest.approx(trips.at_platform(t0 + 360, route))
    assert r["forecastErrSec"] == pytest.approx(trips.at_platform(t0 + 360, route) - (t0 + 315)) and r["ranItsCourse"] and not r["falseStart"]
    assert r["doorToDoorErrSec"] == pytest.approx((t0 + 450) - (t0 - 400) - 900)
    # without the phone's word, the felt departure picks the train that left then, on the leg's own line
    obs2 = dict(obs, id="t2", boarded=None)
    r2 = trips.review_trip(obs2, static, m)
    assert r2["legs"][0]["matchedBy"] == "departure" and r2["legs"][0]["train"]["trip_id"] == "0600_X..N1"
    # a route ended by hand after a minute is a false start: listed, never scored
    obs3 = dict(obs, id="t3", endedTs=t0 - 340, endedBy="hand", events=[{"kind": "departed", "ts": t0 + 50}])
    r3 = trips.review_trip(obs3, static, m)
    assert r3["falseStart"] and "forecastErrSec" not in r3
    s = trips.summarize([r, r2, r3])
    assert s["n"] == 3 and s["nMatched"] == 3 and s["forecast"]["n"] == 2
    assert s["detection"]["stopsExact"] == 2 and s["detection"]["falseStarts"] == 1
    assert any("scored" in f or "unbiased" in f or "early" in f or "late" in f for f in s["findings"])
    legs = trips.legs_for_training([r, r2, r3], static)
    assert len(legs) == 1 and legs[0]["k"] == 3 and legs[0]["trips"] == 2 and legs[0]["routes"] == [route]
    md = trips.render_markdown([r, r2, r3], s, pd.Timestamp(t0, unit="s", tz="UTC").to_pydatetime(), legs)
    assert "false start" in md


def test_review_all_writes_the_report_and_the_ledger(static, tmp_path):
    route, seq = _line(static)
    t0 = 1_700_000_000.0

    class FakeStore:
        path = str(tmp_path / "x.sqlite")

        def telemetry(self):
            return [{"id": "up", "installId": "p", "createdTs": t0 - 100, "endedTs": t0 + 500, "endedBy": "hand", "routeLabel": "x",
                     "legs": [{"line": f"{route}_N", "from": seq[0], "to": seq[2]}], "events": [{"kind": "departed", "ts": t0 + 30}], "expectedSec": 600}]

        def arrivals(self, stop_ids, t0_, t1_):
            return pd.DataFrame(_arrivals(route, seq, "0600_X..N1", t0))

        def get_frame(self, name):
            return pd.DataFrame()

    dev = tmp_path / "trips" / "device" / "pull1" / "telemetry"
    dev.mkdir(parents=True)
    dev.joinpath("observations.json").write_text(json.dumps([{"id": "dev", "installId": "p", "createdTs": t0 - 50, "legs": [], "events": []},
                                                              {"id": "skip", "installId": "test", "createdTs": 1, "legs": []}]))
    res = trips.review_all(FakeStore(), static, tmp_path / "trips", now=t0 + 1000)
    assert res["n"] == 2 and res["matched"] == 1
    ledger = trips.load_ledger(tmp_path / "trips" / trips.LEDGER)
    assert {o["id"] for o in ledger} == {"up", "dev"}
    doc = json.loads((tmp_path / "trips" / trips.REPORT_JSON).read_text())
    assert doc["summary"]["n"] == 2
    assert (tmp_path / "trips" / trips.REPORT_MD).read_text().startswith("# Trip review")
    assert json.loads((tmp_path / "trips" / trips.LEGS_JSON).read_text())[0]["k"] == 2


def test_the_riders_account_is_the_ground_truth(static, tmp_path):
    route, seq = _line(static)
    t0 = 1_791_400_000.0          # 2026-10-07 afternoon, New York
    other = next(r for r in sorted(static.routes["route_id"].unique()) if r != route)
    from datetime import datetime
    day = datetime.fromtimestamp(t0, trips.NY).strftime("%Y-%m-%d")
    n1 = trips.add_note(tmp_path, day, "afternoon", [route], "took the early one", ["early-train"], "test build")
    n2 = trips.add_note(tmp_path, day, "06:00-07:00", ["E", "F"], "route never started", ["start-not-detected"])
    obs = [{"id": "t1", "createdTs": t0 - 400, "endedTs": t0 + 900, "endedBy": "alighted", "legs": [{"line": f"{other}_N", "from": seq[0], "to": seq[3]}],
            "events": [{"kind": "departed", "ts": t0 + 50}], "expectedSec": 900,
            "boarded": [{"leg": 0, "key": f"{other}_N", "trainId": None, "confidence": 0.8, "verdict": "onPlan", "evidence": ["departure"]}]}]
    by_trip = trips.attach_notes(obs, trips.load_notes(tmp_path))
    assert [n["id"] for n in by_trip["t1"]] == [n1["id"]]
    m = trips.TrainMatcher(static, pd.DataFrame(_arrivals(route, seq, "0600_X..N1", t0)))
    r = trips.review_trip(obs[0], static, m, notes=by_trip["t1"])
    leg = r["legs"][0]
    assert leg["riderRoute"] == route and leg["phoneRight"] is False      # the phone called the plan's line; the rider rode another
    assert leg["train"]["route"] == route                                 # matched on the rider's line, not the plan's
    s = trips.summarize([r])
    assert s["againstRider"] == {"legs": 1, "right": 0, "planRight": 0, "riderLegs": 1}
    notes = trips.load_notes(tmp_path)
    trips.attach_notes(obs, notes)
    assert notes[1]["tripIds"] == []
    md = trips.render_markdown([r], s, datetime.fromtimestamp(t0, trips.NY), [], notes)
    assert "no trip recorded" in md and "took the early one" in md


def test_trips_from_the_github_data_repository_join_the_ledger(tmp_path):
    t = tmp_path / "trips" / "github" / "trips" / "2026" / "10" / "07"
    t.mkdir(parents=True)
    (t / "1791410000-a.json").write_text(json.dumps({"id": "a", "installId": "p", "createdTs": 1791410000, "legs": []}))
    (t / "1791410100-b.json").write_text(json.dumps({"id": "b", "installId": "p", "createdTs": 1791410100, "legs": [], "endedTs": 1791411000}))
    (t / "broken.json").write_text("{not json")
    got = trips.github_observations(tmp_path / "trips" / "github")
    assert sorted(o["id"] for o in got) == ["a", "b"]
    obs = trips.collect_observations(None, tmp_path / "trips")
    assert {o["id"] for o in obs} == {"a", "b"}
    assert trips.sync_data_repo(tmp_path / "trips", repo="") is False      # no repository named: nothing to pull
