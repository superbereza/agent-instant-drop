#!/usr/bin/env python3
"""
drop - Agent Instant Drop

Drop any file, app, or prototype to your human.

Examples:
    drop start                    # Start server
    drop add ./report.html        # Publish file
    drop add ./dist/              # Publish folder
    drop list                     # List pages
    drop remove abc123            # Remove page
    drop stop                     # Stop server
"""

import argparse
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, UTC
from pathlib import Path

from . import storage
from . import tunnel
from .utils import generate_page_id, generate_password, generate_auth_creds, hash_password, detect_ip, load_manifest, MANIFEST_FILE, has_systemd, is_behind_nat, find_cloudflared


def _parse_auth_spec(spec: str) -> tuple[str, str | None, str | None]:
    """Parse --auth value. Returns (scheme, user_or_None, password_or_None)."""
    parts = spec.split(":", 2)
    scheme = parts[0]
    if scheme != "basic":
        raise ValueError(f"unsupported auth scheme: {scheme!r} (only 'basic' supported)")
    if len(parts) == 1:
        return ("basic", None, None)
    if len(parts) == 3:
        user, password = parts[1], parts[2]
        if not user or not password:
            raise ValueError("--auth basic:user:pass requires non-empty user and password")
        return ("basic", user, password)
    raise ValueError("--auth format: 'basic' or 'basic:user:pass'")


def _start_with_systemd(port: int, host: str) -> int:
    """Start server using systemd."""
    # Update unit file with current port
    unit_path = Path.home() / ".config/systemd/user/drop.service"
    if not unit_path.exists():
        print("Error: systemd unit not found. Run ./install.sh", file=sys.stderr)
        return 1

    # Read and update ExecStart with port
    content = unit_path.read_text()
    # Replace the run_server() call to include port
    new_content = re.sub(
        r'run_server\([^)]*\)',
        f'run_server(port={port})',
        content
    )
    unit_path.write_text(new_content)

    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "drop.service"], check=True)
        subprocess.run(["systemctl", "--user", "start", "drop.service"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error: systemctl command failed: {e}", file=sys.stderr)
        return 1

    # Wait and verify
    time.sleep(1)
    result = subprocess.run(
        ["systemctl", "--user", "is-active", "drop.service"],
        capture_output=True,
        text=True,
    )
    if result.stdout.strip() != "active":
        print("Error: Server failed to start", file=sys.stderr)
        return 1
    return 0


def _stop_with_systemd() -> int:
    """Stop server using systemd."""
    subprocess.run(["systemctl", "--user", "stop", "drop.service"])
    subprocess.run(["systemctl", "--user", "disable", "drop.service"])
    print("Server stopped")
    return 0


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
            # Send signal to process group to kill shell and all children
            os.killpg(pid, signal.SIGTERM)
            print("App stopped")
        except OSError:
            print("App was not running")

    storage.update_page_pid(full_id, 0)
    return 0


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
        return _maybe_start_tunnel(args, port, host, systemd_managed=True)

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
    return _maybe_start_tunnel(args, port, host, systemd_managed=False)


def _maybe_start_tunnel(args: argparse.Namespace, port: int, host: str, systemd_managed: bool) -> int:
    """Check for NAT and start tunnel if needed. Print status and return exit code."""
    no_tunnel = getattr(args, 'no_tunnel', False)

    if not no_tunnel and is_behind_nat():
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
        if systemd_managed:
            print("  (systemd managed, auto-restart enabled)")
        else:
            print("  No systemd - auto-restart disabled")

    return 0


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

    if has_systemd():
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "drop.service"],
            capture_output=True,
            text=True,
        )
        if result.stdout.strip() == "active":
            return _stop_with_systemd()
        print("Server not running")
        return 0

    # Fallback: PID-based
    pid = storage.load_pid()
    if not pid:
        print("Server not running")
        return 0

    try:
        os.kill(pid, signal.SIGTERM)
        print("Server stopped")
    except OSError:
        print("Server was not running")

    storage.clear_pid()
    return 0


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

    print()
    pages = storage.load_pages()
    if not pages:
        print("No pages published")
    else:
        print("Pages:")
        for page_id, info in pages.items():
            source = info["source"]
            created = datetime.fromisoformat(info["created_at"])
            age = datetime.now(UTC) - created
            if age.days > 0:
                age_str = f"{age.days}d ago"
            elif age.seconds > 3600:
                age_str = f"{age.seconds // 3600}h ago"
            else:
                age_str = f"{age.seconds // 60}m ago"

            lock = "" if info["password_hash"] else " (public)"
            print(f"  {page_id}  {source}  {age_str}{lock}")

    return 0


