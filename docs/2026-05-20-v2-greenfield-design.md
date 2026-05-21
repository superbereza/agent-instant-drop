# drop v2 — Greenfield Architecture Design

**Date:** 2026-05-20
**Status:** Approved
**Branch:** `v2` (main keeps shipping v1 with optional hotfixes)

## Goal

Rewrite drop from scratch with: clean module boundaries, atomic lifecycle, all known bugs fixed by design, test-first development, and same CLI surface for users. Target = a tool polished enough to recommend openly ("вывести в люди").

## Why greenfield over incremental

The known v1 bugs are architectural, not local:

| Bug | Architectural root |
|---|---|
| `tunnel.start_tunnel` leaks PIPE → cloudflared blocks → 530/1033 | No single discipline for subprocess spawning |
| Dead app/server watchdog (daemon thread in short-lived CLI) | Lifecycle ownership undefined |
| Auth advertised but proxy may not survive | No atomic lifecycle / rollback |
| Duplicate `--name X` creates silent dupes | Storage has no constraints |
| Side-door (app port reachable bypassing proxy auth) | Boundary between user-app and drop-managed surface is undefined |
| `cli.py` is 900 LOC of mixed concerns | No module boundaries |

Incremental fix path: write characterization tests on broken behavior → change behavior → tests break → rewrite tests. That is *more* work than greenfield with tests-first. The codebase is ~1k LOC — small enough that rewriting cleanly beats untangling.

## Non-goals

- **Daemon architecture.** Long-running `dropd` would solve watchdog and coordination "for free" but adds autostart-on-boot, "daemon down" failure modes, and install complexity. drop is a sharing tool, not infrastructure. Stateless CLI matches user mental model.
- **New CLI surface.** Users (humans + agent skill) keep `drop add/start/stop/list/remove/status` unchanged.
- **Named cloudflared tunnels** (persistent URLs). Backlog for V3 — requires Cloudflare account + DNS.

## Design constraints (v1 bugs as requirements)

These six v1 bugs MUST be impossible by construction in v2:

1. **Subprocess output never via undrained PIPE.** Every long-lived subprocess uses `--logfile` (cloudflared) or `DEVNULL` (proxy/app) or a drainer-thread. The single `spawn_managed` helper enforces this. `start_new_session=True` is non-negotiable for every detached spawn.
2. **No threads-as-watchdog inside short-lived CLI.** Either delete watchdog entirely (current decision) or use a subprocess (V3 backlog).
3. **Atomic lifecycle: app+proxy+tunnel all-or-nothing.** Any failure rolls back prior phases in reverse order. After a successful `drop start`, `drop list` shows live state via PID-probe, never stale registry data.
4. **Storage enforces UNIQUE constraint on name.** `add_page(name=X)` raises if name exists. `remove(name=X)` works without ambiguity.
5. **Side-door enforcement** (behaviour change vs v1): if `--auth` is set, drop probes app port from external interface after start; if reachable, refuse + rollback. Override with `--allow-side-door` flag (saved per-page) for apps that legitimately must bind 0.0.0.0.
6. **Persistent vs volatile state split.** `Page` (config: source, auth, name, type) and `PageRuntime` (volatile: pids, ports, tunnel_url) live in separate files. Runtime cleared automatically when processes die (verified via PID-probe).

## Architecture

### Module layout

