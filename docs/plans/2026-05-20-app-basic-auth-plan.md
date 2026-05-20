# App Basic Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--auth` flag for drop apps (basic-auth proxy in front of user app), flip default to password-protected for everything, with `--public` opt-out and `--auth-insecure` cleartext override.

**Architecture:** stdlib HTTP proxy (`src/drop/proxy.py`, ~50 LOC) runs as subprocess between user's app and cloudflared tunnel. When `--auth` is set, drop spawns app → spawns proxy on auto-allocated port (127.0.0.1 if tunnel will be used, 0.0.0.0 only under `--auth-insecure`) → starts tunnel pointing at proxy port. Tunnel REQUIRED when `--auth` is set (HTTPS termination needed for basic auth); `--auth-insecure` overrides with cleartext warning. Per-app tunnel watchdog added for parity with server tunnel.

**Tech Stack:** Python 3.10+ stdlib (`http.server`, `urllib.request`, `socket`, `subprocess`), no new dependencies.

**Reference:** Full spec at `docs/2026-05-20-app-basic-auth-design.md`.

---

## Task 1: Add `generate_auth_creds()` helper to utils.py

**Files:**
- Modify: `src/drop/utils.py`

- [ ] **Step 1: Add helper after existing `generate_password()`**

Find `generate_password()` in `src/drop/utils.py` and add immediately after it:

```python
def generate_auth_creds() -> tuple[str, str]:
    """Generate (user, password) for basic auth. User is fixed 'drop'."""
    return ("drop", generate_password(12))
```

If `generate_password()` does not take a length argument, modify its signature to `generate_password(length: int = 6) -> str` (default preserves existing callsites).

- [ ] **Step 2: Smoke test**

```bash
cd /home/superbereza/dev/agent-instant-drop
PYTHONPATH=src python -c "from drop.utils import generate_auth_creds; u, p = generate_auth_creds(); print(u, p, len(p))"
```

Expected: `drop <12-char-string> 12`

- [ ] **Step 3: Commit**

```bash
git add src/drop/utils.py
git commit -m "feat: add generate_auth_creds helper"
```

---

## Task 2: Extend `PageInfo` and `add_page` signature in storage.py

**Files:**
- Modify: `src/drop/storage.py`

- [ ] **Step 1: Extend `PageInfo` TypedDict**

In `src/drop/storage.py`, modify the `PageInfo` class:

```python
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
    # NEW (app basic auth)
    auth: dict | None  # {"scheme": "basic", "user": "drop", "password_hash": "..."} or None
    public: bool  # True if user explicitly passed --public
    proxy_pid: int  # Proxy process PID (0 if no proxy)
    proxy_port: int  # Proxy listen port (0 if no proxy)
```

- [ ] **Step 2: Update `add_page()` signature**

Replace the `add_page` function body with:

```python
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
    """Add a page to registry."""
    pages = load_pages()
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
```

- [ ] **Step 3: Smoke test**

```bash
cd /home/superbereza/dev/agent-instant-drop
PYTHONPATH=src python -c "
from pathlib import Path
from drop import storage
storage.add_page('test_auth_id', Path('/tmp'), '', name='t', page_type='app', run_cmd='x', port=1, auth={'scheme':'basic','user':'drop','password_hash':'h'}, public=False)
p = storage.get_page('test_auth_id')
print(p['auth'], p['public'], p['proxy_pid'], p['proxy_port'])
storage.remove_page('test_auth_id')
"
```

Expected: `{'scheme': 'basic', 'user': 'drop', 'password_hash': 'h'} False 0 0`

- [ ] **Step 4: Commit**

```bash
git add src/drop/storage.py
git commit -m "feat: extend PageInfo with auth/public/proxy fields"
```

---

## Task 3: Add `update_page_proxy()` and `clear_page_runtime()` helpers

**Files:**
- Modify: `src/drop/storage.py`

- [ ] **Step 1: Add helpers after `update_page_tunnel()`**

Find the `update_page_tunnel` function in `src/drop/storage.py`. Add immediately after it:

```python
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
```

- [ ] **Step 2: Smoke test**

```bash
cd /home/superbereza/dev/agent-instant-drop
PYTHONPATH=src python -c "
from pathlib import Path
from drop import storage
storage.add_page('runtime_test', Path('/tmp'), '', name='rt', page_type='app', run_cmd='x', port=1)
storage.update_page_pid('runtime_test', 9999)
storage.update_page_proxy('runtime_test', 8888, 5555)
storage.update_page_tunnel('runtime_test', 'https://x/', 7777)
storage.clear_page_runtime('runtime_test')
p = storage.get_page('runtime_test')
assert p['pid'] == 0 and p['proxy_pid'] == 0 and p['proxy_port'] == 0 and p['tunnel_pid'] == 0 and p['tunnel_url'] == ''
print('OK')
storage.remove_page('runtime_test')
"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/drop/storage.py
git commit -m "feat: add update_page_proxy and clear_page_runtime helpers"
```

---

## Task 4: Create `src/drop/proxy.py`

**Files:**
- Create: `src/drop/proxy.py`

- [ ] **Step 1: Write the full module**

Create `src/drop/proxy.py` with this exact content:

```python
"""Basic-auth reverse proxy for drop apps.

Runs as a subprocess in front of a user app, terminating HTTP basic auth
and forwarding to 127.0.0.1:<app_port>. V1 = sync HTTP request/response
only; WebSocket / Upgrade requests are rejected with 501.
"""

import argparse
import base64
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import storage
from .utils import verify_password


class ProxyHandler(BaseHTTPRequestHandler):
    APP_PORT: int
    AUTH: dict  # {"scheme": "basic", "user": str, "password_hash": str}

    def _check_auth(self) -> bool:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
        except Exception:
            return False
        user, sep, pw = decoded.partition(":")
        if not sep:
            return False
        return user == self.AUTH["user"] and verify_password(pw, self.AUTH["password_hash"])

    def _reject_upgrade(self) -> bool:
        connection = (self.headers.get("Connection", "") or "").lower()
        if "upgrade" in connection:
            self.send_response(501)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(
                b"WebSocket/Upgrade not supported by drop V1 proxy.\n"
                b"App can still be reached locally on its own port.\n"
            )
            return True
        return False

    def _proxy(self, method: str) -> None:
        if self._reject_upgrade():
            return
        if not self._check_auth():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="drop"')
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Authentication required.\n")
            return

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length > 0 else None

        url = f"http://127.0.0.1:{self.APP_PORT}{self.path}"
        req = urllib.request.Request(url, data=body, method=method)
        for h, v in self.headers.items():
            if h.lower() in ("host", "authorization", "content-length", "connection"):
                continue
            req.add_header(h, v)

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                self.send_response(resp.status)
                for h, v in resp.headers.items():
                    if h.lower() in ("transfer-encoding", "connection"):
                        continue
                    self.send_header(h, v)
                self.end_headers()
                self.wfile.write(resp.read())
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            for h, v in e.headers.items():
                if h.lower() in ("transfer-encoding", "connection"):
                    continue
                self.send_header(h, v)
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"proxy error: {e}\n".encode())

    def do_GET(self) -> None: self._proxy("GET")
    def do_POST(self) -> None: self._proxy("POST")
    def do_PUT(self) -> None: self._proxy("PUT")
    def do_DELETE(self) -> None: self._proxy("DELETE")
    def do_PATCH(self) -> None: self._proxy("PATCH")
    def do_HEAD(self) -> None: self._proxy("HEAD")
    def do_OPTIONS(self) -> None: self._proxy("OPTIONS")

    def log_message(self, format: str, *args) -> None:
        pass  # silence stderr access log


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page-id", required=True)
    ap.add_argument("--proxy-port", type=int, required=True)
    ap.add_argument("--app-port", type=int, required=True)
    ap.add_argument("--bind", default="127.0.0.1")
    args = ap.parse_args()

    page = storage.get_page(args.page_id)
    if not page or not page.get("auth"):
        print(f"error: no auth config for page_id={args.page_id}", file=sys.stderr)
        sys.exit(1)

    ProxyHandler.APP_PORT = args.app_port
    ProxyHandler.AUTH = page["auth"]

    server = ThreadingHTTPServer((args.bind, args.proxy_port), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke test — register a fake app and run proxy standalone**

In one terminal, start a fake app server:
```bash
cd /tmp && python -m http.server 19999
```

In another terminal:
```bash
cd /home/superbereza/dev/agent-instant-drop
PYTHONPATH=src python -c "
from pathlib import Path
from drop import storage
from drop.utils import hash_password
storage.add_page('proxy_smoke', Path('/tmp'), '', name='ps', page_type='app',
    run_cmd='x', port=19999,
    auth={'scheme':'basic','user':'drop','password_hash':hash_password('test123')})
