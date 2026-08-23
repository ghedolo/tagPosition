"""End-to-end tests for server.py: a real HTTP server on an ephemeral port."""
import json
import os
import stat
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from conftest import geo_entry, write_archive


@pytest.fixture
def live_server(monkeypatch, archive_path, tmp_path):
    import map as map_mod
    import server as server_mod

    ext_path = str(tmp_path / "out" / "data_extended.json")
    monkeypatch.setattr(map_mod, "ARCHIVE_PATH", archive_path)
    monkeypatch.setattr(map_mod, "EXTENDED_JSON_PATH", ext_path)
    monkeypatch.setattr(server_mod, "ARCHIVE_PATH", archive_path)
    monkeypatch.setattr(server_mod, "EXTENDED_JSON_PATH", ext_path)
    monkeypatch.setattr(server_mod, "load_entries", map_mod.load_entries)
    monkeypatch.setattr(server_mod, "POLL_INTERVAL", 0.2)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_mod.Handler)
    httpd.daemon_threads = True
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield type("S", (), {"base": base, "ext_path": ext_path, "archive": archive_path})
    httpd.shutdown()
    httpd.server_close()


def _get(url, timeout=5):
    return urllib.request.urlopen(url, timeout=timeout)


def test_root_serves_map_html(live_server, archive_path):
    write_archive(archive_path, [geo_entry("Alpha")])
    r = _get(live_server.base + "/")
    body = r.read().decode()
    assert r.status == 200
    assert r.headers["Content-Type"] == "text/html; charset=utf-8"
    assert r.headers["Cache-Control"] == "no-store"
    assert body.startswith("<!DOCTYPE html>")


def test_root_page_is_the_live_variant(live_server, archive_path):
    write_archive(archive_path, [geo_entry("Alpha")])
    assert "EventSource" in _get(live_server.base + "/").read().decode()


def test_index_html_is_an_alias_for_root(live_server, archive_path):
    write_archive(archive_path, [geo_entry("Alpha")])
    assert _get(live_server.base + "/index.html").status == 200


def test_query_string_is_ignored_in_routing(live_server, archive_path):
    write_archive(archive_path, [geo_entry("Alpha")])
    assert _get(live_server.base + "/?x=1").status == 200


def test_unknown_path_returns_404(live_server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(live_server.base + "/nope")
    assert exc.value.code == 404


def test_path_traversal_attempt_returns_404_and_reads_nothing(live_server):
    for path in ("/../../etc/passwd", "/..%2f..%2fetc%2fpasswd", "/data/positions.json"):
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(live_server.base + path)
        assert exc.value.code == 404


def test_root_returns_503_when_archive_is_missing(live_server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(live_server.base + "/")
    assert exc.value.code == 503


def test_root_returns_503_when_no_entries_in_last_24h(live_server, archive_path):
    write_archive(archive_path, [geo_entry("Alpha", minutes_ago=60 * 48)])
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(live_server.base + "/")
    assert exc.value.code == 503


def test_extended_json_404_before_the_map_is_generated(live_server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(live_server.base + "/data_extended.json")
    assert exc.value.code == 404


def test_serving_the_map_writes_the_extended_json(live_server, archive_path):
    old = geo_entry("Alpha", minutes_ago=60 * 48)
    write_archive(archive_path, [geo_entry("Alpha"), old])
    _get(live_server.base + "/").read()
    r = _get(live_server.base + "/data_extended.json")
    payload = json.loads(r.read().decode())
    assert r.headers["Content-Type"] == "application/json"
    assert [e["location_time"] for e in payload] == [old["location_time"]]


def test_sse_sends_an_initial_ping(live_server, archive_path):
    write_archive(archive_path, [geo_entry("Alpha")])
    r = _get(live_server.base + "/events")
    assert r.headers["Content-Type"] == "text/event-stream"
    assert r.headers["Cache-Control"] == "no-cache"
    assert b"event: ping" in r.readline() + r.readline()
    r.close()


def test_sse_pushes_an_update_when_the_archive_changes(live_server, archive_path):
    write_archive(archive_path, [geo_entry("Alpha", minutes_ago=30)])
    r = _get(live_server.base + "/events", timeout=15)
    r.readline(); r.readline(); r.readline()  # initial ping

    time.sleep(0.5)
    new = geo_entry("Alpha", minutes_ago=1)
    with open(archive_path, "a") as f:
        f.write(json.dumps(new) + "\n")

    deadline = time.time() + 10
    payload = None
    while time.time() < deadline:
        line = r.readline().decode()
        if line.startswith("event: update"):
            payload = json.loads(r.readline().decode().split("data: ", 1)[1])
            break
    r.close()
    assert payload is not None, "no update event received"
    assert new["location_time"] in [e["location_time"] for e in payload]


def test_extended_json_is_written_atomically_and_owner_only(live_server, archive_path, tmp_path):
    write_archive(archive_path, [geo_entry("Alpha"), geo_entry("Alpha", minutes_ago=60 * 48)])
    _get(live_server.base + "/").read()
    ext = live_server.ext_path
    assert stat.S_IMODE(os.stat(ext).st_mode) & 0o077 == 0
    leftovers = [f for f in os.listdir(os.path.dirname(ext)) if f.endswith(".tmp")]
    assert leftovers == []


def test_sse_refuses_connections_above_the_cap(live_server, archive_path, monkeypatch):
    import server as server_mod
    monkeypatch.setattr(server_mod, "MAX_SSE_CLIENTS", 1)
    write_archive(archive_path, [geo_entry("Alpha")])

    first = _get(live_server.base + "/events")
    first.readline()  # make sure the handler is inside the SSE loop
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(live_server.base + "/events")
        assert exc.value.code == 503
    finally:
        first.close()


def test_sse_slot_is_released_when_the_client_disconnects(live_server, archive_path, monkeypatch):
    import server as server_mod
    monkeypatch.setattr(server_mod, "MAX_SSE_CLIENTS", 1)
    write_archive(archive_path, [geo_entry("Alpha")])

    r = _get(live_server.base + "/events")
    r.readline()
    r.close()

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            again = _get(live_server.base + "/events")
            again.close()
            return
        except urllib.error.HTTPError:
            time.sleep(0.3)
    pytest.fail("SSE slot was never released")
