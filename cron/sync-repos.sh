#!/usr/bin/env bash
# sync-repos.sh — daily git sync (replaces 3 Hermes AI jobs, zero tokens).
set -euo pipefail

git -C "$HOME/Personal/techno-tavern" pull -q --rebase --autostash origin main
git -C "$HOME/hermes-scripts" pull -q --rebase --autostash origin main

cd "$HOME/Personal/family-wiki"
if [ -n "$(git status --porcelain)" ]; then
  git add -A
  git commit -qm "auto: daily wiki sync"
fi
git pull -q --rebase origin main
git push -q origin main

echo "✅ repos synced"