"
PYTHONPATH=src python -m drop.proxy --page-id proxy_smoke --proxy-port 19998 --app-port 19999 --bind 127.0.0.1 &
PROXY_PID=$!
sleep 1

# No auth -> 401
curl -s -o /dev/null -w "no auth: %{http_code}\n" http://127.0.0.1:19998/

# Bad auth -> 401
curl -s -o /dev/null -w "bad auth: %{http_code}\n" -u drop:wrong http://127.0.0.1:19998/

# Good auth -> 200
curl -s -o /dev/null -w "good auth: %{http_code}\n" -u drop:test123 http://127.0.0.1:19998/

# WebSocket upgrade -> 501
curl -s -o /dev/null -w "upgrade: %{http_code}\n" -H "Connection: Upgrade" -H "Upgrade: websocket" -u drop:test123 http://127.0.0.1:19998/

kill $PROXY_PID 2>/dev/null
PYTHONPATH=src python -c "from drop import storage; storage.remove_page('proxy_smoke')"
```

Expected:
```
no auth: 401
bad auth: 401
good auth: 200
upgrade: 501
```

Kill the `python -m http.server 19999` in the first terminal.

- [ ] **Step 3: Commit**

```bash
git add src/drop/proxy.py
git commit -m "feat: add basic-auth reverse proxy module"
```

---

## Task 5: Add `--auth` and `--public` flags to `drop add` (parsing + validation)

**Files:**
- Modify: `src/drop/cli.py`

- [ ] **Step 1: Add argparse definitions in `main()`**

In `src/drop/cli.py`, find the `# add` block in `main()` and add two flags after `--port`:

```python
    p_add.add_argument(
        "--auth", nargs="?", const="basic", default=None,
        help="Basic auth proxy for apps. 'basic' auto-gens drop:<12char>. "
             "'basic:user:pass' for explicit creds. Apps only."
    )
    p_add.add_argument(
        "--public", action="store_true",
        help="Explicit opt-out from default auth (page/app stays public)."
    )
```

- [ ] **Step 2: Add a helper function for parsing `--auth` value**

Add this function in `src/drop/cli.py` near the top (after imports, before `_start_with_systemd`):

```python
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
```

- [ ] **Step 3: Add validation block at the top of `cmd_add()`**

In `src/drop/cli.py`, find `cmd_add()`. After the existing app args validation (where it checks `--run` + `--port`), add:

```python
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
```

- [ ] **Step 4: Smoke test (validation paths only — full creds flow in next task)**

```bash
cd /home/superbereza/dev/agent-instant-drop

# --auth without --run
PYTHONPATH=src python -m drop.cli add /tmp/fake.txt --auth basic 2>&1 | grep -i "only applies to apps"

# --auth + --public
PYTHONPATH=src python -m drop.cli add /tmp/fake.txt --run "x" --port 1 --auth basic --public 2>&1 | grep -i "cannot combine"

# --auth + --password
PYTHONPATH=src python -m drop.cli add /tmp/fake.txt --run "x" --port 1 --auth basic --password p 2>&1 | grep -i "cannot combine"

# Bad --auth spec
echo "" > /tmp/fake.txt
PYTHONPATH=src python -m drop.cli add /tmp/fake.txt --run "x" --port 1 --auth basic:onlyuser 2>&1 | grep -i "auth format"
```

Expected: each grep matches its substring.

- [ ] **Step 5: Commit**

```bash
git add src/drop/cli.py
git commit -m "feat: add --auth and --public flag parsing and validation"
```

---

## Task 6: `drop add` — default-flip, creds generation, output

**Files:**
- Modify: `src/drop/cli.py`

- [ ] **Step 1: Update imports**

In `src/drop/cli.py`, find the import line `from .utils import ...` and add `generate_auth_creds` to it.

- [ ] **Step 2: Replace the password/auth resolution block in `cmd_add()`**

In `cmd_add()`, replace the existing block:

```python
    # Handle password (default: no password)
    if args.password:
        password = args.password if args.password is not True else generate_password()
        password_hash = hash_password(password)
    else:
        password = None
        password_hash = ""
```

with:

```python
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
```

- [ ] **Step 3: Update the `storage.add_page()` call**

Find the `storage.add_page(` call in `cmd_add()` and replace it with:

```python
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
```

- [ ] **Step 4: Update the output block**

Find the URL printing block at the bottom of `cmd_add()` (where it prints `Published:` / `App registered:` and optionally `Password:`). Replace with:

```python
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
```

