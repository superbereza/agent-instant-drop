# v2 Phase 5 — proxy.py

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Port the v1 basic-auth reverse proxy with all its bug fixes (SSRF guard, redirect pass-through, dup-header fix) AND add `--rewrite-host` body rewriting natively. Use the v2 `auth.parse_basic_auth` + `auth.verify_password` instead of inlining.

**Architecture:** Stdlib `http.server.ThreadingHTTPServer` + `urllib.request`. Single `ProxyHandler` class with all methods routed through `_proxy(method)`. Module config via class attributes set in `main()`.

**Tech Stack:** Phase 0-4 modules. `auth.parse_basic_auth`, `auth.verify_password`, `auth.AUTH_REALM` (via `config.AUTH_REALM`), `storage.get_page`.

**Reference spec:** `docs/2026-05-20-v2-greenfield-design.md` — Proxy section + "`--rewrite-host` flag" section.

**Branch:** `v2`.

---

## Task 1: `proxy.py` — basic-auth reverse proxy + SSRF guard + rewrite-host

**Files:**
- Modify: `src/drop/proxy.py`
- Create: `tests/integration/test_proxy.py`

- [ ] **Step 1: Write tests**

Create `tests/integration/test_proxy.py`:

```python
"""Integration tests for drop.proxy — boots a fake upstream + drop.proxy as
subprocess + curls through it."""

import base64
import json
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from drop import storage, utils


@pytest.fixture
def fake_upstream(free_port):
    """Tiny HTTP server with configurable handlers per path."""
    handlers = {}
    port = free_port

    class H(BaseHTTPRequestHandler):
        def _serve(self):
            handler = handlers.get(self.path)
            if handler is None:
                self.send_response(404); self.end_headers(); return
            status, headers, body = handler(self)
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            if body:
                self.wfile.write(body)

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _serve
        def log_message(self, *a, **k): pass

    srv = HTTPServer(("127.0.0.1", port), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield port, handlers
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def registered_app(drop_home, fake_upstream):
    """Register an app with auth in storage, return (page_id, password, app_port, proxy_port)."""
    port, _handlers = fake_upstream
    pw = "test-pass"
    page = storage.Page(
        page_id="proxysmoke",
        source=Path("/tmp/x"),
        type="app",
        name="proxysmoke",
        run_cmd="",
        port=port,
        auth=storage.AuthConfig(scheme="basic", user="drop", password_hash="sha256:" +
                                __import__("hashlib").sha256(pw.encode()).hexdigest()),
    )
    storage.add_page(page)
    return page.page_id, pw, port


@pytest.fixture
def proxy_running(registered_app, free_port, tmp_path):
    """Start drop.proxy subprocess against the registered app; yield proxy port."""
    page_id, pw, app_port = registered_app
    proxy_port = free_port

    # Inherit DROP_HOME env so the subprocess sees the same storage
    env = {"DROP_HOME": str(Path.cwd().resolve()), **__import__("os").environ}
    # Override DROP_HOME to point at our fixture's drop_home
    import os
    env = dict(os.environ)
    proc = subprocess.Popen(
        [sys.executable, "-m", "drop.proxy",
         "--page-id", page_id,
         "--proxy-port", str(proxy_port),
         "--app-port", str(app_port),
         "--bind", "127.0.0.1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=env,
        start_new_session=True,
    )
    assert utils.wait_for_port("127.0.0.1", proxy_port, timeout=3)
    try:
        yield proxy_port, page_id, pw
    finally:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass
        proc.wait(timeout=2)


def _basic_auth_header(user: str, pw: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


def _http_get(host: str, port: int, path: str, headers: dict | None = None) -> tuple[int, dict, bytes]:
    """Hand-rolled HTTP GET to bypass urllib's path normalization."""
    headers = headers or {}
    s = socket.create_connection((host, port), timeout=5)
    s.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n".encode() +
              "".join(f"{k}: {v}\r\n" for k, v in headers.items()).encode() + b"\r\n")
    raw = b""
    while True:
        chunk = s.recv(4096)
        if not chunk: break
        raw += chunk
    s.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    status = int(lines[0].split(b" ")[1])
    resp_headers = {}
    for line in lines[1:]:
        k, _, v = line.partition(b": ")
        resp_headers[k.decode().lower()] = v.decode()
    return status, resp_headers, body


# Auth tests

@pytest.mark.integration
def test_no_auth_returns_401(proxy_running, fake_upstream):
    proxy_port, _, _ = proxy_running
    _, handlers = fake_upstream
    handlers["/"] = lambda h: (200, {"Content-Type": "text/plain"}, b"hi")
    status, _, _ = _http_get("127.0.0.1", proxy_port, "/")
    assert status == 401


@pytest.mark.integration
def test_wrong_auth_returns_401(proxy_running, fake_upstream):
    proxy_port, _, _ = proxy_running
    _, handlers = fake_upstream
    handlers["/"] = lambda h: (200, {"Content-Type": "text/plain"}, b"hi")
    status, _, _ = _http_get("127.0.0.1", proxy_port, "/",
                              headers={"Authorization": _basic_auth_header("drop", "wrong")})
    assert status == 401


@pytest.mark.integration
def test_correct_auth_returns_200(proxy_running, fake_upstream):
    proxy_port, _, pw = proxy_running
    _, handlers = fake_upstream
    handlers["/"] = lambda h: (200, {"Content-Type": "text/plain"}, b"hi")
    status, _, body = _http_get("127.0.0.1", proxy_port, "/",
                                 headers={"Authorization": _basic_auth_header("drop", pw)})
    assert status == 200
    assert body == b"hi"


@pytest.mark.integration
def test_realm_in_www_authenticate(proxy_running):
    proxy_port, _, _ = proxy_running
    status, headers, _ = _http_get("127.0.0.1", proxy_port, "/")
    assert status == 401
    assert "basic realm=" in headers.get("www-authenticate", "").lower()


# SSRF guard

@pytest.mark.integration
def test_ssrf_relative_path_rejected(proxy_running):
    """A path like @evil.com/foo should not allow upstream redirection."""
    proxy_port, _, pw = proxy_running
    status, _, body = _http_get("127.0.0.1", proxy_port, "@evil.com/foo",
                                 headers={"Authorization": _basic_auth_header("drop", pw)})
    assert status == 400
    assert b"absolute" in body.lower() or b"bad request" in body.lower()


# Upgrade rejection

@pytest.mark.integration
def test_websocket_upgrade_returns_501(proxy_running):
    proxy_port, _, pw = proxy_running
    status, _, body = _http_get("127.0.0.1", proxy_port, "/",
                                 headers={
                                     "Authorization": _basic_auth_header("drop", pw),
                                     "Connection": "Upgrade",
                                     "Upgrade": "websocket",
                                 })
    assert status == 501
    assert b"upgrade" in body.lower()


# Redirect pass-through

@pytest.mark.integration
def test_302_redirect_passes_through(proxy_running, fake_upstream):
    proxy_port, _, pw = proxy_running
    _, handlers = fake_upstream
    handlers["/r"] = lambda h: (302, {"Location": "https://example.com/x"}, b"")
    status, headers, _ = _http_get("127.0.0.1", proxy_port, "/r",
                                    headers={"Authorization": _basic_auth_header("drop", pw)})
    assert status == 302
    assert headers.get("location") == "https://example.com/x"
```

