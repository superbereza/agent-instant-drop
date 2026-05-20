# Phase 3: Tunnel Integration — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Автоматически поднимать cloudflared tunnel при запуске за NAT, чтобы пользователь получал публичный URL без дополнительных действий.

**Architecture:** При `drop start` детектим NAT (external IP != local IP). Если за NAT — запускаем cloudflared tunnel, парсим URL из stderr, сохраняем в storage. Watchdog-тред мониторит и рестартит tunnel при падении.

**Tech Stack:** Python, cloudflared binary, subprocess, threading.

---

## Task 1: Add cloudflared installation to install.sh

**Files:**
- Modify: `install.sh`

**Step 1: Add cloudflared download after systemd block**

Add after line 64 (after systemd block):

```bash
# Cloudflared (tunnel support)
CLOUDFLARED_BIN="$HOME/.drop/bin/cloudflared"
if ! command -v cloudflared &>/dev/null && [[ ! -f "$CLOUDFLARED_BIN" ]]; then
    echo "Installing cloudflared..."
    mkdir -p "$HOME/.drop/bin"
    ARCH=$(uname -m)
    case "$ARCH" in
        x86_64)  CF_ARCH="amd64" ;;
        aarch64) CF_ARCH="arm64" ;;
        armv7l)  CF_ARCH="arm" ;;
        *)       CF_ARCH="amd64" ;;
    esac
    OS=$(uname -s | tr '[:upper:]' '[:lower:]')
    curl -sL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-${OS}-${CF_ARCH}" \
        -o "$CLOUDFLARED_BIN"
    chmod +x "$CLOUDFLARED_BIN"
    echo "  ✓ cloudflared"
else
    echo "  ✓ cloudflared (already installed)"
fi
```

**Step 2: Test manually**

Run: `./install.sh`
Expected: See "✓ cloudflared" or "✓ cloudflared (already installed)"

Run: `~/.drop/bin/cloudflared --version`
Expected: Shows cloudflared version

**Step 3: Commit**

```bash
git add install.sh
git commit -m "feat: add cloudflared installation to install.sh"
```

---

## Task 2: Add is_behind_nat() function to utils.py

**Files:**
- Modify: `src/drop/utils.py`

**Step 1: Add NAT detection function**

Add after `has_systemd()`:

```python
def is_behind_nat() -> bool:
    """Check if we're behind NAT (external IP != local IP)."""
    external = get_external_ip()
    if not external:
        # Can't determine external IP, assume not behind NAT
        return False
    local = get_local_ip()
    return external != local
```

**Step 2: Test manually**

Run: `PYTHONPATH=src python -c "from drop.utils import is_behind_nat; print(is_behind_nat())"`
Expected: `False` on VPS with public IP, `True` on laptop behind NAT

**Step 3: Commit**

```bash
git add src/drop/utils.py
git commit -m "feat: add NAT detection utility"
```

---

## Task 3: Add find_cloudflared() function to utils.py

**Files:**
- Modify: `src/drop/utils.py`

**Step 1: Add cloudflared finder function**

Add after `is_behind_nat()`:

```python
import shutil


def find_cloudflared() -> str | None:
    """Find cloudflared binary. Returns path or None if not found."""
    # Check PATH first
    path = shutil.which("cloudflared")
    if path:
        return path

    # Check ~/.drop/bin/
    drop_bin = Path.home() / ".drop" / "bin" / "cloudflared"
    if drop_bin.exists() and drop_bin.is_file():
        return str(drop_bin)

    return None
```

**Step 2: Test manually**

Run: `PYTHONPATH=src python -c "from drop.utils import find_cloudflared; print(find_cloudflared())"`
Expected: Path to cloudflared or None

**Step 3: Commit**

```bash
git add src/drop/utils.py
git commit -m "feat: add cloudflared finder utility"
```

---

## Task 4: Add tunnel module with start/stop functions

**Files:**
- Create: `src/drop/tunnel.py`

**Step 1: Create tunnel.py**

