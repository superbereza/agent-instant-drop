# v2 Phase 0 — Test Harness + Skeleton

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set up pytest infrastructure on the `v2` branch, create empty v2 module skeleton, prove the harness works end-to-end. After this phase, every subsequent v2 phase writes tests first against an already-working test runner.

**Architecture:** pytest with config in `pyproject.toml`, three test scopes (`unit`/`integration`/`e2e`), shared fixtures in `tests/conftest.py` (`drop_home` for `~/.drop` isolation via `DROP_HOME` env var, `free_port` for socket allocation). On `v2` branch, wipe existing v1 `src/drop/` and create empty stubs for every v2 module so `pip install -e .` keeps working and `pytest --collect-only` succeeds. GitHub Actions runs pytest on every push to `v2`.

**Tech Stack:** pytest 8+, pytest-cov, uv for installs, GitHub Actions for CI. Python 3.10+ (matches existing `requires-python`).

**Reference spec:** `docs/2026-05-20-v2-greenfield-design.md`

**Branch:** `v2` (already created, tracks `origin/v2`). All work in this phase happens here. `main` is untouched.

---

## Task 1: Configure pytest in `pyproject.toml`

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add pytest dev dependencies + pytest config**

Open `pyproject.toml` and replace its full content with:

```toml
[project]
name = "agent-instant-drop"
version = "0.3.0"
description = "Drop any file, app, or prototype to your human"
readme = "README.md"
requires-python = ">=3.10"
dependencies = [
    "flask>=3.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=4.0",
]

[project.scripts]
drop = "drop.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/drop"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v --tb=short --strict-markers"
markers = [
    "integration: spawns real subprocesses (slower, may need network)",
    "e2e: invokes the drop CLI end-to-end via subprocess",
    "slow: takes more than 5 seconds",
]
```

- [ ] **Step 2: Install dev dependencies into the venv**

Run:
```bash
cd /home/superbereza/dev/agent-instant-drop
/home/superbereza/dev/agent-instant-drop/.venv/bin/pip install -e ".[dev]"
```

Expected: pip installs `pytest`, `pytest-cov`, and re-installs `agent-instant-drop` in editable mode. No errors.

- [ ] **Step 3: Verify pytest is available**

Run:
```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest --version
```

Expected: prints `pytest 8.x.y`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "feat(v2): add pytest dev dep + pytest config in pyproject.toml"
```

---

## Task 2: Create `tests/` directory tree

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/integration/__init__.py`
- Create: `tests/e2e/__init__.py`

- [ ] **Step 1: Create directories and empty `__init__.py`s**

```bash
cd /home/superbereza/dev/agent-instant-drop
mkdir -p tests/unit tests/integration tests/e2e
touch tests/__init__.py tests/unit/__init__.py tests/integration/__init__.py tests/e2e/__init__.py
```

(The `__init__.py` files mark directories as packages so pytest discovers them consistently and so test files can import shared helpers later.)

- [ ] **Step 2: Verify pytest can collect (no tests yet, but no errors either)**

Run:
```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest --collect-only
```

Expected:
```
collected 0 items
```
Exit code 5 (no tests collected) is acceptable here; what we're verifying is that pytest finds the test root with no errors.

- [ ] **Step 3: Commit**

```bash
git add tests/
git commit -m "feat(v2): create tests/ directory tree (unit/integration/e2e)"
```

---

## Task 3: Write `tests/conftest.py` with `drop_home` and `free_port` fixtures

**Files:**
- Create: `tests/conftest.py`

- [ ] **Step 1: Write the conftest**

Create `tests/conftest.py` with this exact content:

```python
"""Shared fixtures for drop v2 tests.

Fixtures:
    drop_home   — tmp dir set as DROP_HOME env so v2 modules see an isolated
                  ~/.drop. Restored after the test via monkeypatch.
    free_port   — int, a free local TCP port allocated from the OS.
"""

import socket

import pytest


@pytest.fixture
def drop_home(tmp_path, monkeypatch):
    """Isolated DROP_HOME for one test.

    v2 modules read `os.environ.get("DROP_HOME")` and fall back to
    `~/.drop` when unset. By setting DROP_HOME to a tmp dir here, each
    test gets a fresh storage root and cleanup is automatic.
    """
    home = tmp_path / ".drop"
    home.mkdir()
    monkeypatch.setenv("DROP_HOME", str(home))
    return home


@pytest.fixture
def free_port():
    """Allocate a free TCP port from the OS.

    Note: the port is released as soon as the socket goes out of scope.
    A second caller within the same test may receive a different (also
    free) port. Race-on-bind is possible if another process binds in
    the gap; in practice this is rare on a developer machine.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]
```

- [ ] **Step 2: Commit (fixtures alone — self-test follows in Task 4)**