```
src/drop/                              # v2 — replaces v1 on cutover
├── __init__.py
├── cli.py                  ~200 LOC   argparse + dispatch + format only
├── config.py                ~50 LOC   DROP_HOME paths, constants, env overrides
├── storage.py              ~150 LOC   Page CRUD with UNIQUE(name) constraint
├── runtime.py               ~80 LOC   PageRuntime, PID-probe alive-check
├── lifecycle/
│   ├── __init__.py
│   ├── process.py           ~80 LOC   spawn_managed/kill_pg/wait_alive — single source of truth
│   ├── app.py              ~150 LOC   atomic app start/stop (with proxy + tunnel)
│   ├── server.py           ~100 LOC   atomic drop static-server start/stop
│   └── tunnel.py            ~80 LOC   cloudflared via --logfile, drainer-thread fallback
├── proxy.py                ~130 LOC   basic-auth proxy (copied from v1, clean)
├── server.py               ~180 LOC   Flask static-page server (polished: name in index, escape)
├── auth.py                  ~60 LOC   basic-auth parse, password gen/hash, rate-limit
├── manifest.py              ~80 LOC   safe_path + matches_manifest (extracted from utils)
└── utils.py                 ~80 LOC   IP detection, port-alloc, has_systemd, find_cloudflared

tests/
├── conftest.py                        DROP_HOME isolation fixture, free-port fixture
├── unit/
│   ├── test_auth.py
│   ├── test_config.py
│   ├── test_manifest.py
│   ├── test_storage.py
│   ├── test_runtime.py
│   ├── test_proxy_handler.py
│   └── test_lifecycle_process.py
├── integration/
│   ├── test_lifecycle_app.py          spawn real subprocesses, verify lifecycle
│   ├── test_lifecycle_server.py
│   ├── test_tunnel.py                 optional: real cloudflared
│   └── test_server_routes.py          Flask test client
└── e2e/
    └── test_cli.py                    subprocess.run(["drop", ...])
```

### Data model

```python
# storage.py — persistent
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime
from typing import Literal

@dataclass(frozen=True)
class AuthConfig:
    scheme: str           # "basic"
    user: str
    password_hash: str    # sha256:<hex>

@dataclass
class Page:
    page_id: str
    source: Path
    type: Literal["static", "app"]
    name: str = ""                    # UNIQUE if non-empty
    description: str = ""
    is_public: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # static-only
    password_hash: str = ""

    # app-only
    run_cmd: str = ""
    port: int = 0
    auth: AuthConfig | None = None
    allow_side_door: bool = False     # explicit opt-in if app binds 0.0.0.0


# runtime.py — volatile, separate file
@dataclass
class PageRuntime:
    page_id: str
    app_pid: int = 0
    proxy_pid: int = 0
    proxy_port: int = 0
    tunnel_pid: int = 0
    tunnel_url: str = ""

    def is_app_alive(self) -> bool: ...     # os.kill(pid, 0)
    def is_proxy_alive(self) -> bool: ...
    def is_tunnel_alive(self) -> bool: ...
```

### Storage files

```
~/.drop/
├── pages.json              { "version": 2, "pages": { <page_id>: <Page> } }
├── runtime.json            { "version": 2, "runtimes": { <page_id>: <PageRuntime> } }
├── server.pid              (drop server PID, when not under systemd)
├── port                    (server port)
├── host                    (override IP)
├── tunnel.json             (server tunnel state)
├── logs/
│   ├── <page_id>.app.log         user app stdout+stderr
│   ├── <page_id>.proxy.log       drop.proxy stdout+stderr
│   ├── <page_id>.tunnel.log      cloudflared --logfile
│   └── server.log                drop server (if not systemd)
└── bin/cloudflared
```

`pages.json.v1.bak` written on first v2 run (see Migration).

### Lifecycle API

```python
# lifecycle/app.py
@dataclass
class StartResult:
    url: str
    creds: tuple[str, str] | None       # (user, plaintext_password) for one-time print
    warnings: list[str]                 # side-door, cleartext, ...
    error: str | None = None
    hint: str | None = None

def start_app(page: Page, *, auth_insecure: bool, no_tunnel: bool, allow_side_door: bool) -> StartResult:
    """
    Atomic start: app → proxy → tunnel. Any failure rolls back prior phases.
    Side-door probe after app starts: if --auth and not --allow-side-door and app reachable
    on external IP, rolls back and returns error.
    """

def stop_app(page: Page) -> None:
    """Stop tunnel → proxy → app. Idempotent. Always clears runtime."""
```

CLI becomes thin:

```python
def cmd_start_app(args):
    page = storage.get_page(args.name)
    if not page: return _err(f"'{args.name}' not found")
    result = lifecycle.start_app(
        page,
        auth_insecure=args.auth_insecure,
        no_tunnel=args.no_tunnel,
        allow_side_door=args.allow_side_door,
    )
    return _print_start_result(result)
```