```python
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
    """
    cloudflared = find_cloudflared()
    if not cloudflared:
        return None

    # Start cloudflared with quick tunnel
    proc = subprocess.Popen(
        [cloudflared, "tunnel", "--url", f"http://localhost:{port}", "--no-autoupdate"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # Parse URL from stderr (cloudflared outputs URL there)
    # Example: "Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):\nhttps://random-words.trycloudflare.com"
    url = None
    start_time = time.time()
    timeout = 30  # seconds

    while time.time() - start_time < timeout:
        if proc.poll() is not None:
            # Process exited
            return None

        # Read stderr line by line
        line = proc.stderr.readline()
        if not line:
            time.sleep(0.1)
            continue

        # Look for URL in output
        match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', line)
        if match:
            url = match.group(0)
            break

    if not url:
        proc.terminate()
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
```

**Step 2: Test manually**

Run:
```bash
PYTHONPATH=src python -c "
from drop.tunnel import start_tunnel, stop_tunnel
result = start_tunnel(8080)
if result:
    url, pid = result
    print(f'URL: {url}')
    print(f'PID: {pid}')
    import time; time.sleep(5)
    stop_tunnel(pid)
else:
    print('Failed to start tunnel')
"
```

**Step 3: Commit**

```bash
git add src/drop/tunnel.py
git commit -m "feat: add tunnel module with start/stop/watchdog"
```

---

## Task 5: Add tunnel_url and tunnel_pid to PageInfo

**Files:**
- Modify: `src/drop/storage.py`

**Step 1: Update PageInfo TypedDict**

Add two new fields after `pid`:

```python
class PageInfo(TypedDict):
    source: str
    is_dir: bool
    password_hash: str  # Empty string if no password
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
```

**Step 2: Update add_page function**

Add default values for new fields in the dict:

```python
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
    }
```

**Step 3: Add update_page_tunnel function**

Add after `update_page_pid`:

```python
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
```

**Step 4: Commit**

```bash
git add src/drop/storage.py
git commit -m "feat: add tunnel_url and tunnel_pid to storage"
```

---

## Task 6: Add --no-tunnel flag to cmd_start

**Files:**
- Modify: `src/drop/cli.py`

**Step 1: Add imports**

Add to imports:

```python
from .utils import generate_page_id, generate_password, hash_password, detect_ip, load_manifest, MANIFEST_FILE, has_systemd, is_behind_nat, find_cloudflared
from . import tunnel
```

**Step 2: Add --no-tunnel argument to start parser**

In `main()`, update `p_start`:

```python
    # start
    p_start = subparsers.add_parser("start", help="Start server or app")
    p_start.add_argument("name", nargs="?", help="App name/ID to start (omit for server)")
    p_start.add_argument("--port", "-p", type=int, default=8080, help="Server port (default: 8080)")
    p_start.add_argument("--host", help="Override auto-detected IP")
    p_start.add_argument("--no-tunnel", action="store_true", help="Disable automatic tunnel when behind NAT")
    p_start.set_defaults(func=cmd_start)
```

**Step 3: Commit**

```bash
git add src/drop/cli.py
git commit -m "feat: add --no-tunnel flag to start command"
```

---

## Task 7: Integrate tunnel into cmd_start for server

**Files:**
- Modify: `src/drop/cli.py`

**Step 1: Update cmd_start to use tunnel when behind NAT**

Replace the success messages in `cmd_start` (both systemd and fallback paths) to check for NAT and start tunnel:

After the server successfully starts (after `print(f"Server started: http://{host}:{port}")`), add:

```python
    # Check for NAT and start tunnel if needed
    if not getattr(args, 'no_tunnel', False) and is_behind_nat():
        cloudflared = find_cloudflared()
        if cloudflared:
            print("Detected NAT, starting tunnel...")
            result = tunnel.start_tunnel(port)
            if result:
                tunnel_url, tunnel_pid = result
                tunnel.save_tunnel_state(tunnel_url, tunnel_pid)
                tunnel.start_watchdog(port)
                print(f"Server started: {tunnel_url}")
                print("  (tunneled via cloudflared)")
                return 0
            else:
                print("  Warning: Failed to start tunnel, using local URL")
        else:
            print("  Note: Behind NAT but cloudflared not found. Run ./install.sh")
```

The full updated `cmd_start` function for server (when not starting an app):