(Remove the old standalone `if password: print(f"Password: {password}")` block at the end since it's now inline.)

- [ ] **Step 5: Smoke test**

```bash
cd /home/superbereza/dev/agent-instant-drop

# Static auto-gen password (NEW DEFAULT)
echo "<h1>test</h1>" > /tmp/smoke.html
out=$(PYTHONPATH=src python -m drop.cli add /tmp/smoke.html 2>&1)
echo "$out" | grep -q "Published:" && echo "[ok] published printed"
echo "$out" | grep -q "Password: " && echo "[ok] auto-password printed"
# cleanup
PYTHONPATH=src python -m drop.cli list -a | grep smoke | awk '{print $1}' | xargs -I{} python -m drop.cli remove {} 2>/dev/null

# Static --public (NEW FLAG)
out=$(PYTHONPATH=src python -m drop.cli add /tmp/smoke.html --public 2>&1)
echo "$out" | grep -q "Published:" && echo "[ok] public published"
echo "$out" | grep -q "Password: " && echo "[fail] password printed for public" || echo "[ok] no password for public"
PYTHONPATH=src python -m drop.cli list -a | grep smoke | awk '{print $1}' | xargs -I{} python -m drop.cli remove {} 2>/dev/null

# App auto-gen basic auth (NEW DEFAULT)
mkdir -p /tmp/smokeapp && touch /tmp/smokeapp/app.py
out=$(PYTHONPATH=src python -m drop.cli add /tmp/smokeapp/app.py --run "true" --port 19000 2>&1)
echo "$out" | grep -q "App registered:" && echo "[ok] app registered"
echo "$out" | grep -qE "Auth: basic \(drop / .{12}\)" && echo "[ok] auto basic auth printed"
PYTHONPATH=src python -m drop.cli list -a | awk '/smokeapp/ {print $1}' | xargs -I{} python -m drop.cli remove {} 2>/dev/null

# App explicit --auth basic:user:pass
out=$(PYTHONPATH=src python -m drop.cli add /tmp/smokeapp/app.py --run "true" --port 19001 --auth basic:admin:s3cret 2>&1)
echo "$out" | grep -q "Auth: basic (admin / s3cret)" && echo "[ok] explicit auth printed"
PYTHONPATH=src python -m drop.cli list -a | awk '/smokeapp/ {print $1}' | xargs -I{} python -m drop.cli remove {} 2>/dev/null

# App --public
out=$(PYTHONPATH=src python -m drop.cli add /tmp/smokeapp/app.py --run "true" --port 19002 --public 2>&1)
echo "$out" | grep -q "(public — no auth)" && echo "[ok] public app marker"
PYTHONPATH=src python -m drop.cli list -a | awk '/smokeapp/ {print $1}' | xargs -I{} python -m drop.cli remove {} 2>/dev/null
```

Expected: all `[ok]` lines printed, no `[fail]`.

- [ ] **Step 6: Commit**

```bash
git add src/drop/cli.py
git commit -m "feat: default-flip to password/auth-protected, --public opt-out"
```

---

## Task 7: `drop start <app>` — proxy spawn integration + side-door warning

**Files:**
- Modify: `src/drop/cli.py`

- [ ] **Step 1: Add `_allocate_free_port()` helper**

Add this near the top of `src/drop/cli.py` (after imports):

```python
def _allocate_free_port() -> int:
    """Allocate a free TCP port from the OS."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_for_port(host: str, port: int, timeout: float = 5.0) -> bool:
    """Block until host:port accepts connections or timeout."""
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            try:
                s.connect((host, port))
                return True
            except OSError:
                time.sleep(0.1)
    return False
```

- [ ] **Step 2: Add `_print_side_door_warning()` helper**

Add this near the other helpers in `src/drop/cli.py`:

```python
def _print_side_door_warning() -> None:
    print(
        "⚠ --auth protects tunnel only. If your app binds 0.0.0.0 on a public IP,\n"
        "  app port is still reachable bypassing auth. "
        "Use --host 127.0.0.1 in --run.",
        file=sys.stderr,
    )
```

- [ ] **Step 3: Add `_spawn_proxy()` helper**

```python
def _spawn_proxy(page_id: str, app_port: int, bind: str) -> tuple[int, int] | None:
    """Spawn drop.proxy subprocess. Returns (proxy_pid, proxy_port) or None on failure."""
    proxy_port = _allocate_free_port()
    cmd = [
        sys.executable, "-m", "drop.proxy",
        "--page-id", page_id,
        "--proxy-port", str(proxy_port),
        "--app-port", str(app_port),
        "--bind", bind,
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    if not _wait_for_port(bind if bind != "0.0.0.0" else "127.0.0.1", proxy_port, timeout=5):
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass
        return None
    return (proc.pid, proxy_port)
```

- [ ] **Step 4: Integrate into `cmd_start_app()` — proxy spawn block**

In `cmd_start_app()`, after the app PID save and verification (the `time.sleep(1)` + `os.kill(proc.pid, 0)` block), and BEFORE the existing tunnel block, add:

```python
    # Auth path: spawn proxy in front of app
    auth_block = page.get("auth")
    proxy_pid = 0
    proxy_port = 0
    if auth_block:
        _print_side_door_warning()
        # Bind: 127.0.0.1 by default (tunnel-only); 0.0.0.0 only under --auth-insecure
        bind_addr = "0.0.0.0" if getattr(args, 'auth_insecure', False) else "127.0.0.1"
        result = _spawn_proxy(full_id, app_port, bind_addr)
        if not result:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                pass
            storage.update_page_pid(full_id, 0)
            print("Error: proxy failed to start", file=sys.stderr)
            return 1
        proxy_pid, proxy_port = result
        storage.update_page_proxy(full_id, proxy_pid, proxy_port)
```

Then change the tunnel `start_tunnel()` call from `tunnel.start_tunnel(app_port)` to:

```python
        target_port = proxy_port if auth_block else app_port
        result = tunnel.start_tunnel(target_port)
```

(Tunnel-required logic and the cleartext branch come in Task 8 — this task just gets proxy spawning wired in.)

- [ ] **Step 5: Smoke test**

Test that proxy is spawned and reachable on localhost when an app with auth is started (no tunnel logic yet — `--no-tunnel` to skip tunnel attempt):

```bash
cd /home/superbereza/dev/agent-instant-drop

mkdir -p /tmp/smokeapp2
PYTHONPATH=src python -m drop.cli add /tmp/smokeapp2 --run "python -m http.server 19100" --port 19100 --name sa2 2>&1
PYTHONPATH=src python -m drop.cli start sa2 --no-tunnel 2>&1 | tee /tmp/start.log

# Side-door warning should appear
grep -q "protects tunnel only" /tmp/start.log && echo "[ok] side-door warning"

# Proxy port saved in registry
proxy_port=$(PYTHONPATH=src python -c "from drop import storage; print(storage.get_page('sa2')['proxy_port'])")
[[ "$proxy_port" =~ ^[0-9]+$ ]] && [ "$proxy_port" -gt 0 ] && echo "[ok] proxy_port=$proxy_port"

# Proxy responds on localhost (would bind 127.0.0.1 since no --auth-insecure)
sleep 1
code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$proxy_port/")
[ "$code" = "401" ] && echo "[ok] proxy 401 without auth"

PYTHONPATH=src python -m drop.cli stop sa2
PYTHONPATH=src python -m drop.cli remove sa2
```

Expected: all `[ok]` lines printed.

- [ ] **Step 6: Commit**

```bash
git add src/drop/cli.py
git commit -m "feat: spawn auth proxy in cmd_start_app, print side-door warning"
```

---

## Task 8: `drop start` — tunnel-required logic + `--auth-insecure` flag

**Files:**
- Modify: `src/drop/cli.py`

- [ ] **Step 1: Add `--auth-insecure` to start subparser**

In `main()` find the `# start` block and add:

```python
    p_start.add_argument(
        "--auth-insecure", action="store_true",
        help="Allow cleartext basic auth without tunnel (override safety check). "
             "Use only on trusted LAN/dev."
    )
```

- [ ] **Step 2: Replace the tunnel branch in `cmd_start_app()`**

In `cmd_start_app()`, find the existing tunnel block (starting `# Check for NAT and start tunnel`). Replace from that comment through to the final `return 0` of the function with:

```python
    # Tunnel + URL printing
    auth_insecure = getattr(args, 'auth_insecure', False)
    no_tunnel = getattr(args, 'no_tunnel', False)
    target_port = proxy_port if auth_block else app_port

    cleartext_mode = bool(auth_block) and auth_insecure
    want_tunnel = not no_tunnel and (bool(auth_block) or is_behind_nat())

    if cleartext_mode:
        # Skip tunnel entirely, print cleartext warning, return
        url = f"http://{host}:{target_port}/"
        print(f"App started: {url}")
        if auth_block:
            print(f"  Auth: basic ({auth_block['user']} / <hidden> — see 'drop add' output)")
        print(
            "⚠ CLEARTEXT: basic auth credentials transmitted in base64 over plain HTTP.\n"
            "  Anyone on the network path can read them. Use only on trusted LAN.",
            file=sys.stderr,
        )
        return 0

    if want_tunnel:
        cloudflared = find_cloudflared()
        if not cloudflared:
            if auth_block:
                # Refuse: --auth requires tunnel
                _abort_auth_app(full_id, proxy_pid, proc.pid,
                    "cloudflared not installed",
                    "Install via ./install.sh or pass --auth-insecure to allow cleartext.")
                return 1
            else:
                # Non-auth app behind NAT: degrade gracefully
                print(f"App started: http://{host}:{app_port}/")
                print("  Note: Behind NAT but cloudflared not found. Run ./install.sh")
                return 0
        print("Starting tunnel...")
        result = tunnel.start_tunnel(target_port)
        if not result:
            if auth_block:
                _abort_auth_app(full_id, proxy_pid, proc.pid,
                    "tunnel failed to start",
                    "Retry, or pass --auth-insecure to allow cleartext.")
                return 1
            else:
                print(f"App started: http://{host}:{app_port}/")
                print("  Warning: Failed to start tunnel")
                return 0
        tunnel_url, tunnel_pid = result
        storage.update_page_tunnel(full_id, tunnel_url, tunnel_pid)
        print(f"App started: {tunnel_url}")
        if auth_block:
            print(f"  Auth: basic ({auth_block['user']} / <hidden> — see 'drop add' output)")
        print("  (tunneled via cloudflared)")
        return 0

    # No tunnel wanted, no auth_block (or auth_block + --no-tunnel already handled)
    if auth_block and no_tunnel:
        _abort_auth_app(full_id, proxy_pid, proc.pid,
            "--no-tunnel conflicts with --auth (cleartext over HTTP)",
            "Drop --no-tunnel, or pass --auth-insecure to confirm.")
        return 1

    print(f"App started: http://{host}:{app_port}/")
    return 0
```

- [ ] **Step 3: Add `_abort_auth_app()` helper**

Add near other helpers in `src/drop/cli.py`:

```python
def _abort_auth_app(full_id: str, proxy_pid: int, app_pid: int, reason: str, hint: str) -> None:
    """Kill proxy + app, clear runtime state, print error + hint."""
    if proxy_pid > 0:
        try:
            os.killpg(proxy_pid, signal.SIGTERM)
        except OSError:
            pass
    if app_pid > 0:
        try:
            os.killpg(app_pid, signal.SIGTERM)
        except OSError:
            pass
    storage.clear_page_runtime(full_id)
    print(f"Error: {reason}", file=sys.stderr)
    print(f"  Hint: {hint}", file=sys.stderr)
```

- [ ] **Step 4: Smoke test — `--no-tunnel` + auth refuses**

```bash
cd /home/superbereza/dev/agent-instant-drop
mkdir -p /tmp/smokeapp3
PYTHONPATH=src python -m drop.cli add /tmp/smokeapp3 --run "python -m http.server 19200" --port 19200 --name sa3 2>&1

# --no-tunnel + auth should refuse
out=$(PYTHONPATH=src python -m drop.cli start sa3 --no-tunnel 2>&1)
echo "$out" | grep -qi "conflicts with --auth" && echo "[ok] no-tunnel refuse"

# Verify no leftover processes
PYTHONPATH=src python -c "from drop import storage; p=storage.get_page('sa3'); print('pid:', p['pid'], 'proxy_pid:', p['proxy_pid'])"

# --auth-insecure + --no-tunnel succeeds with warning
out=$(PYTHONPATH=src python -m drop.cli start sa3 --no-tunnel --auth-insecure 2>&1)
echo "$out" | grep -qi "CLEARTEXT" && echo "[ok] cleartext warning"
echo "$out" | grep -q "App started:" && echo "[ok] started insecure"

# Cleanup
PYTHONPATH=src python -m drop.cli stop sa3
PYTHONPATH=src python -m drop.cli remove sa3
```

Expected: all `[ok]` lines printed; `pid: 0 proxy_pid: 0` after the failed start.

- [ ] **Step 5: Commit**

```bash
git add src/drop/cli.py
git commit -m "feat: tunnel-required for --auth, --auth-insecure cleartext override"
```

---

## Task 9: `drop stop <app>` — proxy cleanup + use `clear_page_runtime`

**Files:**
- Modify: `src/drop/cli.py`

- [ ] **Step 1: Replace `cmd_stop_app()` body**

Replace the entire `cmd_stop_app()` function in `src/drop/cli.py` with:

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

    # Stop tunnel
    tunnel_pid = page.get("tunnel_pid", 0)
    if tunnel_pid > 0:
        tunnel.stop_tunnel(tunnel_pid)

    # Stop proxy
    proxy_pid = page.get("proxy_pid", 0)
    if proxy_pid > 0:
        try:
            os.kill(proxy_pid, signal.SIGTERM)
        except OSError:
            pass

    # Stop app
    pid = page.get("pid", 0)
    status = storage.get_app_status(args.name)
    if pid > 0:
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            pass

    storage.clear_page_runtime(full_id)

    if status == "running":
        print("App stopped")
    else:
        print("App was not running")
    return 0
```

- [ ] **Step 2: Smoke test — full lifecycle**

```bash
cd /home/superbereza/dev/agent-instant-drop
mkdir -p /tmp/smokeapp4
PYTHONPATH=src python -m drop.cli add /tmp/smokeapp4 --run "python -m http.server 19300" --port 19300 --name sa4
PYTHONPATH=src python -m drop.cli start sa4 --no-tunnel --auth-insecure 2>&1 | tail -1

proxy_pid=$(PYTHONPATH=src python -c "from drop import storage; print(storage.get_page('sa4')['proxy_pid'])")
app_pid=$(PYTHONPATH=src python -c "from drop import storage; print(storage.get_page('sa4')['pid'])")
echo "before stop: app=$app_pid proxy=$proxy_pid"

PYTHONPATH=src python -m drop.cli stop sa4

# Verify processes died
sleep 1
kill -0 "$proxy_pid" 2>/dev/null && echo "[fail] proxy still alive" || echo "[ok] proxy gone"
kill -0 "$app_pid" 2>/dev/null && echo "[fail] app still alive" || echo "[ok] app gone"

# Verify runtime fields cleared
PYTHONPATH=src python -c "
from drop import storage
p = storage.get_page('sa4')
assert p['pid'] == 0 and p['proxy_pid'] == 0 and p['proxy_port'] == 0 and p['tunnel_pid'] == 0 and p['tunnel_url'] == ''
print('[ok] runtime cleared')
"
PYTHONPATH=src python -m drop.cli remove sa4
```

Expected: `[ok]` for all three checks.

- [ ] **Step 3: Commit**

```bash
git add src/drop/cli.py
git commit -m "feat: cmd_stop_app kills proxy and clears runtime state"
```

---

## Task 10: `drop list` — `[auth]` indicator

**Files:**
- Modify: `src/drop/cli.py`

- [ ] **Step 1: Add `[auth]` tag to app list lines**

In `cmd_list()`, find the block that builds `status_str` for app entries. Modify it to include an auth indicator:

```python
        if page_type == "app":
            # App: show tunnel URL if available, otherwise direct port
            port = info.get("port", 0)
            if app_tunnel_url:
                url = app_tunnel_url
            else:
                url = f"http://{host}:{port}/"
            status = storage.get_app_status(page_id)
            auth_tag = " [auth]" if info.get("auth") else ""
            status_str = f" [{status}]{auth_tag}"
```

(The line `auth_tag = ...` and its use in `status_str` are the only changes.)

- [ ] **Step 2: Smoke test**

```bash
cd /home/superbereza/dev/agent-instant-drop
mkdir -p /tmp/sa5
PYTHONPATH=src python -m drop.cli add /tmp/sa5 --run "true" --port 19400 --name sa5
PYTHONPATH=src python -m drop.cli list -a | grep sa5 | grep -q "\[auth\]" && echo "[ok] auth tag"

PYTHONPATH=src python -m drop.cli add /tmp/sa5 --run "true" --port 19401 --name sa5pub --public
PYTHONPATH=src python -m drop.cli list -a | grep sa5pub | grep -q "\[auth\]" && echo "[fail] auth tag on public" || echo "[ok] no auth tag on public"

PYTHONPATH=src python -m drop.cli remove sa5
PYTHONPATH=src python -m drop.cli remove sa5pub
```

Expected: `[ok] auth tag`, `[ok] no auth tag on public`.

- [ ] **Step 3: Commit**

```bash
git add src/drop/cli.py
git commit -m "feat: [auth] indicator in drop list for auth-protected apps"
```

---

## Task 11: Per-app tunnel watchdog in `tunnel.py`

**Files:**
- Modify: `src/drop/tunnel.py`

- [ ] **Step 1: Add per-app watchdog state and functions**

In `src/drop/tunnel.py`, add at the bottom of the file:

```python
# Per-app watchdog state: {page_id: (thread, stop_event)}
_app_watchdogs: dict[str, tuple[threading.Thread, threading.Event]] = {}


def _app_watchdog_loop(page_id: str, port: int, stop_event: threading.Event) -> None:
    """Watchdog loop for a single app tunnel. Updates page registry on restart."""
    import os
    from . import storage

    while not stop_event.is_set():
        page = storage.get_page(page_id)
        if not page:
            return
        tpid = page.get("tunnel_pid", 0)
        if tpid > 0:
            try:
                os.kill(tpid, 0)
                if stop_event.wait(10):
                    return
                continue
            except OSError:
                pass  # dead, restart below

        result = start_tunnel(port)
        if result:
            url, pid = result
            storage.update_page_tunnel(page_id, url, pid)
        if stop_event.wait(10):
            return


def start_app_watchdog(page_id: str, port: int) -> None:
    """Start watchdog thread for an app's tunnel."""
    stop_event = threading.Event()
    t = threading.Thread(
        target=_app_watchdog_loop,
        args=(page_id, port, stop_event),
        daemon=True,
    )
    _app_watchdogs[page_id] = (t, stop_event)
    t.start()


def stop_app_watchdog(page_id: str) -> None:
    """Stop watchdog thread for an app."""
    entry = _app_watchdogs.pop(page_id, None)
    if entry:
        thread, stop_event = entry
        stop_event.set()
        thread.join(timeout=2)
```

- [ ] **Step 2: Smoke test (module-level only — full integration in Task 12)**

```bash
cd /home/superbereza/dev/agent-instant-drop
PYTHONPATH=src python -c "
from drop import tunnel
# Test that start/stop watchdog API exists and doesn't crash on a fake page
tunnel.start_app_watchdog('fake_page_xyz', 65432)
import time; time.sleep(0.2)
tunnel.stop_app_watchdog('fake_page_xyz')
print('[ok] watchdog API works')
"
```

Expected: `[ok] watchdog API works` (the watchdog loop will exit when storage.get_page returns None).

- [ ] **Step 3: Commit**

```bash
git add src/drop/tunnel.py
git commit -m "feat: per-app tunnel watchdog for app lifecycle"
```

---

## Task 12: Integrate per-app watchdog into `cmd_start_app` and `cmd_stop_app`

**Files:**
- Modify: `src/drop/cli.py`

- [ ] **Step 1: Start watchdog after successful tunnel in `cmd_start_app`**

In `cmd_start_app()`, find the line that prints `App started: {tunnel_url}` (in the success branch of the tunnel block). Add immediately after `storage.update_page_tunnel(full_id, tunnel_url, tunnel_pid)`:

```python
        tunnel.start_app_watchdog(full_id, target_port)
```

- [ ] **Step 2: Stop watchdog at start of `cmd_stop_app`**

In `cmd_stop_app()`, after `full_id = storage.get_full_page_id(args.name)` and before the tunnel stop block, add:

```python
    tunnel.stop_app_watchdog(full_id)
```

- [ ] **Step 3: Smoke test (manual, requires NAT or tunnel-capable env)**

If running on a machine where cloudflared works (NAT or VPS):

```bash
cd /home/superbereza/dev/agent-instant-drop
mkdir -p /tmp/sa6
PYTHONPATH=src python -m drop.cli add /tmp/sa6 --run "python -m http.server 19500" --port 19500 --name sa6
PYTHONPATH=src python -m drop.cli start sa6 2>&1 | tail -3

# Find cloudflared PID and kill it to simulate crash
sleep 5  # let tunnel come up
tunnel_pid=$(PYTHONPATH=src python -c "from drop import storage; print(storage.get_page('sa6')['tunnel_pid'])")
kill -9 "$tunnel_pid"

# Wait for watchdog to detect + restart (~10-15s)
sleep 20
new_pid=$(PYTHONPATH=src python -c "from drop import storage; print(storage.get_page('sa6')['tunnel_pid'])")
[ "$new_pid" != "$tunnel_pid" ] && [ "$new_pid" -gt 0 ] && echo "[ok] watchdog restarted tunnel (pid $tunnel_pid -> $new_pid)"

PYTHONPATH=src python -m drop.cli stop sa6
PYTHONPATH=src python -m drop.cli remove sa6
```

If on a machine without working cloudflared, skip this and rely on Task 16 end-to-end test.

- [ ] **Step 4: Commit**

```bash
git add src/drop/cli.py
git commit -m "feat: integrate per-app tunnel watchdog into start/stop"
```

---

## Task 13: Help text, subcommand epilogs, top-level `__doc__`

**Files:**
- Modify: `src/drop/cli.py`

- [ ] **Step 1: Update top-level module docstring**

Replace the docstring at the top of `src/drop/cli.py` with:

```python
"""
drop - Agent Instant Drop

Drop any file, app, or prototype to your human. Password-protected by default.

Examples:
    drop start                                          # Start server
    drop add ./report.html                              # Publish (auto-password)
    drop add ./report.html --public                     # Public link
    drop add ./bin --run "..." --port N                 # App (auto basic auth)
    drop list                                           # List from cwd
    drop remove abc123                                  # Remove
    drop stop                                           # Stop server
"""
```

- [ ] **Step 2: Add epilog to `drop add` subparser**

In `main()`, find `p_add = subparsers.add_parser("add", ...)` and replace it with:

```python
    p_add = subparsers.add_parser(
        "add",
        help="Publish a file/folder/app (password-protected by default)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  drop add ./report.html                          # static + auto-password\n"
            "  drop add ./report.html --public                 # public static\n"
            "  drop add ./report.html --password mypass        # custom password\n"
            "  drop add ./bin --run \"myserver\" --port 7777     # app + auto basic auth\n"
            "  drop add ./bin --run \"myserver\" --port 7777 --public\n"
            "  drop add ./bin --run \"myserver\" --port 7777 --auth basic:admin:s3cret\n"
        ),
    )
