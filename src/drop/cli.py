"""drop CLI — argparse + dispatch + formatting.

All business logic lives in lifecycle/ and storage/runtime modules.
This file translates user input into Page/StartResult and prints output.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from . import config, runtime, storage, utils
from .auth import (generate_auth_creds, generate_password, hash_password)
from .lifecycle import app as app_lifecycle, server as server_lifecycle
from .lifecycle.tunnel import _URL_PATTERN
from .manifest import MANIFEST_FILE, load_manifest


def _drop_base() -> Path:
    return Path(os.environ.get("DROP_HOME") or Path.home() / ".drop")


def _server_port() -> str | None:
    """The static server's real local port (systemd.env DROP_PORT, or the port file)."""
    base = _drop_base()
    env_file = base / "systemd.env"
    if env_file.exists():
        try:
            for line in env_file.read_text().splitlines():
                if line.startswith("DROP_PORT="):
                    p = line.split("=", 1)[1].strip()
                    if p:
                        return p
        except OSError:
            pass
    port_f = base / "port"
    if port_f.exists():
        try:
            p = port_f.read_text().strip()
            if p:
                return p
        except OSError:
            pass
    return None


def _resolve_public_base(explicit: str | None = None) -> tuple[str, bool, str]:
    """Resolve the static server's public base URL.

    Returns (base_url, shareable, source). `shareable` is False only for a
    loopback address (reachable, but not from outside — caller should point the
    user at their tunnel/tailnet).

    Priority (authoritative first, guesses last):
      explicit → $DROP_PUBLIC_URL → ~/.drop/base_url → tunnel.json (drop start)
      → static.tunnel.log → host/port files → loopback on the REAL port.

    We never guess a public `IP:8080` anymore — on a systemd deployment that
    address is whatever else owns :8080 (e.g. a legacy web server), so handing
    it out gives a dead/foreign link. When nothing authoritative is known we
    fall back to loopback and flag it not-shareable.
    """
    if explicit:
        return (explicit.rstrip("/"), True, "--base-url")
    base = _drop_base()

    env_url = os.environ.get("DROP_PUBLIC_URL", "").strip()
    if env_url:
        return (env_url.rstrip("/"), True, "$DROP_PUBLIC_URL")

    if config.PUBLIC_URL_FILE.exists():
        try:
            url = config.PUBLIC_URL_FILE.read_text().strip()
            if url:
                return (url.rstrip("/"), True, "base_url file")
        except OSError:
            pass

    tunnel_json = base / "tunnel.json"
    if tunnel_json.exists():
        try:
            url = json.loads(tunnel_json.read_text()).get("url", "").strip()
            if url:
                return (url.rstrip("/"), True, "tunnel.json")
        except (OSError, json.JSONDecodeError):
            pass

    # systemd deployment (drop-install-env) never runs `drop start`, so the
    # tunnel URL lives in the tunnel log. NOTE: this log can be stale (a quick
    # tunnel that cloudflare has since reaped) — `drop status` health-probes it.
    tunnel_log = base / "logs" / "static.tunnel.log"
    if tunnel_log.exists():
        try:
            urls = _URL_PATTERN.findall(tunnel_log.read_text(errors="replace"))
            if urls:
                # LAST match — the freshest tunnel. The log accumulates across
                # restarts, so .search() (first match) returns a long-dead URL.
                return (urls[-1].rstrip("/"), True, "static.tunnel.log")
        except OSError:
            pass

    host_f, port_f = base / "host", base / "port"
    if host_f.exists() and port_f.exists():
        try:
            host = host_f.read_text().strip()
            port = port_f.read_text().strip()
            if host and port:
                shareable = host not in ("127.0.0.1", "localhost")
                return (f"http://{host}:{port}", shareable, "host/port files")
        except OSError:
            pass

    port = _server_port() or str(config.DEFAULT_SERVER_PORT)
    return (f"http://127.0.0.1:{port}", False, "loopback (no public URL set)")


