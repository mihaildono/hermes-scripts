#!/usr/bin/env python3
"""
Plex-Torrents Stack Configuration Script
-----------------------------------------
Configures the full Radarr + Prowlarr + qBittorrent stack with:
1. qBittorrent Web API authentication
2. qBittorrent as download client in Prowlarr
3. Prowlarr as indexer in Radarr
4. Discord notifications via `hermes send`

All operations are auto-approved (no confirmation prompts).

Usage:
    python3 configure_stack.py --qbit-username admin --qbit-password password
    python3 configure_stack.py --qbit-username admin --qbit-password password --dry-run
"""

import argparse
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
import requests
from dotenv import load_dotenv

# Load environment
script_dir = Path(__file__).resolve().parent
load_dotenv(script_dir / "../.env")

RADARR_URL = os.getenv("RADARR_URL", "http://localhost:7878").rstrip("/")
RADARR_API_KEY = os.getenv("RADARR_API_KEY", "")
PROWLARR_URL = os.getenv("PROWLARR_URL", "http://localhost:9696").rstrip("/")
PROWLARR_API_KEY = os.getenv("PROWLARR_API_KEY", "")
QB_HOST = os.getenv("QB_HOST", "localhost")
QB_PORT = int(os.getenv("QB_PORT", 8080))
DISCORD_CHANNEL = os.getenv("DISCORD_CHANNEL", "")


def send_discord(message: str):
    """Send notification to Discord #general using hermes send."""
    print(f"[DISCORD] {message}")
    for bin_path in ["/home/neuromancer/.local/bin/hermes", "hermes"]:
        try:
            subprocess.Popen(
                [bin_path, "send", "--to", f"discord:{DISCORD_CHANNEL}", message],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return
        except Exception:
            continue


def get_qbittorrent_config():
    """Get current qBittorrent config path."""
    return Path.home() / "plex/plex-torrents/config/qbittorrent/qBittorrent/qBittorrent.conf"


def configure_qbittorrent(username: str, password: str, dry_run: bool = False):
    """Configure qBittorrent Web API credentials."""
    config_path = get_qbittorrent_config()
    
    if not config_path.exists():
        print(f"[ERROR] qBittorrent config not found: {config_path}")
        return False
    
    try:
        root = ET.parse(config_path).getroot()
        webui = root.find("WebUI")
        if webui is None:
            webui = ET.SubElement(root, "WebUI")
        
        username_el = webui.find("Username")
        if username_el is None:
            username_el = ET.SubElement(webui, "Username")
        username_el.text = username
        
        password_el = webui.find("Password")
        if password_el is None:
            password_el = ET.SubElement(webui, "Password")
        password_el.text = password
        
        print(f"[qBittorrent] Set Web API credentials: {username}")
        
        if not dry_run:
            tree = ET.ElementTree(root)
            tree.write(config_path, encoding="utf-8", xml_declaration=True)
            print(f"[qBittorrent] Config written to {config_path}")
            subprocess.run(["docker", "restart", "qbittorrent"], capture_output=True, timeout=30)
        return True
    except Exception as e:
        print(f"[qBittorrent] Error configuring: {e}")
        return False


def add_prowlarr_download_client(dry_run: bool = False):
    """Add qBittorrent as download client in Prowlarr."""
    if not PROWLARR_API_KEY:
        print("[!] PROWLARR_API_KEY not set")
        return False

    headers = {"X-Api-Key": PROWLARR_API_KEY, "Content-Type": "application/json"}
    payload = {
        "name": "qBittorrent",
        "protocol": "torrent",
        "enable": True,
        "supportsRss": True,
        "supportsSearch": True,
        "downloadClientItemUrl": f"http://{QB_HOST}:{QB_PORT}",
        "settings": {
            "host": QB_HOST,
            "port": QB_PORT,
            "username": "",
            "password": ""
        }
    }
    
    if dry_run:
        print(f"[Prowlarr] Would add download client: {json.dumps(payload, indent=2)}")
        return True
    
    try:
        r = requests.post(
            f"{PROWLARR_URL}/api/v1/downloadclient",
            headers=headers,
            json=payload,
            timeout=15,
        )
        if r.status_code in [200, 201]:
            print("[Prowlarr] qBittorrent added as download client")
            return True
        print(f"[Prowlarr] Failed: HTTP {r.status_code} - {r.text[:200]}")
        return False
    except Exception as e:
        print(f"[Prowlarr] Error: {e}")
        return False


def add_radarr_indexer(dry_run: bool = False):
    """Add Prowlarr as indexer in Radarr."""
    if not RADARR_API_KEY or not PROWLARR_API_KEY:
        print("[!] RADARR_API_KEY or PROWLARR_API_KEY not set")
        return False

    headers = {"X-Api-Key": RADARR_API_KEY, "Content-Type": "application/json"}
    payload = {
        "name": "Prowlarr",
        "protocol": "torrent",
        "enable": True,
        "supportsRss": True,
        "supportsSearch": True,
        "indexerBaseUrl": f"{PROWLARR_URL}",
        "settings": {
            "apiKey": PROWLARR_API_KEY
        }
    }
    
    if dry_run:
        print(f"[Radarr] Would add indexer: {json.dumps(payload, indent=2)}")
        return True
    
    try:
        r = requests.post(
            f"{RADARR_URL}/api/v3/indexer",
            headers=headers,
            json=payload,
            timeout=15,
        )
        if r.status_code in [200, 201]:
            print("[Radarr] Prowlarr added as indexer")
            return True
        print(f"[Radarr] Failed: HTTP {r.status_code} - {r.text[:200]}")
        return False
    except Exception as e:
        print(f"[Radarr] Error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Configure Plex-Torrents stack")
    parser.add_argument("--qbit-username", required=True, help="qBittorrent Web API username")
    parser.add_argument("--qbit-password", required=True, help="qBittorrent Web API password")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    args = parser.parse_args()
    
    print("=" * 60)
    print("Plex-Torrents Stack Configuration")
    print("=" * 60)
    
    # Step 1: Configure qBittorrent
    print("\n[1/3] Configuring qBittorrent Web API...")
    configure_qbittorrent(args.qbit_username, args.qbit_password, args.dry_run)
    
    # Step 2: Add qBittorrent to Prowlarr
    print("\n[2/3] Adding qBittorrent to Prowlarr...")
    add_prowlarr_download_client(args.dry_run)
    
    # Step 3: Add Prowlarr to Radarr
    print("\n[3/3] Adding Prowlarr indexer to Radarr...")
    add_radarr_indexer(args.dry_run)
    
    print("\n" + "=" * 60)
    print("Configuration complete!")
    print("=" * 60)
    send_discord("✅ Plex-Torrents stack configuration complete!")


if __name__ == "__main__":
    main()
