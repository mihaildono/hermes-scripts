#!/usr/bin/env bash
# daily-report.sh — one zero-token status line for the last 24h to #reports.
set -uo pipefail

HERMES=$HOME/.hermes/hermes-agent/venv/bin/hermes
REPORTS=discord:1546793280459636758
HISTORY=$HOME/.local/state/hermes-cron/history
since=$(( $(date +%s) - 86400 ))

line=$(awk -v s="$since" '$1 >= s { n[$2]++; if ($3 != 0) f[$2]++ }
  END { for (j in n) printf "%s %d/%d %s · ", j, n[j] - f[j], n[j], (f[j] ? "⚠️" : "✅") }' "$HISTORY" 2>/dev/null)

msg="🗓️ Jobs (24h): ${line% · }"
[ -z "$line" ] && msg="🗓️ Jobs (24h): no runs recorded ⚠️"
if [ -n "${DRY_RUN:-}" ]; then echo "$msg"; else "$HERMES" send -q --to "$REPORTS" "$msg"; fi
