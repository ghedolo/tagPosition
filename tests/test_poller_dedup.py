"""Integration tests for poller.main() dedup logic, with the network layer stubbed out.

All Google/FCM calls are replaced by monkeypatched module-level names, so these
tests exercise only the local decision of which fixes get appended to the archive.
"""
import json

import pytest

from conftest import geo_entry, write_archive


class _FakeFcmReceiver:
    stopped = 0

    def stop_listening(self):
        type(self).stopped += 1


@pytest.fixture
def offline_poller(poller_env, monkeypatch):
    """poller with every network dependency stubbed. Set .fixes to drive _fetch_location."""
    _FakeFcmReceiver.stopped = 0
    monkeypatch.setattr(poller_env, "_check_auth", lambda: None)
    monkeypatch.setattr(poller_env, "request_device_list", lambda: "00")
    monkeypatch.setattr(poller_env, "parse_device_list_protobuf", lambda hexstr: object())
    monkeypatch.setattr(poller_env, "get_canonic_ids", lambda dl: [("Alpha", "cid-alpha")])
    monkeypatch.setattr(poller_env, "FcmReceiver", _FakeFcmReceiver)
    monkeypatch.setattr(poller_env.sys, "argv", ["poller.py"])
    poller_env._test_fixes = []
    monkeypatch.setattr(poller_env, "_fetch_location",
                        lambda cid, name, timeout=60: list(poller_env._test_fixes))
    return poller_env


def _archive(path):
    try:
        with open(path) as f:
            return [json.loads(l) for l in f if l.strip()]
    except FileNotFoundError:
        return []


def test_main_writes_new_entries(offline_poller, archive_path):
    offline_poller._test_fixes = [geo_entry("Alpha", minutes_ago=5)]
    offline_poller.main()
    assert len(_archive(archive_path)) == 1


def test_main_skips_entry_already_in_archive(offline_poller, archive_path):
    fix = geo_entry("Alpha", minutes_ago=5)
    write_archive(archive_path, [fix])
    offline_poller._test_fixes = [fix]
    offline_poller.main()
    assert len(_archive(archive_path)) == 1


def test_main_skips_fix_older_than_latest_known_for_that_tag(offline_poller, archive_path):
    write_archive(archive_path, [geo_entry("Alpha", minutes_ago=5)])
    offline_poller._test_fixes = [geo_entry("Alpha", minutes_ago=120)]
    offline_poller.main()
    assert len(_archive(archive_path)) == 1


def test_main_deduplicates_within_a_single_batch(offline_poller, archive_path):
    fix = geo_entry("Alpha", minutes_ago=5)
    offline_poller._test_fixes = [fix, dict(fix)]
    offline_poller.main()
    assert len(_archive(archive_path)) == 1


def test_main_keeps_only_the_newest_when_batch_has_several_timestamps(offline_poller, archive_path):
    older = geo_entry("Alpha", minutes_ago=30)
    newer = geo_entry("Alpha", minutes_ago=5)
    offline_poller._test_fixes = [newer, older]
    offline_poller.main()
    written = _archive(archive_path)
    assert [e["location_time"] for e in written] == [newer["location_time"]]


def test_main_appends_without_truncating_existing_data(offline_poller, archive_path):
    existing = geo_entry("Bravo", minutes_ago=200)
    write_archive(archive_path, [existing])
    offline_poller._test_fixes = [geo_entry("Alpha", minutes_ago=5)]
    offline_poller.main()
    tags = [e["tag"] for e in _archive(archive_path)]
    assert tags == ["Bravo", "Alpha"]


def test_main_stops_the_fcm_listener(offline_poller):
    offline_poller._test_fixes = [geo_entry("Alpha", minutes_ago=5)]
    offline_poller.main()
    assert _FakeFcmReceiver.stopped == 1


def test_main_exits_when_no_devices(offline_poller, monkeypatch):
    monkeypatch.setattr(offline_poller, "get_canonic_ids", lambda dl: [])
    with pytest.raises(SystemExit) as exc:
        offline_poller.main()
    assert exc.value.code == 1


def test_main_purge_mode_skips_auth_and_network(poller_env, monkeypatch, archive_path):
    calls = []
    monkeypatch.setattr(poller_env.sys, "argv", ["poller.py", "--purge"])
    monkeypatch.setattr(poller_env, "_check_auth", lambda: calls.append("auth"))
    monkeypatch.setattr(poller_env, "request_device_list", lambda: calls.append("net"))
    monkeypatch.setattr(poller_env, "_purge", lambda: calls.append("purge"))
    poller_env.main()
    assert calls == ["purge"]