```bash
git add tests/conftest.py
git commit -m "feat(v2): add drop_home and free_port pytest fixtures"
```

---

## Task 4: Self-test the fixtures

**Files:**
- Create: `tests/unit/test_harness.py`

- [ ] **Step 1: Write the failing test (it will fail at collection time, since the test references env behavior not yet validated)**

Create `tests/unit/test_harness.py` with this exact content:

```python
"""Self-tests for the test harness itself. If these fail, no other test
in v2 can be trusted."""

import os
import socket


def test_drop_home_sets_env_var(drop_home):
    assert os.environ["DROP_HOME"] == str(drop_home)
    assert drop_home.exists()
    assert drop_home.is_dir()


def test_drop_home_is_isolated_per_test(drop_home, tmp_path):
    # drop_home must live under tmp_path (pytest gives each test a
    # fresh tmp_path)
    assert drop_home.is_relative_to(tmp_path)


def test_free_port_is_unused(free_port):
    assert isinstance(free_port, int)
    assert 1024 < free_port < 65536
    # Verify we can actually bind to it
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", free_port))
        s.listen(1)  # no exception => port is usable


def test_free_port_gives_different_ports():
    """Two separate fixture instances should not necessarily collide.
    This is a sanity check, not a strict invariant.
    """
    # We can't actually invoke the fixture twice in one test; instead
    # we directly allocate two ports the way the fixture does.
    def alloc():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return s.getsockname()[1]
    p1 = alloc()
    p2 = alloc()
    # Both must be valid; equality is permitted (OS may reuse) but
    # extremely rare in practice.
    assert isinstance(p1, int) and isinstance(p2, int)
    assert 1024 < p1 < 65536 and 1024 < p2 < 65536
```

- [ ] **Step 2: Run the test, verify all four tests pass**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest tests/unit/test_harness.py -v
```

Expected output (last lines):
```
tests/unit/test_harness.py::test_drop_home_sets_env_var PASSED
tests/unit/test_harness.py::test_drop_home_is_isolated_per_test PASSED
tests/unit/test_harness.py::test_free_port_is_unused PASSED
tests/unit/test_harness.py::test_free_port_gives_different_ports PASSED

============ 4 passed in 0.XXs ============
```

If any test fails, investigate the fixture definition in `tests/conftest.py` before moving on. The harness must be green.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_harness.py
git commit -m "test(v2): self-test drop_home and free_port fixtures"
```

---

## Task 5: Wipe existing v1 `src/drop/` on the v2 branch

**Files:**
- Delete: every file under `src/drop/`

This is the destructive cutover from v1 to v2 source on this branch. v1 still lives on `main`. After this task the `v2` branch will not have a runnable `drop` CLI until Task 6 creates skeleton stubs and the later phases fill them in.

- [ ] **Step 1: Confirm you are on the `v2` branch**

```bash
cd /home/superbereza/dev/agent-instant-drop
git branch --show-current
```

Expected: `v2`

If anything else is printed, run `git checkout v2` and try again. **Do not** run the next step on `main`.

- [ ] **Step 2: Delete v1 source**

```bash
git rm -r src/drop/
```

Expected: every file under `src/drop/` is marked for deletion. `git status` should show 6+ files deleted.

- [ ] **Step 3: Do NOT commit yet — Task 6 adds the skeleton and the two go in one commit**

The repo is intentionally broken between this step and Task 6's commit. Move directly to Task 6.

---

## Task 6: Create v2 skeleton module files

**Files:**
- Create: `src/drop/__init__.py`
- Create: `src/drop/cli.py`
- Create: `src/drop/config.py`
- Create: `src/drop/storage.py`
- Create: `src/drop/runtime.py`
- Create: `src/drop/lifecycle/__init__.py`
- Create: `src/drop/lifecycle/process.py`
- Create: `src/drop/lifecycle/app.py`
- Create: `src/drop/lifecycle/server.py`
- Create: `src/drop/lifecycle/tunnel.py`
- Create: `src/drop/proxy.py`
- Create: `src/drop/server.py`
- Create: `src/drop/auth.py`
- Create: `src/drop/manifest.py`
- Create: `src/drop/utils.py`

Each file is a stub: a docstring describing the module's intended responsibility (per spec) plus the minimum content required for imports and the `drop` entry-point to not break. Real code arrives in Phases 1-10.

- [ ] **Step 1: Create the lifecycle subpackage directory**

```bash
cd /home/superbereza/dev/agent-instant-drop
mkdir -p src/drop/lifecycle
```

- [ ] **Step 2: Create `src/drop/__init__.py`**

Content:
```python
"""drop — agent instant drop (v2 in development).

See docs/2026-05-20-v2-greenfield-design.md for the architecture.
"""

__version__ = "2.0.0a1"
```

- [ ] **Step 3: Create `src/drop/cli.py`**

