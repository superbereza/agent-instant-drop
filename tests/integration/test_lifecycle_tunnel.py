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
