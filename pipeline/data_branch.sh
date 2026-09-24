#!/usr/bin/env bash
# Manage the `data` branch that stores collected arrivals, alerts and context datasets.
#   pipeline/data_branch.sh checkout   -> creates ./data-branch worktree on the data branch
#   pipeline/data_branch.sh commit     -> commits and pushes any changes in ./data-branch
set -euo pipefail
BRANCH="${DATA_BRANCH:-data}"
DIR="${DATA_DIR:-data-branch}"
case "${1:-}" in
  checkout)
    git config --global user.name "mta-insights-bot"
    git config --global user.email "mta-insights-bot@users.noreply.github.com"
    if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
      git fetch origin "$BRANCH" --depth 1
      git worktree add "$DIR" "origin/$BRANCH"
      git -C "$DIR" checkout -B "$BRANCH"
    else
      git worktree add --orphan -b "$BRANCH" "$DIR"
      echo "# data branch" > "$DIR/README.md"
    fi
    ;;
  commit)
    cd "$DIR"
    git add -A
    if git diff --cached --quiet; then echo "no data changes"; exit 0; fi
    git commit -q -m "data: $(date -u +%Y-%m-%dT%H:%MZ) ${GITHUB_RUN_ID:-local}"
    for i in 1 2 3; do
      if git push origin "HEAD:$BRANCH"; then exit 0; fi
      git fetch origin "$BRANCH" && git rebase "origin/$BRANCH" || true
      sleep $((i * 5))
    done
    echo "push failed" >&2; exit 1
    ;;
  *) echo "usage: $0 checkout|commit" >&2; exit 2;;
esac
