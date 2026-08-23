"""Tests for the archive-side logic of poller.py (no network calls involved)."""
import datetime
import json
import os

import pytest

from conftest import geo_entry, write_archive, now_utc, iso


def _read_ndjson(path):
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def test_ts_to_fname_strips_separators(poller_env):
    assert poller_env._ts_to_fname("2026-05-01T12:34:56Z") == "20260501T123456Z"


def test_status_name_known_and_unknown(poller_env):
    from ProtoDecoders import Common_pb2
    assert poller_env._status_name(Common_pb2.Status.AGGREGATED) == "AGGREGATED"
    assert poller_env._status_name(Common_pb2.Status.SEMANTIC) == "SEMANTIC"
    assert poller_env._status_name(9999) == "9999"


def test_data_lock_is_reentrant_across_sequential_calls(poller_env):
    with poller_env._data_lock():
        pass
    with poller_env._data_lock():
        pass
    assert os.path.exists(poller_env.LOCK_PATH)


def test_load_archive_state_empty_when_no_file(poller_env):
    seen, latest = poller_env._load_archive_state()
    assert seen == set() and latest == {}


def test_load_archive_state_collects_keys_and_latest_per_tag(poller_env, archive_path):
    a_old = geo_entry("Alpha", minutes_ago=100)
    a_new = geo_entry("Alpha", minutes_ago=1)
    b = geo_entry("Bravo", minutes_ago=50)
    write_archive(archive_path, [a_old, a_new, b])
    seen, latest = poller_env._load_archive_state()
    assert ("Alpha", a_old["location_time"]) in seen
    assert latest["Alpha"] == a_new["location_time"]
    assert latest["Bravo"] == b["location_time"]


def test_load_archive_state_ignores_malformed_lines(poller_env, archive_path):
    good = geo_entry("Alpha")
    with open(archive_path, "w") as f:
        f.write("garbage\n\n" + json.dumps(good) + "\n")
    seen, latest = poller_env._load_archive_state()
    assert len(seen) == 1


def test_purge_no_file_is_a_noop(poller_env, capsys):
    poller_env._purge()
    assert "No data file found" in capsys.readouterr().out


def test_purge_keeps_recent_entries_and_writes_nothing_when_all_recent(poller_env, archive_path, data_dir):
    write_archive(archive_path, [geo_entry("Alpha", minutes_ago=60)])
    poller_env._purge()
    assert len(_read_ndjson(archive_path)) == 1
    assert not list(data_dir.glob("position_*.json"))


def test_purge_moves_old_entries_to_a_dated_archive_file(poller_env, archive_path, data_dir):
    old1 = geo_entry("Alpha", minutes_ago=60 * 24 * 30)
    old2 = geo_entry("Alpha", minutes_ago=60 * 24 * 20)
    recent = geo_entry("Alpha", minutes_ago=60)
    write_archive(archive_path, [recent, old2, old1])

    poller_env._purge()

    kept = _read_ndjson(archive_path)
    assert [e["location_time"] for e in kept] == [recent["location_time"]]

    archives = list(data_dir.glob("position_*.json"))
    assert len(archives) == 1
    moved = _read_ndjson(str(archives[0]))
    assert [e["location_time"] for e in moved] == [old1["location_time"], old2["location_time"]]

    expected = f"position_{poller_env._ts_to_fname(old1['location_time'])}_" \
               f"{poller_env._ts_to_fname(old2['location_time'])}.json"
    assert archives[0].name == expected


def test_purge_cutoff_is_purge_days(poller_env, archive_path, data_dir):
    assert poller_env.PURGE_DAYS == 7
    just_inside = geo_entry("Alpha", minutes_ago=60 * 24 * 7 - 60)
    just_outside = geo_entry("Alpha", minutes_ago=60 * 24 * 7 + 60)
    write_archive(archive_path, [just_inside, just_outside])
    poller_env._purge()
    assert [e["location_time"] for e in _read_ndjson(archive_path)] == [just_inside["location_time"]]


def test_purge_keeps_entries_with_unparsable_timestamp(poller_env, archive_path):
    bad = geo_entry("Alpha", minutes_ago=60 * 24 * 30)
    bad["location_time"] = "not-a-date"
    write_archive(archive_path, [bad, geo_entry("Alpha", minutes_ago=60 * 24 * 30)])
    poller_env._purge()
    kept = _read_ndjson(archive_path)
    assert [e["location_time"] for e in kept] == ["not-a-date"]


def test_purge_drops_malformed_lines_from_the_archive(poller_env, archive_path):
    """Documents current behaviour: unparsable NDJSON lines are silently discarded on purge."""
    good_old = geo_entry("Alpha", minutes_ago=60 * 24 * 30)
    good_new = geo_entry("Alpha", minutes_ago=10)
    with open(archive_path, "w") as f:
        f.write(json.dumps(good_old) + "\n{broken\n" + json.dumps(good_new) + "\n")
    poller_env._purge()
    assert len(_read_ndjson(archive_path)) == 1
