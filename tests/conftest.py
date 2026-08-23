"""Shared fixtures for the tagPosition test suite.

The application modules resolve their data paths at import time into module-level
constants (ARCHIVE_PATH, EXTENDED_JSON_PATH, LOCK_PATH). Tests therefore redirect
those constants to a temporary directory instead of touching data/ or tmp/.
"""
import datetime
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc():
    return datetime.datetime.now(datetime.UTC)


def geo_entry(tag="Alpha", minutes_ago=10, lat=44.5, lon=11.35, accuracy=25.0,
              status="AGGREGATED", polled_ago=5):
    """Build one geolocated NDJSON entry shaped exactly like poller.py writes it."""
    t = now_utc() - datetime.timedelta(minutes=minutes_ago)
    p = now_utc() - datetime.timedelta(minutes=polled_ago)
    return {
        "polled_at": iso(p),
        "tag": tag,
        "lat": lat,
        "lon": lon,
        "altitude_m": 0,
        "accuracy_m": accuracy,
        "status": status,
        "is_own_report": False,
        "location_time": iso(t),
    }


def semantic_entry(tag="Alpha", minutes_ago=10, name="Home"):
    t = now_utc() - datetime.timedelta(minutes=minutes_ago)
    return {
        "polled_at": iso(now_utc()),
        "tag": tag,
        "status": "SEMANTIC",
        "semantic_name": name,
        "location_time": iso(t),
    }


def write_archive(path, entries):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def archive_path(data_dir):
    return str(data_dir / "positions.json")


@pytest.fixture
def map_env(monkeypatch, archive_path, tmp_path):
    """Point map.py at a temporary archive and a temporary extended-data file."""
    import map as map_mod
    ext = str(tmp_path / "out" / "data_extended.json")
    monkeypatch.setattr(map_mod, "ARCHIVE_PATH", archive_path)
    monkeypatch.setattr(map_mod, "EXTENDED_JSON_PATH", ext)
    return map_mod


@pytest.fixture
def show_env(monkeypatch, archive_path):
    import show as show_mod
    monkeypatch.setattr(show_mod, "ARCHIVE_PATH", archive_path)
    return show_mod


@pytest.fixture
def poller_env(monkeypatch, archive_path, data_dir):
    import poller as poller_mod
    monkeypatch.setattr(poller_mod, "ARCHIVE_PATH", archive_path)
    monkeypatch.setattr(poller_mod, "LOCK_PATH", str(data_dir / ".poller.lock"))
    return poller_mod