def cmd_add(args: argparse.Namespace) -> int:
    """Add a page or app."""
    source = Path(args.path).resolve()
    if not source.exists():
        print(f"Error: {args.path} not found", file=sys.stderr)
        return 1

    # Validate app args
    is_app = bool(args.run)
    if is_app and not args.port:
        print("Error: --port is required when using --run", file=sys.stderr)
        return 1
    if args.port and not args.run:
        print("Error: --run is required when using --port", file=sys.stderr)
        return 1

    # Auth flag validation
    if args.auth is not None and not is_app:
        print("Error: --auth only applies to apps (use with --run)", file=sys.stderr)
        return 1
    if args.public and args.password is not None:
        print("Error: cannot combine --password with --public", file=sys.stderr)
        return 1
    if args.public and args.auth is not None:
        print("Error: cannot combine --auth with --public", file=sys.stderr)
        return 1
    if args.auth is not None and args.password is not None:
        print("Error: cannot combine --auth (apps) with --password (static)", file=sys.stderr)
        return 1
    if args.auth is not None:
        try:
            _parse_auth_spec(args.auth)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    # Directory requires manifest (for static only)
    if source.is_dir() and not is_app:
        manifest = load_manifest(source)
        if manifest is None:
            print(f"Error: Directory requires {MANIFEST_FILE} manifest", file=sys.stderr)
            print(f"Create {source / MANIFEST_FILE} with allowed file patterns:", file=sys.stderr)
            print("  index.html", file=sys.stderr)
            print("  assets/**", file=sys.stderr)
            return 1
        print(f"Using manifest: {', '.join(manifest)}")

    page_id = generate_page_id()

    # Resolve auth/password: default = protected (auto-gen) unless --public
    password = None
    password_hash = ""
    auth_block = None
    auth_creds_shown = None  # (user, password) tuple to print at end

    if is_app:
        if args.public:
            pass  # explicit public, no auth
        elif args.auth is not None:
            scheme, user, raw_pw = _parse_auth_spec(args.auth)
            if user is None:
                user, raw_pw = generate_auth_creds()
            auth_block = {
                "scheme": scheme,
                "user": user,
                "password_hash": hash_password(raw_pw),
            }
            auth_creds_shown = (user, raw_pw)
        else:
            # Default for apps: auto-gen basic auth
            user, raw_pw = generate_auth_creds()
            auth_block = {
                "scheme": "basic",
                "user": user,
                "password_hash": hash_password(raw_pw),
            }
            auth_creds_shown = (user, raw_pw)
    else:
        # Static page
        if args.public:
            pass  # explicit public, no password
        elif args.password is not None:
            raw_pw = args.password if args.password is not True else generate_password()
            password = raw_pw
            password_hash = hash_password(raw_pw)
        else:
            # Default for static: auto-gen password
            raw_pw = generate_password()
            password = raw_pw
            password_hash = hash_password(raw_pw)

    name = args.name or ""

    # Add to storage
    storage.add_page(
        page_id,
        source,
        password_hash,
        args.desc or "",
        name,
        page_type="app" if is_app else "static",
        run_cmd=args.run or "",
        port=args.port or 0,
        auth=auth_block,
        public=bool(args.public),
    )

    # Get URL
    server_port = storage.load_port() or 8080
    host = storage.load_host() or detect_ip()

    if is_app:
        url = f"http://{host}:{args.port}/"
        print(f"App registered: {url}")
        if auth_creds_shown:
            user, raw_pw = auth_creds_shown
            print(f"  Auth: basic ({user} / {raw_pw})")
        elif args.public:
            print("  (public — no auth)")
        print(f"Run 'drop start {page_id}' to start the app")
    else:
        if name:
            url = f"http://{host}:{server_port}/p/{page_id}/{name}/"
        else:
            url = f"http://{host}:{server_port}/p/{page_id}/"
        print(f"Published: {url}")
        if password:
            print(f"Password: {password}")
        elif args.public:
            print("  (public — no password)")

    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """List pages (filtered by current directory by default)."""
    pages = storage.load_pages()
    if not pages:
        print("No pages published")
        return 0

    server_port = storage.load_port() or 8080
    host = storage.load_host() or detect_ip()
    cwd = Path.cwd().resolve()

    # Filter by current directory unless --all
    if not args.all:
        filtered = {}
        for page_id, info in pages.items():
            source = Path(info["source"])
            try:
                if source.is_relative_to(cwd):
                    filtered[page_id] = info
            except (ValueError, OSError):
                pass
        pages = filtered

    if not pages:
        print(f"No pages from {cwd}")
        print("Use 'drop list --all' to see all pages")
        return 0

    for page_id, info in pages.items():
        page_type = info.get("type", "static")
        name = info.get("name", "")
        app_tunnel_url = info.get("tunnel_url", "")

        if page_type == "app":
            # App: show tunnel URL if available, otherwise direct port
            port = info.get("port", 0)
            if app_tunnel_url:
                url = app_tunnel_url
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

        lock = "" if info["password_hash"] else " (public)"

        # Check source exists
        source_exists = Path(info["source"]).exists()
        source_warning = " ⚠️ source deleted" if not source_exists else ""

        type_label = f"[{page_type}]" if page_type == "app" else ""
        print(f"{page_id[:8]}  {type_label}{status_str}  {url}{lock}{source_warning}")

        desc = info.get("description", "")
        if desc:
            print(f"  {desc}")
        print(f"  Source: {info['source']}")
        if page_type == "app":
            print(f"  Run: {info.get('run_cmd', '')}")

    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    """Remove a page."""
    if storage.remove_page(args.id):
        print(f"Removed: {args.id}")
        return 0
    else:
        print(f"Error: page {args.id} not found", file=sys.stderr)
        return 1