```

- [ ] **Step 3: Add epilog to `drop start` subparser**

In `main()`, find `p_start = subparsers.add_parser("start", ...)` and replace it with:

```python
    p_start = subparsers.add_parser(
        "start",
        help="Start server or app",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  drop start                          # Start the drop server (default port 8080)\n"
            "  drop start --port 9000              # Different port\n"
            "  drop start myapp                    # Start a registered app\n"
            "  drop start myapp --auth-insecure    # Start app + allow cleartext basic auth\n"
        ),
    )
```

- [ ] **Step 4: Smoke test**

```bash
cd /home/superbereza/dev/agent-instant-drop
PYTHONPATH=src python -m drop.cli --help | grep -q "Password-protected by default" && echo "[ok] top doc"
PYTHONPATH=src python -m drop.cli add --help | grep -q "auto basic auth" && echo "[ok] add epilog"
PYTHONPATH=src python -m drop.cli add --help | grep -q "\-\-auth" && echo "[ok] --auth in help"
PYTHONPATH=src python -m drop.cli add --help | grep -q "\-\-public" && echo "[ok] --public in help"
PYTHONPATH=src python -m drop.cli start --help | grep -q "auth-insecure" && echo "[ok] --auth-insecure in help"
```

Expected: all `[ok]` lines.

- [ ] **Step 5: Commit**

```bash
git add src/drop/cli.py
git commit -m "docs: help text and epilogs for new flags"
```

---

## Task 14: Update `skills/drop.md`

**Files:**
- Modify: `skills/drop.md`

- [ ] **Step 1: Rewrite the file**

Replace the entire content of `skills/drop.md` with:

```markdown
---
name: drop
description: Drop files, apps, or prototypes to your human via drop CLI
---