def _static_base_url(explicit: str | None = None) -> tuple[str, bool]:
    """(base_url, shareable) — thin wrapper over _resolve_public_base for callers
    that don't need the resolution source."""
    url, shareable, _src = _resolve_public_base(explicit)
    return (url, shareable)


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
    if getattr(args, 'allow_side_door', False) and not is_app:
        return _err("--allow-side-door only applies to apps (use with --run)")

    # Directory requires manifest (for static only)
    if source.is_dir() and not is_app:
        m = load_manifest(source)
        if m is None:
            return _err(
                f"Directory requires {MANIFEST_FILE} manifest",
                hint=f"Create {source / MANIFEST_FILE} with allowed file patterns.",
            )

    # Dedupe re-publishes of the same STATIC source: return the existing page
    # instead of spawning a new id/password each time (the server serves the file
    # live, so the same path is always the same link). --new forces a fresh one.
    # Apps are NOT deduped — same file with a different --run/--port is a distinct
    # registration; accidental collisions are caught by the name-uniqueness guard.
    if not is_app and not getattr(args, "new", False):
        existing = storage.find_by_source(source, "static")
        if existing:
            pid, pg = existing
            base, _sh = _static_base_url(getattr(args, "base_url", None))
            suffix = f"/p/{pid}/{pg.name}/" if pg.name else f"/p/{pid}/"
            print(f"Already published (same source): {base}{suffix}")
            lock = "" if pg.password_hash else "  (public)"
            print(f"  Same path → same link, content served live.{lock}")
            print(f"  Rotate the password: 'drop update {pid[:8]} --password'. "
                  f"Fresh page: 'drop add --new'.")
            return 0

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
            raw_pw = (args.password if args.password is not True
                      else generate_password(config.STATIC_PASSWORD_LENGTH))
            plaintext_pw = raw_pw
            password_hash = hash_password(raw_pw)
        else:
            raw_pw = generate_password(config.STATIC_PASSWORD_LENGTH)
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
        allow_side_door=bool(getattr(args, 'allow_side_door', False)),
        rewrite_host=bool(getattr(args, 'rewrite_host', False)),
    )
    try:
        storage.add_page(page)
    except ValueError as e:
        return _err(str(e))

    if is_app:
        host = utils.detect_ip()
        url = f"http://{host}:{args.port}/"
        print(f"App registered: {url}")
        if auth_creds_shown:
            user, raw_pw = auth_creds_shown
            print(f"  Auth: basic ({user} / {raw_pw})")
        elif args.public:
            print("  (public — no auth)")
        print(f"Run 'drop start {args.name or page_id}' to start the app")
    else:
        base, shareable = _static_base_url(getattr(args, "base_url", None))
        suffix = f"/p/{page_id}/{args.name}/" if args.name else f"/p/{page_id}/"
        url = f"{base}{suffix}"
        print(f"Published: {url}")
        if not shareable:
            print("  Note: this is the server's local address. Share via your "
                  "tunnel/tailnet URL, or pass --base-url.", file=sys.stderr)
        if plaintext_pw:
            print(f"Password: {plaintext_pw}")
        elif args.public:
            print("  (public — no password)")

    return 0


def cmd_remove(args) -> int:
    matches = storage.matching_page_ids(args.id)
    if len(matches) > 1:
        return _err(
            f"'{args.id}' is ambiguous — matches {len(matches)} pages",
            hint="Use a longer prefix or the full id: "
                 + ", ".join(m[:8] for m in matches),
        )
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
    static_base, _shareable = _static_base_url()
    _host_cache: list[str] = []

    def _host() -> str:
        # Lazy: detect_ip() hits the network (~2s). Only pay it when an app row
        # actually needs a fallback URL, so `drop list` stays fast/offline-safe.
        if not _host_cache:
            _host_cache.append(utils.detect_ip())
        return _host_cache[0]

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
                broken = []
                if page.auth and rt.proxy_pid > 0 and not rt.is_proxy_alive():
                    broken.append("proxy")
                if rt.tunnel_pid > 0 and not rt.is_tunnel_alive():
                    broken.append("tunnel")
                status = f"[degraded: {'+'.join(broken)} down]" if broken else "[running]"
            elif rt.app_pid > 0:
                status = "[crashed]"
            else:
                status = "[stopped]"
            auth_tag = " [auth]" if page.auth else ""
            url = rt.tunnel_url or f"http://{_host()}:{page.port}/"
            lock = "" if page.auth else " (public)"
            print(f"{pid[:8]}  [app] {status}{auth_tag}  {url}{lock}")
        else:
            base = rt.tunnel_url or static_base
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


