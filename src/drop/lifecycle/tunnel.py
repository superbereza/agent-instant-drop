"""Cloudflared tunnel subprocess management via --logfile (no PIPE).

Why --logfile and not subprocess.PIPE: cloudflared writes ~64KB of stderr
within the first minute. If we capture via PIPE without draining, the
kernel buffer fills, the child blocks on write(), and the tunnel
freezes (CF error 1033/530). Using --logfile (which cloudflared owns)
makes the bug architecturally impossible.
"""

import os
import re
import signal
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
    """Kill the cloudflared process group and reap the zombie. No-op if pid is 0 or dead.

    cloudflared ignores SIGTERM, so we escalate directly to SIGKILL and
    reap the resulting zombie via waitpid so callers can reliably check
    liveness with os.kill(pid, 0).
    """
    if pid <= 0:
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        return
    # Reap the zombie so os.kill(pid, 0) no longer sees it as alive.
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        # Not our child (spawned detached) — nothing to reap from this process.
        pass
    except OSError:
        pass
