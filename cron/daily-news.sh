#!/usr/bin/env bash
# daily-news.sh — Techno Tavern daily summary.
# Scripts do all the work; the only AI step is one bare model call (no agent,
# no tools, no skills) that turns the digest into top-news.json.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
HERMES=$HOME/.hermes/hermes-agent/venv/bin/hermes
NEWS=discord:1546793311115681882
# Once-per-slot guard: retries, the watchdog, Hermes or a manual run can never post twice.
# A run belongs to the most recent 18:30 slot, so a retry that finally succeeds after
# midnight posts the missed edition and does not block that evening's run.
POSTED=$HOME/.local/state/hermes-cron/daily-news.posted
if [ "$(date +%H%M)" -ge 1830 ]; then SLOT=$(date +%F); else SLOT=$(date -d yesterday +%F); fi
if [ "$(cat "$POSTED" 2>/dev/null)" = "$SLOT" ]; then
  echo "ℹ️ edition $SLOT already posted, nothing to do"
  exit 0
fi

cd "$HOME/Personal/techno-tavern"
./scripts/daily-top-news.sh prepare
node "$HERE/summarize-news.mjs" prompts/daily-news-summary.md news/data/news-digest.md news/data/top-news.json
./scripts/daily-top-news.sh finish
"$HERMES" send -q --to "$NEWS" --file news/data/discord-message.txt
echo "$SLOT" > "$POSTED"
echo "📨 posted to #news"
