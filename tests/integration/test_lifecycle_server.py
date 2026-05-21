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
