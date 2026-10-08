"""The rider's own trips, reviewed against what the trains did.

The phone records one observation per route it followed (see the app's Telemetry: the route and the forecast
at the start, the departures and alightings the sensors felt, the train and line the phone settled on for each
leg, the walks it measured). Those observations reach this side two ways: uploaded to the local server's
POST /api/telemetry at the end of the trip, or copied off the phone over USB / Wi-Fi (pipeline.trip_review
--pull). Here they are merged into one ledger, each trip is matched to the trains in the arrival store, and the
result is written as a report (trip_review.md / .json) plus the rider's legs (legs.json) that the model
trainer evaluates the arrival model on.

What is scored per trip
  * the forecast the rider acted on: the app's predicted arrival at the start against the boarded train's real
    arrival at the destination platform (the store's own observation of it)
  * the door-to-door expectation against the measured trip, when the trip ended on its own at the destination
  * the sensors: the felt departure against the matched train's departure, the felt alighting against its
    arrival, the stops felt against the stops the train made, the line the phone settled on against the train
    that actually left when the phone felt the pull-away
  * the walks: the planner's figure for the change against the measured one, the time to the platform
  * the server's own forecast for that train at that platform, when the forecast evaluation kept it
"""
from __future__ import annotations

import json
import logging
import math
import os
import statistics
import subprocess
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

log = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
LEDGER = "observations.jsonl"
REPORT_MD = "trip_review.md"
REPORT_JSON = "trip_review.json"
LEGS_JSON = "legs.json"
NOTES = "rider_notes.jsonl"
PERIODS = {"morning": (5, 12), "afternoon": (12, 18), "evening": (18, 24), "night": (0, 5)}
ENDED_AT_DESTINATION = {"arrived", "alighted", "walked"}
MATCH_WINDOW_SEC = 240.0        # a felt departure matches a train that left the platform within this

# The feed's last time for a stop (the store's arrival_ts) runs this long after the train really reaches the
# platform, by route: measured Oct 7 2026 from the vehicle feed and the phone's felt departures (the app's
# Core/PlatformTiming.swift carries the same table). Trains stand about DWELL_SEC before pulling away.
RECORDED_LAG = {"1": 30, "2": 30, "3": 30, "4": 30, "5": 30, "6": 30, "6X": 30, "7": 30, "7X": 35, "GS": 30, "L": 30, "G": 60,
                "A": 75, "C": 70, "E": 80, "B": 70, "D": 80, "F": 80, "FX": 80, "M": 85, "N": 85, "Q": 95, "R": 90, "W": 95,
                "J": 75, "Z": 75, "FS": 30, "H": 50, "SI": 30}
DEFAULT_LAG = 60.0
DWELL_SEC = 40.0


def at_platform(ts: float, route: str | None) -> float:
    return float(ts) - RECORDED_LAG.get(str(route or ""), DEFAULT_LAG)


def pulls_away(ts: float, route: str | None) -> float:
    return at_platform(ts, route) + DWELL_SEC


# ----------------------------------------------------------------------------- the ledger

def _richness(o: dict) -> tuple:
    return (1 if o.get("endedTs") else 0, len(o.get("events") or []), len(json.dumps(o, sort_keys=True)))


def merge_observations(sources: list[list[dict]]) -> list[dict]:
    """One record per trip id, the fullest copy winning (an ended trip over one still open, more events over fewer)."""
    by_id: dict[str, dict] = {}
    for items in sources:
        for o in items or []:
            oid = o.get("id")
            if not isinstance(oid, str) or not oid:
                continue
            cur = by_id.get(oid)
            if cur is None or _richness(o) >= _richness(cur):
                by_id[oid] = o
    return sorted(by_id.values(), key=lambda o: o.get("createdTs") or 0)


def load_ledger(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def save_ledger(path: Path, obs: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(o, separators=(",", ":"), sort_keys=True) + "\n" for o in obs))


def device_observations(root: Path) -> list[dict]:
    """Every observations.json under the device pulls (root/device/<pull>/.../telemetry/observations.json)."""
    out: list[dict] = []
    if not root.exists():
        return out
    for p in sorted(root.rglob("observations.json")):
        try:
            data = json.loads(p.read_text())
        except Exception as exc:
            log.warning("unreadable %s: %s", p, exc)
            continue
        if isinstance(data, list):
            out.extend(x for x in data if isinstance(x, dict))
    return out


# ----------------------------------------------------------------------------- motion traces (developer builds)

def load_traces(root: Path) -> dict[int, dict]:
    """The app's motion traces from the device pulls (traces/trace-<start>.json), newest copy per route start."""
    out: dict[int, dict] = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("trace-*.json")):
        try:
            d = json.loads(p.read_text())
            out[int(d["startTs"])] = d
        except Exception:
            continue
    return out


def motion_around(trace: dict, ts: float, before: float = 10, after: float = 20) -> dict | None:
    """What the phone felt around one moment: the strongest push and vibration, and the stepping before and after."""
    rows = [r for r in trace.get("seconds") or [] if ts - before <= r[0] <= ts + after]
    if not rows:
        return None
    pre = [r for r in rows if r[0] < ts]
    post = [r for r in rows if r[0] >= ts]
    m = lambda xs, i: round(max((x[i] for x in xs), default=0.0), 4)
    a = lambda xs, i: round(statistics.fmean(x[i] for x in xs), 4) if xs else None
    return {"pushMax": m(post, 2), "shakeMax": m(post, 3), "stepBefore": a(pre, 1), "stepAfter": a(post, 1),
            "pushSecs": sum(1 for x in post if x[2] >= 0.05), "shakeSecs": sum(1 for x in post if x[3] >= 0.02), "n": len(rows)}


# ----------------------------------------------------------------------------- the rider's own account

