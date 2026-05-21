"""drop CLI — argparse + dispatch + formatting.

All business logic lives in lifecycle/ and storage/runtime modules.
This file translates user input into Page/StartResult and prints output.
"""

import argparse
import sys
from pathlib import Path

from . import config, runtime, storage, utils
from .auth import (generate_auth_creds, generate_password, hash_password)
from .lifecycle import app as app_lifecycle, server as server_lifecycle
from .manifest import MANIFEST_FILE, load_manifest


__doc__ = """\
drop - agent instant drop.

Drop any file, app, or prototype to your human. Password-protected by default.

Examples:
    drop start                                          # Start static server
    drop add ./report.html                              # Publish (auto-password)
    drop add ./report.html --public                     # Public link
    drop add ./bin --run "..." --port N                 # App + auto basic auth
    drop list                                           # List from cwd
    drop remove abc123                                  # Remove
    drop stop                                           # Stop server
"""


# ---- helpers ----

def _err(msg: str, hint: str | None = None) -> int:
    print(f"Error: {msg}", file=sys.stderr)
    if hint:
        print(f"  Hint: {hint}", file=sys.stderr)
    return 1


def _parse_auth_spec(spec: str) -> tuple[str, str | None, str | None]:
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


def _print_start_result(result) -> int:
    if result.error:
        return _err(result.error, hint=result.hint)
    for w in result.warnings:
        print(f"Warning: {w}", file=sys.stderr)
    print(f"App started: {result.url}")
    if result.creds:
        user, pw = result.creds
        print(f"  Auth: basic ({user} / {pw})")
    return 0


# ---- commands ----

def cmd_add(args) -> int:
    source = Path(args.path).resolve()
    if not source.exists():
        return _err(f"{args.path} not found")

    is_app = bool(args.run)
    if is_app and not args.port:
        return _err("--port is required when using --run")
    if args.port and not args.run:
        return _err("--run is required when using --port")

    # Flag validation
    if args.auth is not None and not is_app:
        return _err("--auth only applies to apps (use with --run)")
    if args.public and args.password is not None:
        return _err("cannot combine --password with --public")
    if args.public and args.auth is not None:
        return _err("cannot combine --auth with --public")
    if args.auth is not None and args.password is not None:
        return _err("cannot combine --auth (apps) with --password (static)")
    if getattr(args, 'rewrite_host', False):
        if not is_app:
            return _err("--rewrite-host only applies to apps")
        if args.public:
            return _err("--rewrite-host requires the proxy (cannot combine with --public)")

    # Directory requires manifest (for static only)
    if source.is_dir() and not is_app:
        m = load_manifest(source)
        if m is None:
            return _err(
                f"Directory requires {MANIFEST_FILE} manifest",
                hint=f"Create {source / MANIFEST_FILE} with allowed file patterns.",
            )

    page_id = utils.generate_page_id()

    # Resolve auth/password
    auth_block = None
    auth_creds_shown = None
    password_hash = ""
    plaintext_pw = None
    if is_app:
        if args.public:
            pass
        elif args.auth is not None:
            try:
                scheme, user, raw_pw = _parse_auth_spec(args.auth)
            except ValueError as e:
                return _err(str(e))
            if user is None:
                user, raw_pw = generate_auth_creds()
            auth_block = storage.AuthConfig(scheme=scheme, user=user,
                                             password_hash=hash_password(raw_pw))
            auth_creds_shown = (user, raw_pw)
        else:
            user, raw_pw = generate_auth_creds()
            auth_block = storage.AuthConfig(scheme="basic", user=user,
                                             password_hash=hash_password(raw_pw))
            auth_creds_shown = (user, raw_pw)
    else:
        if args.public:
            pass
        elif args.password is not None:
            raw_pw = args.password if args.password is not True else generate_password()
            plaintext_pw = raw_pw
            password_hash = hash_password(raw_pw)
        else:
            raw_pw = generate_password()
            plaintext_pw = raw_pw
            password_hash = hash_password(raw_pw)

    page = storage.Page(
        page_id=page_id,
        source=source,
        type="app" if is_app else "static",
        name=args.name or "",
        description=args.desc or "",
        is_public=bool(args.public),
        password_hash=password_hash,
        run_cmd=args.run or "",
        port=args.port or 0,
        auth=auth_block,
        rewrite_host=bool(getattr(args, 'rewrite_host', False)),
    )
    try:
        storage.add_page(page)
    except ValueError as e:
        return _err(str(e))

    server_port = config.DEFAULT_SERVER_PORT
    host = utils.detect_ip()

    if is_app:
        url = f"http://{host}:{args.port}/"
        print(f"App registered: {url}")
        if auth_creds_shown:
            user, raw_pw = auth_creds_shown
            print(f"  Auth: basic ({user} / {raw_pw})")
        elif args.public:
            print("  (public — no auth)")
        print(f"Run 'drop start {args.name or page_id}' to start the app")
    else:
        if args.name:
            url = f"http://{host}:{server_port}/p/{page_id}/{args.name}/"
        else:
            url = f"http://{host}:{server_port}/p/{page_id}/"
        print(f"Published: {url}")
        if plaintext_pw:
            print(f"Password: {plaintext_pw}")
        elif args.public:
            print("  (public — no password)")

    return 0


