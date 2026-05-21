# v2 Phase 6 — lifecycle/app.py + lifecycle/server.py

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Atomic lifecycle for both apps (`drop start <name>`) and the static-page server (`drop start`). All-or-nothing: any phase failure rolls back prior phases in reverse order. After this phase, `cli.py` (Phase 7) only needs to call `start_app(page)` and pretty-print the result.

**Architecture:** Two modules — `lifecycle/app.py` orchestrates app+proxy+tunnel atomically; `lifecycle/server.py` does the simpler drop static server (systemd or PID fallback). Shared `StartResult` dataclass.

**Tech Stack:** Phase 0-5 modules. `lifecycle.process.spawn_managed`, `lifecycle.tunnel.start_tunnel`, `storage.get_page`, `runtime.save_runtime`, `utils.allocate_free_port`, `utils.wait_for_port`, `utils.is_behind_nat`, `utils.detect_ip`, `utils.find_cloudflared`, `config.LOGS_DIR`.

**Reference spec:** `docs/2026-05-20-v2-greenfield-design.md` — "Lifecycle API" + "Atomic lifecycle pseudo-code" + "Side-door enforcement".

**Branch:** `v2`.

---

## Task 1: `lifecycle/app.py` — atomic app start/stop with side-door + tunnel-required

**Files:**
- Modify: `src/drop/lifecycle/app.py`
- Create: `tests/integration/test_lifecycle_app.py`

- [ ] **Step 1: Write tests**

Create `tests/integration/test_lifecycle_app.py`:

