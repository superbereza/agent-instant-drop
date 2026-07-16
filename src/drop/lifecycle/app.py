"""Atomic app lifecycle.

start_app:
  1. If runtime says already running → idempotent return
  2. Spawn app via shell run_cmd, log to ~/.drop/logs/<id>.app.log
  3. Wait for app port to bind (5s)
  4. If auth + not allow_side_door: probe external IP for side-door, rollback if open
  5. If auth: spawn proxy on auto-allocated port, log to <id>.proxy.log
  6. Decide tunnel: --auth implies tunnel unless --auth-insecure;
     non-auth uses NAT-detect heuristic. --no-tunnel overrides.
  7. If tunnel needed but no cloudflared / failed: rollback (kill proxy+app)
  8. On success: save_runtime, build StartResult with url + creds + warnings

stop_app:
  Tunnel → proxy → app → clear_runtime. Idempotent.
"""

from dataclasses import dataclass, field
import socket
import sys
from pathlib import Path

from .. import config, runtime, storage, utils
from . import process as proc, tunnel as tunnel_mod


@dataclass
class StartResult:
    url: str = ""
    creds: tuple[str, str] | None = None  # (user, plaintext password) — for one-time print
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    hint: str | None = None


_SIDE_DOOR_WARNING = (
    "--auth protects tunnel only. If your app binds 0.0.0.0 on a public IP, "
    "app port is still reachable bypassing auth. Use --host 127.0.0.1 in --run."
)

_CLEARTEXT_WARNING = (
    "CLEARTEXT: basic auth credentials transmitted in base64 over plain HTTP. "
    "Anyone on the network path can read them. Use only on trusted LAN."
)


