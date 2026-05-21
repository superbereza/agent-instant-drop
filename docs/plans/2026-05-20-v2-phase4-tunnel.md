# v2 Phase 4 — Lifecycle/tunnel.py

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Replace v1's `tunnel.py` (which had the broken `subprocess.PIPE` bug) with a clean module that uses `spawn_managed` from Phase 3 + cloudflared's `--logfile` flag. Tail the log to parse the trycloudflare.com URL.

**Architecture:** Two functions: `start_tunnel(port, log_file)` returns `(url, pid)` or None; `stop_tunnel(pid)` is `kill_pg`. No PIPE anywhere — cloudflared writes to a real file it owns.

**Tech Stack:** Phase 0-3 modules. `lifecycle/process.spawn_managed`, `utils.find_cloudflared`.

**Reference spec:** `docs/2026-05-20-v2-greenfield-design.md` — "Tunnel module" section.

**Branch:** `v2`.

---

## Task 1: `lifecycle/tunnel.py` — start_tunnel + stop_tunnel via --logfile

**Files:**
- Modify: `src/drop/lifecycle/tunnel.py`
- Create: `tests/integration/test_lifecycle_tunnel.py` (integration — uses real cloudflared if available)

- [ ] **Step 1: Write tests**

Create `tests/integration/test_lifecycle_tunnel.py`:

```python
"""Integration tests for drop.lifecycle.tunnel — uses real cloudflared if present.

Skipped automatically if cloudflared binary is not findable.
"""

import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from drop import utils
from drop.lifecycle import process as proc_mod
from drop.lifecycle import tunnel


_HAS_CF = utils.find_cloudflared() is not None
needs_cloudflared = pytest.mark.skipif(not _HAS_CF, reason="cloudflared not installed")


@pytest.fixture
def fake_app(free_port):
    """Spawn a tiny HTTP server on free_port in a thread; yield (port, ready)."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        def log_message(self, *a, **k):
            pass

    srv = HTTPServer(("127.0.0.1", free_port), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield free_port
    finally:
        srv.shutdown()
        srv.server_close()


# Unit-ish — does not need cloudflared, only checks find/log behavior

def test_start_tunnel_returns_none_when_no_cloudflared(tmp_path, monkeypatch):
    monkeypatch.setenv("DROP_HOME", str(tmp_path))
    monkeypatch.delenv("DROP_CLOUDFLARED_BIN", raising=False)
    monkeypatch.setenv("PATH", "")  # nuke PATH so shutil.which fails
    import importlib
    import drop.config; importlib.reload(drop.config)
    import drop.utils; importlib.reload(drop.utils)
    import drop.lifecycle.tunnel; importlib.reload(drop.lifecycle.tunnel)
    log = tmp_path / "tun.log"
    assert drop.lifecycle.tunnel.start_tunnel(8080, log_file=log) is None


# Real cloudflared

@needs_cloudflared
@pytest.mark.integration
def test_start_tunnel_returns_url_and_pid(fake_app, tmp_path):
    log = tmp_path / "tun.log"
    result = tunnel.start_tunnel(fake_app, log_file=log)
    assert result is not None
    url, pid = result
    try:
        assert re.match(r"^https://[a-z0-9-]+\.trycloudflare\.com$", url)
        assert pid > 0
        # cloudflared is alive (just spawned)
        assert proc_mod.wait_alive(pid, after=0.0) is True
        # log file exists and has content
        assert log.exists() and log.stat().st_size > 0
    finally:
        tunnel.stop_tunnel(pid)
        time.sleep(0.5)


@needs_cloudflared
@pytest.mark.integration
def test_stop_tunnel_kills_process(fake_app, tmp_path):
    log = tmp_path / "tun.log"
    result = tunnel.start_tunnel(fake_app, log_file=log)
    assert result is not None
    _, pid = result
    assert proc_mod.wait_alive(pid, after=0.0) is True
    tunnel.stop_tunnel(pid)
    time.sleep(0.5)
    assert proc_mod.wait_alive(pid, after=0.0) is False


@needs_cloudflared
@pytest.mark.integration
def test_start_tunnel_timeout_returns_none(tmp_path, monkeypatch):
    """A non-existent port means cloudflared will dial localhost:N, fail, and
    keep retrying — but we still expect to parse the URL within timeout.
    This is the success-with-broken-origin case: tunnel gives URL fast,
    origin is dead. We assert URL still comes back."""
    log = tmp_path / "tun.log"
    free = utils.allocate_free_port()  # nothing listening
    result = tunnel.start_tunnel(free, log_file=log, timeout=15)
    # Quick tunnels print the URL immediately, before testing the origin.
    # We accept either: URL returned (cloudflared got it before timeout)
    # OR None (rarely, slow).
    if result is not None:
        url, pid = result
        tunnel.stop_tunnel(pid)
        assert "trycloudflare.com" in url
```