```python
def cmd_start(args: argparse.Namespace) -> int:
    """Start the server or an app."""
    # If name provided, start app instead
    if hasattr(args, 'name') and args.name:
        return cmd_start_app(args)

    port = args.port
    host = args.host or detect_ip()

    # Save config
    storage.save_port(port)
    if args.host:
        storage.save_host(args.host)

    # Check if already running (systemd or PID)
    if has_systemd():
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "drop.service"],
            capture_output=True,
            text=True,
        )
        if result.stdout.strip() == "active":
            # Check if tunnel is running
            state = tunnel.load_tunnel_state()
            if state and state.get("url"):
                print(f"Server already running: {state['url']}")
            else:
                print(f"Server already running: http://{host}:{port}")
            return 0

        exit_code = _start_with_systemd(port, host)
        if exit_code != 0:
            return exit_code

        # Check for NAT and start tunnel
        if not getattr(args, 'no_tunnel', False) and is_behind_nat():
            cloudflared = find_cloudflared()
            if cloudflared:
                print("Detected NAT, starting tunnel...")
                result = tunnel.start_tunnel(port)
                if result:
                    tunnel_url, tunnel_pid = result
                    tunnel.save_tunnel_state(tunnel_url, tunnel_pid)
                    tunnel.start_watchdog(port)
                    print(f"Tunnel URL: {tunnel_url}")
                else:
                    print("  Warning: Failed to start tunnel")
            else:
                print("  Note: Behind NAT but cloudflared not found. Run ./install.sh")
        return 0

    # Fallback: PID-based management
    pid = storage.load_pid()
    if pid:
        try:
            os.kill(pid, 0)
            state = tunnel.load_tunnel_state()
            if state and state.get("url"):
                print(f"Server already running: {state['url']}")
            else:
                print(f"Server already running: http://{host}:{port}")
            return 0
        except OSError:
            storage.clear_pid()

    # Start server in background
    cmd = [
        sys.executable, "-c",
        f"from drop.server import run_server; run_server(port={port})"
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    storage.save_pid(proc.pid)

    # Wait a moment and check it started
    time.sleep(0.5)
    try:
        os.kill(proc.pid, 0)
    except OSError:
        print("Error: Server failed to start", file=sys.stderr)
        storage.clear_pid()
        return 1

    # Check for NAT and start tunnel
    if not getattr(args, 'no_tunnel', False) and is_behind_nat():
        cloudflared = find_cloudflared()
        if cloudflared:
            print("Detected NAT, starting tunnel...")
            result = tunnel.start_tunnel(port)
            if result:
                tunnel_url, tunnel_pid = result
                tunnel.save_tunnel_state(tunnel_url, tunnel_pid)
                tunnel.start_watchdog(port)
                print(f"Server started: {tunnel_url}")
                print("  (tunneled via cloudflared)")
                return 0
            else:
                print(f"Server started: http://{host}:{port}")
                print("  Warning: Failed to start tunnel")
        else:
            print(f"Server started: http://{host}:{port}")
            print("  Note: Behind NAT but cloudflared not found. Run ./install.sh")
    else:
        print(f"Server started: http://{host}:{port}")
        if not has_systemd():
            print("  No systemd - auto-restart disabled")

    return 0
```

**Step 2: Update cmd_stop to stop tunnel**

Add tunnel cleanup at the start of `cmd_stop`:

```python
def cmd_stop(args: argparse.Namespace) -> int:
    """Stop the server or an app."""
    # If name provided, stop app instead
    if hasattr(args, 'name') and args.name:
        return cmd_stop_app(args)

    # Stop server tunnel if running
    tunnel.stop_watchdog()
    state = tunnel.load_tunnel_state()
    if state:
        tunnel.stop_tunnel(state.get("pid", 0))
        tunnel.clear_tunnel_state()

    # ... rest of existing code
```

**Step 3: Test manually**

On VPS (no NAT):
```bash
drop stop && drop start
```
Expected: Normal start without tunnel

**Step 4: Commit**

```bash
git add src/drop/cli.py
git commit -m "feat: integrate tunnel into server start/stop"
```

---

## Task 8: Update cmd_status to show tunnel URL

**Files:**
- Modify: `src/drop/cli.py`

**Step 1: Update cmd_status**

Replace server status output section:

```python
def cmd_status(args: argparse.Namespace) -> int:
    """Show server status."""
    port = storage.load_port() or 8080
    host = storage.load_host() or detect_ip()

    running = False
    systemd_managed = False
    tunnel_url = None

    # Check tunnel state
    state = tunnel.load_tunnel_state()
    if state:
        tunnel_url = state.get("url")

    if has_systemd():
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "drop.service"],
            capture_output=True,
            text=True,
        )
        if result.stdout.strip() == "active":
            running = True
            systemd_managed = True
    else:
        pid = storage.load_pid()
        if pid:
            try:
                os.kill(pid, 0)
                running = True
            except OSError:
                storage.clear_pid()

    if running:
        if tunnel_url:
            print(f"Server: {tunnel_url} (running, tunneled)")
        else:
            extra = " (systemd)" if systemd_managed else ""
            print(f"Server: http://{host}:{port} (running{extra})")
    else:
        print("Server: not running")

    # ... rest of existing code (pages listing)
```

**Step 2: Commit**

```bash
git add src/drop/cli.py
git commit -m "feat: show tunnel URL in status"
```

---

## Task 9: Integrate tunnel into cmd_start_app

**Files:**
- Modify: `src/drop/cli.py`

**Step 1: Update cmd_start_app to start tunnel for apps behind NAT**

After the app successfully starts, add tunnel logic:

```python
def cmd_start_app(args: argparse.Namespace) -> int:
    """Start an app by name/ID."""
    page = storage.get_page(args.name)
    if not page:
        print(f"Error: '{args.name}' not found", file=sys.stderr)
        return 1

    if page.get("type") != "app":
        print(f"Error: '{args.name}' is not an app (use 'drop start' for server)", file=sys.stderr)
        return 1

    # Check if already running
    status = storage.get_app_status(args.name)
    if status == "running":
        host = storage.load_host() or detect_ip()
        tunnel_url = page.get("tunnel_url")
        if tunnel_url:
            print(f"App already running: {tunnel_url}")
        else:
            print(f"App already running: http://{host}:{page['port']}/")
        return 0

    # Start the app
    source_dir = Path(page["source"]).parent if not Path(page["source"]).is_dir() else Path(page["source"])
    run_cmd = page["run_cmd"]

    proc = subprocess.Popen(
        run_cmd,
        shell=True,
        cwd=source_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    # Save PID
    full_id = storage.get_full_page_id(args.name)
    storage.update_page_pid(full_id, proc.pid)

    # Wait and verify
    time.sleep(1)
    try:
        os.kill(proc.pid, 0)
    except OSError:
        storage.update_page_pid(full_id, 0)
        print("Error: App failed to start", file=sys.stderr)
        return 1

    host = storage.load_host() or detect_ip()
    app_port = page["port"]

    # Check for NAT and start tunnel
    no_tunnel = getattr(args, 'no_tunnel', False)
    if not no_tunnel and is_behind_nat():
        cloudflared = find_cloudflared()
        if cloudflared:
            print("Detected NAT, starting tunnel...")
            result = tunnel.start_tunnel(app_port)
            if result:
                tunnel_url, tunnel_pid = result
                storage.update_page_tunnel(full_id, tunnel_url, tunnel_pid)
                print(f"App started: {tunnel_url}")
                print("  (tunneled via cloudflared)")
                return 0
            else:
                print(f"App started: http://{host}:{app_port}/")
                print("  Warning: Failed to start tunnel")
        else:
            print(f"App started: http://{host}:{app_port}/")
            print("  Note: Behind NAT but cloudflared not found. Run ./install.sh")
    else:
        print(f"App started: http://{host}:{app_port}/")

    return 0
```

**Step 2: Commit**

```bash
git add src/drop/cli.py
git commit -m "feat: integrate tunnel into app start"
```

---

## Task 10: Update cmd_stop_app to stop tunnel

**Files:**
- Modify: `src/drop/cli.py`

**Step 1: Update cmd_stop_app to stop app tunnel**

Add tunnel cleanup:

```python
def cmd_stop_app(args: argparse.Namespace) -> int:
    """Stop an app by name/ID."""
    page = storage.get_page(args.name)
    if not page:
        print(f"Error: '{args.name}' not found", file=sys.stderr)
        return 1

    if page.get("type") != "app":
        print(f"Error: '{args.name}' is not an app (use 'drop stop' for server)", file=sys.stderr)
        return 1

    full_id = storage.get_full_page_id(args.name)

    # Stop tunnel if running
    tunnel_pid = page.get("tunnel_pid", 0)
    if tunnel_pid > 0:
        tunnel.stop_tunnel(tunnel_pid)
        storage.update_page_tunnel(full_id, "", 0)

    status = storage.get_app_status(args.name)
    if status != "running":
        print("App not running")
        return 0

    pid = page.get("pid", 0)
    if pid <= 0:
        print("App was not running")
    else:
        try:
            os.killpg(pid, signal.SIGTERM)
            print("App stopped")
        except OSError:
            print("App was not running")

    storage.update_page_pid(full_id, 0)
    return 0
```

