#!/usr/bin/env python3
"""
Hermes Book Manager Script: ebooks via Prowlarr + qBittorrent, delivered to KOReader
-----------------------------------------------------------------------------------
- Searches Prowlarr's Books category, prefers EPUB, ranks by indexer safety priority then seeders
- Downloads with qBittorrent (category "books"), copies the ebook to ~/plex/books, removes the torrent
- Pushes the book to the Kindle over KOReader's SSH server when it is reachable;
  otherwise it waits in the OPDS catalog (opds_server.py) for KOReader to pull
- Compact CLI output optimized for low-parameter / free LLMs

Usage:
    python3 book_manager.py download --query "Project Hail Mary"
    python3 book_manager.py list
    python3 book_manager.py push --query "Hail Mary"
    python3 book_manager.py delete --query "Project Hail Mary"
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
import requests

from media_manager import send_discord_notification, script_dir

PROWLARR_URL = "http://127.0.0.1:9696"
QBIT_URL = "http://127.0.0.1:8080/api/v2"   # localhost is whitelisted, no login needed
CONFIG_XML = Path.home() / "plex/plex-torrents/config/prowlarr/config.xml"
BOOKS_DIR = Path(os.getenv("BOOKS_DIR", Path.home() / "plex/books"))
TORRENTS_HOST = Path.home() / "plex/torrents"   # mounted as /downloads in qBittorrent
CATEGORY = "books"

# KOReader SSH server (Tools > More tools > SSH server); set KINDLE_HOST in .env to enable pushing
KINDLE_HOST = os.getenv("KINDLE_HOST", "")
KINDLE_PORT = os.getenv("KINDLE_PORT", "2222")
KINDLE_DIR = os.getenv("KINDLE_DIR", "/mnt/us/documents/heavenpi")
KINDLE_KEY = Path.home() / ".ssh/kindle_ed25519"

FORMATS = [".epub", ".azw3", ".mobi", ".fb2", ".pdf"]   # KOReader-readable, in order of preference
MAX_SIZE = 300 * 1024**2                                   # skip "1000 books" packs


def prowlarr(path: str, **kwargs):
    key = re.search(r"<ApiKey>([^<]+)</ApiKey>", CONFIG_XML.read_text()).group(1)
    res = requests.get(f"{PROWLARR_URL}/api/v1/{path}", headers={"X-Api-Key": key}, timeout=120, **kwargs)
    res.raise_for_status()
    return res.json()


def ensure_stack():
    """Containers run on demand (stack.sh stops them when idle); start the book ones."""
    res = subprocess.run([str(script_dir / "stack.sh"), "up", "books"], capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[WARN] stack.sh up books: {res.stderr.strip()}", file=sys.stderr)


def words(text: str):
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def pick_release(query: str):
    """All query words must appear in the title. Rank: format, indexer safety priority, seeders."""
    priority = {i["id"]: i["priority"] for i in prowlarr("indexer")}
    results = prowlarr("search", params={"query": query, "categories": 7000, "type": "search"})
    wanted = words(query)
    candidates = []
    for r in results:
        title_words = words(r["title"])
        if not wanted <= title_words or r.get("seeders", 0) < 1 or r.get("size", 0) > MAX_SIZE:
            continue
        if title_words & {"audiobook", "mp3", "m4b", "audio"}:
            continue
        fmt = next((i for i, f in enumerate(FORMATS) if f[1:] in title_words), len(FORMATS))
        candidates.append((fmt, priority.get(r["indexerId"], 25), -r.get("seeders", 0), r))
    candidates.sort(key=lambda c: c[:3])
    return candidates[0][3] if candidates else None


def qbit(path: str, **data):
    res = requests.post(f"{QBIT_URL}/{path}", data=data, timeout=30)
    if res.status_code not in (200, 409):   # 409 = category already exists
        res.raise_for_status()
    return res


def download_book(query: str):
    try:
        release = pick_release(query)
    except Exception as e:
        print(f"[!] Search failed: {e}")
        send_discord_notification(f"@everyone ⚠️ **Book Error:** search for '{query}' failed ({e})")
        sys.exit(1)
    if not release:
        print(f"[!] No ebook found for '{query}'. Try the author's name or a shorter title.")
        sys.exit(1)

    tag = f"book-{int(time.time())}"
    title = release["title"]
    try:
        qbit("torrents/createCategory", category=CATEGORY, savePath=f"/downloads/{CATEGORY}")
        qbit("torrents/add", urls=release.get("magnetUrl") or release["downloadUrl"], category=CATEGORY, tags=tag)
    except Exception as e:
        print(f"[!] Could not add torrent: {e}")
        send_discord_notification(f"@everyone ⚠️ **Book Error:** could not start '{title}' ({e})")
        sys.exit(1)

    print(f"✅ Downloading: {title} ({release['indexer']}, {release.get('seeders')} seeders)")
    send_discord_notification(f"@everyone 📚 **Book Download Started:** {title}")
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "monitor", "--tag", tag, "--title", title],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def epub_metadata(path: Path):
    """(title, author) from an EPUB's OPF, or (None, None)."""
    try:
        with zipfile.ZipFile(path) as z:
            opf = re.search(rb'full-path="([^"]+)"', z.read("META-INF/container.xml")).group(1).decode()
            xml = z.read(opf).decode("utf-8", "replace")
        title = re.search(r"<dc:title[^>]*>([^<]+)", xml)
        author = re.search(r"<dc:creator[^>]*>([^<]+)", xml)
        return (title.group(1).strip() if title else None, author.group(1).strip() if author else None)
    except Exception:
        return None, None