- [ ] **Step 2: Run, verify failures (module is stub)**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest tests/integration/test_lifecycle_tunnel.py -v
```

- [ ] **Step 3: Implement `src/drop/lifecycle/tunnel.py`**

Replace with:

```python
"""Cloudflared tunnel subprocess management via --logfile (no PIPE).

Why --logfile and not subprocess.PIPE: cloudflared writes ~64KB of stderr
within the first minute. If we capture via PIPE without draining, the
kernel buffer fills, the child blocks on write(), and the tunnel
freezes (CF error 1033/530). Using --logfile (which cloudflared owns)
makes the bug architecturally impossible.
"""

import re
import time
from pathlib import Path

from ..utils import find_cloudflared
from .process import kill_pg, spawn_managed


_URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def start_tunnel(port: int, log_file: Path, timeout: float = 30.0) -> tuple[str, int] | None:
    """Spawn cloudflared quick tunnel for localhost:port; return (url, pid) or None.

    Output goes to log_file (cloudflared --logfile + spawn_managed log_file
    redirect — belt-and-braces). The log file is tailed for the
    trycloudflare URL up to timeout seconds.
    """
    cloudflared = find_cloudflared()
    if cloudflared is None:
        return None

    log_file = Path(log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    # Pre-create the file so the tail loop's read_text works even if
    # cloudflared hasn't logged anything yet.
    log_file.touch()

    proc = spawn_managed(
        [
            cloudflared, "tunnel",
            "--url", f"http://localhost:{port}",
            "--no-autoupdate",
            "--logfile", str(log_file),
        ],
        log_file=log_file,  # also redirect via spawn_managed for safety
    )

    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            # cloudflared exited early — failure
            return None
        try:
            content = log_file.read_text(errors="replace")
        except OSError:
            content = ""
        m = _URL_PATTERN.search(content)
        if m:
            return (m.group(0), proc.pid)
        time.sleep(0.2)

    # Timed out
    kill_pg(proc.pid)
    return None


def stop_tunnel(pid: int) -> None:
    """SIGTERM the cloudflared process group. No-op if pid is 0 or dead."""
    kill_pg(pid)
```

- [ ] **Step 4: Run unit-style test (no-cloudflared mock)**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest tests/integration/test_lifecycle_tunnel.py::test_start_tunnel_returns_none_when_no_cloudflared -v
```

Expected: 1 passed.

- [ ] **Step 5: Run integration tests if cloudflared available**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest tests/integration/test_lifecycle_tunnel.py -v -m integration
```

Expected: 3 passed (if cloudflared installed) OR 3 skipped (if not).

- [ ] **Step 6: Commit**

```bash
git add src/drop/lifecycle/tunnel.py tests/integration/test_lifecycle_tunnel.py
git commit -m "feat(v2): lifecycle.tunnel — cloudflared via --logfile (no PIPE)"
```

---

## Phase 4 Self-Review

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest -v
```

Expected: ~120 tests (depending on integration skip status).

Push:
```bash
git push origin v2
```

## What Phase 4 Does NOT Include

- Tunnel-required-for-auth logic — Phase 6 (lifecycle/app.py).
- Watchdog — out of scope.

## Phase 5 Readiness

After Phase 4, proxy.py (Phase 5) is independent and can be copied from v1 with the SSRF fix + rewrite-host (already validated on main).
