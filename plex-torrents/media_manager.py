#!/usr/bin/env python3
"""
Hermes Media Manager Script: Radarr & Media Automation
------------------------------------------------------
Features:
- Smart Quality Fallback: Ultra-HD (4K) / HD-1080p -> HD-720p fallback
- Search & Download movies directly to Plex (Auto-approved)
- Detached Background Monitoring with Discord @everyone notifications
- Delete movies from Radarr, Plex disk, and qBittorrent (Auto-approved)
- Compact CLI output optimized for low-parameter / free LLMs

Usage:
    python3 media_manager.py download --query "The Dark Knight"
    python3 media_manager.py download --query "Interstellar" --quality "ultra-hd"
    python3 media_manager.py download --query "Inception" --quality "1080p"
    python3 media_manager.py list
    python3 media_manager.py delete --query "The Road"
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
import requests
from dotenv import load_dotenv

# Load environment from current dir or script dir
script_dir = Path(__file__).resolve().parent
env_file = script_dir / ".env"
if env_file.exists():
    load_dotenv(dotenv_path=env_file)
else:
    load_dotenv()

RADARR_URL = os.getenv("RADARR_URL", "http://127.0.0.1:7878").rstrip("/")
RADARR_API_KEY = os.getenv("RADARR_API_KEY", "")
ROOT_FOLDER = os.getenv("RADARR_ROOT_FOLDER", "/data/movies")
DISCORD_CHANNEL = os.getenv("DISCORD_CHANNEL", "")

HEADERS = {
    "X-Api-Key": RADARR_API_KEY,
    "Content-Type": "application/json"
}


def find_hermes_bin():
    """Finds the hermes executable path dynamically."""
    home_dir = Path.home()
    for path in [
        shutil.which("hermes"),
        str(home_dir / ".local/bin/hermes"),
        str(home_dir / ".hermes/hermes-agent/venv/bin/hermes"),
        "/usr/local/bin/hermes"
    ]:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return "hermes"


def send_discord_notification(message: str):
    """Send notification to Discord using hermes send (non-blocking)."""
    hermes_bin = find_hermes_bin()
    target = f"discord:{DISCORD_CHANNEL}" if DISCORD_CHANNEL else "discord"
    try:
        subprocess.Popen(
            [hermes_bin, "send", "--to", target, message],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        print(f"[WARN] Discord send failed: {e}", file=sys.stderr)


def check_connection():
    """Checks connection to Radarr."""
    try:
        res = requests.get(f"{RADARR_URL}/api/v3/system/status", headers=HEADERS, timeout=5)
        res.raise_for_status()
    except Exception as e:
        err_msg = f"Cannot connect to Radarr at {RADARR_URL}: {e}"
        print(f"[!] Error: {err_msg}")
        send_discord_notification(f"@everyone ⚠️ **Download Error:** Radarr service unreachable on Plex server.")
        sys.exit(1)


def get_quality_profiles():
    """Fetches all quality profiles available in Radarr."""
    try:
        res = requests.get(f"{RADARR_URL}/api/v3/qualityprofile", headers=HEADERS, timeout=5)
        res.raise_for_status()
        return res.json()
    except Exception:
        return []


def resolve_quality_profile_id(requested_quality: str = None) -> int:
    """
    Resolves profile based on rule:
    - If user specifies 'ultra-hd' / '4k' -> Ultra-HD
    - If user specifies '1080p' / 'hd' -> HD-1080p
    - If user specifies '720p' -> HD-720p
    - Default: HD-1080p
    """
    profiles = get_quality_profiles()
    if not profiles:
        return 1

    profile_map = {}
    for p in profiles:
        name_lower = p.get("name", "").lower()
        if "ultra" in name_lower or "2160" in name_lower or "4k" in name_lower:
            profile_map["ultra-hd"] = p["id"]
        elif "1080" in name_lower or "hd-1080" in name_lower:
            profile_map["1080p"] = p["id"]
        elif "720" in name_lower:
            profile_map["720p"] = p["id"]
        elif "any" in name_lower:
            profile_map["any"] = p["id"]

    if requested_quality:
        q = requested_quality.lower()
        if "4k" in q or "ultra" in q or "2160" in q:
            if "ultra-hd" in profile_map:
                return profile_map["ultra-hd"]
        elif "720" in q:
            if "720p" in profile_map:
                return profile_map["720p"]
        elif "1080" in q or "hd" in q:
            if "1080p" in profile_map:
                return profile_map["1080p"]

    return profile_map.get("1080p", profile_map.get("any", profiles[0]["id"]))


def search_tmdb_movie(query: str, year: int = None):
    """Searches TMDB via Radarr."""
    try:
        res = requests.get(f"{RADARR_URL}/api/v3/movie/lookup", headers=HEADERS, params={"term": query}, timeout=10)
        res.raise_for_status()
        movies = res.json()
    except Exception as e:
        print(f"[!] Lookup failed for '{query}': {e}")
        send_discord_notification(f"@everyone ⚠️ **Download Error:** Could not search TMDB for '{query}' ({e})")
        return None

    if not movies:
        print(f"[!] No matching movies found for '{query}'.")
        send_discord_notification(f"@everyone ⚠️ **Download Error:** Movie '{query}' was not found on TMDB.")
        return None

    if year:
        for m in movies:
            if m.get("year") == year:
                return m

    return movies[0]


def get_existing_movies():
    """Gets all movies currently in Radarr library."""
    try:
        res = requests.get(f"{RADARR_URL}/api/v3/movie", headers=HEADERS, timeout=10)
        res.raise_for_status()
        return res.json()
    except Exception:
        return []


def download_movie(query: str, quality: str = None, year: int = None):
    """Adds movie and starts download with auto-approval and Discord notifications."""
    movie = search_tmdb_movie(query, year)
    if not movie:
        sys.exit(1)

    title = movie.get("title")
    movie_year = movie.get("year")
    tmdb_id = movie.get("tmdbId")
    quality_id = resolve_quality_profile_id(quality)
    quality_label = quality.upper() if quality else "HD-1080p"

    # Immediate Discord notification for Start
    send_discord_notification(f"@everyone 🎬 **Download Started:** {title} ({movie_year}) [{quality_label}]")

    payload = {
        "title": title,
        "qualityProfileId": quality_id,
        "titleSlug": movie.get("titleSlug"),
        "tmdbId": tmdb_id,
        "year": movie_year,
        "rootFolderPath": ROOT_FOLDER,
        "monitored": True,
        "addOptions": {
            "searchForMovie": True
        }
    }

    try:
        res = requests.post(f"{RADARR_URL}/api/v3/movie", headers=HEADERS, json=payload, timeout=15)
        
        if res.status_code == 400 and "already been added" in res.text:
            existing = [m for m in get_existing_movies() if m.get("tmdbId") == tmdb_id]
            if existing:
                cmd_payload = {"name": "MoviesSearch", "movieIds": [existing[0]["id"]]}
                requests.post(f"{RADARR_URL}/api/v3/command", headers=HEADERS, json=cmd_payload, timeout=10)
                print(f"✅ Re-triggered search for existing movie: {title} ({movie_year})")
        else:
            res.raise_for_status()
            print(f"✅ Queued for download: {title} ({movie_year}) [{quality_label}]")

        # Spawn detached background process to monitor download until completion
        spawn_background_monitor(title, movie_year, quality_label)

    except Exception as e:
        err_detail = str(e)
        if hasattr(e, 'response') and e.response is not None:
            err_detail = e.response.text[:200]
        print(f"[!] Error adding movie: {err_detail}")
        send_discord_notification(f"@everyone ⚠️ **Download Error:** Failed to queue {title} ({movie_year}) - {err_detail}")
        sys.exit(1)


def spawn_background_monitor(title: str, year: int, quality_label: str):
    """Spawns this script in monitor mode in background detached."""
    script_path = str(Path(__file__).resolve())
    cmd = [
        sys.executable,
        script_path,
        "monitor",
        "--title", str(title),
        "--year", str(year),
        "--quality", str(quality_label)
    ]
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
    except Exception as e:
        print(f"[WARN] Failed to spawn background monitor: {e}", file=sys.stderr)


def run_monitor(title: str, year: int, quality_label: str, timeout_seconds: int = 3600):
    """Detached monitor: checks if movie finishes downloading or fails."""
    start_time = time.time()
    last_reported_status = None

    while time.time() - start_time < timeout_seconds:
        try:
            movies = get_existing_movies()
            movie = next((m for m in movies if (m.get("title") == title or str(m.get("year")) == str(year))), None)

            if movie and movie.get("hasFile"):
                size_gb = round(movie.get("sizeOnDisk", 0) / (1024**3), 2)
                size_str = f" ({size_gb} GB)" if size_gb > 0 else ""
                send_discord_notification(
                    f"@everyone ✅ **Download Complete:** {title} ({year}) [{quality_label}]{size_str} is ready on Plex!"
                )
                return

            # Check Radarr queue for errors/warnings
            q_res = requests.get(f"{RADARR_URL}/api/v3/queue?pageSize=50", headers=HEADERS, timeout=10)
            if q_res.status_code == 200:
                queue_data = q_res.json().get("records", [])
                movie_queue = next((item for item in queue_data if item.get("title") == title), None)
                if movie_queue:
                    status = movie_queue.get("status", "")
                    tracked_status = movie_queue.get("trackedDownloadStatus", "")
                    if "warning" in tracked_status.lower() or "error" in tracked_status.lower():
                        status_msgs = [m.get("messages", []) for m in movie_queue.get("statusMessages", [])]
                        msg = " / ".join(sum(status_msgs, [])) or status
                        if msg and msg != last_reported_status:
                            last_reported_status = msg
                            send_discord_notification(f"@everyone ⚠️ **Download Warning:** {title} ({year}) - {msg}")

            time.sleep(30)
        except Exception:
            time.sleep(30)

    # If timeout reached without file
    send_discord_notification(f"@everyone ⚠️ **Download Timeout:** {title} ({year}) did not complete within 1 hour. Please check tracker seeders in Radarr.")


def delete_movie(query: str, delete_files: bool = True):
    """Deletes a movie from Radarr and Plex disk (Auto-approved)."""
    existing_movies = get_existing_movies()
    if not existing_movies:
        print("[!] No movies found in Radarr library.")
        return

    matches = [m for m in existing_movies if query.lower() in m.get("title", "").lower()]
    if not matches:
        print(f"[!] Movie '{query}' not found in your library.")
        return

    movie = matches[0]
    movie_id = movie["id"]
    title = movie["title"]
    year = movie.get("year", "")

    params = {
        "deleteFiles": "true" if delete_files else "false",
        "addImportExclusion": "false"
    }

    try:
        res = requests.delete(f"{RADARR_URL}/api/v3/movie/{movie_id}", headers=HEADERS, params=params, timeout=15)
        res.raise_for_status()
        action = "removed from disk" if delete_files else "kept on disk"
        print(f"✅ Deleted '{title} ({year})' ({action}).")
        send_discord_notification(f"@everyone 🗑️ **Movie Deleted:** {title} ({year}) has been removed from Plex.")
    except Exception as e:
        print(f"[!] Failed to delete movie: {e}")
        send_discord_notification(f"@everyone ⚠️ **Deletion Error:** Could not delete {title} ({year}) - {e}")


def list_movies():
    """Lists all movies in the Radarr library in a compact format."""
    movies = get_existing_movies()
    if not movies:
        print("Library is currently empty.")
        return

    print(f"\n--- Plex Library ({len(movies)} movies) ---")
    for m in movies:
        status = "✅ Ready" if m.get("hasFile") else "⏳ Downloading/Searching"
        size_gb = round(m.get("sizeOnDisk", 0) / (1024**3), 2)
        print(f"- {m.get('title')} ({m.get('year')}) | {status} | {size_gb} GB")
    print("------------------------------------------\n")


def main():
    parser = argparse.ArgumentParser(description="Hermes Media Automation CLI")
    subparsers = parser.add_subparsers(dest="action", help="Action to perform")

    # Download
    dl_parser = subparsers.add_parser("download", help="Download a movie")
    dl_parser.add_argument("--query", "-q", required=True, help="Movie title")
    dl_parser.add_argument("--quality", help="Quality: '1080p', 'ultra-hd', or '720p'")
    dl_parser.add_argument("--year", "-y", type=int, help="Movie year (optional)")

    # Monitor (internal background worker)
    mon_parser = subparsers.add_parser("monitor", help="Background download monitor")
    mon_parser.add_argument("--title", required=True, help="Movie title")
    mon_parser.add_argument("--year", type=int, default=0, help="Movie year")
    mon_parser.add_argument("--quality", default="HD-1080p", help="Quality label")

    # Delete
    del_parser = subparsers.add_parser("delete", help="Delete a movie")
    del_parser.add_argument("--query", "-q", required=True, help="Movie title to delete")
    del_parser.add_argument("--keep-files", action="store_true", help="Keep files on disk")

    # List
    subparsers.add_parser("list", help="List all movies in library")

    args = parser.parse_args()

    if args.action == "monitor":
        run_monitor(args.title, args.year, args.quality)
        return

    check_connection()

    if args.action == "download":
        download_movie(args.query, args.quality, args.year)
    elif args.action == "delete":
        delete_movie(args.query, delete_files=not args.keep_files)
    elif args.action == "list":
        list_movies()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
