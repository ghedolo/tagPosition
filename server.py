#!/usr/bin/env python3
"""Live map server — serves the map page and pushes SSE updates when poller writes new data."""
import os
import sys
import json
import time
import threading
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from map import (
    load_entries, split_entries, render_html, secure_dir, secure_file,
    TAG_RENAME, TAG_COLORS, ARCHIVE_PATH, EXTENDED_JSON_PATH,
)

POLL_INTERVAL = 5  # seconds between mtime checks
MAX_SSE_CLIENTS = 8  # each /events connection holds a thread for its whole lifetime

_sse_clients = 0
_sse_lock = threading.Lock()

_TAG_PRIORITY = set(TAG_RENAME.values())


def _build_data():
    by_tag_all = load_entries()
    all_tags = sorted(by_tag_all.keys(), key=lambda n: (n not in _TAG_PRIORITY, n))
    tag_color = {name: TAG_COLORS[i % len(TAG_COLORS)] for i, name in enumerate(all_tags)}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    data_24h, extended = split_entries(by_tag_all, cutoff)
    return data_24h, extended, all_tags, tag_color


def _flat_24h(data_24h, all_tags):
    entries = []
    for tag in all_tags:
        entries.extend(data_24h.get(tag, []))
    return entries


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] {self.address_string()} {fmt % args}")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._serve_map()
        elif path == "/data_extended.json":
            self._serve_file(EXTENDED_JSON_PATH, "application/json")
        elif path == "/events":
            self._serve_sse()
        else:
            self.send_error(404)

    def _serve_map(self):
        try:
            data_24h, extended, all_tags, tag_color = _build_data()
        except SystemExit:
            # the reason phrase is latin-1 encoded by http.server: ASCII only
            self.send_error(503, "No data - run poller.py first")
            return
        try:
            html = render_html(data_24h, all_tags, tag_color, live=True)
        except SystemExit:
            self.send_error(503, "No entries in last 24h")
            return
        # write to a temporary file in the same directory and rename: a concurrent
        # GET /data_extended.json must never observe a half-written file
        secure_dir(os.path.dirname(EXTENDED_JSON_PATH))
        tmp_path = EXTENDED_JSON_PATH + f".{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp_path, "w") as f:
            json.dump(extended, f, separators=(",", ":"))
        secure_file(tmp_path)
        os.replace(tmp_path, EXTENDED_JSON_PATH)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path, content_type):
        if not os.path.exists(path):
            self.send_error(404)
            return
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_sse(self):
        global _sse_clients
        with _sse_lock:
            if _sse_clients >= MAX_SSE_CLIENTS:
                self.send_error(503, "Too many live connections")
                return
            _sse_clients += 1
        try:
            self._sse_loop()
        finally:
            with _sse_lock:
                _sse_clients -= 1

    def _sse_loop(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        last_mtime = os.path.getmtime(ARCHIVE_PATH) if os.path.exists(ARCHIVE_PATH) else 0

        if not self._sse_write("ping", "{}"):
            return

        while True:
            time.sleep(POLL_INTERVAL)
            try:
                mtime = os.path.getmtime(ARCHIVE_PATH) if os.path.exists(ARCHIVE_PATH) else 0
            except OSError:
                mtime = 0

            if mtime <= last_mtime:
                if not self._sse_write("ping", "{}"):
                    return
                continue

            last_mtime = mtime
            try:
                data_24h, _, all_tags, _ = _build_data()
                entries = _flat_24h(data_24h, all_tags)
                payload = json.dumps(entries, separators=(",", ":"))
            except Exception as exc:
                print(f"[SSE] build error: {exc}", file=sys.stderr)
                continue

            if not self._sse_write("update", payload):
                return

    def _sse_write(self, event, data):
        try:
            msg = f"event: {event}\ndata: {data}\n\n"
            self.wfile.write(msg.encode("utf-8"))
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError):
            return False


def main():
    parser = argparse.ArgumentParser(description="Live map server")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[Server] http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Server] stopped")


if __name__ == "__main__":
    main()
