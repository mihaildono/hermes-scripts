#!/usr/bin/env bash
# watchdog.sh — zero-token watchdog, runs every 30 min from cron.
#  1. Re-runs jobs whose last run failed, every 30 min until they succeed.
#     Alerts stay fixed per streak (see run-job.sh), so retries never spam Discord.
#  2. Alerts once if a job has not run within its expected window (Pi off, cron broken…).
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
HERMES=$HOME/.hermes/hermes-agent/venv/bin/hermes
REPORTS=discord:1546793280459636758
STATE=$HOME/.local/state/hermes-cron
now=$(date +%s)

send() { if [ -n "${DRY_RUN:-}" ]; then echo "[send] $*"; else "$HERMES" send -q --to "$REPORTS" "$@"; fi; }

# 1. Retry failed jobs (NO_RETRY: watchdog itself is the retry loop)
for f in "$STATE"/*.fails; do
  [ -e "$f" ] || continue
  name=$(basename "$f" .fails)
  [ -s "$STATE/$name.cmd" ] || continue
  eval "NO_RETRY=1 \"$HERE/run-job.sh\" \"$name\" $(cat "$STATE/$name.cmd")"
done

# 2. Missed-run check: job name → max seconds between runs
declare -A MAX_AGE=( [news-fetch]=$((4*3600)) [daily-news]=$((25*3600)) [sync-repos]=$((25*3600)) )
for name in "${!MAX_AGE[@]}"; do
  last=$(awk -v n="$name" '$2 == n { t = $1 } END { print t + 0 }' "$STATE/history" 2>/dev/null)
  [ "$last" -eq 0 ] && continue   # never ran yet (fresh install)
  flag=$STATE/$name.missed
  if [ $(( now - last )) -gt "${MAX_AGE[$name]}" ]; then
    [ -e "$flag" ] || { send "🕒 $name has not run since $(date -d @"$last" '+%a %H:%M'). Check: crontab -l"; touch "$flag"; }
  else
    rm -f "$flag"
  fi
done
