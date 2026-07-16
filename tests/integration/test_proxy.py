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
def proxy_running(registered_app, tmp_path):
    """Start drop.proxy subprocess against the registered app; yield proxy port."""
    import os
    page_id, pw, app_port = registered_app

    # Allocate a dedicated port for the proxy (separate from fake_upstream's port).
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
        _s.bind(("", 0))
        proxy_port = _s.getsockname()[1]

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


# Brute-force throttling (O4)

@pytest.mark.integration
def test_proxy_rate_limits_failed_auth(proxy_running):
    # Fresh proxy subprocess → fresh limiter. Wrong creds should start
    # returning 429 once the per-IP cap is hit.
    proxy_port, _, _ = proxy_running
    bad = _basic_auth_header("drop", "definitely-wrong")
    codes = [
        _http_get("127.0.0.1", proxy_port, "/", {"Authorization": bad})[0]
        for _ in range(8)
    ]
    assert 429 in codes, f"expected a 429 among {codes}"


@pytest.mark.integration
def test_proxy_no_creds_not_locked_out(proxy_running):
    # Requests with NO credentials are not counted, so they always get the
    # 401 challenge (never 429) — a browser's first hit must not be throttled.
    proxy_port, _, _ = proxy_running
    codes = [_http_get("127.0.0.1", proxy_port, "/")[0] for _ in range(8)]
    assert set(codes) == {401}, f"expected all 401, got {codes}"
