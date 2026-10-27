#!/usr/bin/env bash
# social-post.sh — post the latest Techno Tavern cards to Facebook + Instagram.
# Started by daily-news.sh through run-job.sh, so a failure gets the same flow as
# the digest: retry after 2 min → one Hermes diagnosis → watchdog every 30 min.
# social-post.js records each platform once posted, so retries never post twice.
# Success → post links to #reports. Failures are reported by run-job.sh (max 3 per streak).
set -euo pipefail

HERMES=$HOME/.hermes/hermes-agent/venv/bin/hermes
REPORTS=discord:1546793280459636758
REPORT=news/data/social/discord-message.txt

cd "$HOME/Personal/techno-tavern"
node scripts/social-post.js --auto   # no-op unless AUTO_POST=1 in .env; non-zero exit → retry
if [ -s "$REPORT" ]; then
  "$HERMES" send -q --to "$REPORTS" --file "$REPORT"
  echo "📨 social report sent to #reports"
fi