```python
"""Integration tests for drop.lifecycle.app — atomic start/stop with rollback."""

import os
import socket
import sys
import time
from pathlib import Path

import pytest

from drop import storage, runtime, utils
from drop.lifecycle import app, process


# --- Helpers ---

def _register_app(name, port, *, auth=False, rewrite_host=False, allow_side_door=False,
                   run_cmd=None):
    page_id = utils.generate_page_id()
    run = run_cmd or f"{sys.executable} -m http.server {port} --bind 127.0.0.1"
    page = storage.Page(
        page_id=page_id,
        source=Path("/tmp"),
        type="app",
        name=name,
        run_cmd=run,
        port=port,
        auth=(storage.AuthConfig(scheme="basic", user="drop",
                                  password_hash="sha256:" +
                                  __import__("hashlib").sha256(b"pw").hexdigest()))
              if auth else None,
        rewrite_host=rewrite_host,
        allow_side_door=allow_side_door,
    )
    storage.add_page(page)
    return page


# --- Public app (no auth, no tunnel) ---

@pytest.mark.integration
def test_start_app_public_no_tunnel(drop_home, free_port):
    page = _register_app("appA", free_port)
    result = app.start_app(page, auth_insecure=False, no_tunnel=True)
    try:
        assert result.error is None
        assert result.url == f"http://127.0.0.1:{free_port}/"
        rt = runtime.get_runtime(page.page_id)
        assert rt.app_pid > 0
        assert rt.proxy_pid == 0
        assert rt.tunnel_pid == 0
    finally:
        app.stop_app(page)


@pytest.mark.integration
def test_start_app_app_fails_to_bind(drop_home, free_port):
    """App that exits immediately should be detected; runtime cleared."""
    page = _register_app("appBoom", free_port, run_cmd=f"{sys.executable} -c 'pass'")
    result = app.start_app(page, auth_insecure=False, no_tunnel=True)
    assert result.error is not None
    assert "bind" in result.error.lower() or "start" in result.error.lower()
    rt = runtime.get_runtime(page.page_id)
    assert rt.app_pid == 0


@pytest.mark.integration
def test_stop_app_idempotent(drop_home, free_port):
    page = _register_app("appC", free_port)
    app.start_app(page, auth_insecure=False, no_tunnel=True)
    app.stop_app(page)
    # second stop — no error
    app.stop_app(page)
    rt = runtime.get_runtime(page.page_id)
    assert rt.app_pid == 0


# --- Auth app: --auth-insecure (cleartext, no tunnel) ---

@pytest.mark.integration
def test_start_app_with_auth_insecure(drop_home, free_port):
    page = _register_app("appD", free_port, auth=True)
    result = app.start_app(page, auth_insecure=True, no_tunnel=True)
    try:
        assert result.error is None
        assert "cleartext" in " ".join(result.warnings).lower()
        rt = runtime.get_runtime(page.page_id)
        assert rt.proxy_pid > 0
        assert rt.proxy_port > 0
    finally:
        app.stop_app(page)


@pytest.mark.integration
def test_start_app_auth_no_tunnel_no_insecure_refused(drop_home, free_port):
    """--no-tunnel + --auth without --auth-insecure → refuse + rollback."""
    page = _register_app("appE", free_port, auth=True)
    result = app.start_app(page, auth_insecure=False, no_tunnel=True)
    assert result.error is not None
    rt = runtime.get_runtime(page.page_id)
    # All processes rolled back
    assert rt.app_pid == 0
    assert rt.proxy_pid == 0
    assert rt.tunnel_pid == 0


# --- Side-door enforcement ---

@pytest.mark.integration
def test_side_door_refuse(drop_home, free_port):
    """App binds 0.0.0.0 + auth + not --allow-side-door → refuse on probe."""
    # Bind on 0.0.0.0 so detect_ip can probe it
    page = _register_app(
        "appF", free_port, auth=True,
        run_cmd=f"{sys.executable} -m http.server {free_port}",  # default = 0.0.0.0
    )
    # Force detect_ip to return a non-loopback addr the test can actually
    # probe (use local LAN IP from get_local_ip)
    import drop.utils as u
    orig = u.detect_ip
    u.detect_ip = lambda host=None: u.get_local_ip()
    try:
        result = app.start_app(page, auth_insecure=True, no_tunnel=True)
        # If get_local_ip is 127.0.0.1 (loopback-only host), test is moot.
        if u.get_local_ip() != "127.0.0.1":
            assert result.error is not None
            assert "side" in result.error.lower() or "0.0.0.0" in result.error
            rt = runtime.get_runtime(page.page_id)
            assert rt.app_pid == 0
        else:
            # Loopback-only env: side-door probe is automatically skipped
            try:
                assert result.error is None
            finally:
                app.stop_app(page)
    finally:
        u.detect_ip = orig
        # extra cleanup just in case
        app.stop_app(page)


@pytest.mark.integration
def test_side_door_allow_override(drop_home, free_port):
    """allow_side_door=True bypasses the probe."""
    page = _register_app(
        "appG", free_port, auth=True, allow_side_door=True,
        run_cmd=f"{sys.executable} -m http.server {free_port}",
    )
    result = app.start_app(page, auth_insecure=True, no_tunnel=True)
    try:
        # No side-door error — allow_side_door wins
        assert result.error is None
    finally:
        app.stop_app(page)


# --- Idempotent already-running detection ---

@pytest.mark.integration
def test_start_app_already_running(drop_home, free_port):
    page = _register_app("appH", free_port)
    app.start_app(page, auth_insecure=False, no_tunnel=True)
    try:
        # Starting again returns OK with same url
        result = app.start_app(page, auth_insecure=False, no_tunnel=True)
        assert result.error is None
        assert "already" in " ".join(result.warnings).lower() or result.url.endswith(f":{free_port}/")
    finally:
        app.stop_app(page)
```

- [ ] **Step 2: Run tests, verify failures**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest tests/integration/test_lifecycle_app.py -v -m integration
```

- [ ] **Step 3: Implement `src/drop/lifecycle/app.py`**

Replace with:

```python
"""Atomic app lifecycle.

start_app:
  1. If runtime says already running → idempotent return
  2. Spawn app via shell run_cmd, log to ~/.drop/logs/<id>.app.log
  3. Wait for app port to bind (5s)
  4. If auth + not allow_side_door: probe external IP for side-door, rollback if open
  5. If auth: spawn proxy on auto-allocated port, log to <id>.proxy.log
  6. Decide tunnel: --auth implies tunnel unless --auth-insecure;
     non-auth uses NAT-detect heuristic. --no-tunnel overrides.
  7. If tunnel needed but no cloudflared / failed: rollback (kill proxy+app)
  8. On success: save_runtime, build StartResult with url + creds + warnings

stop_app:
  Tunnel → proxy → app → clear_runtime. Idempotent.
"""

