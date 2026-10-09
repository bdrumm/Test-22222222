#!/usr/bin/env bash
# A launchd agent that keeps the local server (`make serve`: the site on http://localhost:8000, the feed collector
# into data/mta.sqlite, the trip review) running: it starts at login and is restarted whenever it exits, so a
# reboot or a crash no longer leaves a gap in the store (Oct 8 2026: the Mac restarted at 9:31 and the server,
# a one-off background process, stayed down until the evening).
#   scripts/serve_agent.sh install   -> writes ~/Library/LaunchAgents/com.whichway.serve.plist and starts it
#   scripts/serve_agent.sh remove    -> stops and deletes it
#   scripts/serve_agent.sh status    -> is it running, and the last lines of its log
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.whichway.serve"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$REPO/.local/serve.log"
PORT="${PORT:-8000}"
case "${1:-}" in
  install)
    mkdir -p "$REPO/.local" "$HOME/Library/LaunchAgents"
    test -x "$REPO/.venv/bin/mta-insights" || { echo "no .venv/bin/mta-insights: run make venv first" >&2; exit 1; }
    # a server started by hand on the port would fight the agent for it
    if pid=$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1) && [ -n "$pid" ]; then
      echo "stopping the server already on port $PORT (pid $pid)"; kill "$pid" || true; sleep 2
    fi
    cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$REPO/.venv/bin/mta-insights</string><string>serve</string>
    <string>--site</string><string>_site</string><string>--port</string><string>$PORT</string><string>--db</string><string>data/mta.sqlite</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin</string></dict>
</dict></plist>
PL
    launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    echo "installed $PLIST (serves http://localhost:$PORT at login, restarted if it stops; log: $LOG)"
    ;;
  remove)
    launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
    rm -f "$PLIST"
    echo "removed $LABEL"
    ;;
  status)
    launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E "state =|pid =" || echo "$LABEL is not installed"
    curl -s -m 3 "http://localhost:$PORT/api/health" | cut -c1-120 || true; echo
    tail -5 "$LOG" 2>/dev/null || true
    ;;
  *) echo "usage: $0 install|remove|status" >&2; exit 2;;
esac