def safe_name(text: str):
    return re.sub(r"[^\w .,'()&-]+", "", text).strip()[:120] or "book"


def import_book(content_path: Path):
    """Copy the best ebook file from a finished torrent into BOOKS_DIR; return the new path."""
    files = [content_path] if content_path.is_file() else [p for p in content_path.rglob("*") if p.is_file()]
    books = sorted((f for f in files if f.suffix.lower() in FORMATS), key=lambda f: (FORMATS.index(f.suffix.lower()), -f.stat().st_size))
    if not books:
        return None
    src = books[0]
    title, author = epub_metadata(src) if src.suffix.lower() == ".epub" else (None, None)
    name = safe_name(f"{author} - {title}" if title and author else title or src.stem)
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    dest = BOOKS_DIR / f"{name}{src.suffix.lower()}"
    shutil.copy2(src, dest)
    return dest


def push_to_kindle(book: Path):
    """Copy a book to KOReader over SSH. Returns True on success; False if not configured or Kindle asleep."""
    if not KINDLE_HOST or not KINDLE_KEY.exists():
        return False
    ssh = ["-i", str(KINDLE_KEY), "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new"]
    target = f"root@{KINDLE_HOST}"
    try:
        subprocess.run(["ssh", *ssh, "-p", KINDLE_PORT, target, f"mkdir -p '{KINDLE_DIR}'"], check=True, capture_output=True, timeout=30)
        subprocess.run(["scp", *ssh, "-P", KINDLE_PORT, str(book), f"{target}:{KINDLE_DIR}/"], check=True, capture_output=True, timeout=300)
        return True
    except Exception:
        return False


def deliver(book: Path):
    if push_to_kindle(book):
        return f"pushed to the Kindle ({KINDLE_DIR.rsplit('/', 1)[-1]} folder in KOReader)"
    return "ready in the OPDS catalog (KOReader: Search > OPDS catalog > Heavenpi Books)"


def run_monitor(tag: str, title: str, timeout_seconds: int = 6 * 3600):
    """Detached monitor: waits for the torrent, imports the ebook, delivers it, removes the torrent."""
    start = time.time()
    while time.time() - start < timeout_seconds:
        try:
            torrents = requests.get(f"{QBIT_URL}/torrents/info", params={"tag": tag}, timeout=10).json()
            if torrents and torrents[0]["progress"] >= 1:
                t = torrents[0]
                content = TORRENTS_HOST / Path(t["content_path"]).relative_to("/downloads")
                book = import_book(content)
                qbit("torrents/delete", hashes=t["hash"], deleteFiles="true")
                if not book:
                    send_discord_notification(f"@everyone ⚠️ **Book Error:** {title} had no readable ebook file (looked for {', '.join(FORMATS)}).")
                    return
                send_discord_notification(f"@everyone ✅ **Book Ready:** {book.stem} - {deliver(book)}")
                return
        except Exception:
            pass
        time.sleep(30)
    send_discord_notification(f"@everyone ⚠️ **Book Timeout:** {title} did not finish within 6 hours (probably no seeders).")


def find_books(query: str):
    books = sorted(p for p in BOOKS_DIR.glob("*") if p.suffix.lower() in FORMATS) if BOOKS_DIR.exists() else []
    exact = [b for b in books if b.stem.lower() == query.lower()]
    return exact or [b for b in books if words(query) <= words(b.stem)]


def list_books():
    books = sorted((p for p in BOOKS_DIR.glob("*") if p.suffix.lower() in FORMATS), key=lambda p: -p.stat().st_mtime) if BOOKS_DIR.exists() else []
    if not books:
        print("Book library is currently empty.")
        return
    print(f"\n--- Books ({len(books)}) ---")
    for b in books:
        print(f"- {b.stem} | {b.suffix[1:].upper()} | {round(b.stat().st_size / 1024**2, 1)} MB")
    print("-" * 42)


def main():
    parser = argparse.ArgumentParser(description="Hermes Book Automation CLI")
    sub = parser.add_subparsers(dest="action")
    dl = sub.add_parser("download", help="Find and download an ebook")
    dl.add_argument("--query", "-q", required=True, help="Title, optionally with author")
    mon = sub.add_parser("monitor", help="Background download monitor")
    mon.add_argument("--tag", required=True)
    mon.add_argument("--title", required=True)
    for name, help_text in (("push", "Send a book to the Kindle again"), ("delete", "Delete a book from the library")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--query", "-q", required=True, help="Book title words")
    sub.add_parser("list", help="List downloaded books")
    args = parser.parse_args()

    if args.action == "monitor":
        run_monitor(args.tag, args.title)
    elif args.action == "download":
        ensure_stack()
        download_book(args.query)
    elif args.action in ("push", "delete"):
        matches = find_books(args.query)
        if len(matches) != 1:
            print("[!] " + ("Ambiguous, ask the user which one: " + "; ".join(b.stem for b in matches) if matches else f"No book matching '{args.query}'."))
            sys.exit(1)
        book = matches[0]
        if args.action == "push":
            print(f"✅ {book.stem}: {deliver(book)}")
        else:
            book.unlink()
            print(f"✅ Deleted '{book.stem}' from the library (copies already on the Kindle stay there).")
    elif args.action == "list":
        list_books()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