from dataclasses import dataclass, field
import socket
import sys
from pathlib import Path

from .. import config, runtime, storage, utils
from . import process as proc, tunnel as tunnel_mod


@dataclass
class StartResult:
    url: str = ""
    creds: tuple[str, str] | None = None  # (user, plaintext password) — for one-time print
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    hint: str | None = None


_SIDE_DOOR_WARNING = (
    "--auth protects tunnel only. If your app binds 0.0.0.0 on a public IP, "
    "app port is still reachable bypassing auth. Use --host 127.0.0.1 in --run."
)

_CLEARTEXT_WARNING = (
    "CLEARTEXT: basic auth credentials transmitted in base64 over plain HTTP. "
    "Anyone on the network path can read them. Use only on trusted LAN."
)


def _logs_dir() -> Path:
    """LOGS_DIR honoring current DROP_HOME env (re-read for test isolation)."""
    home_env = __import__("os").environ.get("DROP_HOME")
    base = Path(home_env) if home_env else Path.home() / ".drop"
    log_dir = base / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def _probe_side_door(host: str, port: int, timeout: float = 1.0) -> bool:
    """Return True if (host, port) accepts a TCP connection from outside."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _already_running(page: storage.Page) -> StartResult | None:
    """If runtime claims this app is alive, return idempotent StartResult."""
    rt = runtime.get_runtime(page.page_id)
    if rt.app_pid > 0 and rt.is_app_alive():
        url = rt.tunnel_url or f"http://127.0.0.1:{page.port}/"
        return StartResult(url=url, warnings=["app already running"])
    return None


def start_app(page: storage.Page, *, auth_insecure: bool, no_tunnel: bool) -> StartResult:
    """Atomic start: app → proxy → tunnel. Any phase failure rolls back."""
    # Idempotent: already running?
    existing = _already_running(page)
    if existing is not None:
        return existing

    rt = runtime.PageRuntime(page_id=page.page_id)
    warnings: list[str] = []

    # Phase 1: spawn app
    app_log = _logs_dir() / f"{page.page_id}.app.log"
    try:
        app_proc = proc.spawn_managed(page.run_cmd, shell=True, log_file=app_log)
    except Exception as e:
        return StartResult(error=f"failed to spawn app: {e}", hint=f"see {app_log}")

    # Wait for app to actually bind its port
    if not utils.wait_for_port("127.0.0.1", page.port, timeout=5):
        proc.kill_pg(app_proc.pid)
        return StartResult(
            error=f"app did not bind 127.0.0.1:{page.port} within 5s",
            hint=f"see {app_log}",
        )
    rt.app_pid = app_proc.pid
    runtime.save_runtime(rt)

    # Side-door enforcement (only meaningful with auth)
    if page.auth and not page.allow_side_door:
        external_host = utils.detect_ip()
        if external_host and external_host != "127.0.0.1":
            if _probe_side_door(external_host, page.port):
                proc.kill_pg(app_proc.pid)
                runtime.clear_runtime(page.page_id)
                return StartResult(
                    error=f"app listens on 0.0.0.0:{page.port}; --auth would not protect "
                          f"the direct path (side-door)",
                    hint="Bind app to 127.0.0.1, or `drop add ... --allow-side-door` "
                         "to override.",
                )

    if page.auth:
        warnings.append(_SIDE_DOOR_WARNING)

    # Phase 2: spawn proxy (if auth)
    proxy_port = 0
    proxy_proc = None
    if page.auth is not None:
        bind = "0.0.0.0" if auth_insecure else "127.0.0.1"
        proxy_port = utils.allocate_free_port()
        proxy_log = _logs_dir() / f"{page.page_id}.proxy.log"
        proxy_cmd = [
            sys.executable, "-m", "drop.proxy",
            "--page-id", page.page_id,
            "--proxy-port", str(proxy_port),
            "--app-port", str(page.port),
            "--bind", bind,
        ]
        proxy_proc = proc.spawn_managed(proxy_cmd, log_file=proxy_log)
        probe_host = "127.0.0.1" if bind == "0.0.0.0" else bind
        if not utils.wait_for_port(probe_host, proxy_port, timeout=5):
            proc.kill_pg(proxy_proc.pid)
            proc.kill_pg(app_proc.pid)
            runtime.clear_runtime(page.page_id)
            return StartResult(
                error="proxy failed to start",
                hint=f"see {proxy_log}",
            )
        rt.proxy_pid = proxy_proc.pid
        rt.proxy_port = proxy_port
        runtime.save_runtime(rt)

    target_port = proxy_port if page.auth else page.port

    # Phase 3: tunnel
    want_tunnel = (not no_tunnel) and (
        (page.auth is not None and not auth_insecure) or utils.is_behind_nat()
    )

    if want_tunnel:
        cloudflared = utils.find_cloudflared()
        if cloudflared is None:
            # Roll back if auth required tunnel
            if page.auth is not None:
                if proxy_proc:
                    proc.kill_pg(proxy_proc.pid)
                proc.kill_pg(app_proc.pid)
                runtime.clear_runtime(page.page_id)
                return StartResult(
                    error="cloudflared not installed",
                    hint="Install via ./install.sh or pass --auth-insecure to allow cleartext.",
                )
            # Non-auth + NAT but no cloudflared: degrade gracefully
            warnings.append("behind NAT but cloudflared not found")
            url = f"http://{utils.detect_ip()}:{target_port}/"
        else:
            tunnel_log = _logs_dir() / f"{page.page_id}.tunnel.log"
            result = tunnel_mod.start_tunnel(target_port, log_file=tunnel_log)
            if result is None:
                if page.auth is not None:
                    if proxy_proc:
                        proc.kill_pg(proxy_proc.pid)
                    proc.kill_pg(app_proc.pid)
                    runtime.clear_runtime(page.page_id)
                    return StartResult(
                        error="tunnel failed to start",
                        hint=f"see {tunnel_log}; retry or pass --auth-insecure",
                    )
                warnings.append("tunnel failed; falling back to direct URL")
                url = f"http://{utils.detect_ip()}:{target_port}/"
            else:
                tunnel_url, tunnel_pid = result
                rt.tunnel_url = tunnel_url
                rt.tunnel_pid = tunnel_pid
                runtime.save_runtime(rt)
                url = tunnel_url
    else:
        # No tunnel wanted
        if page.auth is not None and no_tunnel and not auth_insecure:
            # --no-tunnel + --auth without --auth-insecure → refuse
            if proxy_proc:
                proc.kill_pg(proxy_proc.pid)
            proc.kill_pg(app_proc.pid)
            runtime.clear_runtime(page.page_id)
            return StartResult(
                error="--no-tunnel conflicts with --auth (would expose cleartext credentials)",
                hint="Pass --auth-insecure to confirm cleartext over HTTP.",
            )
        if auth_insecure and page.auth is not None:
            warnings.append(_CLEARTEXT_WARNING)
        # Bind URL: 127.0.0.1 for local, otherwise detect_ip
        host = utils.detect_ip() if (auth_insecure or proxy_port == 0) else "127.0.0.1"
        url = f"http://{host}:{target_port}/"

    creds = (page.auth.user, "<hidden>") if page.auth else None
    return StartResult(url=url, creds=creds, warnings=warnings)


def stop_app(page: storage.Page) -> None:
    """Stop tunnel → proxy → app. Idempotent. Clears runtime at end."""
    rt = runtime.get_runtime(page.page_id)
    if rt.tunnel_pid > 0:
        tunnel_mod.stop_tunnel(rt.tunnel_pid)
    if rt.proxy_pid > 0:
        proc.kill_pg(rt.proxy_pid)
    if rt.app_pid > 0:
        proc.kill_pg(rt.app_pid)
    runtime.clear_runtime(page.page_id)
```

- [ ] **Step 4: Run tests**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest tests/integration/test_lifecycle_app.py -v -m integration
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/drop/lifecycle/app.py tests/integration/test_lifecycle_app.py
git commit -m "feat(v2): lifecycle.app — atomic app start/stop with side-door + tunnel-required"
```

---

## Task 2: `lifecycle/server.py` — start/stop drop static server (systemd or PID)

**Files:**
- Modify: `src/drop/lifecycle/server.py`
- Create: `tests/integration/test_lifecycle_server.py`

- [ ] **Step 1: Write tests**

Create `tests/integration/test_lifecycle_server.py`:

```python
"""Integration tests for drop.lifecycle.server — PID-fallback path.

