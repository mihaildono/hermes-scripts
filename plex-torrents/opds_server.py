#!/usr/bin/env python3
"""
Minimal OPDS catalog for KOReader: serves ~/plex/books, newest first. Stdlib only (~15 MB RAM).
KOReader: Search > OPDS catalog > + > http://<pi>:8091/opds
Runs as systemd service heavenpi-opds. LAN/Tailscale only (host firewall), read-only.
"""

import html
import os
import re
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote

BOOKS_DIR = Path(os.getenv("BOOKS_DIR", Path.home() / "plex/books")).resolve()
PORT = int(os.getenv("OPDS_PORT", "8091"))
MIME = {".epub": "application/epub+zip", ".azw3": "application/vnd.amazon.ebook", ".mobi": "application/x-mobipocket-ebook",
        ".fb2": "application/x-fictionbook+xml", ".pdf": "application/pdf"}
_meta_cache = {}


def metadata(path: Path):
    """(title, author) from EPUB OPF, cached by mtime; falls back to 'Author - Title' file name."""
    key = (path, path.stat().st_mtime)
    if key not in _meta_cache:
        title = author = None
        if path.suffix.lower() == ".epub":
            try:
                with zipfile.ZipFile(path) as z:
                    opf = re.search(rb'full-path="([^"]+)"', z.read("META-INF/container.xml")).group(1).decode()
                    xml = z.read(opf).decode("utf-8", "replace")
                t, a = re.search(r"<dc:title[^>]*>([^<]+)", xml), re.search(r"<dc:creator[^>]*>([^<]+)", xml)
                title, author = (t.group(1).strip() if t else None), (a.group(1).strip() if a else None)
            except Exception:
                pass
        if not title:
            author, _, title = path.stem.rpartition(" - ")
        _meta_cache[key] = (title, author or "")
    return _meta_cache[key]


def feed():
    books = sorted((p for p in BOOKS_DIR.glob("*") if p.suffix.lower() in MIME), key=lambda p: -p.stat().st_mtime)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entries = []
    for b in books:
        title, author = metadata(b)
        updated = datetime.fromtimestamp(b.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entries.append(f"""<entry><title>{html.escape(title)}</title><id>urn:heavenpi:{quote(b.name)}</id><updated>{updated}</updated>
<author><name>{html.escape(author)}</name></author>
<link rel="http://opds-spec.org/acquisition" href="/books/{quote(b.name)}" type="{MIME[b.suffix.lower()]}"/></entry>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opds="http://opds-spec.org/2010/catalog">
<id>urn:heavenpi:books</id><title>Heavenpi Books</title><updated>{now}</updated>
<link rel="self" href="/opds" type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>
<link rel="start" href="/opds" type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>
{"".join(entries)}
</feed>""".encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = unquote(self.path.split("?")[0])
        if path in ("/", "/opds", "/opds/"):
            return self.send(200, "application/atom+xml;profile=opds-catalog;kind=acquisition", feed())
        if path.startswith("/books/"):
            book = (BOOKS_DIR / path[len("/books/"):]).resolve()
            if book.parent == BOOKS_DIR and book.is_file() and book.suffix.lower() in MIME:
                return self.send(200, MIME[book.suffix.lower()], book.read_bytes(),
                                 {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(book.name)}"})
        self.send(404, "text/plain", b"not found")

    def send(self, code, ctype, body, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
