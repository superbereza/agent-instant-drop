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
