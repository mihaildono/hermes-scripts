#!/usr/bin/env bash
# run-job.sh NAME CMD [ARGS...]
# Runs a scheduled job from OS cron. Success costs zero tokens and stays silent.
#   fail → automatic retry after 2 min (zero tokens)
#   still failing, 1st time → Hermes gets the log tail once to diagnose/fix; reply to #reports
#   later failures → watchdog.sh re-runs it every 30 min until it succeeds; one 🔴 at 3 strikes
#   success after failures → "recovered" message
# Max 3 Discord messages per failure streak, however long it lasts.
set -uo pipefail

NAME=$1; shift
HERMES=$HOME/.hermes/hermes-agent/venv/bin/hermes
REPORTS=discord:1546793280459636758
STATE=$HOME/.local/state/hermes-cron
LOG=$STATE/$NAME.log
FAILS=$STATE/$NAME.fails
RETRY_DELAY=${RETRY_DELAY:-120}
export PATH="$HOME/.local/bin:$PATH"
mkdir -p "$STATE"

send() { if [ -n "${DRY_RUN:-}" ]; then echo "[send] $*"; else "$HERMES" send -q --to "$REPORTS" "$@"; fi; }

# Never overlap with a still-running previous run of the same job
exec 9>"$STATE/$NAME.lock"
flock -n 9 || { echo "$(date -Is) skipped: previous run still active" >> "$LOG"; exit 0; }

# Remember the command so watchdog.sh can re-run it
printf '%q ' "$@" > "$STATE/$NAME.cmd"

run() {
  { echo "=== $(date -Is) $*"; "$@"; } > "$LOG.last" 2>&1
  local rc=$?
  cat "$LOG.last" >> "$LOG"
  return $rc
}

run "$@"; rc=$?
if [ $rc -ne 0 ] && [ -z "${NO_RETRY:-}" ]; then
  sleep "$RETRY_DELAY"
  run "$@"; rc=$?
fi

tail -n 3000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
echo "$(date +%s) $NAME $rc" >> "$STATE/history"

if [ $rc -eq 0 ]; then
  [ -s "$FAILS" ] && send "✅ $NAME recovered after $(cat "$FAILS") failed run(s)"
  rm -f "$FAILS"
  exit 0
fi

fails=$(( $(cat "$FAILS" 2>/dev/null || echo 0) + 1 ))
echo $fails > "$FAILS"

if [ $fails -eq 1 ]; then
  prompt="OS cron job '$NAME' failed twice (exit $rc). Command: $*
Last log lines:
$(tail -n 30 "$LOG.last")

Load skill pi-cron. Diagnose and fix the cause if safe. Do NOT re-run the job:
the watchdog re-runs it within 30 min (re-running here caused duplicate Discord posts).
Never force-push or delete user data. Reply in max 3 short lines: cause, fix, status."
  if [ -n "${DRY_RUN:-}" ]; then
    reply="(dry run: Hermes not called)"
  else
    reply=$(cd "$HOME" && timeout 900 "$HERMES" -z "$prompt" -t terminal,file,skills --ignore-rules 2>&1 | tail -n 15)
  fi
  send --subject "⚠️ $NAME failed (exit $rc). Hermes:" "${reply:-(no reply)}"
elif [ $fails -eq 3 ]; then
  send "🔴 $NAME failed 3 runs in a row (exit $rc). Still retrying every 30 min until it succeeds. Log: $LOG"
fi
exit $rc
