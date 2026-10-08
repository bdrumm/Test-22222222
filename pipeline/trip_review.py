"""Collect the rider's trips and review them against the trains.

    python -m pipeline.trip_review --pull          # copy the app's files off the connected phone first
    python -m pipeline.trip_review                 # just merge and review what has arrived

Observations reach the Mac three ways: the app writes each trip to a private GitHub repository from any
connection (WHICHWAY_DATA_REPO, default bdrumm/whichway-data; `--pull` and the local server pull it into
data/trips/github), the app uploads to the local server (POST /api/telemetry, stored in data/mta.sqlite) at the
end of each trip when its trip server is set and reachable, and `--pull` copies the app's own files
(trip observations, pace model, habits) off a paired phone over USB or Wi-Fi with devicectl (the phone must be
unlocked and the app's bundle id known: Config/Local.xcconfig or --bundle). Everything lands in data/trips/:
device/<pull>/… (raw pulls), observations.jsonl (the merged ledger), trip_review.md / .json (the review),
legs.json (the rider's legs for `make model`).
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from mta_delay_insights import trips
from mta_delay_insights.storage.db import Store
from pipeline import lib

log = logging.getLogger("trip_review")
APP_FILES = "Library/Application Support/WhichWay"


def bundle_id(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    p = lib.ROOT / "ios" / "WhichWay" / "Config" / "Local.xcconfig"
    if p.exists():
        m = re.search(r"^\s*PRODUCT_BUNDLE_IDENTIFIER\s*=\s*(\S+)", p.read_text(), re.M)
        if m:
            return m.group(1)
    return None


def paired_devices() -> list[dict]:
    """Physical devices devicectl knows, newest-paired first."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "devices.json"
        try:
            subprocess.run(["xcrun", "devicectl", "list", "devices", "--json-output", str(out)], check=True, capture_output=True, timeout=60)
            data = json.loads(out.read_text())
        except Exception as exc:
            log.warning("devicectl list failed: %s", exc)
            return []
    devs = []
    for d in data.get("result", {}).get("devices", []):
        hw = d.get("hardwareProperties", {})
        if hw.get("reality") != "physical":
            continue
        devs.append({"udid": hw.get("udid") or d.get("identifier"), "name": d.get("deviceProperties", {}).get("name"),
                     "state": d.get("connectionProperties", {}).get("pairingState")})
    return devs


def pull(out_dir: Path, udid: str | None, bundle: str | None) -> Path | None:
    """Copy the app's files off the phone into out_dir/device/<timestamp>/; None when no phone answers."""
    bundle = bundle_id(bundle)
    if not bundle:
        log.warning("no bundle id: pass --bundle or set PRODUCT_BUNDLE_IDENTIFIER in ios/WhichWay/Config/Local.xcconfig")
        return None
    if not udid:
        devs = paired_devices()
        if not devs:
            log.warning("no paired phone")
            return None
        udid = devs[0]["udid"]
        log.info("phone: %s (%s)", devs[0].get("name"), udid)
    dest = out_dir / "device" / datetime.now().strftime("%Y%m%d-%H%M%S")
    dest.mkdir(parents=True, exist_ok=True)
    cmd = ["xcrun", "devicectl", "device", "copy", "from", "--device", udid, "--domain-type", "appDataContainer", "--domain-identifier", bundle,
           "--source", APP_FILES, "--destination", str(dest)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or b"").decode(errors="replace").strip().splitlines()
        log.warning("copy from the phone failed (locked, asleep, or not on the network?): %s", err[-1] if err else exc)
        shutil.rmtree(dest, ignore_errors=True)
        return None
    except Exception as exc:
        log.warning("copy from the phone failed: %s", exc)
        shutil.rmtree(dest, ignore_errors=True)
        return None
    # the app's data cache (feeds, schedule) comes along with the container; only its own records are kept
    for c in [d for d in dest.rglob("cache") if d.is_dir()]:
        shutil.rmtree(c, ignore_errors=True)
    got = sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file())
    log.info("pulled %d file(s) into %s: %s", len(got), dest, ", ".join(got[:6]))
    return dest


def import_export(out_dir: Path, path: Path) -> Path | None:
    """A whichway-trips.json the app exported (Settings > Export as JSON, AirDropped or mailed over) joins the
    pulls as its own device folder, so the ledger takes it up like a copy off the phone."""
    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        log.warning("not a trips export (%s): %s", path, exc)
        return None
    if not isinstance(data, list) or not all(isinstance(x, dict) and x.get("id") for x in data):
        log.warning("not a trips export: %s", path)
        return None
    dest = out_dir / "device" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-export") / "telemetry"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest / "observations.json")
    log.info("imported %d observation(s) from %s", len(data), path)
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pull", action="store_true", help="copy the app's files off the connected phone first (non-fatal when it is not there)")
    ap.add_argument("--device", default=None, help="the phone's UDID (default: the first paired phone)")
    ap.add_argument("--bundle", default=None, help="the app's bundle id (default: Config/Local.xcconfig)")
    ap.add_argument("--import", dest="import_", default=None, metavar="FILE", help="a whichway-trips.json the app exported (Settings > Export as JSON)")
    ap.add_argument("--note", default=None, metavar="TEXT", help="the rider's account of a trip, kept as ground truth (with --when and --lines)")
    ap.add_argument("--when", default=None, help='for --note: "YYYY-MM-DD morning|afternoon|evening" or "YYYY-MM-DD HH:MM-HH:MM"')
    ap.add_argument("--lines", default="", help="for --note: the lines ridden, in order, e.g. E,F")
    ap.add_argument("--issue", action="append", default=[], help="for --note: a short tag, repeatable (start-not-detected, line-wrong, ...)")
    ap.add_argument("--build", default=None, help="for --note: the app build on the phone then")
    ap.add_argument("--transfer", action="append", default=[], help="for --note: where the rider changed, when not where the plan did")
    ap.add_argument("--db", default="data/mta.sqlite")
    ap.add_argument("--gtfs", default="data/gtfs_subway.zip")
    ap.add_argument("--out", default="data/trips")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    out = Path(args.out)
    if args.note:
        if not args.when or " " not in args.when.strip():
            ap.error('--note needs --when "YYYY-MM-DD afternoon" (or "YYYY-MM-DD HH:MM-HH:MM")')
        date, window = args.when.strip().split(None, 1)
        n = trips.add_note(out, date, window, args.lines.split(","), args.note, args.issue, args.build, args.transfer)
        log.info("note kept: %s", n["id"])
    if args.import_:
        import_export(out, Path(args.import_).expanduser())
    if args.pull:
        if trips.sync_data_repo(out):
            log.info("new trips from the GitHub data repository %s", trips.data_repo())
        pull(out, args.device, args.bundle)
    static = lib.load_static(args.gtfs)
    store = Store(args.db) if Path(args.db).exists() else None
    if store is None:
        log.warning("no store at %s: trips are merged but cannot be matched to trains", args.db)
    res = trips.review_all(store, static, out, now=time.time())
    print(f"{res['n']} trips, {res['matched']} matched to a train → {out / trips.REPORT_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
