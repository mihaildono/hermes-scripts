#!/usr/bin/env python3
"""
Hermes TV Manager Script: Sonarr counterpart of media_manager.py
----------------------------------------------------------------
- Search & download a whole series or one season to Plex (Auto-approved)
- Detached background monitor with Discord @everyone notifications
- Delete a series from Sonarr and disk (exact match required)
- Compact CLI output optimized for low-parameter / free LLMs

Usage:
    python3 tv_manager.py download --query "Severance" --season 1
    python3 tv_manager.py download --query "Dark" --quality "720p"
    python3 tv_manager.py list
    python3 tv_manager.py delete --query "Severance"
"""

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import requests

from media_manager import send_discord_notification, script_dir

SONARR_URL = os.getenv("SONARR_URL", "http://127.0.0.1:8989").rstrip("/")
ROOT_FOLDER = os.getenv("SONARR_ROOT_FOLDER", "/data/tv")
CONFIG_XML = Path.home() / "plex/plex-torrents/config/sonarr/config.xml"


def read_api_key():
    """Sonarr's own config is the source of truth, so key rotation needs no .env edit."""
    try:
        return re.search(r"<ApiKey>([^<]+)</ApiKey>", CONFIG_XML.read_text()).group(1)
    except Exception:
        return os.getenv("SONARR_API_KEY", "")


HEADERS = {"X-Api-Key": read_api_key(), "Content-Type": "application/json"}


def api(method: str, path: str, **kwargs):
    res = requests.request(method, f"{SONARR_URL}/api/v3/{path}", headers=HEADERS, timeout=15, **kwargs)
    res.raise_for_status()
    return res.json() if res.content else None


