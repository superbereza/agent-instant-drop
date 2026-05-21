"""Single source of truth for subprocess spawning in drop.

All detached subprocesses go through spawn_managed. Two invariants:

  1. start_new_session=True — child is in its own session, so SIGHUP on
     the CLI's terminal does not propagate to it.
  2. Output goes to a log file OR DEVNULL — never undrained subprocess.PIPE.
     Undrained PIPEs fill the kernel's ~64KB buffer in seconds and block
     the child's next write(), freezing it. cloudflared hits this in v1.

If you find yourself reaching for subprocess.Popen directly anywhere
in v2, route through this module instead.
"""

import os
import signal
import subprocess
import time
from pathlib import Path


def spawn_managed(
    cmd,
    *,
    log_file: Path | None = None,
    cwd: Path | None = None,
    shell: bool = False,
) -> subprocess.Popen:
    """Spawn a detached subprocess.

    Args:
        cmd: list[str] (no shell) or str (with shell=True).
        log_file: if given, stdout+stderr go to this file. Otherwise DEVNULL.
        cwd: working directory.
        shell: pass cmd to the shell. Use only for user-supplied run_cmd.

    Returns the Popen object. The parent's reference to log_file's FD is
    closed before returning, so the child owns it; subsequent writes work
    even after the parent exits.
    """
    if log_file is not None:
        # Append mode in case caller reuses the same log path
        fh = open(log_file, "ab")
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=fh,
                stderr=subprocess.STDOUT,
                cwd=str(cwd) if cwd is not None else None,
                shell=shell,
                start_new_session=True,
            )
        finally:
            fh.close()  # parent's FD released; child still has its dup
    else:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(cwd) if cwd is not None else None,
            shell=shell,
            start_new_session=True,
        )
    return proc


def wait_alive(pid: int, after: float = 1.0) -> bool:
    """Check if pid is alive after waiting `after` seconds.

    The wait gives the process time to either bind a port / open a file
    or crash. Returns True if alive at the end of the wait.
    """
    if pid <= 0:
        return False
    if after > 0:
        time.sleep(after)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# Re-export wait_for_port for convenience — lifecycle code uses it for
# "did the spawned process bind its port yet?".
from ..utils import wait_for_port  # noqa: E402


def kill_pg(pid: int) -> bool:
    """SIGTERM the process group led by pid. Returns True if signal was sent."""
    if pid <= 0:
        return False
    try:
        os.killpg(pid, signal.SIGTERM)
        return True
    except OSError:
        return False
