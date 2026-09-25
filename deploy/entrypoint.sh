#!/usr/bin/env bash
# Modes: serve (default) | collect | build | backfill | context
set -euo pipefail
DATA="${MTA_DATA_DIR:-/data}"
mkdir -p "$DATA"
GTFS="$DATA/gtfs_subway.zip"
if [ ! -f "$GTFS" ] || [ "$(find "$GTFS" -mmin +1440 2>/dev/null)" ]; then
  echo "downloading static GTFS"; curl -sSL -o "$GTFS" https://rrgtfsfeeds.s3.amazonaws.com/gtfs_subway.zip || true
fi
mode="${1:-serve}"; shift || true
case "$mode" in
  serve)   exec mta-insights serve --site /app/site --gtfs "$GTFS" --db "$DATA/mta.sqlite" --targets "${MTA_TARGETS}" --port "${PORT:-8000}" --interval "${INTERVAL:-30}" "$@" ;;
  collect) exec mta-insights collect --gtfs "$GTFS" --db "$DATA/mta.sqlite" --feeds 1234567S,ace,bdfm,g,jz,l,nqrw,si --all-stops --alerts "$@" ;;
  build)   exec python -m pipeline.build_site --data-dir "$DATA/branch" --gtfs "$GTFS" --targets "${MTA_TARGETS}" --out "$DATA/site" "$@" ;;
  backfill) exec python -m pipeline.backfill --data-dir "$DATA/branch" "$@" ;;
  context) exec python -m pipeline.context --data-dir "$DATA/branch" --gtfs "$GTFS" --targets "${MTA_TARGETS}" "$@" ;;
  *) exec "$mode" "$@" ;;
esac
