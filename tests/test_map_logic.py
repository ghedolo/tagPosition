"""Unit tests for the data-loading and grouping logic in map.py."""
import datetime
import json

import pytest

from conftest import geo_entry, semantic_entry, write_archive, now_utc, iso


def test_assign_letters_unique_first_letter(map_env):
    result = map_env.assign_letters(["Alpha", "Bravo", "Charlie"])
    assert result == {"Alpha": "A", "Bravo": "B", "Charlie": "C"}


def test_assign_letters_collision_uses_two_chars(map_env):
    result = map_env.assign_letters(["Alpha", "Anna", "Bravo"])
    assert result == {"Alpha": "AL", "Anna": "AN", "Bravo": "B"}


def test_assign_letters_strips_and_uppercases(map_env):
    result = map_env.assign_letters(["  keys "])
    assert result == {"  keys ": "K"}


def test_load_entries_missing_archive_exits_zero(map_env):
    with pytest.raises(SystemExit) as exc:
        map_env.load_entries()
    assert exc.value.code == 0


def test_load_entries_groups_by_tag_and_sorts_by_time(map_env, archive_path):
    write_archive(archive_path, [
        geo_entry("Alpha", minutes_ago=10),
        geo_entry("Bravo", minutes_ago=30),
        geo_entry("Alpha", minutes_ago=60),
    ])
    by_tag = map_env.load_entries()
    assert set(by_tag) == {"Alpha", "Bravo"}
    assert len(by_tag["Alpha"]) == 2
    times = [e["location_time"] for e in by_tag["Alpha"]]
    assert times == sorted(times)


def test_load_entries_skips_blank_and_malformed_lines(map_env, archive_path):
    good = geo_entry("Alpha")
    with open(archive_path, "w") as f:
        f.write("\n")
        f.write("{not json}\n")
        f.write("   \n")
        f.write(json.dumps(good) + "\n")
    by_tag = map_env.load_entries()
    assert len(by_tag["Alpha"]) == 1


def test_load_entries_drops_semantic_entries_without_coords(map_env, archive_path):
    write_archive(archive_path, [semantic_entry("Alpha"), geo_entry("Alpha")])
    by_tag = map_env.load_entries()
    assert len(by_tag["Alpha"]) == 1
    assert "lat" in by_tag["Alpha"][0]


def test_load_entries_applies_tag_rename(map_env, archive_path, monkeypatch):
    monkeypatch.setattr(map_env, "TAG_RENAME", {"Google Pixel 9": "My Phone"})
    write_archive(archive_path, [geo_entry("Google Pixel 9")])
    by_tag = map_env.load_entries()
    assert list(by_tag) == ["My Phone"]


def test_load_entries_raises_on_entry_without_tag_field(map_env, archive_path):
    """Documents current behaviour: a 'tag'-less line aborts the whole load."""
    bad = geo_entry("Alpha")
    del bad["tag"]
    write_archive(archive_path, [bad])
    with pytest.raises(KeyError):
        map_env.load_entries()


def test_split_entries_partitions_on_cutoff(map_env):
    cutoff = now_utc() - datetime.timedelta(hours=24)
    by_tag = {
        "Alpha": [geo_entry("Alpha", minutes_ago=60), geo_entry("Alpha", minutes_ago=60 * 40)],
        "Bravo": [geo_entry("Bravo", minutes_ago=10)],
    }
    within, extended = map_env.split_entries(by_tag, cutoff)
    assert len(within["Alpha"]) == 1
    assert len(within["Bravo"]) == 1
    assert len(extended) == 1
    assert extended[0]["tag"] == "Alpha"


def test_split_entries_returns_plain_dict(map_env):
    cutoff = now_utc() - datetime.timedelta(hours=24)
    within, extended = map_env.split_entries({"Alpha": [geo_entry("Alpha")]}, cutoff)
    assert type(within) is dict
    assert within["Alpha"]


def test_split_entries_tag_with_only_old_entries_is_absent_from_within(map_env):
    cutoff = now_utc() - datetime.timedelta(hours=24)
    by_tag = {"Old": [geo_entry("Old", minutes_ago=60 * 100)]}
    within, extended = map_env.split_entries(by_tag, cutoff)
    assert "Old" not in within
    assert len(extended) == 1