The systemd path requires a real systemd user instance; we only test
the PID-based fallback here (which is also what runs on macOS / CI
containers without systemd).
"""

import os
import sys
import time
from pathlib import Path

import pytest

from drop import config, utils
from drop.lifecycle import server


@pytest.mark.integration
def test_start_server_pid_fallback(drop_home, free_port, monkeypatch):
    # Force the PID-fallback branch even on Linux with systemd
    monkeypatch.setattr("drop.lifecycle.server.has_systemd", lambda: False)
    result = server.start_server(port=free_port, host="127.0.0.1", no_tunnel=True)
    try:
        assert result.error is None
        # PID file should exist now
        # Use config.SERVER_PID_FILE — recompute since DROP_HOME changed
        pid_file = drop_home / "server.pid"
        assert pid_file.exists()
        pid = int(pid_file.read_text().strip())
        assert pid > 0
        # Server is actually listening
        assert utils.wait_for_port("127.0.0.1", free_port, timeout=5) is True
    finally:
        server.stop_server()


@pytest.mark.integration
def test_stop_server_clears_pid(drop_home, free_port, monkeypatch):
    monkeypatch.setattr("drop.lifecycle.server.has_systemd", lambda: False)
    server.start_server(port=free_port, host="127.0.0.1", no_tunnel=True)
    server.stop_server()
    time.sleep(0.3)
    pid_file = drop_home / "server.pid"
    # PID file removed on stop
    assert not pid_file.exists()
```

- [ ] **Step 2: Run tests, verify failures**

- [ ] **Step 3: Implement `src/drop/lifecycle/server.py`**

Replace with:

```python
"""Atomic drop static-server lifecycle.