### Process discipline (lifecycle/process.py)

```python
def spawn_managed(
    cmd: list[str],
    *,
    log_file: Path | None = None,   # if given: stdout+stderr piped to file
    cwd: Path | None = None,
) -> subprocess.Popen:
    """
    Single source of truth for detached subprocess spawning.
    - Always start_new_session=True
    - Either log_file (file handle) or DEVNULL — never undrained PIPE
    """

def wait_port(host: str, port: int, timeout: float = 5.0) -> bool: ...
def wait_alive(pid: int, after: float = 1.0) -> bool: ...
def kill_pg(pid: int) -> bool: ...
```

Every subprocess in v2 goes through `spawn_managed`. Bug 1 (PIPE) becomes architecturally impossible.

### Tunnel module (lifecycle/tunnel.py)

```python
def start_tunnel(port: int, *, log_file: Path) -> tuple[str, int] | None:
    """
    Spawn cloudflared via spawn_managed with --logfile <log_file>.
    Tail log_file for the trycloudflare.com URL (timeout 30s).
    No PIPE — log file is the IO channel.
    Returns (url, pid) or None.
    """
```

### Side-door enforcement (lifecycle/app.py)

After `start_app` spawns the user app:

```python
def _probe_side_door(host: str, app_port: int) -> bool:
    """Return True if app is reachable on a non-loopback interface."""
    # Try external IP, return True iff connect() succeeds within 1s.

if auth and not allow_side_door:
    external_host = detect_ip()
    if external_host != "127.0.0.1" and _probe_side_door(external_host, app_port):
        rollback_and_return_error(
            "App listens on 0.0.0.0; --auth would advertise security that does not exist.",
            hint="Bind app to 127.0.0.1 in --run, or pass --allow-side-door for explicit override."
        )
```

`--allow-side-door` flag is registered at `drop add` time (saved to Page), not at start time — because the choice is about *this app*'s binding behaviour, not a per-run decision.

### Atomic lifecycle pseudo-code

```python
def start_app(page, ...):
    runtime = runtime.get_or_create(page.page_id)

    # Phase 1: app
    # User-provided run_cmd is invoked with shell=True (matches v1 behaviour); the
    # user is the trust boundary for what gets executed.
    app_log = logs_path(page) / "app.log"
    app_proc = spawn_managed(page.run_cmd, shell=True, log_file=app_log, cwd=page.source.parent)
    # Wait for app to actually listen, not just exist (PID-alive is too weak —
    # the side-door probe below races otherwise).
    if not wait_port("127.0.0.1", page.port, timeout=5) and not wait_port(detect_ip(), page.port, timeout=1):
        kill_pg(app_proc.pid)
        return StartResult(error="app did not bind port within 5s", hint=f"see {app_log}")
    runtime.app_pid = app_proc.pid; runtime.save()

    # Side-door probe
    if page.auth and not page.allow_side_door:
        external_host = detect_ip()
        if external_host != "127.0.0.1" and _probe_side_door(external_host, page.port):
            kill_pg(app_proc.pid); runtime.clear()
            return StartResult(
                error="app binds 0.0.0.0 with --auth (side-door would bypass auth)",
                hint="Bind app to 127.0.0.1 in --run, or `drop add ... --allow-side-door` to override."
            )

    # Phase 2: proxy (if auth)
    if page.auth:
        bind = "0.0.0.0" if auth_insecure else "127.0.0.1"
        proxy_log = logs_path(page) / "proxy.log"
        proxy_port = allocate_free_port()
        proxy_proc = spawn_managed(
            [sys.executable, "-m", "drop.proxy", "--page-id", page.page_id,
             "--proxy-port", str(proxy_port), "--app-port", str(page.port), "--bind", bind],
            log_file=proxy_log,
        )
        if not wait_port(bind if bind != "0.0.0.0" else "127.0.0.1", proxy_port):
            kill_pg(app_proc.pid); runtime.clear()
            return StartResult(error="proxy failed to start", hint=f"see {proxy_log}")
        runtime.proxy_pid = proxy_proc.pid
        runtime.proxy_port = proxy_port
        runtime.save()

    # Phase 3: tunnel
    # needs_tunnel = always-on for apps with --auth (HTTPS for basic auth),
    # NAT-detect-driven for non-auth apps. --no-tunnel / --auth-insecure override.
    target_port = runtime.proxy_port or page.port
    needs_tunnel = (not no_tunnel) and (
        (page.auth and not auth_insecure) or is_behind_nat()
    )
    if needs_tunnel:
        tunnel_log = logs_path(page) / "tunnel.log"
        result = tunnel.start_tunnel(target_port, log_file=tunnel_log)
        if not result:
            kill_pg(runtime.proxy_pid)
            kill_pg(runtime.app_pid)
            runtime.clear()
            return StartResult(error="tunnel failed", hint="...")
        runtime.tunnel_url, runtime.tunnel_pid = result
        runtime.save()

    return StartResult(url=runtime.tunnel_url or f"http://{host}:{target_port}/", ...)
```