# Agent Instant Drop

Drop any file, app, or prototype to your human. **Password-protected by default.** Use `--public` to opt out.

## Quick Start

```bash
# Start server (once)
drop start

# Publish a page (password auto-generated)
drop add ./report.html --desc "Weekly report"
# → http://192.168.1.50:8080/p/abc123
# → Password: xK9mP2qLx

# Explicit public page
drop add ./public.html --public

# Custom password
drop add ./secret.html --password mysecret

# Register an app — basic auth is added automatically
drop add ./app --run "flask run --port 5000" --port 5000 --name api
# → App registered: http://...:5000/
# → Auth: basic (drop / 8wK2mP4qLxNR)

# Public app (no auth)
drop add ./demo --run "python -m http.server 7777" --port 7777 --public

# Custom basic auth credentials
drop add ./app --run "flask run --port 5000" --port 5000 --auth basic:admin:s3cret

# List pages from current directory
drop list                  # [auth] tag marks protected apps
drop list --all            # all pages

# Remove when done
drop remove abc123

# Stop server
drop stop
```

## Commands

| Command | Description |
|---------|-------------|
| `drop start [--port N]` | Start server (default: 8080) |
| `drop start <name>` | Start a registered app |
| `drop start <name> --auth-insecure` | Allow cleartext basic auth without tunnel |
| `drop stop` / `drop stop <name>` | Stop server or app |
| `drop status` | Show server URL and all pages |
| `drop add <path>` | Publish file/folder (auto-password) |
| `drop add <path> --public` | Publish without password |
| `drop add <path> --run "cmd" --port N` | Register an app (auto basic auth) |
| `drop list` | List pages from current directory |
| `drop list --all` | List all pages |
| `drop remove <id>` | Remove published page |
| `drop cleanup` | Remove crashed/orphaned apps |

