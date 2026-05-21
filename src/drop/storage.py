"""Storage management for drop."""

import json
import os
from datetime import datetime, UTC
from pathlib import Path
from typing import TypedDict


class PageInfo(TypedDict):
    source: str
    is_dir: bool
    password_hash: str  # Empty string if no password (static cookie-form)
    created_at: str
    description: str  # Optional description
    name: str  # URL slug (human-readable name)
    # App-specific fields (optional)
    type: str  # "static" or "app"
    run_cmd: str  # Command to run (for apps)
    port: int  # App port (for apps)
    pid: int  # Running process PID (for apps, 0 if not running)
    tunnel_url: str  # Tunnel URL (empty if no tunnel)
    tunnel_pid: int  # Tunnel process PID (0 if no tunnel)
    # App basic auth (V2)
    auth: dict | None  # {"scheme": "basic", "user": str, "password_hash": str} or None
    public: bool  # True if user explicitly passed --public
    proxy_pid: int  # Proxy process PID (0 if no proxy)
    proxy_port: int  # Proxy listen port (0 if no proxy)


DROP_DIR = Path.home() / ".drop"
PAGES_FILE = DROP_DIR / "pages.json"
PID_FILE = DROP_DIR / "server.pid"
PORT_FILE = DROP_DIR / "port"
HOST_FILE = DROP_DIR / "host"


def ensure_dir() -> None:
    """Ensure ~/.drop directory exists."""
    DROP_DIR.mkdir(parents=True, exist_ok=True)


def load_pages() -> dict[str, PageInfo]:
    """Load pages registry."""
    if not PAGES_FILE.exists():
        return {}
    try:
        return json.loads(PAGES_FILE.read_text())
    except Exception:
        return {}


def save_pages(pages: dict[str, PageInfo]) -> None:
    """Save pages registry."""
    ensure_dir()
    PAGES_FILE.write_text(json.dumps(pages, indent=2))


def add_page(
    page_id: str,
    source: Path,
    password_hash: str,
    description: str = "",
    name: str = "",
    page_type: str = "static",
    run_cmd: str = "",
    port: int = 0,
    auth: dict | None = None,
    public: bool = False,
) -> None:
    """Add a page to registry. Raises ValueError if `name` already exists."""
    pages = load_pages()
    if name:
        for existing_id, existing in pages.items():
            if existing.get("name") == name:
                raise ValueError(
                    f"name '{name}' already exists (page_id {existing_id[:8]}). "
                    f"Use a different --name, or 'drop remove {name}' first."
                )
    pages[page_id] = {
        "source": str(source.resolve()),
        "is_dir": source.is_dir(),
        "password_hash": password_hash,
        "created_at": datetime.now(UTC).isoformat(),
        "description": description,
        "name": name,
        "type": page_type,
        "run_cmd": run_cmd,
        "port": port,
        "pid": 0,
        "tunnel_url": "",
        "tunnel_pid": 0,
        "auth": auth,
        "public": public,
        "proxy_pid": 0,
        "proxy_port": 0,
    }
    save_pages(pages)


def remove_page(page_id: str) -> bool:
    """Remove a page from registry. Returns True if found."""
    pages = load_pages()
    if page_id in pages:
        del pages[page_id]
        save_pages(pages)
        return True
    # Try partial match
    matches = [k for k in pages if k.startswith(page_id)]
    if len(matches) == 1:
        del pages[matches[0]]
        save_pages(pages)
        return True
    return False


def get_page(page_id: str) -> PageInfo | None:
    """Get page by ID or name (supports partial ID match)."""
    pages = load_pages()
    if page_id in pages:
        return pages[page_id]
    # Try partial ID match
    matches = [k for k in pages if k.startswith(page_id)]
    if len(matches) == 1:
        return pages[matches[0]]
    # Try name match
    for pid, info in pages.items():
        if info.get("name") == page_id:
            return info
    return None


def get_full_page_id(partial_id: str) -> str | None:
    """Get full page ID from partial ID or name match."""
    pages = load_pages()
    if partial_id in pages:
        return partial_id
    # Try partial ID match
    matches = [k for k in pages if k.startswith(partial_id)]
    if len(matches) == 1:
        return matches[0]
    # Try name match
    for pid, info in pages.items():
        if info.get("name") == partial_id:
            return pid
    return None


def update_page_pid(page_id: str, pid: int) -> bool:
    """Update running PID for an app. Returns True if found."""
    pages = load_pages()
    full_id = get_full_page_id(page_id)
    if not full_id:
        return False
    pages[full_id]["pid"] = pid
    save_pages(pages)
    return True


def update_page_tunnel(page_id: str, tunnel_url: str, tunnel_pid: int) -> bool:
    """Update tunnel info for a page. Returns True if found."""
    pages = load_pages()
    full_id = get_full_page_id(page_id)
    if not full_id:
        return False
    pages[full_id]["tunnel_url"] = tunnel_url
    pages[full_id]["tunnel_pid"] = tunnel_pid
    save_pages(pages)
    return True


def update_page_proxy(page_id: str, proxy_pid: int, proxy_port: int) -> bool:
    """Update proxy info for a page. Returns True if found."""
    pages = load_pages()
    full_id = get_full_page_id(page_id)
    if not full_id:
        return False
    pages[full_id]["proxy_pid"] = proxy_pid
    pages[full_id]["proxy_port"] = proxy_port
    save_pages(pages)
    return True


def clear_page_runtime(page_id: str) -> bool:
    """Zero out all runtime fields (pid/tunnel/proxy). Returns True if found."""
    pages = load_pages()
    full_id = get_full_page_id(page_id)
    if not full_id:
        return False
    pages[full_id]["pid"] = 0
    pages[full_id]["tunnel_url"] = ""
    pages[full_id]["tunnel_pid"] = 0
    pages[full_id]["proxy_pid"] = 0
    pages[full_id]["proxy_port"] = 0
    save_pages(pages)
    return True


def get_app_status(page_id: str) -> str:
    """Get app status: 'running', 'stopped', or 'crashed'."""
    page = get_page(page_id)
    if not page or page.get("type") != "app":
        return "not_app"

    pid = page.get("pid", 0)
    if pid == 0:
        return "stopped"

    try:
        os.kill(pid, 0)
        return "running"
    except OSError:
        return "crashed"


# Server state

def save_pid(pid: int) -> None:
    """Save server PID."""
    ensure_dir()
    PID_FILE.write_text(str(pid))


def load_pid() -> int | None:
    """Load server PID."""
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except Exception:
        return None


def clear_pid() -> None:
    """Clear server PID."""
    if PID_FILE.exists():
        PID_FILE.unlink()


def save_port(port: int) -> None:
    """Save server port."""
    ensure_dir()
    PORT_FILE.write_text(str(port))


def load_port() -> int | None:
    """Load server port."""
    if not PORT_FILE.exists():
        return None
    try:
        return int(PORT_FILE.read_text().strip())
    except Exception:
        return None


def save_host(host: str) -> None:
    """Save configured host."""
    ensure_dir()
    HOST_FILE.write_text(host)


def load_host() -> str | None:
    """Load configured host."""
    if not HOST_FILE.exists():
        return None
    return HOST_FILE.read_text().strip() or None