Same pattern for `stop_app`: always tunnel→proxy→app, idempotent, clears runtime at end.

## Migration v1 → v2

On first `drop` run with v2, detect old schema and migrate:

```python
def maybe_migrate():
    pages_file = config.PAGES_FILE
    if not pages_file.exists():
        return
    data = json.loads(pages_file.read_text())
    if isinstance(data, dict) and data.get("version") == 2:
        return  # already migrated
    # v1: flat dict { page_id: page_info }
    backup = pages_file.with_suffix(".json.v1.bak")
    shutil.copy(pages_file, backup)
    print(f"Migrating to v2 schema (backup at {backup})", file=sys.stderr)
    migrated_pages = {}
    migrated_runtimes = {}
    for pid, info in data.items():
        migrated_pages[pid] = _v1_to_page(info).to_dict()
        migrated_runtimes[pid] = _v1_to_runtime(info, pid).to_dict()
    pages_file.write_text(json.dumps({"version": 2, "pages": migrated_pages}, indent=2))
    config.RUNTIME_FILE.write_text(json.dumps({"version": 2, "runtimes": migrated_runtimes}, indent=2))
```

`_v1_to_page` strips runtime fields (pid/proxy_pid/tunnel_url/...); `_v1_to_runtime` carries them across so live processes stay tracked.

## Configuration / env overrides (config.py)

```python
DROP_HOME = Path(os.environ.get("DROP_HOME") or Path.home() / ".drop")
PAGES_FILE = DROP_HOME / "pages.json"
RUNTIME_FILE = DROP_HOME / "runtime.json"
LOGS_DIR = DROP_HOME / "logs"
BIN_DIR = DROP_HOME / "bin"
SERVER_PID_FILE = DROP_HOME / "server.pid"
SERVER_PORT_FILE = DROP_HOME / "port"
SERVER_HOST_FILE = DROP_HOME / "host"
SERVER_TUNNEL_FILE = DROP_HOME / "tunnel.json"

CLOUDFLARED_BIN_OVERRIDE = os.environ.get("DROP_CLOUDFLARED_BIN")  # tests use this

DEFAULT_SERVER_PORT = 8080
AUTH_REALM = "drop"
DEFAULT_AUTH_USER = "drop"
SCHEMA_VERSION = 2
```

`DROP_HOME` env override is the test isolation primitive — every test gets a tmp dir.

## Logging

Every spawned process writes to `~/.drop/logs/<page_id>.<role>.log` via `spawn_managed(log_file=...)`. Files are append-mode; rotation deferred to V3. Cloudflared uses `--logfile`. The drop server (when not under systemd) logs to `~/.drop/logs/server.log`.

New CLI command:

```bash
drop logs <name>           # tails app.log
drop logs <name> --proxy   # tails proxy.log
drop logs <name> --tunnel  # tails tunnel.log
```

Implemented as a thin `tail -f`-like reader (stdlib).

## systemd unit (no more runtime regex)

`install.sh` writes:

```ini
[Service]
ExecStart=/home/user/.local/bin/drop _serve
EnvironmentFile=-%h/.drop/systemd.env
Restart=on-failure
```

