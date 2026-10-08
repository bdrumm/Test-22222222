#!/usr/bin/env bash
# A launchd agent that runs `make trips` every night at 23:40 (pull the phone's trips when it is on the
# network, merge the uploads, review against the trains, write data/trips/trip_review.md).
#   scripts/trips_agent.sh install   -> writes ~/Library/LaunchAgents/com.whichway.trips.plist and loads it
#   scripts/trips_agent.sh remove    -> unloads and deletes it
#   scripts/trips_agent.sh run       -> runs the job now (what the agent runs)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.whichway.trips"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$REPO/data/trips/logs"
case "${1:-}" in
  install)
    mkdir -p "$LOG" "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>-lc</string>
    <string>cd "$REPO" && make trips</string>
  </array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>23</integer><key>Minute</key><integer>40</integer></dict>
  <key>StandardOutPath</key><string>$LOG/nightly.log</string>
  <key>StandardErrorPath</key><string>$LOG/nightly.log</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin</string></dict>
</dict></plist>
PL
    launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    echo "installed $PLIST (nightly 23:40; log: $LOG/nightly.log)"
    ;;
  remove)
    launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
    rm -f "$PLIST"
    echo "removed $LABEL"
    ;;
  run)
    cd "$REPO" && make trips
    ;;
  *) echo "usage: $0 install|remove|run" >&2; exit 2;;
esac
