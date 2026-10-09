#!/usr/bin/env bash
# What changed in the iOS app since the last Android port pass (the commit in android/PORTING.md), with the
# Kotlin file each change maps to. Read-only: it never touches ios/.
#   android/scripts/ios-drift.sh          # summary
#   android/scripts/ios-drift.sh -p       # with the diffs
set -euo pipefail
cd "$(dirname "$0")/../.."
BASE=${SINCE:-}; [ -n "$BASE" ] || BASE=$(grep -oE 'iOS at `[0-9a-f]+`' android/PORTING.md | head -1 | grep -oE '[0-9a-f]{7,}')
[ -n "$BASE" ] || { echo "no 'iOS at \`<sha>\`' line in android/PORTING.md"; exit 1; }
echo "iOS changes since the last port pass ($BASE, $(git log -1 --format=%ad --date=short "$BASE")):"
echo
FILES=$(git diff --name-only "$BASE" HEAD -- ios/WhichWay/WhichWay ios/WhichWay/WhichWayShared ios/WhichWayCore/Tests/WhichWayCoreTests/Fixtures | sort)
if [ -z "$FILES" ]; then echo "  nothing: Android is level with iOS"; exit 0; fi
for f in $FILES; do
  base=$(basename "$f" .swift)
  row=$(grep -F "$base.swift" android/PORTING.md | head -1 | awk -F'|' '{print $3}' | sed 's/^ *//;s/ *$//' || true)
  printf '  %-60s %s\n' "$f" "${row:-(not ported)}"
done
echo
git log --oneline "$BASE..HEAD" -- ios/WhichWay/WhichWay ios/WhichWay/WhichWayShared
if [ "${1:-}" = "-p" ]; then git diff "$BASE" HEAD -- $FILES; fi