def cmd_index_password(args) -> int:
    """Set (or clear) the dashboard/index password.

    The server's `/` index lists every published page. It stays disabled until
    an index password is set here; without one, `/` refuses to enumerate.
    """
    base = _drop_base()
    base.mkdir(parents=True, exist_ok=True)
    hash_file = base / "index.hash"
    if args.clear:
        if hash_file.exists():
            hash_file.unlink()
        print("Index password cleared — the dashboard listing is now disabled.")
        return 0
    raw_pw = args.password or generate_password(config.STATIC_PASSWORD_LENGTH)
    hash_file.write_text(hash_password(raw_pw))
    print(f"Index password set: {raw_pw}")
    print("The dashboard at / now requires this password.")
    return 0


def _probe_url(url: str, timeout: float = 3.0) -> tuple[bool, str]:
    """Best-effort liveness probe. Returns (alive, detail). A cloudflare quick
    tunnel whose process is alive but whose tunnel cloudflare has reaped answers
    with a connection error / 5xx — that's how we surface a 'zombie' tunnel."""
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return (True, f"HTTP {r.status}")
    except urllib.error.HTTPError as e:
        # Reached the origin (even a 401/404 means the tunnel is live).
        return (True, f"HTTP {e.code}")
    except Exception as e:  # URLError, timeout, connection reset, DNS…
        return (False, f"unreachable ({type(e).__name__})")


def _tailnet_url(port: str | None) -> str | None:
    """Best-effort tailnet URL from `tailscale serve status`. Returns the mapping
    whose backend is our port, else the first served URL. None if tailscale/serve
    isn't set up.

    `tailscale serve status` puts the URL and its proxy target on ADJACENT lines:
        https://host.ts.net:9443 (tailnet only)
        |-- / proxy http://127.0.0.1:8090
    so we pair each header URL with the 127.0.0.1:<port> on the lines beneath it.
    """
    import re as _re
    import subprocess
    try:
        out = subprocess.run(["tailscale", "serve", "status"],
                             capture_output=True, text=True, timeout=4).stdout
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    header = _re.compile(r"^(https://[^\s|]+\.ts\.net(?::\d+)?)\b")
    first = None
    current = None
    for line in out.splitlines():
        h = header.match(line.strip())
        if h:
            current = h.group(1).rstrip("/")
            if first is None:
                first = current
            continue
        if port and current and _re.search(rf"127\.0\.0\.1:{port}\b", line):
            return current
    return first


def cmd_status(args) -> int:
    base = _drop_base()
    pages = storage.list_pages()
    port = _server_port()

    # Static server liveness
    srv_pid_f = base / "server.pid"
    srv_state = "not running"
    if srv_pid_f.exists():
        try:
            pid = int(srv_pid_f.read_text().strip())
            os.kill(pid, 0)
            srv_state = f"running (pid {pid})"
        except (OSError, ValueError):
            srv_state = "stale pid (not running)"
    elif port:
        srv_state = "systemd (see: systemctl --user status drop)"
    print(f"Server:   127.0.0.1:{port or config.DEFAULT_SERVER_PORT}  [{srv_state}]")

    # Resolved public base URL (what `drop add` will actually print)
    pub, shareable, src = _resolve_public_base()
    tag = "" if shareable else "  ⚠ loopback — not shareable"
    print(f"Public:   {pub}  [source: {src}]{tag}")

    # Tailnet (stable path)
    tnet = _tailnet_url(port)
    if tnet:
        print(f"Tailnet:  {tnet}")

    # Cloudflare tunnel + health (reveals the '20-day zombie': process up, tunnel dead)
    tunnel_log = base / "logs" / "static.tunnel.log"
    tunnel_url = None
    if (base / "tunnel.json").exists():
        try:
            tunnel_url = json.loads((base / "tunnel.json").read_text()).get("url", "").strip() or None
        except (OSError, json.JSONDecodeError):
            pass
    if not tunnel_url and tunnel_log.exists():
        try:
            urls = _URL_PATTERN.findall(tunnel_log.read_text(errors="replace"))
            tunnel_url = urls[-1] if urls else None  # freshest, not first
        except OSError:
            pass
    if tunnel_url:
        alive, detail = _probe_url(tunnel_url)
        verdict = detail if alive else f"DEAD ({detail}) — restart the tunnel"
        print(f"Tunnel:   {tunnel_url}  [{verdict}]")
    else:
        print("Tunnel:   none (cloudflare quick-tunnel not recorded)")

    print(f"Pages:    {len(pages)} registered")
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


