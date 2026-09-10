---
name: plex-torrents
description: "Download, manage, list, and delete movies on the Plex server with Radarr, qBittorrent, and Bazarr. Auto-approved with Discord notifications."
---

# Plex Torrents & Media Manager

Manage movies on the Raspberry Pi Plex server.

## Auto-Approval & Rules
- **NEVER ASK FOR CONFIRMATION.** All download and deletion actions are 100% auto-approved.
- When the user asks to download, delete, or list movies, run the CLI immediately and return its output.
- Discord `@everyone` notifications for download start, completion, errors, and deletions are handled automatically by the CLI.

## Commands

### 1. Download Movie
Run:
```bash
python3 /home/neuromancer/hermes-scripts/plex-torrents/media_manager.py download --query "<TITLE>" [--year <YEAR>] [--quality <4k|1080p|720p>]
```
*Quality Rules:*
- Default (unspecified): **1080p** (do NOT pass `--quality` or pass `--quality 1080p`)
- If user requests 4K / UHD: `--quality "ultra-hd"`
- If user requests 720p: `--quality "720p"`

### 2. Delete Movie
Run:
```bash
python3 /home/neuromancer/hermes-scripts/plex-torrents/media_manager.py delete --query "<TITLE>"
```

### 3. List Movies
Run:
```bash
python3 /home/neuromancer/hermes-scripts/plex-torrents/media_manager.py list
```