## Flags for `drop add`

- `--name "slug"` / `-n "slug"` — human-readable name in URL
- `--desc "text"` / `-d "text"` — description for listing
- `--password [PASS]` / `-p [PASS]` — explicit password for static pages (auto-gen if no value). Default: auto-password.
- `--auth basic[:user:pass]` — basic auth for apps. Default: auto-gen `drop:<12char>`. Apps only.
- `--public` — opt out of default auth. For pages/apps that are intentionally public.
- `--run "command"` / `-r "command"` — run command for apps
- `--port <N>` — app port (required with `--run`)

**URL format:** `http://host:port/p/<secret>/<name>/`

## Flags for `drop start`

- `--port <N>` — server port (default: 8080)
- `--host <ip>` — override detected IP
- `--no-tunnel` — disable automatic tunnel when behind NAT
- `--auth-insecure` — for apps with auth: allow cleartext (basic auth over plain HTTP) without a tunnel. Use only on trusted LAN/dev.

## Auth model

**Static pages** use cookie-form auth with rate limiting. A password form appears; correct password sets a cookie.

**Apps** use HTTP basic auth via a thin reverse proxy spawned by drop:
- Proxy lives in front of your app on an auto-allocated port
- Tunnel (cloudflared) points to the proxy, not directly to your app
- Default: proxy binds 127.0.0.1, only the tunnel can reach it → secure
- With `--auth-insecure`: proxy binds 0.0.0.0 for LAN access, basic auth travels in cleartext (warning shown)

### Important: side door

Drop's auth protects the proxy port. Your app's own port (the one in `--port`) is not protected by drop. If your app binds `0.0.0.0` on a public IP, anyone can reach it directly bypassing the proxy. Always bind your app to `127.0.0.1` when using `--auth`:

```bash
drop add ./api --run "flask run --host 127.0.0.1 --port 5000" --port 5000
```

Drop prints a warning to this effect on every `drop start <app>`.

### `--auth` requires a tunnel

When an app has `--auth`, `drop start` will refuse if no tunnel can be started (cloudflared missing, `--no-tunnel` given, etc.). This is to keep basic auth credentials off the wire in cleartext. Override with `--auth-insecure` if you know what you're doing.

## Apps (Lifecycle Manager)

Register, start, and stop applications. Drop manages the process and (if auth is enabled) a reverse proxy.

```bash
# Register an app (auto basic auth)
drop add ./app.py --run "flask run --port 5000" --port 5000 --name api
# → App registered: http://192.168.1.50:5000/
# → Auth: basic (drop / 8wK2mP4qLxNR)

# Register a public app
drop add ./demo --run "python -m http.server 7777" --port 7777 --public --name demo

# Start the app
drop start api
# → ⚠ --auth protects tunnel only. ...
# → App started: https://abc.trycloudflare.com/  (auth: drop / <see add output>)

# Stop the app (tunnel, proxy, app all killed)
drop stop api

# List with status
drop list
# api    [app] [running] [auth]  https://abc.trycloudflare.com/
# demo   [app] [running]         http://192.168.1.50:7777/

# Clean up crashed apps
drop cleanup
```

