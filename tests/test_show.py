"""Tests for the CLI archive reader show.py."""
import datetime
import json

import pytest

from conftest import geo_entry, semantic_entry, write_archive


def test_load_entries_missing_archive_exits_zero(show_env, capsys):
    with pytest.raises(SystemExit) as exc:
        show_env.load_entries()
    assert exc.value.code == 0
    assert "Run poller.py first" in capsys.readouterr().out


def test_load_entries_returns_all_without_filters(show_env, archive_path):
    write_archive(archive_path, [geo_entry("Alpha"), geo_entry("Bravo")])
    assert len(show_env.load_entries()) == 2


def test_load_entries_tag_filter_is_case_insensitive(show_env, archive_path):
    write_archive(archive_path, [geo_entry("Alpha"), geo_entry("Bravo")])
    entries = show_env.load_entries(tag_filter="alpha")
    assert [e["tag"] for e in entries] == ["Alpha"]


def test_load_entries_tag_filter_requires_exact_name(show_env, archive_path):
    write_archive(archive_path, [geo_entry("Alpha")])
    assert show_env.load_entries(tag_filter="Alp") == []


def test_load_entries_date_range_filters_on_location_time(show_env, archive_path):
    old = geo_entry("Alpha", minutes_ago=60 * 24 * 10)
    new = geo_entry("Alpha", minutes_ago=10)
    write_archive(archive_path, [old, new])
    cut = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    assert len(show_env.load_entries(date_from=cut)) == 1
    assert len(show_env.load_entries(date_to=cut)) == 1


def test_load_entries_skips_malformed_lines(show_env, archive_path):
    with open(archive_path, "w") as f:
        f.write("{oops\n\n" + json.dumps(geo_entry("Alpha")) + "\n")
    assert len(show_env.load_entries()) == 1


def test_load_entries_keeps_semantic_entries(show_env, archive_path):
    """Unlike map.load_entries, show.py keeps coordinate-less entries."""
    write_archive(archive_path, [semantic_entry("Alpha")])
    assert len(show_env.load_entries()) == 1


def test_print_summary_shows_latest_fix_per_tag(show_env, archive_path, capsys):
    old = geo_entry("Alpha", minutes_ago=300, lat=1.0, lon=2.0)
    new = geo_entry("Alpha", minutes_ago=5, lat=3.0, lon=4.0)
    show_env.print_summary([old, new])
    out = capsys.readouterr().out
    assert "Tag : Alpha  (2 entries)" in out
    assert "lat=3.0  lon=4.0" in out
    assert "query=3.0,4.0" in out


def test_print_summary_semantic_entry_shows_name_not_coordinates(show_env, capsys):
    show_env.print_summary([semantic_entry("Alpha", name="Office")])
    out = capsys.readouterr().out
    assert "Loc : Office" in out
    assert "lat=" not in out


def test_print_summary_groups_multiple_tags(show_env, capsys):
    show_env.print_summary([geo_entry("Bravo"), geo_entry("Alpha")])
    out = capsys.readouterr().out
    assert out.index("Tag : Alpha") < out.index("Tag : Bravo")
    assert "Total entries: 2" in out


def test_main_no_matches_exits_zero(show_env, archive_path, monkeypatch, capsys):
    write_archive(archive_path, [geo_entry("Alpha")])
    monkeypatch.setattr(show_env.sys, "argv", ["show.py", "--tag", "Nonexistent"])
    with pytest.raises(SystemExit) as exc:
        show_env.main()
    assert exc.value.code == 0
    assert "No entries match" in capsys.readouterr().out


def test_main_all_flag_dumps_ndjson(show_env, archive_path, monkeypatch, capsys):
    write_archive(archive_path, [geo_entry("Alpha")])
    monkeypatch.setattr(show_env.sys, "argv", ["show.py", "--all"])
    show_env.main()
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert json.loads(lines[0])["tag"] == "Alpha"
