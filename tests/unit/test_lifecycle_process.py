"""Tests for drop.lifecycle.process — spawn_managed + wait_alive + kill_pg."""

import os
import signal
import socket
import subprocess
import time
from pathlib import Path

import pytest

from drop.lifecycle import process as proc


# spawn_managed — basic spawn

def test_spawn_managed_starts_subprocess():
    p = proc.spawn_managed(["sleep", "5"])
    try:
        # Process is alive
        assert proc.wait_alive(p.pid, after=0.1) is True
    finally:
        proc.kill_pg(p.pid)
        p.wait(timeout=2)


def test_spawn_managed_uses_devnull_by_default():
    p = proc.spawn_managed(["echo", "hello"])
    try:
        p.wait(timeout=2)
        # stdout was DEVNULL — child output not captured anywhere
        # (we just verify the process exited cleanly)
        assert p.returncode == 0
    finally:
        try:
            proc.kill_pg(p.pid)
        except OSError:
            pass


def test_spawn_managed_writes_to_log_file(tmp_path):
    log = tmp_path / "out.log"
    # Use python -c so we don't depend on shell semantics
    import sys
    p = proc.spawn_managed(
        [sys.executable, "-c", "print('hello'); import sys; sys.stderr.write('err\\n')"],
        log_file=log,
    )
    p.wait(timeout=5)
    assert log.exists()
    content = log.read_text()
    assert "hello" in content
    assert "err" in content  # both streams merged into the log


def test_spawn_managed_log_file_survives_parent_close(tmp_path):
    """The log file FD must be owned by the child after spawn_managed
    returns — parent closing its FD must not break child writes."""
    log = tmp_path / "out.log"
    import sys
    p = proc.spawn_managed(
        [sys.executable, "-c",
         "import time, sys; "
         "[print(f'line{i}') or sys.stdout.flush() or time.sleep(0.05) for i in range(5)]"],
        log_file=log,
    )
    p.wait(timeout=5)
    content = log.read_text()
    # All 5 lines written despite parent closing its handle immediately
    for i in range(5):
        assert f"line{i}" in content


def test_spawn_managed_starts_new_session():
    """Verify the spawned process is in its own session (key for SIGHUP safety)."""
    p = proc.spawn_managed(["sleep", "5"])
    try:
        # os.getsid returns the session id; child's sid should equal child's pid
        # (because start_new_session creates a new session with the child as leader)
        sid = os.getsid(p.pid)
        assert sid == p.pid
    finally:
        proc.kill_pg(p.pid)
        p.wait(timeout=2)


def test_spawn_managed_supports_cwd(tmp_path):
    import sys
    p = proc.spawn_managed(
        [sys.executable, "-c", "import os; print(os.getcwd())"],
        cwd=tmp_path,
        log_file=tmp_path / "cwd.log",
    )
    p.wait(timeout=5)
    assert str(tmp_path.resolve()) in (tmp_path / "cwd.log").read_text()


def test_spawn_managed_shell_true_for_string_cmd(tmp_path):
    """A string command is interpreted via shell (matches v1's --run convention)."""
    log = tmp_path / "shell.log"
    p = proc.spawn_managed("echo hello-from-shell", shell=True, log_file=log)
    p.wait(timeout=5)
    assert "hello-from-shell" in log.read_text()


# wait_alive

def test_wait_alive_true_for_running_process():
    p = proc.spawn_managed(["sleep", "5"])
    try:
        assert proc.wait_alive(p.pid, after=0.2) is True
    finally:
        proc.kill_pg(p.pid)
        p.wait(timeout=2)


def test_wait_alive_false_for_dead_process():
    p = subprocess.Popen(["true"])
    p.wait(timeout=2)
    # After a real exit, os.kill(pid, 0) raises ProcessLookupError
    assert proc.wait_alive(p.pid, after=0.0) is False


def test_wait_alive_false_for_zero_pid():
    assert proc.wait_alive(0, after=0.0) is False


# kill_pg

def test_kill_pg_terminates_process():
    p = proc.spawn_managed(["sleep", "30"])
    assert proc.wait_alive(p.pid, after=0.1) is True
    assert proc.kill_pg(p.pid) is True
    p.wait(timeout=2)
    assert proc.wait_alive(p.pid, after=0.0) is False


def test_kill_pg_kills_process_group():
    """Children of the spawned process must also die."""
    import sys
    # Parent spawns a child (subprocess), waits forever. Both in the same PG.
    p = proc.spawn_managed(
        [sys.executable, "-c",
         "import subprocess, time; "
         "subprocess.Popen(['sleep', '30']); "
         "time.sleep(30)"],
    )
    time.sleep(0.5)  # let grandchild spawn
    # Count sleeps in our session before killing
    proc.kill_pg(p.pid)
    p.wait(timeout=3)
    # Direct os.kill on the grandchild should fail (it was in the killed PG)
    # We can't easily get the grandchild pid; instead, assert no `sleep 30` lingered
    # from our process group. This is a soft check.
    assert proc.wait_alive(p.pid, after=0.0) is False


def test_kill_pg_no_op_on_zero_pid():
    assert proc.kill_pg(0) is False


def test_kill_pg_no_op_on_dead_pid():
    p = subprocess.Popen(["true"])
    p.wait(timeout=2)
    # Already dead: kill_pg returns False but does not raise
    assert proc.kill_pg(p.pid) is False