**App lifecycle:**
- `drop add --run --port` — registers app (stopped state). Auth is added by default.
- `drop start <name>` — runs the command on specified port. Spawns proxy + tunnel if auth.
- `drop stop <name>` — kills tunnel, proxy, app
- `drop cleanup` — removes crashed/orphaned apps

**Apps vs pages:** Pages are served through the drop server (port 8080) with optional cookie-form auth. Apps run on their own port behind a basic-auth proxy (when auth is enabled).

**Status indicators:**
- `[running]` — app process is active
- `[stopped]` — registered but not running
- `[crashed]` — process exited unexpectedly
- `[auth]` — app has basic auth configured

## Tunnel (NAT Support and HTTPS for auth)

Drop uses cloudflared to create a public HTTPS URL.

- **Behind NAT** (laptop on a home router): tunnel auto-starts so the URL is reachable from outside.
- **Apps with `--auth`**: tunnel is always required (HTTPS termination for basic auth). If cloudflared is missing or `--no-tunnel` is set, drop refuses to start with a hint about `--auth-insecure`.
- **Watchdog**: if cloudflared crashes, drop restarts it automatically. The new URL replaces the old one in `drop list`.

```bash
# Behind NAT - tunnel starts automatically
$ drop start
Starting tunnel...
Server started: https://random-words.trycloudflare.com

# App with auth
$ drop start myapp
⚠ --auth protects tunnel only. ...
Starting tunnel...
App started: https://other-random.trycloudflare.com/
  Auth: basic (drop / <hidden>)
  (tunneled via cloudflared)

# App with auth, no tunnel available — refuse
$ drop start myapp --no-tunnel
Error: --no-tunnel conflicts with --auth (cleartext over HTTP)
  Hint: Drop --no-tunnel, or pass --auth-insecure to confirm.

# Override
$ drop start myapp --no-tunnel --auth-insecure
App started: http://192.168.1.50:41234/
  Auth: basic (drop / <hidden>)
⚠ CLEARTEXT: basic auth credentials transmitted in base64 over plain HTTP.
  Anyone on the network path can read them. Use only on trusted LAN.

# Disable tunnel (for non-auth apps)
$ drop start mydemo --no-tunnel
App started: http://192.168.1.50:7777/
```

## Publishing Directories (Manifest Required)

To publish a directory as static content, create `.drop-publish` manifest first:

```bash
# 1. Create manifest with allowed patterns
cat > ./project/.drop-publish << 'EOF'
index.html
assets/**
EOF

# 2. Now publish works
drop add ./project/ --desc "My project"
```

**Why manifest?** Prevents accidental exposure of `.env`, config files, etc. Only files matching manifest patterns are served.

**Manifest syntax:**
- `index.html` — exact file
- `assets/**` — directory and all contents
- `*.html` — glob pattern

**Before creating manifest, check what HTML loads:**
```bash
grep -E "src=|href=" index.html | grep -oE '\./[^"'"'"']*' | sort -u
```
Add ALL referenced directories to manifest (assets/, config/, js/, etc.)

**Security:** `.env` files are always blocked, even if in manifest (except `.env.example`).

**API calls won't work in static pages** — drop serves static files only. If HTML uses `fetch('/api/...')`, either embed mock data or register as an app with `--run`.

**Single files** work without manifest:
```bash
drop add ./report.html  # OK, no manifest needed
```

## Tips

- **Always `cd` to project first, then `drop add .`** — don't use absolute paths, so `drop list` works correctly.
- **Bind your app to 127.0.0.1 when using `--auth`** — otherwise the app port is reachable bypassing the proxy.
- **Save the auto-generated credentials** — drop prints them once at `drop add`. After that, they're stored as a hash; you can't recover the plaintext.
```

- [ ] **Step 2: Smoke test**

```bash
grep -q "Password-protected by default" /home/superbereza/dev/agent-instant-drop/skills/drop.md && echo "[ok]"
grep -q "auth-insecure" /home/superbereza/dev/agent-instant-drop/skills/drop.md && echo "[ok]"
grep -q "side door" /home/superbereza/dev/agent-instant-drop/skills/drop.md && echo "[ok]"
```

Expected: three `[ok]` lines.

- [ ] **Step 3: Commit**

```bash
git add skills/drop.md
git commit -m "docs(skill): update drop.md for default-auth and --auth/--public/--auth-insecure"
```

---

## Task 15: Update `README.md`

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace Quick Start and Features sections**

In `README.md`, replace the `## Quick Start` and `## Features` sections with:

```markdown
## Quick Start

```bash
drop start                                       # Start server
drop add ./report.html                           # Publish file (auto-password)
drop add ./report.html --public                  # Public link
drop add ./dist/                                 # Publish folder (manifest required)
drop add ./bin --run "myapp --port 7777" --port 7777   # App + auto basic auth
drop list                                        # List pages
drop remove abc123                               # Remove page
drop stop                                        # Stop server
```

## Features

- **Password-protected by default** — `--public` opt-out
- Apps get built-in HTTP basic auth via a thin reverse proxy
- Tunnel required for `--auth` (HTTPS termination); `--auth-insecure` to override on trusted LAN
- Manifest-based security for directories (`.drop-publish`)
- Human-readable URLs: `/p/<secret>/<name>/`
- External IP detection for shareable URLs
- Rate limiting on static-page passwords (3 attempts/min/IP)
- Auto cloudflared tunnel when behind NAT; per-app watchdog
```

- [ ] **Step 2: Smoke test**

```bash
grep -q "Password-protected by default" /home/superbereza/dev/agent-instant-drop/README.md && echo "[ok]"
grep -q "auto basic auth" /home/superbereza/dev/agent-instant-drop/README.md && echo "[ok]"
```

Expected: two `[ok]` lines.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: update README for password-by-default and app auth"
```

---

## Task 16: End-to-end smoke test

**Files:** (no code changes)

- [ ] **Step 1: Run the full happy-path scenario locally**

```bash
cd /home/superbereza/dev/agent-instant-drop

# Setup: a test app
mkdir -p /tmp/e2e && cat > /tmp/e2e/app.py <<'EOF'
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type","text/plain"); self.end_headers()
        self.wfile.write(b"hello from app\n")
    def log_message(self, *a, **k): pass
HTTPServer(("127.0.0.1", 19800), H).serve_forever()
EOF

# Register app (default auth)
PYTHONPATH=src python -m drop.cli add /tmp/e2e/app.py \
    --run "python /tmp/e2e/app.py" --port 19800 --name e2e 2>&1 | tee /tmp/add.log

creds=$(grep -oP "Auth: basic \(\K[^)]+" /tmp/add.log)
user=$(echo "$creds" | cut -d/ -f1 | tr -d ' ')
pw=$(echo "$creds" | cut -d/ -f2 | tr -d ' ')
echo "user=$user pw=$pw"

# Start (will refuse if no cloudflared/tunnel — exercise --auth-insecure path)
PYTHONPATH=src python -m drop.cli start e2e --no-tunnel --auth-insecure 2>&1 | tee /tmp/start.log
grep -q "CLEARTEXT" /tmp/start.log && echo "[ok] cleartext warning"
grep -q "protects tunnel only" /tmp/start.log && echo "[ok] side-door warning"