def _logs_dir() -> Path:
    """LOGS_DIR honoring current DROP_HOME env (re-read for test isolation)."""
    import os
    home_env = os.environ.get("DROP_HOME")
    base = Path(home_env) if home_env else Path.home() / ".drop"
    log_dir = base / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def _probe_side_door(host: str, port: int, timeout: float = 1.0) -> bool:
    """Return True if (host, port) accepts a TCP connection from outside."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _already_running(page: storage.Page) -> "StartResult | None":
    """If runtime claims this app is alive, return idempotent StartResult."""
    rt = runtime.get_runtime(page.page_id)
    if rt.app_pid > 0 and rt.is_app_alive():
        url = rt.tunnel_url or f"http://127.0.0.1:{page.port}/"
        return StartResult(url=url, warnings=["app already running"])
    return None


def start_app(page: storage.Page, *, auth_insecure: bool, no_tunnel: bool) -> StartResult:
    """Atomic start: app → proxy → tunnel. Any phase failure rolls back."""
    # Idempotent: already running?
    existing = _already_running(page)
    if existing is not None:
        return existing

    rt = runtime.PageRuntime(page_id=page.page_id)
    warnings: list[str] = []

    # Phase 1: spawn app
    app_log = _logs_dir() / f"{page.page_id}.app.log"
    try:
        app_proc = proc.spawn_managed(page.run_cmd, shell=True, log_file=app_log)
    except Exception as e:
        return StartResult(error=f"failed to spawn app: {e}", hint=f"see {app_log}")

    # Wait for app to actually bind its port
    if not utils.wait_for_port("127.0.0.1", page.port, timeout=5):
        proc.kill_pg(app_proc.pid)
        return StartResult(
            error=f"app did not bind 127.0.0.1:{page.port} within 5s",
            hint=f"see {app_log}",
        )
    rt.app_pid = app_proc.pid
    runtime.save_runtime(rt)

    # Side-door enforcement (only meaningful with auth)
    if page.auth and not page.allow_side_door:
        external_host = utils.detect_ip()
        if external_host and external_host != "127.0.0.1":
            if _probe_side_door(external_host, page.port):
                proc.kill_pg(app_proc.pid)
                runtime.clear_runtime(page.page_id)
                return StartResult(
                    error=f"app listens on 0.0.0.0:{page.port}; --auth would not protect "
                          f"the direct path (side-door)",
                    hint="Bind app to 127.0.0.1, or `drop add ... --allow-side-door` "
                         "to override.",
                )

    if page.auth:
        warnings.append(_SIDE_DOOR_WARNING)

    # Phase 2: spawn proxy (if auth)
    proxy_port = 0
    proxy_proc = None
    if page.auth is not None:
        bind = "0.0.0.0" if auth_insecure else "127.0.0.1"
        proxy_port = utils.allocate_free_port()
        proxy_log = _logs_dir() / f"{page.page_id}.proxy.log"
        proxy_cmd = [
            sys.executable, "-m", "drop.proxy",
            "--page-id", page.page_id,
            "--proxy-port", str(proxy_port),
            "--app-port", str(page.port),
            "--bind", bind,
        ]
        proxy_proc = proc.spawn_managed(proxy_cmd, log_file=proxy_log)
        probe_host = "127.0.0.1" if bind == "0.0.0.0" else bind
        if not utils.wait_for_port(probe_host, proxy_port, timeout=5):
            proc.kill_pg(proxy_proc.pid)
            proc.kill_pg(app_proc.pid)
            runtime.clear_runtime(page.page_id)
            return StartResult(
                error="proxy failed to start",
                hint=f"see {proxy_log}",
            )
        rt.proxy_pid = proxy_proc.pid
        rt.proxy_port = proxy_port
        runtime.save_runtime(rt)

    target_port = proxy_port if page.auth else page.port

    # Phase 3: tunnel
    want_tunnel = (not no_tunnel) and (
        (page.auth is not None and not auth_insecure) or utils.is_behind_nat()
    )

    if want_tunnel:
        cloudflared = utils.find_cloudflared()
        if cloudflared is None:
            # Roll back if auth required tunnel
            if page.auth is not None:
                if proxy_proc:
                    proc.kill_pg(proxy_proc.pid)
                proc.kill_pg(app_proc.pid)
                runtime.clear_runtime(page.page_id)
                return StartResult(
                    error="cloudflared not installed",
                    hint="Run drop-install-env, or pass --auth-insecure to allow cleartext.",
                )
            # Non-auth + NAT but no cloudflared: degrade gracefully
            warnings.append("behind NAT but cloudflared not found")
            url = f"http://{utils.detect_ip()}:{target_port}/"
        else:
            tunnel_log = _logs_dir() / f"{page.page_id}.tunnel.log"
            result = tunnel_mod.start_tunnel(target_port, log_file=tunnel_log)
            if result is None:
                if page.auth is not None:
                    if proxy_proc:
                        proc.kill_pg(proxy_proc.pid)
                    proc.kill_pg(app_proc.pid)
                    runtime.clear_runtime(page.page_id)
                    return StartResult(
                        error="tunnel failed to start",
                        hint=f"see {tunnel_log}; retry or pass --auth-insecure",
                    )
                warnings.append("tunnel failed; falling back to direct URL")
                url = f"http://{utils.detect_ip()}:{target_port}/"
            else:
                tunnel_url, tunnel_pid = result
                rt.tunnel_url = tunnel_url
                rt.tunnel_pid = tunnel_pid
                runtime.save_runtime(rt)
                url = tunnel_url
    else:
        # No tunnel wanted
        if page.auth is not None and no_tunnel and not auth_insecure:
            # --no-tunnel + --auth without --auth-insecure → refuse
            if proxy_proc:
                proc.kill_pg(proxy_proc.pid)
            proc.kill_pg(app_proc.pid)
            runtime.clear_runtime(page.page_id)
            return StartResult(
                error="--no-tunnel conflicts with --auth (would expose cleartext credentials)",
                hint="Pass --auth-insecure to confirm cleartext over HTTP.",
            )
        if auth_insecure and page.auth is not None:
            warnings.append(_CLEARTEXT_WARNING)
        # Bind URL: 127.0.0.1 for local (no auth, no auth_insecure), otherwise detect_ip
        if proxy_port > 0:
            host = utils.detect_ip() if auth_insecure else "127.0.0.1"
        else:
            host = "127.0.0.1"
        url = f"http://{host}:{target_port}/"

    creds = (page.auth.user, "<hidden>") if page.auth else None
    return StartResult(url=url, creds=creds, warnings=warnings)


def stop_app(page: storage.Page) -> None:
    """Stop tunnel → proxy → app. Idempotent. Clears runtime at end."""
    rt = runtime.get_runtime(page.page_id)
    if rt.tunnel_pid > 0:
        tunnel_mod.stop_tunnel(rt.tunnel_pid)
    if rt.proxy_pid > 0:
        proc.kill_pg(rt.proxy_pid)
    if rt.app_pid > 0:
        proc.kill_pg(rt.app_pid)
    runtime.clear_runtime(page.page_id)