**Step 2: Commit**

```bash
git add src/drop/cli.py
git commit -m "feat: stop app tunnel when stopping app"
```

---

## Task 11: Update cmd_list to show tunnel URLs

**Files:**
- Modify: `src/drop/cli.py`

**Step 1: Update cmd_list to prefer tunnel URL**

In the loop, update URL logic:

```python
    for page_id, info in pages.items():
        page_type = info.get("type", "static")
        name = info.get("name", "")
        tunnel_url = info.get("tunnel_url", "")

        if page_type == "app":
            # App: show tunnel URL if available, otherwise direct port
            port = info.get("port", 0)
            if tunnel_url:
                url = tunnel_url
            else:
                url = f"http://{host}:{port}/"
            status = storage.get_app_status(page_id)
            status_str = f" [{status}]"
        else:
            # Static: show tunnel URL if server has tunnel, otherwise drop server URL
            server_tunnel = tunnel.load_tunnel_state()
            if server_tunnel and server_tunnel.get("url"):
                base_url = server_tunnel["url"]
                if name:
                    url = f"{base_url}/p/{page_id}/{name}/"
                else:
                    url = f"{base_url}/p/{page_id}/"
            else:
                if name:
                    url = f"http://{host}:{server_port}/p/{page_id}/{name}/"
                else:
                    url = f"http://{host}:{server_port}/p/{page_id}/"
            status_str = ""
```

**Step 2: Commit**

```bash
git add src/drop/cli.py
git commit -m "feat: show tunnel URLs in list output"
```

---

## Task 12: Update skill documentation

**Files:**
- Modify: `skills/drop.md`

**Step 1: Add tunnel documentation**

Add new section "## Tunnel (NAT Support)" after "Apps" section:

```markdown
## Tunnel (NAT Support)

When running behind NAT, drop automatically creates a public URL via cloudflared tunnel.

```bash
# Behind NAT - tunnel starts automatically
$ drop start
Detected NAT, starting tunnel...
Server started: https://random-words.trycloudflare.com
  (tunneled via cloudflared)

# Apps also get tunnels
$ drop start myapp
Detected NAT, starting tunnel...
App started: https://other-random.trycloudflare.com
  (tunneled via cloudflared)

# Disable tunnel if needed
$ drop start --no-tunnel
Server started: http://192.168.1.50:8080 (local)
```

Tunnel auto-restarts if cloudflared crashes. URLs change on each restart (quick tunnels don't persist).
```

**Step 2: Commit**

```bash
git add skills/drop.md
git commit -m "docs: add tunnel documentation to skill"
```

---

## Summary

| Task | Description | Commit |
|------|-------------|--------|
| 1 | Add cloudflared to install.sh | feat: add cloudflared installation |
| 2 | Add is_behind_nat() | feat: add NAT detection |
| 3 | Add find_cloudflared() | feat: add cloudflared finder |
| 4 | Create tunnel.py module | feat: add tunnel module |
| 5 | Add tunnel fields to storage | feat: add tunnel fields |
| 6 | Add --no-tunnel flag | feat: add --no-tunnel flag |
| 7 | Integrate tunnel into server start | feat: integrate tunnel into server |
| 8 | Show tunnel URL in status | feat: show tunnel in status |
| 9 | Integrate tunnel into app start | feat: integrate tunnel into app start |
| 10 | Stop tunnel when stopping app | feat: stop app tunnel |
| 11 | Show tunnel URLs in list | feat: show tunnel URLs in list |
| 12 | Update skill docs | docs: add tunnel documentation |

After all tasks:
- `drop start` auto-tunnels when behind NAT
- `drop start myapp` auto-tunnels apps too
- `drop status` and `drop list` show tunnel URLs
- `--no-tunnel` opt-out available
- Tunnel auto-restarts via watchdog
