"""Regression tests for the findings of the security review (2026-08-23).

Every test here asserts the DESIRED behaviour and must stay green: each one pins
a fix so the issue cannot come back unnoticed. A test still marked xfail describes
an issue that is not fixed yet; when a fix lands it turns XPASS and the run fails
(strict=True), which is the signal to drop the marker.
"""
import json
import os
import stat

import pytest

from conftest import geo_entry, write_archive

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS = os.path.join(ROOT, "lib", "GoogleFindMyTools", "Auth", "secrets.json")


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.mark.skipif(not os.path.exists(SECRETS), reason="no credentials on this machine")
def test_secrets_json_is_owner_only():
    assert _mode(SECRETS) & 0o077 == 0, f"secrets.json is {oct(_mode(SECRETS))}, expected 0o600"


def test_position_archive_is_owner_only(poller_env, archive_path, monkeypatch):
    monkeypatch.setattr(poller_env, "_check_auth", lambda: None)
    monkeypatch.setattr(poller_env, "request_device_list", lambda: "00")
    monkeypatch.setattr(poller_env, "parse_device_list_protobuf", lambda h: object())
    monkeypatch.setattr(poller_env, "get_canonic_ids", lambda dl: [("Alpha", "cid")])
    monkeypatch.setattr(poller_env, "FcmReceiver", lambda: type("F", (), {"stop_listening": lambda s: None})())
    monkeypatch.setattr(poller_env, "_fetch_location", lambda c, n, timeout=60: [geo_entry("Alpha")])
    monkeypatch.setattr(poller_env.sys, "argv", ["poller.py"])
    poller_env.main()
    assert _mode(archive_path) & 0o077 == 0


def test_purged_archive_file_is_owner_only(poller_env, archive_path, data_dir):
    write_archive(archive_path, [geo_entry("Alpha", minutes_ago=60 * 24 * 30)])
    poller_env._purge()
    rotated = list(data_dir.glob("position_*.json"))
    assert rotated and _mode(str(rotated[0])) & 0o077 == 0


def test_legend_escapes_quotes_in_tag_names(map_env):
    evil = "x' data-evil='1"
    letters = map_env.assign_letters([evil])
    html = map_env._build_legend([evil], {evil: "#facc15"}, letters)
    assert "data-evil='1" not in html


def test_embedded_json_cannot_close_the_script_block(map_env):
    evil = "</script>"
    entries = {evil: [geo_entry(evil)]}
    html = map_env.render_html(entries, [evil], {evil: "#facc15"})
    body = html.split("<script>\n", 1)[1]
    assert body.count("</script>") == 1


def test_cdn_scripts_declare_subresource_integrity(map_env):
    html = map_env.render_html({"Alpha": [geo_entry("Alpha")]}, ["Alpha"], {"Alpha": "#facc15"})
    for line in html.splitlines():
        if "https://unpkg.com" in line or "https://cdn.jsdelivr.net" in line:
            assert "integrity=" in line, line


def test_server_binds_to_loopback_by_default():
    """The default host must stay 127.0.0.1: the server has no authentication of its own."""
    src = open(os.path.join(ROOT, "server.py")).read()
    assert '"--host", default="127.0.0.1"' in src


def test_map_page_sends_no_cors_header(live_map_response):
    assert "Access-Control-Allow-Origin" not in live_map_response.headers


@pytest.fixture
def live_map_response(monkeypatch, tmp_path):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    import map as map_mod
    import server as server_mod

    archive = str(tmp_path / "data" / "positions.json")
    write_archive(archive, [geo_entry("Alpha")])
    monkeypatch.setattr(map_mod, "ARCHIVE_PATH", archive)
    monkeypatch.setattr(map_mod, "EXTENDED_JSON_PATH", str(tmp_path / "out" / "ext.json"))
    monkeypatch.setattr(server_mod, "ARCHIVE_PATH", archive)
    monkeypatch.setattr(server_mod, "EXTENDED_JSON_PATH", str(tmp_path / "out" / "ext.json"))

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_mod.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    r = urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_address[1]}/", timeout=5)
    yield r
    httpd.shutdown()
    httpd.server_close()
