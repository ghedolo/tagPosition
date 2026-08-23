"""Tests for the shell wrappers and for repository hygiene (secrets never tracked)."""
import os
import stat
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHELL_SCRIPTS = ["update.sh", "sendToPi.sh"]


@pytest.mark.parametrize("script", SHELL_SCRIPTS)
def test_shell_script_has_valid_syntax(script):
    path = os.path.join(ROOT, script)
    r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def _run_update_sh(tmp_path, weekday, hour, minute):
    """Run update.sh in a sandbox with stubbed `date`, `python` and venv activation.

    Returns the list of argument strings the stub `python` was called with.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.txt"

    (bin_dir / "date").write_text(
        "#!/bin/sh\n"
        f'case "$1" in\n'
        f'  +%u) echo "{weekday}" ;;\n'
        f'  +%H) echo "{hour}" ;;\n'
        f'  +%M) echo "{minute}" ;;\n'
        f'  *) echo "" ;;\n'
        f'esac\n'
    )
    (bin_dir / "python").write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\n')
    for f in ("date", "python"):
        p = bin_dir / f
        p.chmod(p.stat().st_mode | stat.S_IEXEC)

    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "activate").write_text("true\n")

    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    subprocess.run(["bash", os.path.join(ROOT, "update.sh")],
                   cwd=tmp_path, env=env, capture_output=True, text=True, check=True)
    return calls.read_text().splitlines() if calls.exists() else []


def test_update_sh_always_runs_the_poller(tmp_path):
    calls = _run_update_sh(tmp_path, weekday=3, hour="14", minute="37")
    assert calls == ["-u poller.py"]


def test_update_sh_runs_purge_monday_just_after_midnight(tmp_path):
    calls = _run_update_sh(tmp_path, weekday=1, hour="00", minute="05")
    assert calls == ["-u poller.py", "-u poller.py --purge"]


def test_update_sh_does_not_purge_after_the_15_minute_window(tmp_path):
    calls = _run_update_sh(tmp_path, weekday=1, hour="00", minute="20")
    assert calls == ["-u poller.py"]


def test_update_sh_does_not_purge_on_other_weekdays(tmp_path):
    calls = _run_update_sh(tmp_path, weekday=2, hour="00", minute="05")
    assert calls == ["-u poller.py"]


def test_update_sh_does_not_purge_at_other_hours(tmp_path):
    calls = _run_update_sh(tmp_path, weekday=1, hour="01", minute="05")
    assert calls == ["-u poller.py"]


def test_sendtopi_sh_contains_no_hardcoded_ip():
    """The committed helper must keep the placeholder; the real IP lives in local_*.sh."""
    text = open(os.path.join(ROOT, "sendToPi.sh")).read()
    assert "<YOUR_PI_IP>" in text


# --- repository hygiene -----------------------------------------------------

def _git(*args):
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True)


@pytest.mark.parametrize("path", [
    "data/positions.json",
    "local_sendToPi.sh",
    "tmp/map.html",
    ".venv/pyvenv.cfg",
])
def test_sensitive_path_is_gitignored(path):
    r = _git("check-ignore", "-q", path)
    assert r.returncode == 0, f"{path} is NOT ignored by git"


def test_submodule_secrets_json_is_gitignored():
    """secrets.json lives inside the GoogleFindMyTools submodule, which has its own .gitignore."""
    sub = os.path.join(ROOT, "lib", "GoogleFindMyTools")
    r = subprocess.run(["git", "-C", sub, "check-ignore", "-q", "Auth/secrets.json"],
                       capture_output=True, text=True)
    assert r.returncode == 0, "Auth/secrets.json is NOT ignored inside the submodule"


@pytest.mark.parametrize("name", ["secrets.json", "positions.json"])
def test_sensitive_file_was_never_committed(name):
    r = _git("log", "--all", "--pretty=format:", "--name-only", "--", f"*{name}")
    assert r.stdout.strip() == "", f"{name} appears in git history:\n{r.stdout}"