Content (a working `main` is required so `drop` entry-point doesn't break `pip install -e .`):
```python
"""CLI entry point. Argparse + dispatch + formatting only.

To be filled in Phase 7.
"""

import sys


def main() -> None:
    """Stub entry point. v2 CLI implementation lands in Phase 7."""
    print(
        "drop v2 is under development on the `v2` branch. "
        "Use the `main` branch for a working CLI.",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Create `src/drop/config.py`**

Content:
```python
"""Paths + constants + env overrides. Phase 1.

DROP_HOME (env), PAGES_FILE, RUNTIME_FILE, LOGS_DIR, BIN_DIR,
CLOUDFLARED_BIN_OVERRIDE, AUTH_REALM, DEFAULT_AUTH_USER, SCHEMA_VERSION.
"""
```

- [ ] **Step 5: Create `src/drop/storage.py`**

Content:
```python
"""Page CRUD over ~/.drop/pages.json with UNIQUE(name) constraint. Phase 2.

Page dataclass, add/get/list/remove + v1->v2 migration.
"""
```

- [ ] **Step 6: Create `src/drop/runtime.py`**

Content:
```python
"""Volatile runtime state per page (pids, ports, tunnel_url). Phase 2.

PageRuntime dataclass, stored in ~/.drop/runtime.json. Provides
is_app_alive / is_proxy_alive / is_tunnel_alive via PID-probe.
"""
```

- [ ] **Step 7: Create `src/drop/lifecycle/__init__.py`**

Content:
```python
"""Lifecycle subsystem — atomic start/stop of app/server with proxy + tunnel."""
```

- [ ] **Step 8: Create `src/drop/lifecycle/process.py`**

Content:
```python
"""Single source of truth for subprocess spawning. Phase 3.

spawn_managed (start_new_session=True, log_file or DEVNULL — never
undrained PIPE), wait_alive, wait_port, kill_pg.
"""
```

- [ ] **Step 9: Create `src/drop/lifecycle/app.py`**

Content:
```python
"""Atomic app lifecycle: app + proxy + tunnel with rollback. Phase 6.

start_app / stop_app, side-door probe, --auth-insecure handling.
"""
```

- [ ] **Step 10: Create `src/drop/lifecycle/server.py`**

Content:
```python
"""Atomic drop static-server lifecycle. Phase 6.

start_server / stop_server (systemd-managed when available, PID
fallback otherwise).
"""
```

- [ ] **Step 11: Create `src/drop/lifecycle/tunnel.py`**

Content:
```python
"""Cloudflared subprocess management with --logfile (no PIPE). Phase 4.

start_tunnel(port, log_file) -> (url, pid), stop_tunnel(pid).
"""
```

- [ ] **Step 12: Create `src/drop/proxy.py`**

Content:
```python
"""Basic-auth reverse proxy for drop apps. Phase 5 (port from v1, no
substantial changes — already clean).

Stdlib http.server + urllib.request. Rejects WebSocket/Upgrade with 501.
Validates path is absolute (SSRF guard).
"""
```

- [ ] **Step 13: Create `src/drop/server.py`**

Content:
```python
"""Flask static-page server with cookie-form auth. Phase 8.

Routes: / (index), /p/<page_id>/[filepath]. Polish vs v1:
name in index (not page_id), HTML escape on error rendering, unified URL gen.
"""
```

- [ ] **Step 14: Create `src/drop/auth.py`**

Content:
```python
"""Shared auth utilities. Phase 1.

parse_basic_auth header, generate_password/hash_password/verify_password,
rate_limit dict + helpers.
"""
```

- [ ] **Step 15: Create `src/drop/manifest.py`**

Content:
```python
"""Manifest matching + safe_path (extracted from v1 utils). Phase 1.

load_manifest, matches_manifest, safe_path, is_env_file.
"""
```

- [ ] **Step 16: Create `src/drop/utils.py`**

Content:
```python
"""Pure helpers — IP detection, port allocation, has_systemd, find_cloudflared. Phase 1.

allocate_free_port, wait_for_port, get_external_ip, get_local_ip,
detect_ip, has_systemd, find_cloudflared, is_behind_nat,
generate_page_id.
"""
```

- [ ] **Step 17: Re-install in editable mode so the entry-point picks up the new layout**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pip install -e ".[dev]"
```

Expected: install succeeds, no errors about missing modules.

- [ ] **Step 18: Verify `drop` entry point loads (and prints its v2-stub message)**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/drop 2>&1
```

Expected output (exact text from `cli.main`):
```
drop v2 is under development on the `v2` branch. Use the `main` branch for a working CLI.
```
Exit code: 1.

- [ ] **Step 19: Verify `pytest --collect-only` still succeeds with the new layout**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest --collect-only
```

Expected: collects the 4 tests from `tests/unit/test_harness.py`. No collection errors.

- [ ] **Step 20: Verify all skeleton modules import cleanly**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/python3 -c "
import drop
import drop.cli
import drop.config
import drop.storage
import drop.runtime
import drop.lifecycle
import drop.lifecycle.process
import drop.lifecycle.app
import drop.lifecycle.server
import drop.lifecycle.tunnel
import drop.proxy
import drop.server
import drop.auth
import drop.manifest
import drop.utils
print('all skeleton imports ok')
"
```

Expected output: `all skeleton imports ok`

- [ ] **Step 21: Commit (wipe + skeleton in one logical commit)**

```bash
git add src/
git commit -m "feat(v2): wipe v1 src/drop and create v2 skeleton

Replaces the entire v1 source with empty stub modules matching the v2
spec layout (config, storage, runtime, lifecycle/, proxy, server, auth,
manifest, utils). All modules import cleanly; CLI entry-point prints a
stub message. Real code lands in Phases 1-10."
```

---

## Task 7: GitHub Actions CI workflow for the `v2` branch

**Files:**
- Create: `.github/workflows/tests.yml`

- [ ] **Step 1: Create the workflow directory**

```bash
cd /home/superbereza/dev/agent-instant-drop
mkdir -p .github/workflows
```

- [ ] **Step 2: Write the workflow**

Create `.github/workflows/tests.yml` with this exact content:

```yaml
name: tests
on:
  push:
    branches: [v2]
  pull_request:
    branches: [v2, main]

jobs:
  pytest:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - name: Install
        run: pip install -e ".[dev]"
      - name: Run pytest
        run: pytest
```

(Branch scoping: we run on every push to `v2` and on PRs targeting either `v2` or `main`. Once Phase 11 cuts v2 over to `main`, this file gets extended to also push-trigger on `main`.)

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/tests.yml
git commit -m "ci(v2): pytest on push to v2 and on PRs"
```

---

## Task 8: Push the v2 branch and confirm Phase 0 ends green

**Files:** none modified — verification + push only.

- [ ] **Step 1: Re-run the full test suite locally**

```bash
cd /home/superbereza/dev/agent-instant-drop
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest
```

Expected (last lines):
```
============ 4 passed in 0.XXs ============
```
Exit code 0.

- [ ] **Step 2: Verify git is clean and on the v2 branch**

```bash
git status
git branch --show-current
```

Expected:
- `git status` → "nothing to commit, working tree clean"
- branch → `v2`

- [ ] **Step 3: Push v2 to origin**

```bash
git push origin v2
```

Expected: push succeeds. GitHub Actions kicks off the `tests` workflow for the `v2` branch.

- [ ] **Step 4: Verify CI passed**

After ~1 minute:
```bash
gh run list --branch v2 --limit 1
```

Expected: status `completed`, conclusion `success`. If the run fails, inspect with `gh run view --log-failed <run-id>` and fix locally before continuing to Phase 1.

---

## Phase 0 Self-Review

After completing all 8 tasks, run this verification once:

```bash
cd /home/superbereza/dev/agent-instant-drop
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest --collect-only
/home/superbereza/dev/agent-instant-drop/.venv/bin/drop 2>&1
ls -la src/drop/
git log --oneline v2 ^main
```

Expected:
- `pytest` → 4 passed
- `pytest --collect-only` → no errors
- `drop` → "drop v2 is under development..." exit 1
- `ls src/drop/` → 14 files (`__init__`, `cli`, `config`, `storage`, `runtime`, `proxy`, `server`, `auth`, `manifest`, `utils`, `lifecycle/__init__`, `lifecycle/process`, `lifecycle/app`, `lifecycle/server`, `lifecycle/tunnel`)
- git log → 7 commits ahead of main

If any expectation fails, fix it before declaring Phase 0 done. Phase 1 starts only after Phase 0 is fully green.

---

## What Phase 0 Does NOT Include (deferred to later phases)

- Any real code in v2 modules — just stubs (Phases 1-8).
- Coverage thresholds in CI (added in Phase 11 cutover, when modules have meaningful tests).
- Test for the install.sh — that's part of Phase 10.
- Migration test for v1→v2 pages.json — that's part of Phase 2.
- Anything that requires real subprocesses (`subprocess.Popen`, real cloudflared) — those tests start in Phase 3.

## Pointers for the executor

- The repo convention is one logical commit per task (8 commits for this phase).
- All `pip` and `pytest` invocations use the venv at `/home/superbereza/dev/agent-instant-drop/.venv/bin/`. There is no `python` on PATH, only `python3`.
- If `pip install -e ".[dev]"` complains about a build backend, double-check `pyproject.toml` still has the `[build-system]` block from Task 1 — easy to lose in big edits.
- When in doubt, re-read the spec at `docs/2026-05-20-v2-greenfield-design.md` (architecture + non-goals).
