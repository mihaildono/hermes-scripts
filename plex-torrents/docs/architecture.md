# Hermes Plex Torrents

## Stack Architecture

The Plex media stack runs as 5 Docker containers on the Pi:

| Service | Port | Role |
|---------|------|------|
| **qBittorrent** | 8080 | Downloads torrents |
| **Prowlarr** | 9696 | Indexer manager (feeds Radarr) |
| **Radarr** | 7878 | Movie manager (creates downloads) |
| **Sonarr** | 8989 | TV show manager |
| **Bazarr** | 6767 | Subtitle manager |

**Config directory:** `~/plex/plex-torrents/config/`
- Radarr API key in `config/radarr/config.xml` (NOT `~/.hermes/.env`)
- Prowlarr API key in `config/prowlarr/config.xml`
- qBittorrent WebUI credentials in `config/qbittorrent/qBittorrent/qBittorrent.conf`

## Configuration Gotchas

### API Keys
- Radarr config.xml has the live API key used by `media_manager.py`
- `~/.hermes/.env` may have a stale key — script loads `~/.hermes/.env` first
- Script falls back to config.xml if no RADARR_API_KEY found

### Discord Notification via `hermes send`
The `send_discord_notification()` function uses `hermes send` to post notifications to Discord.

**Non-blocking dispatch:** `send_discord_notification()` uses `subprocess.Popen(..., start_new_session=True)` so notifications run as detached background processes without blocking the CLI or download workflow.

## media_manager.py Architecture

- `download_movie()`: Searches TMDB, adds to Radarr, triggers search
- `send_discord_notification()`: Posts `@everyone` to Discord #general
- `run_monitor()`: Background detached process that watches download progress
- Quality fallback: Ultra-HD → HD-1080p → HD-720p

## Auto-Approval
All Radarr API calls, qBittorrent operations, and file deletions are auto-approved in the script. No user confirmation prompts.

## Discord Delivery Rules (per skill)
- **1 message at download start** with `@everyone` + movie title + quality
- **1 message at download completion** with `@everyone` + movie title + quality
- No intermediate status reports, no polling output
