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
