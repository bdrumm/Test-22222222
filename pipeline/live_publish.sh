#!/usr/bin/env bash
# Publish a fresh data/live.json into the gh-pages branch worktree (during collection runs).
#   pipeline/live_publish.sh <path/to/live.json>
# No-op when the gh-pages worktree does not hold a built site yet.
set -euo pipefail
SRC="$1"
DIR="${PAGES_DIR:-gh-pages-branch}"
if [ ! -f "$DIR/index.html" ]; then echo "no published site in $DIR yet; skipping live publish"; exit 0; fi
mkdir -p "$DIR/data"
cp "$SRC" "$DIR/data/live.json"
cd "$DIR"
git add data/live.json
if git diff --cached --quiet; then echo "live.json unchanged"; exit 0; fi
git -c user.name=mta-insights-bot -c user.email=mta-insights-bot@users.noreply.github.com commit -q -m "live: $(date -u +%Y-%m-%dT%H:%MZ)"
for i in 1 2 3; do
  if git push -q origin HEAD:gh-pages; then echo "published live.json"; exit 0; fi
  git fetch -q origin gh-pages && git rebase -q origin/gh-pages || true
  sleep $((i * 5))
done
echo "live publish failed (non-fatal)"; exit 0