proxy_port=$(PYTHONPATH=src python -c "from drop import storage; print(storage.get_page('e2e')['proxy_port'])")
sleep 1

# Hit proxy without auth
code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$proxy_port/")
[ "$code" = "401" ] && echo "[ok] 401 without auth"

# Hit proxy with bad creds
code=$(curl -s -o /dev/null -w "%{http_code}" -u "drop:wrong" "http://127.0.0.1:$proxy_port/")
[ "$code" = "401" ] && echo "[ok] 401 bad creds"

# Hit proxy with good creds
body=$(curl -s -u "$user:$pw" "http://127.0.0.1:$proxy_port/")
[ "$body" = "hello from app" ] && echo "[ok] 200 with auth"

# WS upgrade rejected
code=$(curl -s -o /dev/null -w "%{http_code}" -H "Connection: Upgrade" -H "Upgrade: websocket" -u "$user:$pw" "http://127.0.0.1:$proxy_port/")
[ "$code" = "501" ] && echo "[ok] 501 on upgrade"

# Stop
PYTHONPATH=src python -m drop.cli stop e2e
sleep 1
# All processes gone?
ss -tln | grep -q ":19800 " && echo "[fail] app port still open" || echo "[ok] app port closed"
ss -tln | grep -q ":$proxy_port " && echo "[fail] proxy port still open" || echo "[ok] proxy port closed"

# Remove
PYTHONPATH=src python -m drop.cli remove e2e

# Conflict tests
PYTHONPATH=src python -m drop.cli add /tmp/e2e/app.py --auth basic 2>&1 | grep -q "only applies to apps" && echo "[ok] auth without run rejected"

# Static default-flip
echo "hi" > /tmp/e2e/page.html
PYTHONPATH=src python -m drop.cli add /tmp/e2e/page.html 2>&1 | grep -q "Password:" && echo "[ok] static default-auth"
pid=$(PYTHONPATH=src python -m drop.cli list -a | grep page.html | awk '{print $1}')
PYTHONPATH=src python -m drop.cli remove "$pid"
```

Expected: all `[ok]` lines, no `[fail]`.

- [ ] **Step 2: NAT happy-path smoke (only if cloudflared works on this host)**

```bash
cd /home/superbereza/dev/agent-instant-drop
PYTHONPATH=src python -m drop.cli add /tmp/e2e/app.py --run "python /tmp/e2e/app.py" --port 19800 --name e2enat
PYTHONPATH=src python -m drop.cli start e2enat 2>&1 | tee /tmp/nat.log

# Should see "Starting tunnel..." and final HTTPS URL
grep -q "trycloudflare.com" /tmp/nat.log && echo "[ok] tunnel URL"
tunnel_url=$(grep -oP "https://[a-z0-9-]+\.trycloudflare\.com" /tmp/nat.log | head -1)
echo "tunnel_url=$tunnel_url"

creds=$(grep -oP "Auth: basic \(\K[^)]+" /tmp/add.log)  # from previous add — re-fetch via 'drop add' output
# (For NAT test, you may need to wait ~10s for cloudflare DNS propagation)
sleep 5
code=$(curl -s -o /dev/null -w "%{http_code}" "$tunnel_url/")
[ "$code" = "401" ] && echo "[ok] tunnel 401 without auth"

# Cleanup
PYTHONPATH=src python -m drop.cli stop e2enat
PYTHONPATH=src python -m drop.cli remove e2enat
```

If running on a host without cloudflared or with no internet → skip; the `--auth-insecure` path in Step 1 already covers the proxy logic.

- [ ] **Step 3: No commit — this task is verification only.**

If any `[fail]` appeared, fix the underlying issue in the relevant Task (5-12) and re-run.

---

## Task 17: `/review` pass — simplification review

**Files:** (may modify any)

- [ ] **Step 1: Invoke the review skill**

In the chat, the agent invokes:
```
/review
```

(Via the `Skill` tool with `skill: review`.)

- [ ] **Step 2: Address findings**

For each suggestion that aligns with YAGNI/simplification, edit the relevant file, re-run the Task 16 smoke test to confirm nothing broke, and commit with `refactor: <what>` message per change.

For suggestions that are out of scope (V2 candidates from the spec) or contradicted by an explicit decision in `docs/2026-05-20-app-basic-auth-design.md`, document the decision in the chat and skip.

- [ ] **Step 3: Verify smoke still passes**

Re-run Task 16 Step 1 commands. All `[ok]` lines must still appear.

---

## Task 18: `/security-review` pass

**Files:** (may modify any)

- [ ] **Step 1: Invoke the security review skill**

In the chat, the agent invokes:
```
/security-review
```

(Via the `Skill` tool with `skill: security-review`.)

- [ ] **Step 2: Address findings**

For each finding, classify:
- **Real vulnerability** in new code (proxy, auth handling, password storage): fix immediately, re-run Task 16, commit with `security: <what>`.
- **Existing issue** unrelated to this PR: file in `docs/backlog.md` and skip.
- **Spec-acknowledged risk** (cleartext under `--auth-insecure`, app-port side door): confirm the warning is printed and the doc explains the risk.

- [ ] **Step 3: Verify smoke still passes**

Re-run Task 16 Step 1.

---

## Task 19: Final push

**Files:** none

- [ ] **Step 1: Verify clean state**

```bash
cd /home/superbereza/dev/agent-instant-drop
git status   # should be clean (no uncommitted changes)
git log --oneline origin/main..HEAD   # show all new commits
```

- [ ] **Step 2: Push**

```bash
git push origin main
```

- [ ] **Step 3: Verify**

```bash
git status
git log --oneline -5
```

Expected: `Your branch is up to date with 'origin/main'`.

---

## Self-Review Notes

(Verification done after writing this plan.)

- **Spec coverage:** Each of the 12 decisions in the spec maps to one or more tasks:
  - V1 scope (501 on upgrade): Task 4 proxy.py `_reject_upgrade`
  - Per-app secret: Task 2 (storage) + Task 4 (proxy reads page-specific auth)
  - Default-flip: Task 6
  - Flag UX (`mirror --password`): Task 5 (`nargs="?"` for `--auth`)
  - Help text: Task 13
  - HTTPS coupling (refuse): Task 8
  - `--auth-insecure`: Task 8
  - Conditional bind: Task 7 (`bind_addr` choice)
  - Side-door warning: Task 7 `_print_side_door_warning`
  - stdlib proxy: Task 4
  - App tunnel watchdog: Tasks 11+12
  - `[auth]` indicator: Task 10
- **Placeholders:** none — all code is concrete.
- **Type consistency:** `auth` field is `dict | None` throughout (storage, proxy, cli). Helper names: `update_page_proxy`, `clear_page_runtime`, `start_app_watchdog`, `stop_app_watchdog`, `_spawn_proxy`, `_abort_auth_app`, `_print_side_door_warning`, `_allocate_free_port`, `_wait_for_port`, `_parse_auth_spec`, `generate_auth_creds` — used consistently.
- **Docs sync:** Tasks 14 (skill) and 15 (README) updated together with code.