def load_notes(out_dir: Path) -> list[dict]:
    """The rider's account of trips (data/trips/rider_notes.jsonl): the ground truth the phone's record is checked
    against. One object per line: date (YYYY-MM-DD), window ("morning" | "afternoon" | "evening" | "HH:MM-HH:MM"),
    lines (the lines ridden, in order), account (what happened, in the rider's words), issues (short tags),
    app_build (the build on the phone then)."""
    return load_ledger(Path(out_dir) / NOTES)


def add_note(out_dir: Path, date: str, window: str, lines: list[str], account: str, issues: list[str] | None = None,
             app_build: str | None = None, transfers: list[str] | None = None) -> dict:
    out_dir = Path(out_dir)
    notes = load_notes(out_dir)
    note = {"id": f"{date}-{window}-{len(notes) + 1}", "date": date, "window": window, "lines": [x.strip().upper() for x in lines if x.strip()],
            "account": account, "issues": issues or [], "app_build": app_build, "transfers": transfers or []}
    notes.append(note)
    save_ledger(out_dir / NOTES, notes)
    return note


def _note_span(note: dict) -> tuple[float, float] | None:
    """The note's window as epoch seconds (New York time)."""
    try:
        day = datetime.strptime(note["date"], "%Y-%m-%d").replace(tzinfo=NY)
    except Exception:
        return None
    w = str(note.get("window") or "").strip().lower()
    if w in PERIODS:
        h0, h1 = PERIODS[w]
        return (day.replace(hour=h0).timestamp(), day.replace(hour=0).timestamp() + h1 * 3600)
    if "-" in w:
        try:
            a, b = (datetime.strptime(x.strip(), "%H:%M") for x in w.split("-", 1))
            return (day.replace(hour=a.hour, minute=a.minute).timestamp(), day.replace(hour=b.hour, minute=b.minute).timestamp())
        except ValueError:
            return None
    return (day.timestamp(), day.timestamp() + 86400)


def attach_notes(obs: list[dict], notes: list[dict]) -> dict[str, list[dict]]:
    """Each note joins the trips started in its window; a note with no trip there is kept as one never recorded."""
    out: dict[str, list[dict]] = {}
    for n in notes:
        span = _note_span(n)
        n["tripIds"] = []
        if span is None:
            continue
        for o in obs:
            ts = o.get("createdTs") or 0
            if span[0] <= ts < span[1]:
                out.setdefault(o["id"], []).append(n)
                n["tripIds"].append(o["id"])
    return out


# ----------------------------------------------------------------------------- the GitHub data repository

def data_repo() -> str:
    """The private repository the phone writes trips to (WHICHWAY_DATA_REPO; empty switches the pull off)."""
    return os.environ.get("WHICHWAY_DATA_REPO", "bdrumm/whichway-data").strip()


