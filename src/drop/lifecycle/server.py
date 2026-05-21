"""Atomic drop static-server lifecycle.

Two paths:
  - systemd-managed (Linux with user systemd): uses ~/.drop/systemd.env
    for port, restarts via systemctl
  - PID fallback (macOS, no-systemd): spawn drop.server.run_server via
    spawn_managed, save pid to ~/.drop/server.pid
"""

import os
import signal
import subprocess
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
        env_file = Path(os.environ.get("DROP_HOME") or Path.home() / ".drop") / "systemd.env"
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text(f"DROP_PORT={port}\n")
        # Restart the unit (or start if not running) so it picks up new env
        try:
            subprocess.run(
                ["systemctl", "--user", "restart", "drop.service"],
                check=True, capture_output=True, timeout=5,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                FileNotFoundError) as e:
            return StartResult(error=f"systemctl failed: {e}",
                               hint="Run install.sh or use --no-systemd fallback.")
        # Wait for port
        if not utils.wait_for_port("127.0.0.1", port, timeout=10):
            return StartResult(error=f"server did not bind {port} after systemd restart")
        return StartResult(url=f"http://{host}:{port}/",
                            warnings=["systemd-managed (auto-restart on failure)"])

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
    """Stop the drop static server."""
    if has_systemd():
        try:
            subprocess.run(
                ["systemctl", "--user", "stop", "drop.service"],
                check=False, capture_output=True, timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return
    pid = _load_pid()
    if pid > 0:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    _clear_pid()
