"""Tests for the rotating poller log.

The poller runs from cron every 15 minutes, so the log must stay bounded on
disk and keep the owner-only permissions used for the position archive.
"""
import os

import pytest


@pytest.fixture
def log_path(tmp_path):
    return str(tmp_path / "logs" / "poller.log")


@pytest.fixture
def file_log(poller_env, log_path):
    """Attach a rotating file handler and detach it again after the test."""
    handler = poller_env._setup_file_log(log_path)
    yield handler
    poller_env.log.removeHandler(handler)
    handler.close()


def _rotated(path):
    d = os.path.dirname(path)
    base = os.path.basename(path)
    return sorted(f for f in os.listdir(d) if f.startswith(base))


def test_setup_file_log_does_not_create_the_file_until_something_is_logged(file_log, log_path):
    assert not os.path.exists(log_path)


def test_log_file_is_owner_only(poller_env, file_log, log_path):
    poller_env.log.info("[Poller] first line")
    assert os.stat(log_path).st_mode & 0o777 == poller_env.DATA_FILE_MODE


def test_log_rotates_and_never_exceeds_the_backup_count(poller_env, log_path, monkeypatch):
    monkeypatch.setattr(poller_env, "LOG_MAX_BYTES", 2048)
    monkeypatch.setattr(poller_env, "LOG_BACKUP_COUNT", 2)
    handler = poller_env._setup_file_log(log_path)
    try:
        for i in range(300):
            poller_env.log.info("[Poller] padding line %d %s", i, "x" * 60)
    finally:
        poller_env.log.removeHandler(handler)
        handler.close()

    files = _rotated(log_path)
    assert files == ["poller.log", "poller.log.1", "poller.log.2"]
    for name in files:
        assert os.path.getsize(os.path.join(os.path.dirname(log_path), name)) <= 2048 + 200


def test_rotated_backups_are_owner_only(poller_env, log_path, monkeypatch):
    monkeypatch.setattr(poller_env, "LOG_MAX_BYTES", 2048)
    monkeypatch.setattr(poller_env, "LOG_BACKUP_COUNT", 2)
    handler = poller_env._setup_file_log(log_path)
    try:
        for i in range(300):
            poller_env.log.info("[Poller] padding line %d %s", i, "x" * 60)
    finally:
        poller_env.log.removeHandler(handler)
        handler.close()

    d = os.path.dirname(log_path)
    for name in _rotated(log_path):
        assert os.stat(os.path.join(d, name)).st_mode & 0o777 == poller_env.DATA_FILE_MODE


def test_warnings_go_to_stderr_and_info_to_stdout(poller_env, capsys):
    poller_env.log.info("[Poller] an info line")
    poller_env.log.warning("[Poller] WARNING: a warning line")
    captured = capsys.readouterr()
    assert "an info line" in captured.out
    assert "an info line" not in captured.err
    assert "a warning line" in captured.err
    assert "a warning line" not in captured.out
