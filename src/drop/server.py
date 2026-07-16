"""Flask static-page server.

Routes:
  GET  /                            — index of all pages (shows name, not id)
  GET  /p/<page_id>/[filepath]     — serve content or login form
  POST /p/<page_id>/[filepath]     — accept password, set cookie

Auth: per-page password via cookie (drop_auth_<page_id>). Rate-limited
(3 attempts/min/IP/page) via auth.RateLimiter.

All user-influenced HTML is escaped via html.escape.
"""

import html
import mimetypes
import os
from datetime import datetime, UTC, timedelta
from pathlib import Path

from flask import Flask, Response, abort, make_response, request, send_file

from . import config, storage
from .auth import RateLimiter, verify_password
from .manifest import load_manifest, safe_path


app = Flask(__name__)

# Two limiters guard every password endpoint:
#   _ip_limiter     — per (remote_addr, page): stops a single host hammering.
#   _global_limiter — per page regardless of IP: the real brute-force cap. It
#                     matters because all tunnel/tailscale traffic arrives as
#                     127.0.0.1, and because the source IP must never be taken
#                     from a client-controlled header (X-Forwarded-For spoofing).
_ip_limiter = RateLimiter(max_attempts=config.RL_PER_IP_MAX, window_sec=config.RL_WINDOW_SEC)
_global_limiter = RateLimiter(max_attempts=config.RL_GLOBAL_MAX, window_sec=config.RL_WINDOW_SEC)


def _client_ip() -> str:
    """Real TCP peer address. Never trust X-Forwarded-For here — it is fully
    client-controlled and was previously the rate-limit bypass."""
    return request.remote_addr or "0.0.0.0"


def _rate_ok(ip: str, page_key: str) -> bool:
    """True if this attempt is within both the global and per-IP limits."""
    if not _global_limiter.check_and_record("*", page_key):
        return False
    if not _ip_limiter.check_and_record(ip, page_key):
        return False
    return True


def _index_hash_file() -> Path:
    base = Path(os.environ.get("DROP_HOME") or Path.home() / ".drop")
    return base / "index.hash"


def _read_index_hash() -> str:
    """Hash of the index (dashboard) password, or '' if none configured."""
    f = _index_hash_file()
    if not f.exists():
        return ""
    try:
        return f.read_text().strip()
    except OSError:
        return ""


# ---- Index ----

_INDEX_TEMPLATE = """\
<!doctype html>
<html><head><meta charset="utf-8"><title>drop</title>
<style>body{{font-family:system-ui;padding:2em}}a{{color:#0366d6}}</style>
</head><body>
<h1>drop</h1>
{listing}
</body></html>
"""


@app.route("/")
def index():
    index_hash = _read_index_hash()
    if not index_hash:
        # No dashboard password configured → never enumerate published pages.
        # (Enumeration leaked every page's name + description to anyone with the
        # server URL.) Set one with `drop index-password` to enable the listing.
        resp = make_response(_INDEX_TEMPLATE.format(
            listing="<p>Index disabled. Run <code>drop index-password</code> "
                    "to enable the dashboard.</p>"), 200)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    if request.cookies.get("drop_index_auth") != index_hash:
        resp = make_response(_login_form(), 401)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    pages = storage.list_pages()
    if not pages:
        listing = "<p>No pages published.</p>"
    else:
        items = []
        for pid, page in pages.items():
            label = page.name or pid
            url = f"/p/{pid}/{page.name}/" if page.name else f"/p/{pid}/"
            desc = page.description or ""
            items.append(
                f'<li><a href="{html.escape(url)}">{html.escape(label)}</a>'
                f" — {html.escape(desc)}</li>"
            )
        listing = "<ul>" + "".join(items) + "</ul>"
    resp = make_response(_INDEX_TEMPLATE.format(listing=listing), 200)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/", methods=["POST"])
def auth_index():
    index_hash = _read_index_hash()
    if not index_hash:
        abort(404)
    if not _rate_ok(_client_ip(), "__index__"):
        return make_response(_login_form("Too many attempts; try again in a minute."), 429)
    if not verify_password(request.form.get("password", ""), index_hash):
        resp = make_response(_login_form("Invalid password"), 401)
        resp.headers["Cache-Control"] = "no-store"
        return resp
    resp = make_response("", 303)
    resp.headers["Location"] = "/"
    resp.set_cookie("drop_index_auth", index_hash, max_age=15 * 60,
                    httponly=True, samesite="Lax")
    return resp