`drop start` writes `~/.drop/systemd.env` with `DROP_PORT=8080`, then `systemctl --user restart drop`. `drop _serve` (new internal subcommand) reads `DROP_PORT` and calls `server.run_server(int(os.environ["DROP_PORT"]))`. No regex over installed unit file.

## Cross-platform

- Linux: full systemd integration (as v1).
- macOS: PID-based fallback (no autostart). launchd integration → V3 backlog.
- Windows: not supported.

Existing behaviour preserved.

## Phases (build order)

Each phase = its own spec → plan → execute under superpowers. Each phase ends green tests + commit. Branch `v2` lives until Phase 11 (cutover).

| # | Phase | Output | Tested by |
|---|---|---|---|
| 0 | Test harness + skeleton | `pytest` runs, `conftest.py` with `DROP_HOME` fixture, empty modules | self-test (pytest --collect-only) |
| 1 | Pure modules: `utils.py`, `manifest.py`, `auth.py`, `config.py` | All pure logic, no I/O | unit only |
| 2 | Storage + Runtime: `storage.py`, `runtime.py` + v1→v2 migration | CRUD with UNIQUE constraint | unit + migration tests with real JSON |
| 3 | Process helpers: `lifecycle/process.py` | spawn_managed, kill_pg, wait_alive, wait_port | unit + subprocess (sleep + check) |
| 4 | Tunnel: `lifecycle/tunnel.py` with --logfile | tunnel start/stop, URL parsing | integration with real cloudflared (optional skip via env) |
| 5 | Proxy: `proxy.py` (port from v1, no changes) | basic-auth + passthrough + SSRF guard + Upgrade reject | integration with fake upstream |
| 6 | Lifecycle: `lifecycle/app.py` + `lifecycle/server.py` (atomic + rollback + side-door probe) | start/stop with all failure paths | integration with real subprocesses |
| 7 | CLI: `cli.py` thin dispatch | All `drop <subcmd>` commands | E2E via `subprocess.run(["drop", ...])` |
| 8 | Server: `server.py` polish (name in index, html escape, unified URL) | Flask routes | integration via Flask test client |
| 9 | Logs command: `drop logs <name>` | tail readers | E2E |
| 10 | install.sh updates: systemd EnvironmentFile, no regex | install.sh idempotent | manual smoke |
| 11 | Cutover: merge v2 → main, delete old code | clean repo | full E2E sanity |

Phases 4, 5, 8 are independent of each other and can be parallelized via subagents if it speeds things up.

## Pre-v2 hotfix on main

Before v2 dev starts, hotfix v1 on main so the user has a working drop:

- **Fix 1:** `tunnel.start_tunnel` — add `start_new_session=True` and either switch to `--logfile` or start a drainer thread for stderr.
- **Fix 2:** `storage.add_page` — raise on duplicate name (or return existing).
- Tag v0.3.0, push.

These same fixes inform v2's `lifecycle/tunnel.py` and `storage.py`.

## Out of scope (V3 backlog)

- Daemon architecture
- Named tunnels (persistent URLs)
- launchd integration (macOS)
- Log rotation
- Plugin/hook API for custom auth backends, custom tunnel providers
- Web UI / TUI for managing pages

## Implementation notes for plan-writers

- Each phase's spec should re-link this design doc and only specify additional details.
- Tests written BEFORE implementation within each phase (TDD per phase).
- Commits per task within a phase, per existing convention (`feat:`, `refactor:`, `fix:`).
- For Phase 11 cutover: keep v1 history in git via merge commit (do not squash) — provenance matters for the rewrite.

## Success criteria

- All 6 v1 bugs impossible by construction (audited via tests).
- `cli.py` < 250 LOC.
- ≥80% line coverage on `lifecycle/`, `storage.py`, `auth.py`, `manifest.py`, `proxy.py`.
- E2E `drop add → drop start → curl with auth → drop stop` passes against real cloudflared.
- Migration of an existing user's `pages.json` v1 → v2 preserves all entries and running PIDs.
- v2 published to a release tag (v1.0.0) with CHANGELOG describing the rewrite.
