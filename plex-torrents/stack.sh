#!/usr/bin/env bash
# stack.sh — run the media containers only when needed (Pi 3 has ~900 MB RAM).
#   stack.sh up <movies|tv|all>   start a group and wait until its APIs answer
#   stack.sh idle-stop            (cron, every 10 min) stop all of them after IDLE_MIN quiet minutes
#   stack.sh status
# "Busy" = a torrent still downloading, or Radarr/Sonarr still have something queued to import.
# Seeding does not count: stopping qBittorrent ends seeding.
set -uo pipefail
cd "$(dirname "$0")"

CFG=$HOME/plex/plex-torrents/config
STAMP=$HOME/.local/state/hermes-cron/media-active
IDLE_MIN=${IDLE_MIN:-30}
ALL="qbittorrent prowlarr radarr sonarr"
declare -A GROUP=( [movies]="qbittorrent prowlarr radarr" [tv]="qbittorrent prowlarr sonarr" [all]="$ALL" )
declare -A PORT=( [qbittorrent]=8080 [prowlarr]=9696 [radarr]=7878 [sonarr]=8989 )
mkdir -p "$(dirname "$STAMP")"

running() { docker ps --format '{{.Names}}' | grep -xE "${ALL// /|}"; }
key() { grep -oP '(?<=<ApiKey>)[^<]+' "$CFG/$1/config.xml"; }

ready() {   # qBittorrent has no /ping; any HTTP answer means it is up
  local code
  code=$(curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:${PORT[$1]}/$([ "$1" = qbittorrent ] || echo ping)")
  [ "$1" = qbittorrent ] && [ "$code" != 000 ] || [ "$code" = 200 ]
}

busy() {   # exit 0 if anything still needs the stack; API errors count as busy (safe side)
  local r n
  r=$(running)
  if grep -qx qbittorrent <<<"$r"; then
    curl -sf -m 10 http://127.0.0.1:8080/api/v2/torrents/info | python3 -c '
import json, sys
dl = {"downloading","metaDL","forcedMetaDL","stalledDL","queuedDL","forcedDL","checkingDL","checkingResumeData","moving","allocating"}
sys.exit(0 if any(t["state"] in dl for t in json.load(sys.stdin)) else 1)'
    case $? in 0) return 0 ;; 1) ;; *) return 0 ;; esac
  fi
  for app in radarr sonarr; do
    grep -qx $app <<<"$r" || continue
    n=$(curl -sf -m 10 -H "X-Api-Key: $(key $app)" "http://127.0.0.1:${PORT[$app]}/api/v3/queue?pageSize=1" \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["totalRecords"])') || return 0
    [ "$n" -gt 0 ] && return 0
  done
  return 1
}

case ${1:-} in
  up)
    svcs=${GROUP[${2:-}]:-}
    [ -n "$svcs" ] || { echo "usage: $0 up <${!GROUP[*]}>" >&2; exit 2; }
    touch "$STAMP"
    docker compose up -d $svcs >/dev/null 2>&1 || { echo "docker compose up failed" >&2; exit 1; }
    for s in $svcs; do
      for _ in $(seq 60); do ready $s && break; sleep 3; done
      ready $s || { echo "$s did not come up within 3 min" >&2; exit 1; }
    done
    echo "up: $svcs"
    ;;
  idle-stop)
    r=$(running) || exit 0                      # nothing running
    if busy || [ ! -e "$STAMP" ]; then touch "$STAMP"; exit 0; fi
    [ -n "$(find "$STAMP" -mmin -"$IDLE_MIN")" ] && exit 0
    docker compose stop $r >/dev/null 2>&1 && echo "idle ${IDLE_MIN}m, stopped: $(echo $r)"
    ;;
  status)
    r=$(running)
    echo "running: ${r:-none}" | tr '\n' ' '; echo
    [ -n "$r" ] && { busy && echo "busy" || echo "idle since $(date -r "$STAMP" '+%H:%M' 2>/dev/null)"; }
    ;;
  *) sed -n '2,7p' "$0"; exit 2 ;;
esac