def ensure_stack():
    """Containers run on demand (stack.sh stops them when idle); start the TV ones."""
    res = subprocess.run([str(script_dir / "stack.sh"), "up", "tv"], capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[WARN] stack.sh up tv: {res.stderr.strip()}", file=sys.stderr)


def check_connection():
    try:
        api("GET", "system/status")
    except Exception as e:
        print(f"[!] Error: Cannot connect to Sonarr at {SONARR_URL}: {e}")
        send_discord_notification("@everyone ⚠️ **Download Error:** Sonarr service unreachable on Plex server.")
        sys.exit(1)


def resolve_quality_profile_id(requested_quality: str = None) -> int:
    """Default HD-1080p; '4k'/'ultra' -> Ultra-HD; '720' -> HD-720p."""
    profiles = {p["name"].lower(): p["id"] for p in api("GET", "qualityprofile")}
    q = (requested_quality or "1080p").lower()
    want = "ultra-hd" if ("4k" in q or "ultra" in q or "2160" in q) else "hd-720p" if "720" in q else "hd-1080p"
    return profiles.get(want, profiles.get("hd-1080p", next(iter(profiles.values()))))


def aired_episodes(series_id: int, season: int = None):
    """Monitored-relevant episodes that have already aired (specials excluded)."""
    now = datetime.now(timezone.utc).isoformat()
    params = {"seriesId": series_id}
    if season is not None:
        params["seasonNumber"] = season
    return [e for e in api("GET", "episode", params=params)
            if e["seasonNumber"] > 0 and e.get("airDateUtc") and e["airDateUtc"] < now]


def download_series(query: str, season: int = None, quality: str = None, year: int = None):
    results = api("GET", "series/lookup", params={"term": query})
    if not results:
        print(f"[!] No matching series found for '{query}'.")
        send_discord_notification(f"@everyone ⚠️ **Download Error:** Series '{query}' was not found.")
        sys.exit(1)
    show = next((s for s in results if year and s.get("year") == year), results[0])
    title, show_year = show["title"], show.get("year", "")
    seasons = [s["seasonNumber"] for s in show.get("seasons", []) if s["seasonNumber"] > 0]
    if season is not None and seasons and season not in seasons:
        print(f"[!] {title} ({show_year}) has no season {season}. Seasons: {', '.join(map(str, seasons))}")
        sys.exit(1)

    scope = f"Season {season}" if season is not None else "all seasons"
    quality_label = quality.upper() if quality else "HD-1080p"
    existing = next((s for s in api("GET", "series") if s["tvdbId"] == show["tvdbId"]), None)

    try:
        if existing:
            series_id = existing["id"]
        else:
            show.update(
                qualityProfileId=resolve_quality_profile_id(quality),
                rootFolderPath=ROOT_FOLDER,
                monitored=True,
                seasonFolder=True,
                monitorNewItems="all" if season is None else "none",
                addOptions={"monitor": "all" if season is None else "none", "searchForMissingEpisodes": False},
            )
            series_id = api("POST", "series", json=show)["id"]

        # Episodes are filled in by Sonarr's refresh right after adding; wait for them
        for _ in range(30):
            episodes = aired_episodes(series_id, season)
            if episodes:
                break
            time.sleep(3)
        if not episodes:
            print(f"[!] {title} ({show_year}) {scope}: no aired episodes yet.")
            return

        api("PUT", "episode/monitor", json={"episodeIds": [e["id"] for e in episodes], "monitored": True})
        if season is None:
            api("POST", "command", json={"name": "SeriesSearch", "seriesId": series_id})
        else:
            series = api("GET", f"series/{series_id}")
            for s in series["seasons"]:
                if s["seasonNumber"] == season:
                    s["monitored"] = True
            api("PUT", f"series/{series_id}", json=series)
            api("POST", "command", json={"name": "SeasonSearch", "seriesId": series_id, "seasonNumber": season})
    except Exception as e:
        print(f"[!] Failed to queue {title}: {e}")
        send_discord_notification(f"@everyone ⚠️ **Download Error:** Could not queue {title} ({show_year}) - {e}")
        sys.exit(1)

    send_discord_notification(f"@everyone 📺 **Download Started:** {title} ({show_year}) {scope}, {len(episodes)} episodes [{quality_label}]")
    print(f"✅ Queued for download: {title} ({show_year}) {scope}, {len(episodes)} episodes [{quality_label}]")
    spawn_background_monitor(series_id, season)


def spawn_background_monitor(series_id: int, season: int = None):
    cmd = [sys.executable, str(Path(__file__).resolve()), "monitor", "--series-id", str(series_id)]
    if season is not None:
        cmd += ["--season", str(season)]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def run_monitor(series_id: int, season: int = None, timeout_seconds: int = 12 * 3600):
    """Detached monitor: reports completion, queue warnings, or timeout."""
    series = api("GET", f"series/{series_id}")
    label = f"{series['title']} ({series.get('year', '')})" + (f" Season {season}" if season is not None else "")
    start, last_warning = time.time(), None

    while time.time() - start < timeout_seconds:
        try:
            episodes = aired_episodes(series_id, season)
            done = sum(1 for e in episodes if e.get("hasFile"))
            if episodes and done == len(episodes):
                send_discord_notification(f"@everyone ✅ **Download Complete:** {label}, {done} episodes are ready on Plex!")
                return
            queue = api("GET", "queue", params={"seriesId": series_id, "pageSize": 100}).get("records", [])
            for item in queue:
                if item.get("trackedDownloadStatus", "").lower() in ("warning", "error"):
                    msg = " / ".join(sum((m.get("messages", []) for m in item.get("statusMessages", [])), [])) or item.get("status", "")
                    if msg and msg != last_warning:
                        last_warning = msg
                        send_discord_notification(f"@everyone ⚠️ **Download Warning:** {label} - {msg}")
        except Exception:
            pass
        time.sleep(60)

    send_discord_notification(f"@everyone ⚠️ **Download Timeout:** {label} did not complete within 12 hours. Please check tracker seeders in Sonarr.")


def delete_series(query: str, delete_files: bool = True):
    shows = api("GET", "series")
    exact = [s for s in shows if s["title"].lower() == query.lower()]
    matches = exact or [s for s in shows if query.lower() in s["title"].lower()]
    if not matches:
        print(f"[!] Series '{query}' not found in your library.")
        return
    if len(matches) > 1:
        print("[!] Ambiguous, ask the user which one: " + "; ".join(f"{s['title']} ({s.get('year', '')})" for s in matches))
        sys.exit(1)
    show = matches[0]
    label = f"{show['title']} ({show.get('year', '')})"
    try:
        api("DELETE", f"series/{show['id']}", params={"deleteFiles": str(delete_files).lower(), "addImportListExclusion": "false"})
        print(f"✅ Deleted '{label}' ({'removed from disk' if delete_files else 'kept on disk'}).")
        send_discord_notification(f"@everyone 🗑️ **Series Deleted:** {label} has been removed from Plex.")
    except Exception as e:
        print(f"[!] Failed to delete series: {e}")
        send_discord_notification(f"@everyone ⚠️ **Deletion Error:** Could not delete {label} - {e}")


def list_series():
    shows = api("GET", "series")
    if not shows:
        print("TV library is currently empty.")
        return
    print(f"\n--- Plex TV Library ({len(shows)} series) ---")
    for s in sorted(shows, key=lambda s: s["title"]):
        st = s.get("statistics", {})
        size_gb = round(st.get("sizeOnDisk", 0) / (1024**3), 2)
        print(f"- {s['title']} ({s.get('year', '')}) | {st.get('episodeFileCount', 0)}/{st.get('episodeCount', 0)} episodes | {size_gb} GB")
    print("-" * 42)


def main():
    parser = argparse.ArgumentParser(description="Hermes TV Automation CLI")
    sub = parser.add_subparsers(dest="action")

    dl = sub.add_parser("download", help="Download a series or one season")
    dl.add_argument("--query", "-q", required=True, help="Series title")
    dl.add_argument("--season", "-s", type=int, help="Season number (default: all aired seasons)")
    dl.add_argument("--quality", help="Quality: '1080p', 'ultra-hd', or '720p'")
    dl.add_argument("--year", "-y", type=int, help="First-air year (optional)")

    mon = sub.add_parser("monitor", help="Background download monitor")
    mon.add_argument("--series-id", type=int, required=True)
    mon.add_argument("--season", type=int)

    dele = sub.add_parser("delete", help="Delete a series")
    dele.add_argument("--query", "-q", required=True, help="Exact series title")
    dele.add_argument("--keep-files", action="store_true", help="Keep files on disk")

    sub.add_parser("list", help="List all series in library")
    args = parser.parse_args()

    if args.action == "monitor":
        run_monitor(args.series_id, args.season)
        return

    ensure_stack()
    check_connection()

    if args.action == "download":
        download_series(args.query, args.season, args.quality, args.year)
    elif args.action == "delete":
        delete_series(args.query, delete_files=not args.keep_files)
    elif args.action == "list":
        list_series()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
