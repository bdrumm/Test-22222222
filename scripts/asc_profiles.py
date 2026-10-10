#!/usr/bin/env python
"""Make (or refresh) the App Store provisioning profiles the TestFlight upload signs with, through the App Store
Connect API, so no Xcode sign-in is needed.

    ASC_KEY_ID=… ASC_ISSUER_ID=… ASC_KEY_PATH=… scripts/asc_profiles.py path/to/distribution.cer [--install]

The certificate is the team's "Apple Distribution" certificate as downloaded from the developer portal (made from a
CSR; see docs/testflight.md). One App Store profile is made per bundle id, named as scripts/testflight.sh expects
(override with WHICHWAY_PROFILE / WHICHWAY_LA_PROFILE); a stale profile of the same name that lacks the certificate
is deleted first. --install also copies the profiles where xcodebuild looks for them.

Needs pyjwt, cryptography and certifi (all in the project venv: make venv).
"""
import base64, json, os, shutil, ssl, subprocess, sys, time, urllib.error, urllib.parse, urllib.request

import certifi, jwt

API = "https://api.appstoreconnect.apple.com/v1"
SSL = ssl.create_default_context(cafile=certifi.where())


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    install = "--install" in sys.argv
    if len(args) != 1:
        print(__doc__)
        return 2
    cert_path = args[0]
    key_id, issuer, key_path = os.environ.get("ASC_KEY_ID"), os.environ.get("ASC_ISSUER_ID"), os.environ.get("ASC_KEY_PATH")
    if not (key_id and issuer and key_path):
        print("set ASC_KEY_ID, ASC_ISSUER_ID and ASC_KEY_PATH (see scripts/testflight.sh)")
        return 2
    bundle = os.environ.get("WHICHWAY_BUNDLE") or _bundle_from_xcconfig()
    bundles = {bundle: os.environ.get("WHICHWAY_PROFILE", "WhichWay App Store"),
               bundle + ".LiveActivity": os.environ.get("WHICHWAY_LA_PROFILE", "WhichWay LiveActivity App Store")}

    with open(key_path) as f:
        token = jwt.encode({"iss": issuer, "iat": int(time.time()), "exp": int(time.time()) + 1200, "aud": "appstoreconnect-v1"},
                           f.read(), algorithm="ES256", headers={"kid": key_id})

    def call(method, path, body=None):
        req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body else None,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, context=SSL) as r:
                return json.load(r) if r.status != 204 else {}
        except urllib.error.HTTPError as e:
            print(f"{method} {path} -> HTTP {e.code}: {e.read().decode()[:600]}")
            sys.exit(1)

    serial = subprocess.run(["openssl", "x509", "-inform", "DER", "-in", cert_path, "-noout", "-serial"],
                            capture_output=True, text=True, check=True).stdout.strip().split("=")[-1].upper()
    certs = call("GET", "/certificates?filter[certificateType]=DISTRIBUTION,IOS_DISTRIBUTION&limit=50")["data"]
    match = [c for c in certs if c["attributes"].get("serialNumber", "").upper() == serial]
    if not match:
        print(f"no distribution certificate on the portal has serial {serial}; the portal has:")
        for c in certs:
            print("  ", c["id"], c["attributes"].get("name"), c["attributes"].get("serialNumber"), c["attributes"].get("expirationDate"))
        return 1
    cert_id = match[0]["id"]

    out_dir = os.path.join(os.path.dirname(os.path.abspath(cert_path)), "profiles")
    os.makedirs(out_dir, exist_ok=True)
    xcode_dir = os.path.expanduser("~/Library/Developer/Xcode/UserData/Provisioning Profiles")
    for bid_name, name in bundles.items():
        bids = [b for b in call("GET", f"/bundleIds?filter[identifier]={bid_name}&filter[platform]=IOS")["data"]
                if b["attributes"]["identifier"] == bid_name]
        if not bids:
            print(f"no bundle id registered for {bid_name}")
            return 1
        prof = None
        for p in call("GET", f"/profiles?filter[name]={urllib.parse.quote(name)}&include=certificates")["data"]:
            cert_ids = [c["id"] for c in p["relationships"].get("certificates", {}).get("data", [])]
            if p["attributes"]["profileState"] == "ACTIVE" and cert_id in cert_ids:
                prof = p
                break
            call("DELETE", f"/profiles/{p['id']}")
            print(f"deleted stale profile {p['id']} ({name})")
        if prof is None:
            prof = call("POST", "/profiles", {"data": {"type": "profiles", "attributes": {"name": name, "profileType": "IOS_APP_STORE"},
                        "relationships": {"bundleId": {"data": {"type": "bundleIds", "id": bids[0]["id"]}},
                                          "certificates": {"data": [{"type": "certificates", "id": cert_id}]}}}})["data"]
            print(f"created profile {name}")
        path = os.path.join(out_dir, f"{prof['attributes']['uuid']}.mobileprovision")
        with open(path, "wb") as f:
            f.write(base64.b64decode(prof["attributes"]["profileContent"]))
        print(f"{bid_name}: {name} -> {path} (expires {prof['attributes']['expirationDate'][:10]})")
        if install:
            os.makedirs(xcode_dir, exist_ok=True)
            shutil.copy(path, xcode_dir)
            print(f"  installed in {xcode_dir}")
    return 0


def _bundle_from_xcconfig() -> str:
    cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ios", "WhichWay", "Config", "Local.xcconfig")
    try:
        with open(cfg) as f:
            for line in f:
                if line.strip().startswith("PRODUCT_BUNDLE_IDENTIFIER"):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return "com.barondrumm.whichway"


if __name__ == "__main__":
    sys.exit(main())
