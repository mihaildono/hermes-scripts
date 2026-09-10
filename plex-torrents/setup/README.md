# Plex-Torrents Stack Setup Guide

## Overview

The Radarr/Prowlarr/qBittorrent/Sonarr/Bazarr stack runs on host networking.

## Quick Fix Script

Run this to configure the full stack:

```bash
cd ~/hermes-scripts/plex-torrents/setup
python3 configure_stack.py --qbit-username <USERNAME> --qbit-password <PASSWORD>
```

## Manual Configuration Steps

### 1. Configure qBittorrent API credentials

qBittorrent Web UI username/password can be set via Web UI or Preferences.

### 2. Add qBittorrent as download client in Prowlarr

In Prowlarr:
- Settings → Download Clients → Add qBittorrent
- Host: `localhost`
- Port: `8080`

### 3. Add Prowlarr indexers to Radarr / Sonarr

In Prowlarr:
- Settings → Apps → Add Radarr / Sonarr
- Radarr URL: `http://localhost:7878`
- Sonarr URL: `http://localhost:8989`
- Set Sync Level to `Full Sync`

### 4. API Keys & Ports

API keys are generated automatically by each service in their respective `config.xml` / `config.yaml` located under `/home/neuromancer/plex/plex-torrents/config/`.

| Service | Port | Config Location |
|---------|------|-----------------|
| Radarr | 7878 | `config/radarr/config.xml` |
| Prowlarr | 9696 | `config/prowlarr/config.xml` |
| Sonarr | 8989 | `config/sonarr/config.xml` |
| Bazarr | 6767 | `config/bazarr/config/config.yaml` |
| qBittorrent | 8080 | `config/qbittorrent/qBittorrent/qBittorrent.conf` |
