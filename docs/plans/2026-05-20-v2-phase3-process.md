# v2 Phase 3 — Lifecycle/process.py

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Single source of truth for spawning detached subprocesses. After this module, all subprocess management in v2 goes through `spawn_managed` — eliminating the v1 PIPE-buffer bug architecturally and the kill-pid inconsistencies.

**Architecture:** Pure helper module with 4 functions: `spawn_managed`, `wait_alive`, `wait_port` (alias to utils.wait_for_port for convenience), `kill_pg`. Every detached process uses `start_new_session=True` + either a log file or `DEVNULL` — never undrained PIPE.

**Tech Stack:** Python stdlib `subprocess`, `os`, `signal`, `time`. Phase 0-2 modules available.

**Reference spec:** `docs/2026-05-20-v2-greenfield-design.md` — "Process discipline (lifecycle/process.py)" section.

**Branch:** `v2`.

---

## Task 1: `lifecycle/process.py` — spawn_managed + wait_alive + kill_pg

**Files:**
- Modify: `src/drop/lifecycle/process.py`
- Create: `tests/unit/test_lifecycle_process.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_lifecycle_process.py`:

```python
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
```

- [ ] **Step 2: Run, verify failures**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest tests/unit/test_lifecycle_process.py -v
```

Expected: collection errors / AttributeError (module is stub).

- [ ] **Step 3: Implement `src/drop/lifecycle/process.py`**

Replace with:

```python
"""Single source of truth for subprocess spawning in drop.

All detached subprocesses go through spawn_managed. Two invariants:

  1. start_new_session=True — child is in its own session, so SIGHUP on
     the CLI's terminal does not propagate to it.
  2. Output goes to a log file OR DEVNULL — never undrained subprocess.PIPE.
     Undrained PIPEs fill the kernel's ~64KB buffer in seconds and block
     the child's next write(), freezing it. cloudflared hits this in v1.

If you find yourself reaching for subprocess.Popen directly anywhere
in v2, route through this module instead.
"""

import os
import signal
import subprocess
import time
from pathlib import Path


def spawn_managed(
    cmd,
    *,
    log_file: Path | None = None,
    cwd: Path | None = None,
    shell: bool = False,
) -> subprocess.Popen:
    """Spawn a detached subprocess.

    Args:
        cmd: list[str] (no shell) or str (with shell=True).
        log_file: if given, stdout+stderr go to this file. Otherwise DEVNULL.
        cwd: working directory.
        shell: pass cmd to the shell. Use only for user-supplied run_cmd.

    Returns the Popen object. The parent's reference to log_file's FD is
    closed before returning, so the child owns it; subsequent writes work
    even after the parent exits.
    """
    if log_file is not None:
        # Append mode in case caller reuses the same log path
        fh = open(log_file, "ab")
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=fh,
                stderr=subprocess.STDOUT,
                cwd=str(cwd) if cwd is not None else None,
                shell=shell,
                start_new_session=True,
            )
        finally:
            fh.close()  # parent's FD released; child still has its dup
    else:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(cwd) if cwd is not None else None,
            shell=shell,
            start_new_session=True,
        )
    return proc


def wait_alive(pid: int, after: float = 1.0) -> bool:
    """Check if pid is alive after waiting `after` seconds.

    The wait gives the process time to either bind a port / open a file
    or crash. Returns True if alive at the end of the wait.
    """
    if pid <= 0:
        return False
    if after > 0:
        time.sleep(after)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# Re-export wait_for_port for convenience — lifecycle code uses it for
# "did the spawned process bind its port yet?".
from ..utils import wait_for_port  # noqa: E402


def kill_pg(pid: int) -> bool:
    """SIGTERM the process group led by pid. Returns True if signal was sent."""
    if pid <= 0:
        return False
    try:
        os.killpg(pid, signal.SIGTERM)
        return True
    except OSError:
        return False
```

- [ ] **Step 4: Run, verify pass**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest tests/unit/test_lifecycle_process.py -v
```

Expected: ~14 passed. Some may take a couple of seconds (subprocess overhead).

- [ ] **Step 5: Commit**

```bash
git add src/drop/lifecycle/process.py tests/unit/test_lifecycle_process.py
git commit -m "feat(v2): lifecycle.process — spawn_managed + wait_alive + kill_pg"
```

---

## Phase 3 Self-Review

After Task 1:

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest -v
```

Expected: all previous tests + ~14 new = ~116 tests, all green.

Push:
```bash
git push origin v2
```

## What Phase 3 Does NOT Include (deferred)

- Cloudflared tunnel — Phase 4 (uses spawn_managed via log_file).
- App/server lifecycle orchestration — Phase 6.
- Watchdog — out of scope per v2 spec.

## Phase 4 Readiness

After Phase 3, Phase 4 (`lifecycle/tunnel.py`) can simply:
```python
proc = spawn_managed(
    [cloudflared, "tunnel", "--url", f"http://localhost:{port}", "--no-autoupdate",
     "--logfile", str(log_file)],
    log_file=log_file,
)
```
And tail the logfile for the URL.
