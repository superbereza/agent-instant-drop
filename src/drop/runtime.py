"""Volatile per-page runtime state.

PageRuntime tracks pids and the tunnel URL. Stored in ~/.drop/runtime.json
separately from the config-side Page (which lives in pages.json). PID
liveness is verified via os.kill(pid, 0).
"""

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config


def _drop_home() -> Path:
    """Return DROP_HOME, re-reading env at call time so tests can override."""
    return Path(os.environ.get("DROP_HOME") or Path.home() / ".drop")


def _runtime_file() -> Path:
    return _drop_home() / "runtime.json"


@dataclass
class PageRuntime:
    page_id: str
    app_pid: int = 0
    proxy_pid: int = 0
    proxy_port: int = 0
    tunnel_pid: int = 0
    tunnel_url: str = ""

    def _alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def is_app_alive(self) -> bool:
        return self._alive(self.app_pid)

    def is_proxy_alive(self) -> bool:
        return self._alive(self.proxy_pid)

    def is_tunnel_alive(self) -> bool:
        return self._alive(self.tunnel_pid)


def _ensure_dir() -> None:
    _drop_home().mkdir(parents=True, exist_ok=True)


def load_runtimes() -> dict[str, PageRuntime]:
    """Load all runtime state. Empty dict if no file."""
    runtime_file = _runtime_file()
    if not runtime_file.exists():
        return {}
    try:
        raw = json.loads(runtime_file.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict) or "runtimes" not in raw:
        return {}
    return {pid: PageRuntime(**d) for pid, d in raw["runtimes"].items()}


def save_runtimes(rtmap: dict[str, PageRuntime]) -> None:
    """Write all runtime state."""
    _ensure_dir()
    envelope = {
        "version": config.SCHEMA_VERSION,
        "runtimes": {pid: asdict(r) for pid, r in rtmap.items()},
    }
    _runtime_file().write_text(json.dumps(envelope, indent=2))


def get_runtime(page_id: str) -> PageRuntime:
    """Get runtime for a page. Returns empty PageRuntime if missing."""
    rtmap = load_runtimes()
    return rtmap.get(page_id, PageRuntime(page_id=page_id))


def save_runtime(r: PageRuntime) -> None:
    """Persist one PageRuntime."""
    rtmap = load_runtimes()
    rtmap[r.page_id] = r
    save_runtimes(rtmap)


def clear_runtime(page_id: str) -> None:
    """Remove runtime entry for a page (no error if missing)."""
    rtmap = load_runtimes()
    if page_id in rtmap:
        del rtmap[page_id]
        save_runtimes(rtmap)