def _git(args: list[str], cwd: Path | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    # GitHub CLI's login does the authentication, with nothing configured globally
    return subprocess.run(["git", "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential", *args],
                          cwd=cwd, capture_output=True, text=True, timeout=timeout)


def sync_data_repo(out_dir: Path, repo: str | None = None) -> bool:
    """Clone or fast-forward the phone's data repository into out_dir/github; True when something new arrived."""
    repo = data_repo() if repo is None else repo
    if not repo:
        return False
    dest = Path(out_dir) / "github"
    url = f"https://github.com/{repo}.git"
    try:
        if not (dest / ".git").exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            r = _git(["clone", "--quiet", url, str(dest)])
            if r.returncode != 0:
                log.warning("data repository %s not cloned: %s", repo, (r.stderr or "").strip().splitlines()[-1:] or r.returncode)
                return False
            return any((dest / "trips").rglob("*.json")) if (dest / "trips").exists() else False
        before = _git(["rev-parse", "HEAD"], cwd=dest).stdout.strip()
        r = _git(["pull", "--quiet", "--ff-only"], cwd=dest)
        if r.returncode != 0:
            msg = (r.stderr or "").strip()
            if "no such ref" not in msg and "does not have any commits" not in msg:
                log.warning("data repository %s not pulled: %s", repo, msg.splitlines()[-1:] or r.returncode)
            return False
        return _git(["rev-parse", "HEAD"], cwd=dest).stdout.strip() != before
    except Exception as exc:
        log.warning("data repository %s: %s", repo, exc)
        return False


def github_observations(root: Path) -> list[dict]:
    """The trips the phone wrote to the data repository: trips/<y>/<m>/<d>/<start>-<id>.json, one each."""
    out: list[dict] = []
    base = Path(root) / "trips"
    if not base.exists():
        return out
    for p in sorted(base.rglob("*.json")):
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        if isinstance(d, dict) and d.get("id"):
            out.append(d)
    return out


# ----------------------------------------------------------------------------- one trip against the trains

def _hm(ts) -> str:
    return "–" if ts is None or (isinstance(ts, float) and math.isnan(ts)) else datetime.fromtimestamp(float(ts), NY).strftime("%H:%M:%S")


def _day(ts) -> str:
    return "–" if ts is None else datetime.fromtimestamp(float(ts), NY).strftime("%a %b %-d")


def _signed(sec) -> str:
    if sec is None or (isinstance(sec, float) and math.isnan(sec)):
        return "–"
    sec = float(sec)
    sign = "+" if sec >= 0 else "−"
    sec = abs(sec)
    return f"{sign}{int(sec // 60)}:{int(sec % 60):02d}"


def _mmss(sec) -> str:
    if sec is None or (isinstance(sec, float) and math.isnan(sec)):
        return "–"
    sec = abs(float(sec))
    return f"{int(sec // 60)}:{int(sec % 60):02d}"


def _split_line(line: str) -> tuple[str, str]:
    route, _, d = (line or "").partition("_")
    return route, d


_COMPLEX: dict[str, list[str]] = {}


def _complex(static, parent: str) -> list[str]:
    """A station and the same-named stations within a short walk of it (4 Av-9 St is R33 for the R and F23 for the
    F/G; W 4 St-Wash Sq is A32 and D20; 14 St at 7 Av, 363 m from 14 St at 8 Av, is not): the rider may board any line there."""
    if parent in _COMPLEX:
        return _COMPLEX[parent]
    out = [parent]
    try:
        st = static.stops.set_index("stop_id")
        name = str(st.loc[parent, "stop_name"])
        lat, lon = float(st.loc[parent, "stop_lat"]), float(st.loc[parent, "stop_lon"])
        same = static.stops[(static.stops["stop_name"] == name) & (static.stops["stop_id"] != parent)
                            & (static.stops["parent_station"].isna() | (static.stops["parent_station"] == ""))]
        for r in same.itertuples():
            dlat = (float(r.stop_lat) - lat) * 111_000
            dlon = (float(r.stop_lon) - lon) * 111_000 * math.cos(math.radians(lat))
            if math.hypot(dlat, dlon) <= 250:
                out.append(str(r.stop_id))
    except Exception:
        pass
    _COMPLEX[parent] = out
    return out


def _platforms(static, stop_id: str, direction: str) -> list[str]:
    """The platforms of the station a stop belongs to and of its same-named neighbours, in the leg's direction (the
    rider may board another line there)."""
    plats: set[str] = {stop_id}
    try:
        parent = static.parent_of(stop_id)
        for p in _complex(static, parent) if parent else []:
            plats.update(x for x in static.station_platforms(p) if x.endswith(direction))
    except Exception:
        pass
    return sorted(plats)


def _station_stops(static, name_or_id: str, direction: str) -> list[str]:
    """A station named in the rider's account (a name or a stop id) as its platforms in a direction."""
    try:
        plats: set[str] = set()
        if name_or_id in set(static.stops["stop_id"]):
            parent = static.parent_of(name_or_id) or name_or_id
            plats.update(p for p in static.station_platforms(parent) if p.endswith(direction))
        else:
            # every station of that exact name (W 4 St-Wash Sq is A32 for the A/C/E and D20 for the B/D/F/M)
            hits = static.find_stations(name_or_id)
            exact = hits[hits["stop_name"].str.lower() == name_or_id.strip().lower()] if len(hits) else hits
            for sid in (exact if len(exact) else hits.head(1))["stop_id"]:
                plats.update(p for p in static.station_platforms(str(sid)) if p.endswith(direction))
        return sorted(plats)
    except Exception:
        return []


def _stops_between(static, route: str, direction: str, a: str, b: str) -> int | None:
    try:
        seq = static.canonical_stop_sequence(route, direction)
        i, j = seq.index(a), seq.index(b)
        return j - i if j > i else None
    except Exception:
        return None


def _pick_time(row) -> float:
    """When the train really pulled away from the platform: the store's time, put back by the line's lag, plus the dwell."""
    d = row.get("departure_ts")
    ts = float(d) if d is not None and not (isinstance(d, float) and math.isnan(d)) else float(row["arrival_ts"])
    return pulls_away(ts, row.get("route_id"))


class TrainMatcher:
    """Arrivals around one trip, by platform, with the matches a leg needs."""

    def __init__(self, static, arrivals: pd.DataFrame):
        self.static = static
        self.arr = arrivals if arrivals is not None else pd.DataFrame()

    def at(self, stops: list[str], trip_id: str | None = None) -> pd.DataFrame:
        if self.arr.empty:
            return self.arr
        d = self.arr[self.arr["stop_id"].isin(stops)]
        if trip_id is not None:
            d = d[d["trip_id"] == trip_id]
        return d

    def by_time(self, stops: list[str], ts: float, routes: set[str] | None = None, window: float = MATCH_WINDOW_SEC,
                exclude_trip: str | None = None) -> dict | None:
        """The train that left one of the platforms nearest to a felt departure (the surest of a near tie)."""
        d = self.at(stops)
        if d.empty:
            return None
        if routes:
            d = d[d["route_id"].isin(routes)]
        if exclude_trip is not None and not d.empty:
            d = d[d["trip_id"] != exclude_trip]
        if d.empty:
            return None
        d = d.assign(_t=d.apply(_pick_time, axis=1))
        d = d.assign(_dt=(d["_t"] - ts).abs())
        d = d[d["_dt"] <= window]
        if d.empty:
            return None
        d = d.sort_values(["_dt", "confidence"], ascending=[True, False])
        r = d.iloc[0].to_dict()
        return r

    def by_trip(self, stops: list[str], trip_id: str) -> dict | None:
        d = self.at(stops, trip_id)
        if d.empty:
            return None
        return d.sort_values("arrival_ts").iloc[0].to_dict()


def review_trip(o: dict, static, matcher: TrainMatcher, feval: pd.DataFrame | None = None, notes: list[dict] | None = None) -> dict:
    legs_in = o.get("legs") or []
    events = sorted((e for e in (o.get("events") or []) if isinstance(e, dict) and e.get("ts")), key=lambda e: e["ts"])
    departs = [float(e["ts"]) for e in events if e.get("kind") == "departed"]
    alights = [float(e["ts"]) for e in events if e.get("kind") == "alighted"]
    boarded = {int(b.get("leg", -1)): b for b in (o.get("boarded") or []) if isinstance(b, dict)}
    ride_stops = o.get("rideStops") or []
    created = float(o.get("createdTs") or 0)
    ended = o.get("endedTs")
    r: dict = {
        "id": o.get("id"), "day": _day(created), "createdTs": created, "endedTs": ended, "endedBy": o.get("endedBy"), "startedBy": o.get("startedBy"),
        "routeLabel": o.get("routeLabel"), "offline": bool(o.get("offline")), "rideAssumed": o.get("rideAssumed"),
        "predictedBoardTs": o.get("predictedBoardTs"), "predictedArriveTs": o.get("predictedArriveTs"), "expectedSec": o.get("expectedSec"),
        "schedSec": o.get("schedSec"), "extraMin": o.get("extraMin"), "trainLateSec": o.get("trainLateSec"), "trainHeld": o.get("trainHeld"),
        "transferStation": o.get("transferStation"), "transferWalkSec": o.get("transferWalkSec"), "measuredTransferSec": o.get("measuredTransferSec"),
        "accessSec": o.get("accessSec"), "walkSpeedMPerMin": o.get("walkSpeedMPerMin"), "startDistanceM": o.get("startDistanceM"),
        "nDeparts": len(departs), "nAlights": len(alights), "legs": [],
        "withdrawnDepartures": o.get("withdrawnDepartures") or [],
    }
    truth = next((n["lines"] for n in notes or [] if n.get("lines")), None)
    truth_xfer = next((n["transfers"] for n in notes or [] if n.get("transfers")), None)
    if notes:
        r["riderNotes"] = [n["id"] for n in notes]
        r["riderLines"] = truth
    r["hour"] = datetime.fromtimestamp(created, NY).hour if created else None
    r["weekday"] = datetime.fromtimestamp(created, NY).weekday() if created else None
    for i, lg in enumerate(legs_in):
        plan_route, direction = _split_line(lg.get("line", ""))
        b = boarded.get(i)
        b_route = _split_line(b.get("key", ""))[0] if b else None
        from_stops = _platforms(static, lg.get("from", ""), direction)
        to_stops = _platforms(static, lg.get("to", ""), direction)
        # the rider changed somewhere else than the plan: the legs split where they did
        if truth_xfer and len(legs_in) == 2:
            xs = _station_stops(static, truth_xfer[0], direction)
            if xs:
                if i == 0:
                    to_stops = xs
                else:
                    from_stops = xs
        leg: dict = {
            "leg": i, "plan": lg.get("line"), "from": lg.get("from"), "to": lg.get("to"),
            "fromName": static.stop_name(lg.get("from", "")) if lg.get("from") else "", "toName": static.stop_name(lg.get("to", "")) if lg.get("to") else "",
            "departedTs": departs[i] if i < len(departs) else None, "alightedTs": alights[i] if i < len(alights) else None,
            "stopsFelt": ride_stops[i] if i < len(ride_stops) else None,
            "boardedKey": b.get("key") if b else None, "boardedTrain": b.get("trainId") if b else None, "boardedVerdict": b.get("verdict") if b else None,
            "boardedConfidence": b.get("confidence") if b else None, "boardedEvidence": b.get("evidence") if b else None,
        }
        if truth_xfer and len(legs_in) == 2:
            leg["riderTransfer"] = truth_xfer[0]
        # the train: the one the phone settled on when it named one; otherwise the one that left when the phone felt
        # the pull-away, on the leg's own lines first (the plan's and the phone's), any line at the platform failing that
        match = None
        prev_trip = (r["legs"][-1].get("train") or {}).get("trip_id") if r["legs"] else None
        rider_route = truth[i] if truth and i < len(truth) else None
        # the phone's own train, unless the rider says the leg was on another line
        if b and b.get("trainId") and "|" in b["trainId"] and (rider_route is None or b_route == rider_route):
            tid = b["trainId"].split("|", 1)[1]
            match = matcher.by_trip(from_stops, tid)
            leg["matchedBy"] = "phone" if match is not None else None
        timed, timed_how = None, None
        if rider_route:
            leg["riderRoute"] = rider_route
            if b_route:
                leg["phoneRight"] = (b_route == rider_route)
        if leg["departedTs"]:
            own = {rider_route} if rider_route else {x for x in (plan_route, b_route) if x}
            timed = matcher.by_time(from_stops, leg["departedTs"], routes=own, exclude_trip=prev_trip) if own else None
            timed_how = "departure"
            if timed is None:
                timed = matcher.by_time(from_stops, leg["departedTs"], exclude_trip=prev_trip)
                timed_how = "departure, another line"
        if match is None and timed is not None:
            match, leg["matchedBy"] = timed, timed_how
        if timed is not None:
            leg["trainByDeparture"] = {"route": timed.get("route_id"), "trip_id": timed.get("trip_id"), "leftTs": _pick_time(timed)}
            if b_route and timed_how == "departure, another line":
                leg["boardedAgrees"] = False
            elif b_route:
                leg["boardedAgrees"] = (timed.get("route_id") == b_route)
        if match is not None:
            leg["train"] = {"route": match.get("route_id"), "trip_id": match.get("trip_id"), "trip_key": match.get("trip_key"),
                            "boardTs": _pick_time(match), "arrivedBoardTs": float(match["arrival_ts"])}
            if leg["departedTs"]:
                leg["departureErrSec"] = leg["departedTs"] - _pick_time(match)
            arr = matcher.by_trip(to_stops, str(match.get("trip_id")))
            if arr is not None:
                leg["arriveTs"] = at_platform(float(arr["arrival_ts"]), arr.get("route_id"))
                if leg["alightedTs"]:
                    leg["alightingErrSec"] = leg["alightedTs"] - leg["arriveTs"]
            route = str(match.get("route_id") or plan_route)
            k = _stops_between(static, route, direction, str(match.get("stop_id") or lg.get("from")), str(arr["stop_id"]) if arr is not None else lg.get("to", ""))
            leg["stopsActual"] = k
            if k is not None and leg["stopsFelt"] is not None:
                leg["stopsFeltErr"] = int(leg["stopsFelt"]) - int(k)
        r["legs"].append(leg)
    r["actualArriveTs"] = r["legs"][-1].get("arriveTs") if r["legs"] else None
    dur = (float(ended) - created) if ended and created else None
    # a route ended by hand or changed within minutes is a false start (a test tap, a change of plan): kept, not scored
    r["falseStart"] = bool(dur is not None and dur < 300 and o.get("endedBy") not in ENDED_AT_DESTINATION)
    r["ranItsCourse"] = (not r["falseStart"]) and (o.get("endedBy") in ENDED_AT_DESTINATION
                                                  or (dur is not None and o.get("expectedSec") and dur >= 0.5 * float(o["expectedSec"])))
    if r["actualArriveTs"] is not None and o.get("predictedArriveTs") and r["ranItsCourse"]:
        r["forecastErrSec"] = r["actualArriveTs"] - float(o["predictedArriveTs"])
    if ended and o.get("endedBy") in ENDED_AT_DESTINATION and o.get("expectedSec") is not None and created:
        r["measuredSec"] = float(ended) - created
        r["doorToDoorErrSec"] = r["measuredSec"] - float(o["expectedSec"])
    if o.get("transferWalkSec") is not None and o.get("measuredTransferSec") is not None:
        r["transferErrSec"] = float(o["measuredTransferSec"]) - float(o["transferWalkSec"])
    # the server's own forecast for the destination platform, as it stood when the route started and at the departure
    if feval is not None and not feval.empty and r["legs"] and r["legs"][-1].get("train"):
        last = r["legs"][-1]
        tid = last["train"]["trip_id"]
        rows = feval[(feval["trip_id"] == tid) & (feval["stop_id"].isin(_platforms(static, last["to"], _split_line(last["plan"])[1])))]
        if not rows.empty:
            def nearest(ts):
                d = rows.assign(_dt=(rows["made_ts"] - ts).abs()).sort_values("_dt").iloc[0]
                return {"madeTs": float(d["made_ts"]), "horizonSec": float(d["horizon_sec"]), "modelErrSec": _f(d.get("model_err_sec")),
                        "feedErrSec": _f(d.get("feed_err_sec")), "simErrSec": _f(d.get("sim_err_sec")), "source": d.get("model_source")}
            r["serverForecast"] = {"atStart": nearest(created)}
            if r["legs"][0].get("departedTs"):
                r["serverForecast"]["atDeparture"] = nearest(r["legs"][0]["departedTs"])
    return r


def _f(v):
    try:
        x = float(v)
        return None if math.isnan(x) else x
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------------- across trips

def _stats(vals: list[float]) -> dict | None:
    xs = [float(v) for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not xs:
        return None
    ab = sorted(abs(x) for x in xs)
    return {"n": len(xs), "mean": statistics.fmean(xs), "median": statistics.median(xs), "mae": statistics.fmean(ab),
            "p90abs": ab[min(len(ab) - 1, int(math.ceil(0.9 * len(ab))) - 1)]}


def summarize(reviews: list[dict]) -> dict:
    s: dict = {"n": len(reviews), "endedBy": dict(Counter(r.get("endedBy") or "open" for r in reviews)),
               "startedBy": dict(Counter(r.get("startedBy") or "?" for r in reviews))}
    s["nMatched"] = sum(1 for r in reviews if r.get("actualArriveTs") is not None)
    s["forecast"] = _stats([r.get("forecastErrSec") for r in reviews])
    s["doorToDoor"] = _stats([r.get("doorToDoorErrSec") for r in reviews])
    by_route: dict[str, list] = defaultdict(list)
    for r in reviews:
        if r.get("forecastErrSec") is not None:
            by_route[r.get("routeLabel") or "?"].append(r["forecastErrSec"])
    s["forecastByRoute"] = {k: _stats(v) for k, v in by_route.items()}
    legs = [l for r in reviews if not r.get("falseStart") for l in r.get("legs", [])]   # a tap that started a route is not a ride
    s["detection"] = {
        "departureErr": _stats([l.get("departureErrSec") for l in legs]),
        "alightingErr": _stats([l.get("alightingErrSec") for l in legs]),
        "stopsFeltErr": _stats([l.get("stopsFeltErr") for l in legs]),
        "stopsExact": sum(1 for l in legs if l.get("stopsFeltErr") == 0), "stopsCompared": sum(1 for l in legs if l.get("stopsFeltErr") is not None),
        "boardedAgree": sum(1 for l in legs if l.get("boardedAgrees") is True), "boardedCompared": sum(1 for l in legs if l.get("boardedAgrees") is not None),
        "verdicts": dict(Counter(l.get("boardedVerdict") or "none" for l in legs)),
        "legsMatched": sum(1 for l in legs if l.get("train")), "legs": len(legs),
        "ridesAssumed": sum(1 for r in reviews if r.get("rideAssumed")),
        "falseStarts": sum(1 for r in reviews if r.get("falseStart")),
        "withdrawn": sum(len(r.get("withdrawnDepartures") or []) for r in reviews),
        "departsFelt": sum(r.get("nDeparts", 0) for r in reviews), "alightsFelt": sum(r.get("nAlights", 0) for r in reviews),
    }
    ridden = [l for r in reviews if not r.get("falseStart") for l in r.get("legs", [])]
    called = [l for l in ridden if l.get("phoneRight") is not None]
    s["againstRider"] = {"legs": len(called), "right": sum(1 for l in called if l["phoneRight"]),
                         "planRight": sum(1 for l in ridden if l.get("riderRoute") and _split_line(l.get("plan") or "")[0] == l["riderRoute"]),
                         "riderLegs": sum(1 for l in ridden if l.get("riderRoute"))}
    xfer: dict[str, list] = defaultdict(list)
    for r in reviews:
        if r.get("transferErrSec") is not None and r.get("transferStation"):
            xfer[r["transferStation"]].append((float(r["transferWalkSec"]), float(r["measuredTransferSec"])))
    s["transfers"] = {k: {"n": len(v), "planner": v[0][0], "measuredMedian": statistics.median(m for _, m in v)} for k, v in xfer.items()}
    s["access"] = _stats([r.get("accessSec") for r in reviews if (r.get("accessSec") or 0) > 5])
    s["walkSpeed"] = _stats([r.get("walkSpeedMPerMin") for r in reviews])
    sf = [r["serverForecast"]["atStart"] for r in reviews if r.get("serverForecast")]
    s["server"] = {"n": len(sf), "model": _stats([x.get("modelErrSec") for x in sf]), "feed": _stats([x.get("feedErrSec") for x in sf]),
                   "sim": _stats([x.get("simErrSec") for x in sf])}
    s["findings"] = findings(s, reviews)
    return s


def findings(s: dict, reviews: list[dict]) -> list[str]:
    out: list[str] = []
    f = s.get("forecast")
    if f and f["n"] >= 3:
        if abs(f["mean"]) >= 60:
            out.append(f"The forecast the rider acted on runs {'late' if f['mean'] > 0 else 'early'} by {_mmss(f['mean'])} on average over {f['n']} trips "
                       f"(MAE {_mmss(f['mae'])}): a bias worth checking against the model's hold-out error on these legs (see legs.json and the model report).")
        else:
            out.append(f"The forecast the rider acted on is unbiased on {f['n']} trips (mean {_signed(f['mean'])}, MAE {_mmss(f['mae'])}).")
    elif f:
        out.append(f"Only {f['n']} trip(s) could be scored against a train; the forecast error so far: {', '.join(_signed(r['forecastErrSec']) for r in reviews if r.get('forecastErrSec') is not None)}.")
    else:
        out.append("No trip could be matched to a train yet (no felt departure near a recorded train, or the trip predates the store).")
    eb = s.get("endedBy", {})
    n = max(1, s.get("n", 0))
    if eb.get("timeout", 0) / n >= 0.3:
        out.append(f"{eb['timeout']} of {n} trips ended by timeout rather than at the destination: the arrival clock or the alighting detection let the trip run on.")
    if eb.get("hand", 0) / n >= 0.3:
        out.append(f"{eb['hand']} of {n} trips were ended by hand.")
    d = s.get("detection", {})
    de = d.get("departureErr")
    if de and de["n"] >= 2:
        msg = f"Felt departures land {_signed(de['median'])} (median) from the matched train's departure over {de['n']} legs"
        if d.get("boardedCompared"):
            msg += f"; {d.get('boardedAgree', 0)} of {d['boardedCompared']} of the phone's line calls agree with the train that left then"
        out.append(msg + ".")
    if d.get("withdrawn"):
        out.append(f"{d['withdrawn']} felt pull-away(s) were withdrawn by the phone because no train left the platform then.")
    if d.get("falseStarts"):
        out.append(f"{d['falseStarts']} false start(s) (ended by hand or changed within five minutes) are listed but not scored.")
    if d.get("stopsCompared"):
        out.append(f"Stops felt matched the train's stops exactly on {d['stopsExact']} of {d['stopsCompared']} legs.")
    if d.get("ridesAssumed"):
        out.append(f"{d['ridesAssumed']} ride(s) were assumed from the schedule (no pull-away felt): the sensors were off or missed it.")
    for st, x in s.get("transfers", {}).items():
        if x["measuredMedian"] - x["planner"] >= 30:
            out.append(f"The change at {st} takes {_mmss(x['measuredMedian'])} measured (median of {x['n']}) against the planner's {_mmss(x['planner'])}: "
                       f"the rider's pace model has it; the server's transfer table could follow.")
    ar = s.get("againstRider") or {}
    if ar.get("riderLegs"):
        out.append(f"Against the rider's own account: the plan named the right line on {ar['planRight']} of {ar['riderLegs']} legs"
                   + (f", the phone's call on {ar['right']} of {ar['legs']} it made." if ar.get("legs") else "; the phone made no call on them."))
    nd = [r for r in reviews if not r.get("falseStart") and r.get("nDeparts", 0) == 0 and (r.get("startDistanceM") or 0) > 150]
    if nd:
        out.append(f"{len(nd)} trip(s) started more than 150 m from the station's point and never felt a departure: the tracker stayed "
                   "'on the way', where a departure is suppressed when the last fix is far and no ride is assumed.")
    sv = s.get("server", {})
    if sv.get("n"):
        m, fe = sv.get("model"), sv.get("feed")
        if m and fe:
            out.append(f"On the rider's own trains the server's model was off by {_mmss(m['mae'])} (MAE, {m['n']} trips) at the start of the route; the feed by {_mmss(fe['mae'])}.")
    return out


def legs_for_training(reviews: list[dict], static) -> list[dict]:
    """The legs the rider actually rides, with the hours they ride them, for the trainer's commute-leg evaluation."""
    acc: dict[tuple, dict] = {}
    for r in reviews:
        if r.get("falseStart"):
            continue
        for l in r.get("legs", []):
            route = (l.get("train") or {}).get("route") or _split_line(l.get("plan") or "")[0]
            direction = _split_line(l.get("plan") or "")[1]
            a, b = l.get("from"), l.get("to")
            if not (route and a and b):
                continue
            k = l.get("stopsActual") or _stops_between(static, route, direction, a, b)
            if not k:
                continue
            key = (a, b, route)
            e = acc.setdefault(key, {"from": a, "to": b, "from_name": static.stop_name(a), "to_name": static.stop_name(b), "routes": [route], "k": int(k),
                                     "journey": "rider", "hours": [], "trips": 0})
            e["trips"] += 1
            if r.get("hour") is not None and r["hour"] not in e["hours"]:
                e["hours"].append(r["hour"])
    out = sorted(acc.values(), key=lambda e: -e["trips"])
    for e in out:
        e["hours"].sort()
    return out


# ----------------------------------------------------------------------------- the report

def _table(headers: list[str], rows: list[list]) -> str:
    return "| " + " | ".join(headers) + " |\n|" + "|".join("---" for _ in headers) + "|\n" + "".join("| " + " | ".join(str(c) for c in row) + " |\n" for row in rows)


def render_markdown(reviews: list[dict], s: dict, generated: datetime, legs: list[dict], notes: list[dict] | None = None) -> str:
    L: list[str] = [f"# Trip review\n", f"Generated {generated.astimezone(NY).strftime('%Y-%m-%d %H:%M %Z')} · {s['n']} trips recorded, {s['nMatched']} matched to a train.\n"]
    L.append("## What the trips say\n")
    L.extend(f"- {x}" for x in s.get("findings", []))
    L.append("")
    L.append("## Trips\n")
    rows = []
    for r in sorted(reviews, key=lambda r: -(r.get("createdTs") or 0)):
        legs_txt = " → ".join(f"{(l.get('train') or {}).get('route') or _split_line(l.get('plan') or '')[0]}"
                              + (f" ({l['boardedVerdict']})" if l.get("boardedVerdict") and l["boardedVerdict"] != "onPlan" else "") for l in r.get("legs", [])) or "–"
        rows.append([f"{r['day']} {_hm(r['createdTs'])}", r.get("routeLabel") or "–",
                     f"{r.get('startedBy') or '?'} / {r.get('endedBy') or 'open'}" + (" (false start)" if r.get("falseStart") else "")
                     + (f" · {int(r['startDistanceM'])} m out" if r.get("startDistanceM") is not None else ""),
                     _hm(r.get("predictedArriveTs")), _hm(r.get("actualArriveTs")), _signed(r.get("forecastErrSec")), legs_txt,
                     f"{r.get('nDeparts', 0)}/{r.get('nAlights', 0)}"])
    L.append(_table(["started", "route", "started / ended by", "forecast arrival", "train's arrival", "error", "lines ridden", "felt dep/alight"], rows))
    L.append("Error: the train's real arrival at the destination platform minus the arrival the app forecast when the route started (+ = arrived later than forecast).\n")
    if notes:
        L.append("## The rider's account\n")
        by_id = {r["id"]: r for r in reviews}
        rows = []
        for n in sorted(notes, key=lambda n: (n.get("date") or "", n.get("window") or "")):
            trips_txt = ", ".join(f"{by_id[t]['day']} {_hm(by_id[t]['createdTs'])} {by_id[t].get('routeLabel') or ''}".strip() for t in n.get("tripIds", []) if t in by_id) or "**no trip recorded**"
            rows.append([f"{n.get('date')} {n.get('window')}", " → ".join(n.get("lines") or []) or "–", trips_txt,
                         n.get("account", "").replace("|", "/"), ", ".join(n.get("issues") or []) or "–", n.get("app_build") or "–"])
        L.append(_table(["when", "lines ridden", "trip on the phone", "account", "issues", "app build"], rows))
        L.append("The rider's lines are the ground truth: a leg's train is matched on the line the rider rode, and the phone's line call is scored against it.\n")
    L.append("## Sensing\n")
    d = s.get("detection", {})
    rows = []
    for r in sorted(reviews, key=lambda r: -(r.get("createdTs") or 0)):
        for l in r.get("legs", []):
            if not l.get("train") and not l.get("departedTs"):
                continue
            t = l.get("train") or {}
            rows.append([f"{r['day']} {_hm(r['createdTs'])}", f"{l['leg'] + 1}: {l.get('fromName')} → {l.get('toName')}", l.get("plan") or "–",
                         f"{l.get('boardedKey') or '–'} {l.get('boardedConfidence'):.2f}" if l.get("boardedConfidence") is not None else (l.get("boardedKey") or "–"),
                         f"{t.get('route', '–')} {t.get('trip_id', '')}".strip() + (f" ({l.get('matchedBy')})" if l.get("matchedBy") else ""),
                         _signed(l.get("departureErrSec")), _signed(l.get("alightingErrSec")),
                         (f"{l['stopsFelt'] if l.get('stopsFelt') is not None else '–'} / {l['stopsActual'] if l.get('stopsActual') is not None else '–'}"
                          if l.get("stopsFelt") is not None or l.get("stopsActual") is not None else "–"),
                         "yes" if l.get("boardedAgrees") is True else ("no" if l.get("boardedAgrees") is False else "–")])
    L.append(_table(["trip", "leg", "plan", "phone's line (conf.)", "train matched", "departure felt vs train", "alighting felt vs train", "stops felt / made", "line agrees"], rows))
    L.append(f"Legs matched to a train: {d.get('legsMatched', 0)} of {d.get('legs', 0)} · departures felt {d.get('departsFelt', 0)}, alightings felt {d.get('alightsFelt', 0)} · "
             f"rides assumed from the schedule {d.get('ridesAssumed', 0)} · verdicts {d.get('verdicts', {})}.\n")
    moved = [(r, m) for r in sorted(reviews, key=lambda r: -(r.get("createdTs") or 0)) for m in r.get("motion") or []]
    if moved:
        L.append("## What the phone felt (motion traces)\n")
        L.append(_table(["trip", "event", "at", "push max (g)", "push s", "vibration max (g)", "vibration s", "stepping before / after"],
                        [[f"{r['day']} {_hm(r['createdTs'])}", m["kind"], _hm(m["ts"]), m.get("pushMax", "–"), m.get("pushSecs", "–"),
                          m.get("shakeMax", "–"), m.get("shakeSecs", "–"), f"{m.get('stepBefore')} / {m.get('stepAfter')}"] for r, m in moved]))
        L.append("Ten seconds before to twenty after each event. A train pulling away is a sustained push of 0.05 g or more; vibration alone "
                 "(0.02 g) for eight seconds also counts, which is what a train on the next track can produce.\n")
    L.append("## Walking\n")
    rows = [[st, x["n"], _mmss(x["planner"]), _mmss(x["measuredMedian"]), _signed(x["measuredMedian"] - x["planner"])] for st, x in s.get("transfers", {}).items()]
    if rows:
        L.append(_table(["change at", "trips", "planner", "measured (median)", "difference"], rows))
    a, w = s.get("access"), s.get("walkSpeed")
    L.append(f"Time from the station radius to the platform: {('median ' + _mmss(a['median']) + f' over {a['n']} trips') if a else 'not measured yet'} · "
             f"street pace: {(f'{w['median']:.0f} m/min median over {w['n']} trips') if w else 'not measured yet'}.\n")
    L.append("## The server's own forecast on these trains\n")
    sv = s.get("server", {})
    if sv.get("n"):
        rows = [[k, _mmss(v["mae"]), _signed(v["mean"]), v["n"]] for k, v in (("model", sv.get("model")), ("feed", sv.get("feed")), ("simulation", sv.get("sim"))) if v]
        L.append(_table(["forecast", "MAE", "bias", "trips"], rows))
        L.append("Scored at the destination platform from the projection made nearest the start of the route (the forecast evaluation the server keeps).\n")
    else:
        L.append("No projection kept for these trains yet (the server scores the monitored platforms every ten minutes while it runs).\n")
    L.append("## Feeding the model\n")
    L.append(f"`{LEGS_JSON}` lists the {len(legs)} leg(s) the rider actually rides, with the hours: `make model` evaluates the arrival model on them "
             "(the commute-leg table in the model report) besides the configured journeys, so the model's error is known where it matters. "
             "The forecast errors above are the end-to-end check of the whole chain (feeds → server model → the app's wait, change and carry) on real rides; "
             "a steady bias on a leg at an hour is the signal to look at that leg's profile and carry table.\n")
    if legs:
        L.append(_table(["from", "to", "route", "stops", "hours", "trips"], [[e["from_name"], e["to_name"], e["routes"][0], e["k"], ", ".join(map(str, e["hours"])), e["trips"]] for e in legs]))
    L.append("\n## Sources\n")
    L.append("The phone's trip observations (uploaded to the local server's /api/telemetry at the end of each trip, or copied off the phone with "
             "`make trips`), matched against the server's arrival store (the feeds' own arrivals at every platform), and the server's forecast evaluation.\n")
    return "\n".join(L)


# ----------------------------------------------------------------------------- the whole run

def collect_observations(store, out_dir: Path) -> list[dict]:
    ledger = out_dir / LEDGER
    sources = [load_ledger(ledger), device_observations(out_dir / "device"), github_observations(out_dir / "github")]
    if store is not None:
        try:
            sources.append(store.telemetry())
        except Exception as exc:
            log.warning("telemetry unreadable: %s", exc)
    obs = [o for o in merge_observations(sources) if o.get("installId") != "test"]
    save_ledger(ledger, obs)
    return obs


def arrivals_for(store, static, obs: list[dict]) -> pd.DataFrame:
    """The store's arrivals at every trip's stations (all their platforms) over the trip's span, ±30 minutes."""
    if store is None or not obs:
        return pd.DataFrame()
    frames = []
    for o in obs:
        stops: set[str] = set()
        for lg in o.get("legs") or []:
            d = _split_line(lg.get("line", ""))[1]
            for s in (lg.get("from"), lg.get("to")):
                if s:
                    stops.update(_platforms(static, s, d) + _platforms(static, s, "S" if d == "N" else "N"))
        if not stops or not o.get("createdTs"):
            continue
        t0 = float(o["createdTs"]) - 1800
        t1 = float(o.get("endedTs") or o["createdTs"] + 7200) + 1800
        frames.append(store.arrivals(sorted(stops), t0, t1))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).drop_duplicates(["trip_key", "stop_id"])


def review_all(store, static, out_dir: Path, feval: pd.DataFrame | None = None, now: float | None = None) -> dict:
    """Merge every observation, score each against the trains, and write the report, the JSON and the legs."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    obs = collect_observations(store, out_dir)
    arrivals = arrivals_for(store, static, obs)
    matcher = TrainMatcher(static, arrivals)
    if feval is None and store is not None:
        try:
            feval = store.get_frame("forecast_eval")
        except Exception:
            feval = None
    notes = load_notes(out_dir)
    by_trip = attach_notes(obs, notes)
    reviews = [review_trip(o, static, matcher, feval, by_trip.get(o["id"])) for o in obs]
    traces = {**load_traces(out_dir / "device"), **load_traces(out_dir / "github")}
    for r in reviews:
        tr = next((traces[k] for k in traces if abs(k - (r.get("createdTs") or 0)) <= 5), None)
        if tr is None:
            continue
        r["motion"] = []
        for e in tr.get("events") or []:
            r["motion"].append({"kind": e["kind"], "ts": e["ts"], **(motion_around(tr, e["ts"]) or {})})
        for w in tr.get("withdrawnDepartures") or []:
            r["motion"].append({"kind": "withdrawn", "ts": w, **(motion_around(tr, w) or {})})
    s = summarize(reviews)
    s["notes"] = {"n": len(notes), "withoutTrip": [n["id"] for n in notes if not n.get("tripIds")]}
    if s["notes"]["withoutTrip"]:
        s["findings"].append(f"{len(s['notes']['withoutTrip'])} trip(s) in the rider's account have no record on the phone yet "
                             f"({', '.join(s['notes']['withoutTrip'])}): not pulled yet, or no route was ever started.")
    legs = legs_for_training(reviews, static)
    generated = datetime.fromtimestamp(now, NY) if now else datetime.now(NY)
    (out_dir / REPORT_JSON).write_text(json.dumps({"generated": generated.isoformat(), "summary": s, "trips": reviews, "legs": legs, "notes": notes}, indent=1, default=str))
    (out_dir / LEGS_JSON).write_text(json.dumps(legs, indent=1))
    (out_dir / REPORT_MD).write_text(render_markdown(reviews, s, generated, legs, notes))
    log.info("trip review: %d trips, %d matched, written to %s", s["n"], s["nMatched"], out_dir)
    return {"n": s["n"], "matched": s["nMatched"], "out": str(out_dir)}