def cmd_cleanup(args: argparse.Namespace) -> int:
    """Remove entries with deleted source files."""
    pages = storage.load_pages()
    if not pages:
        print("No pages to clean")
        return 0

    removed = []
    for page_id, info in list(pages.items()):
        source = Path(info["source"])
        if not source.exists():
            # Stop app if running
            if info.get("type") == "app":
                pid = info.get("pid", 0)
                if pid > 0:
                    try:
                        os.killpg(pid, signal.SIGTERM)
                    except OSError:
                        pass
            removed.append(page_id)
            del pages[page_id]

    if removed:
        storage.save_pages(pages)
        for page_id in removed:
            print(f"Removed: {page_id} (source deleted)")
        print(f"Cleaned {len(removed)} stale entries")
    else:
        print("No stale entries found")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Drop any file, app, or prototype to your human",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # start
    p_start = subparsers.add_parser("start", help="Start server or app")
    p_start.add_argument("name", nargs="?", help="App name/ID to start (omit for server)")
    p_start.add_argument("--port", "-p", type=int, default=8080, help="Server port (default: 8080)")
    p_start.add_argument("--host", help="Override auto-detected IP")
    p_start.add_argument("--no-tunnel", action="store_true", help="Disable automatic tunnel when behind NAT")
    p_start.set_defaults(func=cmd_start)

    # stop
    p_stop = subparsers.add_parser("stop", help="Stop server or app")
    p_stop.add_argument("name", nargs="?", help="App name/ID to stop (omit for server)")
    p_stop.set_defaults(func=cmd_stop)

    # status
    p_status = subparsers.add_parser("status", help="Show status")
    p_status.set_defaults(func=cmd_status)

    # add
    p_add = subparsers.add_parser("add", help="Publish a file or folder")
    p_add.add_argument("path", help="File or folder to publish")
    p_add.add_argument("--name", "-n", help="Human-readable name for URL (slug)")
    p_add.add_argument("--password", "-p", nargs="?", const=True, default=None,
                       help="Protect with password (auto-generate if no value given)")
    p_add.add_argument("--desc", "-d", help="Description for listing")
    p_add.add_argument("--run", "-r", help="Command to run (makes this an app)")
    p_add.add_argument("--port", type=int, help="Port the app listens on (required with --run)")
    p_add.add_argument(
        "--auth", nargs="?", const="basic", default=None,
        help="Basic auth proxy for apps. 'basic' auto-gens drop:<12char>. "
             "'basic:user:pass' for explicit creds. Apps only."
    )
    p_add.add_argument(
        "--public", action="store_true",
        help="Explicit opt-out from default auth (page/app stays public)."
    )
    p_add.set_defaults(func=cmd_add)

    # list
    p_list = subparsers.add_parser("list", help="List published pages")
    p_list.add_argument("--all", "-a", action="store_true", help="Show all pages (not just current directory)")
    p_list.set_defaults(func=cmd_list)

    # remove
    p_remove = subparsers.add_parser("remove", help="Remove a page")
    p_remove.add_argument("id", help="Page ID (or prefix)")
    p_remove.set_defaults(func=cmd_remove)

    # cleanup
    p_cleanup = subparsers.add_parser("cleanup", help="Remove entries with deleted sources")
    p_cleanup.set_defaults(func=cmd_cleanup)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
