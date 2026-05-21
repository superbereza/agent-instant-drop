"""End-to-end tests for drop CLI — drives `drop` via subprocess.run.

Each test sets DROP_HOME to an isolated tmp dir so state is contained.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

DROP = sys.executable, "-m", "drop.cli"


def _drop(*args, drop_home: Path, expect_ok: bool = True):
    env = {**os.environ, "DROP_HOME": str(drop_home)}
    p = subprocess.run([*DROP, *args], capture_output=True, text=True, env=env, timeout=20)
    if expect_ok and p.returncode != 0:
        raise AssertionError(
            f"drop {args} failed: rc={p.returncode}\nstdout={p.stdout}\nstderr={p.stderr}"
        )
    return p


# Basic dispatch

@pytest.mark.e2e
def test_help_works(drop_home):
    p = _drop("--help", drop_home=drop_home)
    assert "drop" in p.stdout.lower()
    assert "add" in p.stdout
    assert "start" in p.stdout


@pytest.mark.e2e
def test_status_no_pages(drop_home):
    p = _drop("status", drop_home=drop_home)
    assert p.returncode == 0


@pytest.mark.e2e
def test_list_no_pages(drop_home):
    p = _drop("list", drop_home=drop_home)
    assert p.returncode == 0


# add — static page (auto-password default)

@pytest.mark.e2e
def test_add_static_auto_password(drop_home, tmp_path):
    f = tmp_path / "report.html"
    f.write_text("<h1>x</h1>")
    p = _drop("add", str(f), drop_home=drop_home)
    assert "Published:" in p.stdout
    assert "Password:" in p.stdout


@pytest.mark.e2e
def test_add_static_public(drop_home, tmp_path):
    f = tmp_path / "report.html"
    f.write_text("<h1>x</h1>")
    p = _drop("add", str(f), "--public", drop_home=drop_home)
    assert "Published:" in p.stdout
    assert "Password:" not in p.stdout


@pytest.mark.e2e
def test_add_static_custom_password(drop_home, tmp_path):
    f = tmp_path / "report.html"
    f.write_text("<h1>x</h1>")
    p = _drop("add", str(f), "--password", "mysecret", drop_home=drop_home)
    assert "mysecret" in p.stdout


# add — app

@pytest.mark.e2e
def test_add_app_auto_auth(drop_home, tmp_path):
    f = tmp_path / "app.py"
    f.write_text("# stub")
    p = _drop("add", str(f), "--run", "true", "--port", "9001",
              "--name", "test-app", drop_home=drop_home)
    assert "App registered" in p.stdout
    assert "Auth: basic" in p.stdout
    assert "drop / " in p.stdout  # auto-generated credentials


@pytest.mark.e2e
def test_add_app_public(drop_home, tmp_path):
    f = tmp_path / "app.py"
    f.write_text("# stub")
    p = _drop("add", str(f), "--run", "true", "--port", "9002",
              "--name", "test-pub", "--public", drop_home=drop_home)
    assert "App registered" in p.stdout
    assert "Auth:" not in p.stdout


@pytest.mark.e2e
def test_add_app_with_rewrite_host(drop_home, tmp_path):
    f = tmp_path / "app.py"
    f.write_text("# stub")
    p = _drop("add", str(f), "--run", "true", "--port", "9003",
              "--name", "test-rh", "--rewrite-host", drop_home=drop_home)
    assert "App registered" in p.stdout


# add — validation

@pytest.mark.e2e
def test_add_duplicate_name_refused(drop_home, tmp_path):
    f = tmp_path / "app.py"; f.write_text("# stub")
    _drop("add", str(f), "--run", "true", "--port", "9010",
          "--name", "dup", drop_home=drop_home)
    p = _drop("add", str(f), "--run", "true", "--port", "9011",
              "--name", "dup", drop_home=drop_home, expect_ok=False)
    assert p.returncode != 0
    assert "already exists" in (p.stderr + p.stdout).lower()


@pytest.mark.e2e
def test_add_auth_without_run_refused(drop_home, tmp_path):
    f = tmp_path / "x.html"; f.write_text("x")
    p = _drop("add", str(f), "--auth", "basic",
              drop_home=drop_home, expect_ok=False)
    assert p.returncode != 0


@pytest.mark.e2e
def test_add_rewrite_host_with_public_refused(drop_home, tmp_path):
    f = tmp_path / "app.py"; f.write_text("# stub")
    p = _drop("add", str(f), "--run", "true", "--port", "9020",
              "--name", "rhp", "--public", "--rewrite-host",
              drop_home=drop_home, expect_ok=False)
    assert p.returncode != 0


# list — shows registered pages

@pytest.mark.e2e
def test_list_after_add(drop_home, tmp_path):
    f = tmp_path / "report.html"
    f.write_text("<h1>x</h1>")
    _drop("add", str(f), "--public", "--name", "r1", drop_home=drop_home)
    p = _drop("list", "-a", drop_home=drop_home)
    assert "r1" in p.stdout


# remove

@pytest.mark.e2e
def test_remove_by_name(drop_home, tmp_path):
    f = tmp_path / "report.html"
    f.write_text("<h1>x</h1>")
    _drop("add", str(f), "--public", "--name", "rm-test", drop_home=drop_home)
    p = _drop("remove", "rm-test", drop_home=drop_home)
    assert p.returncode == 0
    # List no longer shows it
    p2 = _drop("list", "-a", drop_home=drop_home)
    assert "rm-test" not in p2.stdout


# start/stop app (no tunnel, no auth — fastest)

@pytest.mark.e2e
def test_start_stop_app_public(drop_home, tmp_path, free_port):
    f = tmp_path / "app.py"
    f.write_text("# stub")
    cmd = f"{sys.executable} -m http.server {free_port} --bind 127.0.0.1"
    _drop("add", str(f), "--run", cmd, "--port", str(free_port),
          "--name", "ss-test", "--public", drop_home=drop_home)
    p = _drop("start", "ss-test", "--no-tunnel", drop_home=drop_home)
    assert "App started" in p.stdout or "started" in p.stdout.lower()
    _drop("stop", "ss-test", drop_home=drop_home)


@pytest.mark.e2e
def test_logs_missing_app_returns_error(drop_home):
    p = _drop("logs", "nonexistent", drop_home=drop_home, expect_ok=False)
    assert p.returncode != 0


@pytest.mark.e2e
def test_logs_no_log_file_yet(drop_home, tmp_path):
    f = tmp_path / "x.py"; f.write_text("# stub")
    _drop("add", str(f), "--run", "true", "--port", "9100",
          "--name", "logsnone", "--public", drop_home=drop_home)
    # App never started → no log file
    p = _drop("logs", "logsnone", drop_home=drop_home, expect_ok=False)
    assert "no app log" in (p.stderr + p.stdout).lower()


@pytest.mark.e2e
def test_logs_reads_existing_log(drop_home, tmp_path):
    import json
    import re
    f = tmp_path / "x.py"; f.write_text("# stub")
    _drop("add", str(f), "--run", "true", "--port", "9101",
          "--name", "logshas", "--public", drop_home=drop_home)
    # Manually create a log file
    pages = subprocess.run(
        [*DROP, "list", "-a"],
        capture_output=True, text=True,
        env={**os.environ, "DROP_HOME": str(drop_home)},
    )
    # find page_id from list output
    m = re.search(r"\b([a-z0-9]{8,16})\b", pages.stdout)
    assert m
    page_id_short = m.group(1)
    # We need full page_id — read from pages.json
    raw = json.loads((drop_home / "pages.json").read_text())
    full_id = next(pid for pid, p in raw["pages"].items() if p["name"] == "logshas")
    log = drop_home / "logs" / f"{full_id}.app.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("hello from log\n")

    p = _drop("logs", "logshas", drop_home=drop_home)
    assert "hello from log" in p.stdout