def cmd_remove(args) -> int:
    if storage.remove_page(args.id):
        print(f"Removed: {args.id}")
        return 0
    return _err(f"page {args.id} not found")


def cmd_list(args) -> int:
    pages = storage.list_pages()
    if not pages:
        print("No pages published")
        return 0
    cwd = Path.cwd().resolve()
    server_port = config.DEFAULT_SERVER_PORT
    host = utils.detect_ip()
    for pid, page in pages.items():
        if not args.all:
            try:
                if not page.source.is_relative_to(cwd):
                    continue
            except (ValueError, OSError):
                continue
        rt = runtime.get_runtime(pid)
        # Status indicator for apps
        if page.type == "app":
            if rt.is_app_alive():
                status = "[running]"
            elif rt.app_pid > 0:
                status = "[crashed]"
            else:
                status = "[stopped]"
            auth_tag = " [auth]" if page.auth else ""
            url = rt.tunnel_url or f"http://{host}:{page.port}/"
            lock = "" if page.auth else " (public)"
            print(f"{pid[:8]}  [app] {status}{auth_tag}  {url}{lock}")
        else:
            if rt.tunnel_url:
                base = rt.tunnel_url
            else:
                base = f"http://{host}:{server_port}"
            if page.name:
                url = f"{base}/p/{pid}/{page.name}/"
            else:
                url = f"{base}/p/{pid}/"
            lock = "" if page.password_hash else " (public)"
            print(f"{pid[:8]}    {url}{lock}")
        if page.description:
            print(f"  {page.description}")
        print(f"  Source: {page.source}")
        if page.type == "app":
            print(f"  Run: {page.run_cmd}")
    return 0


def cmd_status(args) -> int:
    pages = storage.list_pages()
    print(f"Pages registered: {len(pages)}")
    return 0


def cmd_cleanup(args) -> int:
    pages = storage.list_pages()
    removed = []
    for pid, page in list(pages.items()):
        if not page.source.exists():
            removed.append(pid)
            storage.remove_page(pid)
    if removed:
        for pid in removed:
            print(f"Removed: {pid} (source deleted)")
        print(f"Cleaned {len(removed)} stale entries")
    else:
        print("No stale entries found")
    return 0


def cmd_logs(args) -> int:
    page = storage.get_page(args.name)
    if not page:
        return _err(f"'{args.name}' not found")

    # Determine which log to read
    if args.proxy:
        role = "proxy"
    elif args.tunnel:
        role = "tunnel"
    else:
        role = "app"

    import os
    home_env = os.environ.get("DROP_HOME")
    base = Path(home_env) if home_env else Path.home() / ".drop"
    log_file = base / "logs" / f"{page.page_id}.{role}.log"

    if not log_file.exists():
        return _err(f"no {role} log for '{args.name}' (file: {log_file})")

    if args.follow:
        # Tail -f equivalent
        import time
        with open(log_file, "r", errors="replace") as f:
            f.seek(0, 2)  # end
            try:
                while True:
                    line = f.readline()
                    if line:
                        sys.stdout.write(line)
                        sys.stdout.flush()
                    else:
                        time.sleep(0.2)
            except KeyboardInterrupt:
                return 0
    else:
        print(log_file.read_text(errors="replace"))
        return 0