# ---- Page serving ----

_LOGIN_FORM = """\
<!doctype html>
<html><head><meta charset="utf-8"><title>drop — password</title>
<style>body{{font-family:system-ui;padding:2em;max-width:30em}}
input{{font-size:1.1em;padding:0.4em;width:100%}}
button{{padding:0.5em 1em;margin-top:0.5em}}
.err{{color:#c00;font-weight:bold}}
</style></head><body>
<h1>Password required</h1>
{err}
<form method="post">
<input type="password" name="password" placeholder="Password" autofocus>
<button type="submit">View</button>
</form>
</body></html>
"""


def _login_form(error_msg: str = "") -> str:
    err = f'<p class="err">{html.escape(error_msg)}</p>' if error_msg else ""
    return _LOGIN_FORM.format(err=err)


@app.route("/p/<page_id>/", defaults={"filepath": ""})
@app.route("/p/<page_id>/<path:filepath>")
def serve_page(page_id: str, filepath: str):
    page = storage.get_page(page_id)
    if page is None or page.type != "static":
        abort(404)

    # Slug in URL: /p/<id>/<name>/file → strip the leading <name>/
    if page.name and filepath.startswith(page.name + "/"):
        filepath = filepath[len(page.name) + 1:]
    elif page.name and filepath == page.name:
        filepath = ""

    # Auth check
    cookie_name = f"drop_auth_{page_id}"
    if page.password_hash:
        cookie = request.cookies.get(cookie_name)
        if cookie != page.password_hash:
            # 401, not 200: an unauthenticated GET must not look "healthy" to
            # uptime monitors / CDNs, and the password form must never be
            # cached in place of the real content. No WWW-Authenticate header
            # (the gate is cookie-based, not HTTP Basic).
            resp = make_response(_login_form(), 401)
            resp.headers["Cache-Control"] = "no-store"
            return resp

    # Resolve content
    if page.source.is_dir():
        m = load_manifest(page.source)
        if m is None:
            abort(404)
        # Default to index.html on root
        target_rel = filepath or "index.html"
        target = safe_path(page.source, target_rel, manifest=m)
    else:
        # Single file: ignore filepath
        target = safe_path(page.source.parent, page.source.name)

    if target is None or not target.exists() or not target.is_file():
        abort(404)

    mime, _ = mimetypes.guess_type(target.name)
    return send_file(str(target), mimetype=mime or "application/octet-stream")


@app.route("/p/<page_id>/", methods=["POST"], defaults={"filepath": ""})
@app.route("/p/<page_id>/<path:filepath>", methods=["POST"])
def auth_page(page_id: str, filepath: str):
    page = storage.get_page(page_id)
    if page is None or page.type != "static":
        abort(404)

    if not _rate_ok(_client_ip(), page_id):
        return make_response(_login_form("Too many attempts; try again in a minute."), 429)

    password = request.form.get("password", "")
    if not verify_password(password, page.password_hash):
        return make_response(_login_form("Invalid password"), 401)

    # Success — set cookie and redirect to GET
    resp = make_response("", 303)
    resp.headers["Location"] = request.path
    cookie_name = f"drop_auth_{page_id}"
    resp.set_cookie(
        cookie_name,
        page.password_hash,
        max_age=15 * 60,  # 15 minutes
        httponly=True,
        samesite="Lax",
    )
    return resp


# ---- Entry point used by lifecycle/server.py ----

def run_server(port: int | None = None, host: str | None = None) -> None:
    import os
    if port is None:
        port = int(os.environ.get("DROP_PORT", "8080"))
    if host is None:
        # Bind loopback by default: access is via tailscale-serve / cloudflared,
        # both of which connect over 127.0.0.1. Binding 0.0.0.0 would put the
        # server directly on the public internet. Override with DROP_HOST if
        # you truly need a wider bind.
        host = os.environ.get("DROP_HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=False, use_reloader=False)
