# v2 Phase 8 — server.py (Flask static-page server, full impl)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Replace the Phase 6 minimal `server.py` stub with the full Flask static-page server. Fixes the v1 bug "index page shows page_id instead of name". Uses `html.escape` for all user-influenced output. Unified URL generation.

**Architecture:** Single Flask app with three routes (`/` index, `/p/<page_id>/[file]` serve, `POST /p/<page_id>/[file]` auth). Reuses v2 `auth.verify_password`, `auth.RateLimiter`, `storage.list_pages`, `storage.get_page`, `manifest.safe_path`, `manifest.load_manifest`.

**Tech Stack:** Flask, Phase 0-6 modules.

**Reference spec:** `docs/2026-05-20-v2-greenfield-design.md` — server polish notes.

**Branch:** `v2`.

---

## Task 1: `server.py` — full Flask app with polish

**Files:**
- Modify: `src/drop/server.py`
- Create: `tests/integration/test_server_routes.py`

- [ ] **Step 1: Write tests**

Create `tests/integration/test_server_routes.py`:

```python
"""Integration tests for drop.server — Flask routes with Flask test client.

No subprocess — uses Flask's test client to exercise routes directly.
"""

import html as html_lib
from pathlib import Path

import pytest

from drop import storage, server, auth


@pytest.fixture
def client(drop_home):
    server.app.config["TESTING"] = True
    return server.app.test_client()


# Index page

def test_index_lists_no_pages_when_empty(client):
    r = client.get("/")
    assert r.status_code == 200
    # No <a> link to a page
    assert b"<a href=\"/p/" not in r.data


def test_index_shows_name_not_page_id(client, drop_home, tmp_path):
    src = tmp_path / "report.html"
    src.write_text("<h1>x</h1>")
    storage.add_page(storage.Page(
        page_id="abc123longid",
        source=src,
        type="static",
        name="my-report",
        is_public=True,
    ))
    r = client.get("/")
    assert r.status_code == 200
    body = r.data.decode()
    # Link shows the slug, not the secret id
    assert ">my-report<" in body
    # But the URL still includes the secret
    assert "/p/abc123longid/" in body


def test_index_escapes_user_content(client, drop_home, tmp_path):
    src = tmp_path / "x.html"
    src.write_text("ok")
    storage.add_page(storage.Page(
        page_id="evilpage",
        source=src,
        type="static",
        name="<script>alert('xss')</script>",
        description="desc <img src=x onerror=alert(1)>",
        is_public=True,
    ))
    r = client.get("/")
    body = r.data.decode()
    # No raw <script>
    assert "<script>alert" not in body
    # The escaped version is present
    assert "&lt;script&gt;" in body or "&amp;lt;script&amp;gt;" in body


# Static page serving

def test_serve_public_static_file(client, drop_home, tmp_path):
    src = tmp_path / "report.html"
    src.write_text("<h1>hello</h1>")
    storage.add_page(storage.Page(
        page_id="pubreport",
        source=src,
        type="static",
        is_public=True,
    ))
    r = client.get("/p/pubreport/")
    assert r.status_code == 200
    assert b"<h1>hello</h1>" in r.data


def test_serve_404_for_unknown_page(client, drop_home):
    r = client.get("/p/nonexistent/")
    assert r.status_code == 404


def test_serve_with_auth_requires_password(client, drop_home, tmp_path):
    src = tmp_path / "secret.html"
    src.write_text("secret content")
    storage.add_page(storage.Page(
        page_id="secret1",
        source=src,
        type="static",
        password_hash=auth.hash_password("pw"),
    ))
    r = client.get("/p/secret1/")
    # No cookie → show login form (200 with form)
    assert r.status_code in (200, 401)
    assert b"password" in r.data.lower() or b"Password" in r.data


def test_serve_with_correct_password_returns_content(client, drop_home, tmp_path):
    src = tmp_path / "secret.html"
    src.write_text("secret content")
    storage.add_page(storage.Page(
        page_id="secret2",
        source=src,
        type="static",
        password_hash=auth.hash_password("right"),
    ))
    r = client.post("/p/secret2/", data={"password": "right"}, follow_redirects=True)
    assert r.status_code == 200
    assert b"secret content" in r.data


def test_serve_with_wrong_password_rejected(client, drop_home, tmp_path):
    src = tmp_path / "secret.html"
    src.write_text("secret content")
    storage.add_page(storage.Page(
        page_id="secret3",
        source=src,
        type="static",
        password_hash=auth.hash_password("right"),
    ))
    r = client.post("/p/secret3/", data={"password": "wrong"})
    assert r.status_code == 401


# Directory + manifest

def test_serve_directory_requires_index_html(client, drop_home, tmp_path):
    src = tmp_path / "site"
    src.mkdir()
    (src / "index.html").write_text("<h1>home</h1>")
    (src / ".drop-publish").write_text("index.html\n")
    storage.add_page(storage.Page(
        page_id="sitepage",
        source=src,
        type="static",
        is_public=True,
    ))
    r = client.get("/p/sitepage/")
    assert r.status_code == 200
    assert b"<h1>home</h1>" in r.data


def test_serve_directory_manifest_blocks_unlisted(client, drop_home, tmp_path):
    src = tmp_path / "site"
    src.mkdir()
    (src / "index.html").write_text("<h1>home</h1>")
    (src / "secret.txt").write_text("nope")
    (src / ".drop-publish").write_text("index.html\n")
    storage.add_page(storage.Page(
        page_id="sitepage2",
        source=src,
        type="static",
        is_public=True,
    ))
    r = client.get("/p/sitepage2/secret.txt")
    assert r.status_code in (403, 404)


def test_serve_blocks_env_file(client, drop_home, tmp_path):
    src = tmp_path / "site"
    src.mkdir()
    (src / "index.html").write_text("ok")
    (src / ".env").write_text("SECRET=x")
    (src / ".drop-publish").write_text("index.html\n.env\n")
    storage.add_page(storage.Page(
        page_id="envblock",
        source=src,
        type="static",
        is_public=True,
    ))
    r = client.get("/p/envblock/.env")
    assert r.status_code in (403, 404)
```

- [ ] **Step 2: Run tests, verify failures**

- [ ] **Step 3: Implement `src/drop/server.py`**

Replace with:

```python
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
```

- [ ] **Step 4: Run tests**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest tests/integration/test_server_routes.py -v
```

Expected: ~11 passed.

- [ ] **Step 5: Commit**

```bash
git add src/drop/server.py tests/integration/test_server_routes.py
git commit -m "feat(v2): server.py — full Flask app, name in index, html.escape, rate-limited cookie auth"
```

---

## Phase 8 Self-Review

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest -v
```

Push:
```bash
git push origin v2
```

## Phase 9 Readiness

After Phase 8, `drop logs <name>` (Phase 9) can read from `~/.drop/logs/<page_id>.<role>.log` populated by lifecycle phases.