def cmd_start(args) -> int:
    if args.name:
        page = storage.get_page(args.name)
        if not page:
            return _err(f"'{args.name}' not found")
        if page.type != "app":
            return _err(f"'{args.name}' is not an app (use 'drop start' for server)")
        result = app_lifecycle.start_app(
            page,
            auth_insecure=getattr(args, 'auth_insecure', False),
            no_tunnel=getattr(args, 'no_tunnel', False),
        )
        return _print_start_result(result)
    # No name → start static server
    port = args.port or config.DEFAULT_SERVER_PORT
    host = args.host or utils.detect_ip()
    result = server_lifecycle.start_server(
        port=port, host=host, no_tunnel=getattr(args, 'no_tunnel', False),
    )
    if result.error:
        return _err(result.error, hint=result.hint)
    print(f"Server started: {result.url}")
    return 0


def cmd_stop(args) -> int:
    if args.name:
        page = storage.get_page(args.name)
        if not page:
            return _err(f"'{args.name}' not found")
        if page.type != "app":
            return _err(f"'{args.name}' is not an app")
        app_lifecycle.stop_app(page)
        print(f"Stopped: {args.name}")
        return 0
    server_lifecycle.stop_server()
    print("Server stopped")
    return 0


# ---- main ----

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Drop any file, app, or prototype to your human",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="Start server or app")
    p_start.add_argument("name", nargs="?")
    p_start.add_argument("--port", "-p", type=int)
    p_start.add_argument("--host")
    p_start.add_argument("--no-tunnel", action="store_true")
    p_start.add_argument("--auth-insecure", action="store_true",
                         help="Allow cleartext basic auth without tunnel")
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop", help="Stop server or app")
    p_stop.add_argument("name", nargs="?")
    p_stop.set_defaults(func=cmd_stop)

    p_status = sub.add_parser("status", help="Show status")
    p_status.set_defaults(func=cmd_status)

    p_add = sub.add_parser("add", help="Publish file/folder/app (password by default)")
    p_add.add_argument("path")
    p_add.add_argument("--name", "-n")
    p_add.add_argument("--password", "-p", nargs="?", const=True, default=None)
    p_add.add_argument("--desc", "-d")
    p_add.add_argument("--run", "-r")
    p_add.add_argument("--port", type=int)
    p_add.add_argument("--auth", nargs="?", const="basic", default=None,
                       help="Basic auth for apps. 'basic' auto-gens, "
                            "'basic:user:pass' explicit. Apps only.")
    p_add.add_argument("--public", action="store_true",
                       help="Explicit opt-out from default auth.")
    p_add.add_argument("--rewrite-host", action="store_true",
                       help="Proxy rewrites http://localhost:<port> in text bodies.")
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="List published pages")
    p_list.add_argument("--all", "-a", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_remove = sub.add_parser("remove", help="Remove a page")
    p_remove.add_argument("id")
    p_remove.set_defaults(func=cmd_remove)

    p_cleanup = sub.add_parser("cleanup", help="Remove entries with deleted sources")
    p_cleanup.set_defaults(func=cmd_cleanup)

    p_logs = sub.add_parser("logs", help="Tail or print a page's log")
    p_logs.add_argument("name")
    p_logs.add_argument("--follow", "-f", action="store_true",
                        help="Tail -f mode (follow new lines)")
    p_logs.add_argument("--proxy", action="store_true", help="Show proxy log")
    p_logs.add_argument("--tunnel", action="store_true", help="Show tunnel log")
    p_logs.set_defaults(func=cmd_logs)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
