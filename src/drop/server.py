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
from datetime import datetime, UTC, timedelta
from pathlib import Path

from flask import Flask, Response, abort, make_response, request, send_file

from . import config, storage
from .auth import RateLimiter, verify_password
from .manifest import load_manifest, safe_path


app = Flask(__name__)
_rate_limiter = RateLimiter(max_attempts=3, window_sec=60)


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
    return _INDEX_TEMPLATE.format(listing=listing)


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
            return make_response(_login_form(), 200)

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

    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "0.0.0.0")
    if not _rate_limiter.check_and_record(ip, page_id):
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

def run_server(port: int = 8080, host: str = "0.0.0.0") -> None:
    app.run(host=host, port=port, debug=False, use_reloader=False)