def cmd_update(args) -> int:
    page = storage.get_page(args.id)
    if page is None:
        return _err(f"page '{args.id}' not found")
    changed, new_pw = [], None
    if args.desc is not None:
        page.description = args.desc
        changed.append("desc")
    if args.name is not None:
        page.name = args.name
        changed.append("name")
    if args.password is not None:
        if page.type != "static":
            return _err("--password updates static-page passwords "
                        "(apps set auth via --auth at add time)")
        raw = (args.password if args.password is not True
               else generate_password(config.STATIC_PASSWORD_LENGTH))
        page.password_hash = hash_password(raw)
        new_pw = raw
        changed.append("password")
    if not changed:
        return _err("nothing to update", hint="pass --desc, --name, and/or --password")
    try:
        storage.update_page(page)
    except ValueError as e:
        return _err(str(e))
    if page.type == "static":
        base, _sh = _static_base_url()
        suffix = f"/p/{page.page_id}/{page.name}/" if page.name else f"/p/{page.page_id}/"
        print(f"Updated ({', '.join(changed)}): {base}{suffix}")
    else:
        print(f"Updated ({', '.join(changed)}): {page.page_id[:8]}")
    if new_pw:
        print(f"Password: {new_pw}")
    return 0


def _tunnel_recorded(base: Path) -> tuple[str | None, int | None]:
    """(url, pid) of the recorded static tunnel — tunnel.json first, else the
    freshest URL in the log (pid unknown)."""
    tj = base / "tunnel.json"
    if tj.exists():
        try:
            d = json.loads(tj.read_text())
            return (d.get("url") or None, d.get("pid"))
        except (OSError, json.JSONDecodeError):
            pass
    log_file = base / "logs" / "static.tunnel.log"
    if log_file.exists():
        try:
            urls = _URL_PATTERN.findall(log_file.read_text(errors="replace"))
            return (urls[-1] if urls else None, None)
        except OSError:
            pass
    return (None, None)


def cmd_tunnel(args) -> int:
    """Self-heal the static server's cloudflare tunnel: restart it, or ensure it's
    alive (probe → restart only if dead). Fixes the 'zombie' (process up, quick-
    tunnel reaped by cloudflare → dead URL)."""
    from .lifecycle.tunnel import start_tunnel, stop_tunnel
    base = _drop_base()
    port = _server_port() or str(config.DEFAULT_SERVER_PORT)
    log_file = base / "logs" / "static.tunnel.log"
    tunnel_json = base / "tunnel.json"

    if args.action == "ensure":
        url, _pid = _tunnel_recorded(base)
        if url:
            alive, detail = _probe_url(url)
            if alive:
                print(f"Tunnel healthy: {url}  [{detail}]")
                return 0
        print("Tunnel dead/absent — restarting…")

    # (restart, or ensure that fell through) — kill the recorded tunnel, rotate
    # the log so only the fresh URL remains, then spawn a new quick tunnel.
    _url, pid = _tunnel_recorded(base)
    if pid:
        stop_tunnel(int(pid))
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text("")
    except OSError:
        pass
    result = start_tunnel(int(port), log_file)
    if result is None:
        return _err("could not start cloudflared tunnel",
                    hint="is cloudflared installed? run drop-install-env")
    url, pid = result
    try:
        tunnel_json.write_text(json.dumps({"url": url, "pid": pid}))
    except OSError:
        pass
    print(f"Tunnel up: {url}")
    print("  (cloudflare quick-tunnels get a NEW random URL each restart — for a "
          "stable address prefer tailnet / set ~/.drop/base_url)")
    return 0