- [ ] **Step 2: Run, verify they fail (proxy module is stub)**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest tests/integration/test_proxy.py -v -m integration
```

- [ ] **Step 3: Implement `src/drop/proxy.py`**

Replace with:

```python
"""Basic-auth reverse proxy for drop apps.

V1 sync HTTP req/response only — WebSocket / Upgrade requests are
rejected with 501. Includes:
  - SSRF guard: path must start with "/"
  - Redirect pass-through (no following on the server side)
  - Optional --rewrite-host body rewrite for SPAs with hardcoded
    http://localhost:<port> in their JS bundle
"""

import argparse
import base64
import binascii
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import storage
from .auth import parse_basic_auth, verify_password
from .config import AUTH_REALM


_HOP_BY_HOP_RESPONSE = frozenset({
    "transfer-encoding", "connection", "server", "date",
})

# When we rewrite the body, we also strip the content-encoding (we forced
# identity upstream) and content-length (recomputed).
_HOP_BY_HOP_RESPONSE_REWRITE = _HOP_BY_HOP_RESPONSE | {"content-encoding", "content-length"}

_HOP_BY_HOP_REQUEST = frozenset({
    "host", "authorization", "content-length", "connection",
})

# Only rewrite text MIME types. JSON intentionally excluded.
_REWRITE_TYPES = ("text/html", "text/javascript", "application/javascript", "text/css")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Re-raise 30x as HTTPError so the proxy can forward Location to the client."""

    def http_error_301(self, req, fp, code, msg, headers):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)

    http_error_302 = http_error_301
    http_error_303 = http_error_301
    http_error_307 = http_error_301
    http_error_308 = http_error_301


_opener = urllib.request.build_opener(_NoRedirect)


class ProxyHandler(BaseHTTPRequestHandler):
    APP_PORT: int
    AUTH: dict  # {"scheme": "basic", "user": str, "password_hash": str}
    REWRITE_HOST: bool = False
    REWRITE_NEEDLE: bytes = b""

    def _check_auth(self) -> bool:
        creds = parse_basic_auth(self.headers.get("Authorization", ""))
        if creds is None:
            return False
        user, password = creds
        return user == self.AUTH["user"] and verify_password(password, self.AUTH["password_hash"])

    def _reject_upgrade(self) -> bool:
        if "upgrade" in (self.headers.get("Connection", "") or "").lower():
            self.send_response(501)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(
                b"WebSocket/Upgrade not supported by drop V1 proxy.\n"
            )
            return True
        return False

    def _request_origin(self) -> bytes:
        """Public origin (scheme://host) the client used. cloudflared injects
        X-Forwarded-*; fall back to Host header (assume https)."""
        proto = self.headers.get("X-Forwarded-Proto", "https")
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "localhost"
        return f"{proto}://{host}".encode("utf-8")

    def _proxy(self, method: str) -> None:
        if self._reject_upgrade():
            return
        # SSRF guard: a crafted line like "GET @evil.com/foo HTTP/1.1" sets
        # self.path = "@evil.com/foo"; urllib then parses the constructed
        # URL with "127.0.0.1:<port>" as userinfo and connects to evil.com.
        if not self.path.startswith("/"):
            self.send_response(400)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Bad request: path must be absolute.\n")
            return
        if not self._check_auth():
            self.send_response(401)
            self.send_header("WWW-Authenticate", f'Basic realm="{AUTH_REALM}"')
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Authentication required.\n")
            return

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length > 0 else None

        url = f"http://127.0.0.1:{self.APP_PORT}{self.path}"
        req = urllib.request.Request(url, data=body, method=method)
        for h, v in self.headers.items():
            if h.lower() not in _HOP_BY_HOP_REQUEST:
                req.add_header(h, v)
        if self.REWRITE_HOST:
            req.add_header("Accept-Encoding", "identity")

        try:
            with _opener.open(req, timeout=30) as resp:
                self._forward_response(resp.status, resp.headers, resp.read())
        except urllib.error.HTTPError as e:
            self._forward_response(e.code, e.headers, e.read())
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"proxy error: {e}\n".encode())

    def _forward_response(self, status: int, headers, body: bytes) -> None:
        rewrite = False
        if self.REWRITE_HOST and body:
            ctype = (headers.get("Content-Type") or "").lower()
            if any(ctype.startswith(t) for t in _REWRITE_TYPES) and self.REWRITE_NEEDLE in body:
                body = body.replace(self.REWRITE_NEEDLE, self._request_origin())
                rewrite = True

        self.send_response(status)
        skip = _HOP_BY_HOP_RESPONSE_REWRITE if rewrite else _HOP_BY_HOP_RESPONSE
        for h, v in headers.items():
            if h.lower() not in skip:
                self.send_header(h, v)
        if rewrite:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None: self._proxy("GET")
    def do_POST(self) -> None: self._proxy("POST")
    def do_PUT(self) -> None: self._proxy("PUT")
    def do_DELETE(self) -> None: self._proxy("DELETE")
    def do_PATCH(self) -> None: self._proxy("PATCH")
    def do_HEAD(self) -> None: self._proxy("HEAD")
    def do_OPTIONS(self) -> None: self._proxy("OPTIONS")

    def log_message(self, format: str, *args) -> None:
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page-id", required=True)
    ap.add_argument("--proxy-port", type=int, required=True)
    ap.add_argument("--app-port", type=int, required=True)
    ap.add_argument("--bind", default="127.0.0.1")
    args = ap.parse_args()

    page = storage.get_page(args.page_id)
    if page is None or page.auth is None:
        print(f"error: no auth config for page_id={args.page_id}", file=sys.stderr)
        sys.exit(1)

    ProxyHandler.APP_PORT = args.app_port
    ProxyHandler.AUTH = {
        "scheme": page.auth.scheme,
        "user": page.auth.user,
        "password_hash": page.auth.password_hash,
    }
    ProxyHandler.REWRITE_HOST = bool(getattr(page, "rewrite_host", False))
    ProxyHandler.REWRITE_NEEDLE = f"http://localhost:{args.app_port}".encode("utf-8")

    server = ThreadingHTTPServer((args.bind, args.proxy_port), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest tests/integration/test_proxy.py -v -m integration
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/drop/proxy.py tests/integration/test_proxy.py
git commit -m "feat(v2): proxy module — basic auth, SSRF guard, Upgrade reject, redirect passthrough, rewrite-host"
```

---

## Phase 5 Self-Review

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest -v
```

Expected: ~127 tests.

Push:
```bash
git push origin v2
```

## What Phase 5 Does NOT Include

- Rewrite-host body verification with real Content-Encoding gzip stripping happens in proxy integration but is not deeply tested (covered transitively by setting `Accept-Encoding: identity` upstream).

## Phase 6 Readiness

After Phase 5, Phase 6 (`lifecycle/app.py`) wires:
- `lifecycle/process.spawn_managed` (app + proxy)
- `lifecycle/tunnel.start_tunnel`
- `proxy.py` as subprocess module
- `storage.get_page` + `runtime.save_runtime` for state