Two paths:
  - systemd-managed (Linux with user systemd): uses ~/.drop/systemd.env
    for port, restarts via systemctl
  - PID fallback (macOS, no-systemd): spawn drop.server.run_server via
    spawn_managed, save pid to ~/.drop/server.pid
"""

import os
import signal
import sys
from pathlib import Path

from .. import config, utils
from ..utils import has_systemd, is_behind_nat, find_cloudflared
from . import process as proc, tunnel as tunnel_mod
from .app import StartResult


def _pid_file() -> Path:
    home_env = os.environ.get("DROP_HOME")
    base = Path(home_env) if home_env else Path.home() / ".drop"
    base.mkdir(parents=True, exist_ok=True)
    return base / "server.pid"


def _save_pid(pid: int) -> None:
    _pid_file().write_text(str(pid))


def _load_pid() -> int:
    p = _pid_file()
    if not p.exists():
        return 0
    try:
        return int(p.read_text().strip())
    except (OSError, ValueError):
        return 0


def _clear_pid() -> None:
    p = _pid_file()
    if p.exists():
        p.unlink()


def start_server(*, port: int, host: str, no_tunnel: bool) -> StartResult:
    """Start the drop static server. Returns StartResult."""
    # Already running?
    existing_pid = _load_pid()
    if existing_pid > 0:
        try:
            os.kill(existing_pid, 0)
            return StartResult(
                url=f"http://{host}:{port}/",
                warnings=["server already running"],
            )
        except OSError:
            _clear_pid()

    if has_systemd():
        # systemd path: write env file, restart unit
        env_file = Path(os.environ.get("DROP_HOME") or Path.home() / ".drop") / "systemd.env"
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text(f"DROP_PORT={port}\n")
        # We don't actually shell out to systemctl in tests — only PID path
        # is exercised. systemctl call left as a TODO for Phase 10 when
        # install.sh is updated.
        return StartResult(
            error="systemd path not yet wired (Phase 10)",
            hint="Run with --no-systemd or use the PID fallback.",
        )

    # PID fallback path
    log_file = (Path(os.environ.get("DROP_HOME") or Path.home() / ".drop")
                / "logs" / "server.log")
    log_file.parent.mkdir(parents=True, exist_ok=True)
    # Spawn drop.server.run_server in a subprocess
    cmd = [
        sys.executable, "-c",
        f"from drop.server import run_server; run_server(port={port})",
    ]
    p = proc.spawn_managed(cmd, log_file=log_file)

    if not utils.wait_for_port("127.0.0.1", port, timeout=5):
        proc.kill_pg(p.pid)
        return StartResult(
            error=f"server did not bind 127.0.0.1:{port} within 5s",
            hint=f"see {log_file}",
        )
    _save_pid(p.pid)

    # Tunnel (NAT detection only for the static server)
    if not no_tunnel and is_behind_nat() and find_cloudflared():
        tunnel_log = (Path(os.environ.get("DROP_HOME") or Path.home() / ".drop")
                       / "logs" / "server.tunnel.log")
        result = tunnel_mod.start_tunnel(port, log_file=tunnel_log)
        if result:
            url, _pid = result
            return StartResult(url=url, warnings=["tunneled via cloudflared"])

    return StartResult(url=f"http://{host}:{port}/")


def stop_server() -> None:
    """Stop the drop static server (PID fallback path)."""
    pid = _load_pid()
    if pid > 0:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    _clear_pid()
```

NOTE: server.py (Flask) is a stub — these tests will fail to actually serve because `drop.server.run_server` doesn't exist yet. That's Phase 8. For Phase 6 we test:
- The orchestration logic (spawn_managed, wait_for_port, save_pid)
- The fallback structure

To make the test actually pass without server.py, we provide a stub `run_server` in this commit OR mark the test as expecting failure until Phase 8. We choose: provide a MINIMAL run_server stub in src/drop/server.py that imports Flask and runs an empty app. Phase 8 will replace it with the full implementation.

- [ ] **Step 4: Implement minimal `src/drop/server.py` stub for Phase 8**

Replace `src/drop/server.py` with (just enough for lifecycle tests to pass):

```python
"""Flask static-page server with cookie-form auth (full impl: Phase 8).