def cmd_doctor(args) -> int:
    import shutil
    import subprocess
    ok = True

    # 1. Multiple `drop` on PATH — the "old version silently wins" trap (short
    #    passwords, missing features). PATH order decides; first wins.
    found, seen = [], set()
    for d in os.environ.get("PATH", "").split(os.pathsep):
        cand = Path(d) / "drop"
        if cand.exists() and str(cand) not in seen:
            seen.add(str(cand))
            found.append(str(cand))
    if len(found) > 1:
        ok = False
        print(f"⚠ {len(found)} 'drop' on PATH — the FIRST wins, may be stale:")
        for f in found:
            print(f"    {f}")
        print("    → put the newest plugin bin first, or remove old cache dirs.")
    else:
        print(f"✓ drop on PATH: {found[0] if found else '(none found)'}")

    # 2. cloudflared
    cf = bool(shutil.which("cloudflared")) or (Path.home() / ".drop/bin/cloudflared").exists()
    print(f"{'✓' if cf else '⚠'} cloudflared: "
          f"{'present' if cf else 'missing — run drop-install-env for tunnels'}")

    # 3. systemd static server (Linux)
    try:
        st = subprocess.run(["systemctl", "--user", "is-active", "drop"],
                            capture_output=True, text=True, timeout=4).stdout.strip()
        if st:
            print(f"{'✓' if st == 'active' else '⚠'} drop.service: {st}")
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass

    # 4. Public URL + tunnel health
    pub, shareable, src = _resolve_public_base()
    print(f"{'✓' if shareable else '⚠'} public URL: {pub}  [{src}]")
    url, _pid = _tunnel_recorded(_drop_base())
    if url:
        alive, detail = _probe_url(url)
        print(f"{'✓' if alive else '⚠'} tunnel: {url}  "
              f"[{detail if alive else 'DEAD — drop tunnel restart'}]")
        ok = ok and alive

    return 0 if ok else 1


# ---- main ----

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Drop any file, app, or prototype to your human",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    try:
        from importlib.metadata import version as _pkg_version
        _ver = _pkg_version("agent-instant-drop")
    except Exception:
        _ver = "unknown"
    parser.add_argument("--version", action="version", version=f"%(prog)s {_ver}")
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
    p_add.add_argument("--allow-side-door", action="store_true",
                       help="Apps only: start even if the app binds a public "
                            "interface (auth then protects the tunnel only).")
    p_add.add_argument("--base-url",
                       help="Override the printed base URL (e.g. your tunnel/tailnet URL).")
    p_add.add_argument("--new", action="store_true",
                       help="Force a fresh page even if this source is already "
                            "published (default: reuse the existing link).")
    p_add.set_defaults(func=cmd_add)

    p_update = sub.add_parser("update",
                              help="Update an existing page in place (same URL)")
    p_update.add_argument("id")
    p_update.add_argument("--name", "-n")
    p_update.add_argument("--desc", "-d")
    p_update.add_argument("--password", "-p", nargs="?", const=True, default=None,
                          help="Rotate the static-page password (auto-gen if no value).")
    p_update.set_defaults(func=cmd_update)

    p_tunnel = sub.add_parser("tunnel",
                              help="Restart/ensure the static server's cloudflare tunnel")
    p_tunnel.add_argument("action", nargs="?", choices=["restart", "ensure"],
                          default="ensure",
                          help="'ensure' restarts only if dead (default); 'restart' always.")
    p_tunnel.set_defaults(func=cmd_tunnel)

    p_doctor = sub.add_parser("doctor", help="Diagnose PATH/version, cloudflared, URL, tunnel health")
    p_doctor.set_defaults(func=cmd_doctor)

    p_idx = sub.add_parser("index-password",
                           help="Set/clear the dashboard (/) password")
    p_idx.add_argument("password", nargs="?",
                       help="Password to set (auto-generated if omitted).")
    p_idx.add_argument("--clear", action="store_true",
                       help="Remove the index password and disable the listing.")
    p_idx.set_defaults(func=cmd_index_password)

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
