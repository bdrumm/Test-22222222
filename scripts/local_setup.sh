#!/usr/bin/env bash
# One command for a Mac: Python environment, local site data, Xcode config, the local server and Xcode.
#   scripts/local_setup.sh                 # synthetic (offline) data
#   scripts/local_setup.sh --real          # the full build from the collected history (several minutes)
#   WHICHWAY_TEAM=ABCDE12345 scripts/local_setup.sh   # your Apple team id (else asked for, may be left blank)
set -euo pipefail
cd "$(dirname "$0")/.."
REAL=0; for a in "$@"; do [ "$a" = "--real" ] && REAL=1; done
PORT="${PORT:-8000}"
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

command -v python3 >/dev/null || { echo "python3 is required (brew install python)"; exit 1; }
if ! command -v xcodebuild >/dev/null; then echo "Xcode is required (App Store), then: sudo xcode-select -s /Applications/Xcode.app"; exit 1; fi

say "1/5 Python environment"
make venv

say "2/5 Site data"
if [ "$REAL" = 1 ]; then make site; else make site-synthetic; fi

say "3/5 Xcode configuration"
CFG=ios/WhichWay/Config/Local.xcconfig
if [ ! -f "$CFG" ]; then
  TEAM="${WHICHWAY_TEAM:-}"
  if [ -z "$TEAM" ] && [ -t 0 ]; then read -r -p "Apple team id for signing (Xcode > Settings > Accounts; blank to set it in Xcode later): " TEAM || true; fi
  BUNDLE="com.$(id -un | tr -cd '[:alnum:]' | tr '[:upper:]' '[:lower:]').whichway"
  {
    echo "// Written by scripts/local_setup.sh; edit freely (git-ignored)."
    echo "DEVELOPMENT_TEAM = ${TEAM}"
    echo "PRODUCT_BUNDLE_IDENTIFIER = ${BUNDLE}"
    echo "WHICHWAY_BASE_URL = http:/\$()/localhost:${PORT}/data/"
  } > "$CFG"
  echo "wrote $CFG (bundle id $BUNDLE, team '${TEAM:-unset}')"
else
  echo "$CFG exists; leaving it"
fi

say "4/5 Local server on http://localhost:${PORT}"
mkdir -p .local
if curl -sf "http://localhost:${PORT}/data/client_schedule.json" >/dev/null 2>&1; then
  echo "something already serves port ${PORT}; leaving it"
else
  nohup make serve PORT="$PORT" > .local/serve.log 2>&1 &
  echo $! > .local/serve.pid
  for i in $(seq 1 60); do
    if curl -sf "http://localhost:${PORT}/data/client_schedule.json" >/dev/null 2>&1; then break; fi
    sleep 1
  done
  if curl -sf "http://localhost:${PORT}/data/client_schedule.json" >/dev/null 2>&1; then
    echo "serving (pid $(cat .local/serve.pid), log .local/serve.log); stop with: kill \$(cat .local/serve.pid)"
  else
    echo "the server did not answer within 60 s; see .local/serve.log"; tail -20 .local/serve.log || true
  fi
fi

say "5/5 Xcode"
open ios/WhichWay/WhichWay.xcodeproj
cat <<MSG

Next in Xcode: pick an iPhone Simulator and press Run. The Debug build starts against
http://localhost:${PORT}/data/ and the scheme simulates the phone at 14 St-Union Sq.
If signing complains, set your team under Signing & Capabilities (or put it in $CFG).
Site: http://localhost:${PORT}   ·   package tests: make ios-test   ·   Python tests: make test
MSG
