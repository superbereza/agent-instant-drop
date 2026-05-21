"""Tunnel management for drop."""

import json
import re
import subprocess
import threading
import time
from pathlib import Path

from .utils import find_cloudflared


# Tunnel state file for server
TUNNEL_FILE = Path.home() / ".drop" / "tunnel.json"

# Global watchdog state
_watchdog_thread: threading.Thread | None = None
_watchdog_stop = threading.Event()


def save_tunnel_state(url: str, pid: int) -> None:
    """Save server tunnel state."""
    TUNNEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    TUNNEL_FILE.write_text(json.dumps({"url": url, "pid": pid}))


def load_tunnel_state() -> dict | None:
    """Load server tunnel state."""
    if not TUNNEL_FILE.exists():
        return None
    try:
        return json.loads(TUNNEL_FILE.read_text())
    except Exception:
        return None


def clear_tunnel_state() -> None:
    """Clear server tunnel state."""
    if TUNNEL_FILE.exists():
        TUNNEL_FILE.unlink()


def start_tunnel(port: int) -> tuple[str, int] | None:
    """
    Start cloudflared tunnel for given port.
    Returns (url, pid) or None on failure.

    cloudflared's stdout+stderr are redirected to a log file inside ~/.drop/.
    Avoiding subprocess.PIPE matters for two reasons:
      1. While CLI runs, an undrained PIPE fills the ~64KB kernel buffer and
         blocks cloudflared's write() → tunnel freezes → CF error 1033/530.
      2. After CLI exits, the PIPE's read end closes → cloudflared's next
         write() raises SIGPIPE and kills the process.
    Writing to a file FD that cloudflared owns has neither problem; the file
    is also a useful log when diagnosing tunnel issues.
    """
    cloudflared = find_cloudflared()
    if not cloudflared:
        return None

    LOG_DIR = Path.home() / ".drop" / "logs"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"cloudflared-{port}-{int(time.time())}.log"
    log_fh = open(log_path, "w")

    # start_new_session=True detaches cloudflared from CLI's process group so
    # that SIGHUP on CLI exit does not kill the tunnel.
    proc = subprocess.Popen(
        [cloudflared, "tunnel", "--url", f"http://localhost:{port}", "--no-autoupdate"],
        stdout=log_fh,
        stderr=log_fh,
        start_new_session=True,
    )
    # Parent's FD is no longer needed — cloudflared has its own dup of the
    # underlying file. Closing here doesn't affect the child.
    log_fh.close()

    # Tail the log file for the trycloudflare.com URL.
    url = None
    start_time = time.time()
    timeout = 30
    pattern = re.compile(r'https://[a-z0-9-]+\.trycloudflare\.com')
    while time.time() - start_time < timeout:
        if proc.poll() is not None:
            return None
        try:
            content = log_path.read_text(errors="replace")
        except OSError:
            content = ""
        match = pattern.search(content)
        if match:
            url = match.group(0)
            break
        time.sleep(0.2)

    if not url:
        try:
            proc.terminate()
        except OSError:
            pass
        return None

    return (url, proc.pid)


def stop_tunnel(pid: int) -> None:
    """Stop tunnel by PID."""
    import os
    import signal

    if pid <= 0:
        return

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def _watchdog_loop(port: int, on_url_change: callable) -> None:
    """Watchdog loop that restarts tunnel if it dies."""
    import os

    while not _watchdog_stop.is_set():
        state = load_tunnel_state()
        if state:
            pid = state.get("pid", 0)
            if pid > 0:
                try:
                    os.kill(pid, 0)
                    # Process alive, sleep and check again
                    time.sleep(10)
                    continue
                except OSError:
                    # Process dead, restart tunnel
                    pass

        # Start new tunnel
        result = start_tunnel(port)
        if result:
            url, pid = result
            save_tunnel_state(url, pid)
            if on_url_change:
                on_url_change(url)

        time.sleep(10)


def start_watchdog(port: int, on_url_change: callable = None) -> None:
    """Start watchdog thread for tunnel."""
    global _watchdog_thread, _watchdog_stop

    _watchdog_stop.clear()
    _watchdog_thread = threading.Thread(
        target=_watchdog_loop,
        args=(port, on_url_change),
        daemon=True,
    )
    _watchdog_thread.start()


def stop_watchdog() -> None:
    """Stop watchdog thread."""
    global _watchdog_thread, _watchdog_stop

    _watchdog_stop.set()
    if _watchdog_thread:
        _watchdog_thread.join(timeout=2)
        _watchdog_thread = None