This minimal stub starts a Flask app that returns a placeholder so
Phase 6 lifecycle tests can verify the spawn+bind+stop flow. Phase 8
replaces with real routes (index, /p/<id>/[file], auth).
"""

from flask import Flask


def run_server(port: int = 8080, host: str = "0.0.0.0") -> None:
    app = Flask(__name__)

    @app.route("/")
    def _index():
        return "drop v2 server stub (Phase 8 not yet implemented)"

    app.run(host=host, port=port, debug=False, use_reloader=False)
```

- [ ] **Step 5: Run tests**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest tests/integration/test_lifecycle_server.py -v -m integration
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add src/drop/lifecycle/server.py src/drop/server.py tests/integration/test_lifecycle_server.py
git commit -m "feat(v2): lifecycle.server — PID-fallback start/stop + minimal server stub"
```

---

## Phase 6 Self-Review

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest -v
```

Push:
```bash
git push origin v2
```

## What Phase 6 Does NOT Include

- systemd path wiring (deferred to Phase 10 with install.sh updates)
- Full server.py implementation (Phase 8)
- CLI dispatch (Phase 7)

## Phase 7 Readiness

After Phase 6, CLI can be thin:
- `drop add <path> [--auth] [--public] [--rewrite-host]` → `storage.add_page(Page(...))`
- `drop start <name>` → `app.start_app(page, ...)` then print result
- `drop stop <name>` → `app.stop_app(page)`
- `drop list` → iterate `storage.list_pages()`, render with `runtime.get_runtime()` state
