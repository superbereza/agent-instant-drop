"""Tests for drop.storage — Page dataclass, CRUD, UNIQUE name constraint."""

from datetime import datetime, UTC
from pathlib import Path

import pytest

from drop import storage


# AuthConfig dataclass

def test_auth_config_fields(drop_home):
    a = storage.AuthConfig(scheme="basic", user="drop", password_hash="sha256:abc")
    assert a.scheme == "basic"
    assert a.user == "drop"
    assert a.password_hash == "sha256:abc"


# Page dataclass — defaults

def test_page_static_minimal(drop_home):
    p = storage.Page(page_id="abc", source=Path("/tmp/x"), type="static")
    assert p.page_id == "abc"
    assert p.name == ""
    assert p.description == ""
    assert p.is_public is False
    assert p.password_hash == ""
    assert p.run_cmd == ""
    assert p.port == 0
    assert p.auth is None
    assert p.allow_side_door is False
    assert p.rewrite_host is False


def test_page_app_with_auth(drop_home):
    a = storage.AuthConfig(scheme="basic", user="drop", password_hash="h")
    p = storage.Page(
        page_id="abc",
        source=Path("/tmp/x"),
        type="app",
        name="myapp",
        run_cmd="flask run",
        port=5000,
        auth=a,
    )
    assert p.type == "app"
    assert p.name == "myapp"
    assert p.auth.user == "drop"


# Round-trip via JSON

def test_save_and_load_round_trip(drop_home):
    a = storage.AuthConfig(scheme="basic", user="u", password_hash="h")
    p1 = storage.Page(page_id="abc", source=Path("/tmp/x"), type="app", name="a1",
                      run_cmd="cmd", port=1, auth=a)
    p2 = storage.Page(page_id="def", source=Path("/tmp/y"), type="static",
                      name="s1", password_hash="ph")
    storage.save_pages({"abc": p1, "def": p2})
    loaded = storage.load_pages()
    assert set(loaded.keys()) == {"abc", "def"}
    assert loaded["abc"].name == "a1"
    assert loaded["abc"].auth is not None
    assert loaded["abc"].auth.user == "u"
    assert loaded["def"].type == "static"
    assert loaded["def"].password_hash == "ph"


def test_load_pages_empty_when_no_file(drop_home):
    assert storage.load_pages() == {}


def test_save_pages_writes_versioned_envelope(drop_home):
    import json
    p = storage.Page(page_id="x", source=Path("/tmp/x"), type="static")
    storage.save_pages({"x": p})
    raw = json.loads((drop_home / "pages.json").read_text())
    assert raw["version"] == 2
    assert "pages" in raw
    assert "x" in raw["pages"]


# add_page

def test_add_page_basic(drop_home):
    p = storage.add_page(storage.Page(page_id="a", source=Path("/tmp/a"),
                                       type="static", name="one"))
    assert p.page_id == "a"
    loaded = storage.load_pages()
    assert "a" in loaded


def test_add_page_rejects_duplicate_name(drop_home):
    storage.add_page(storage.Page(page_id="a", source=Path("/tmp/a"),
                                   type="static", name="same"))
    with pytest.raises(ValueError, match="already exists"):
        storage.add_page(storage.Page(page_id="b", source=Path("/tmp/b"),
                                       type="static", name="same"))


def test_add_page_empty_name_allows_multiple(drop_home):
    storage.add_page(storage.Page(page_id="a", source=Path("/tmp/a"), type="static"))
    # No exception even though both have name=""
    storage.add_page(storage.Page(page_id="b", source=Path("/tmp/b"), type="static"))
    assert len(storage.load_pages()) == 2


# get_page

def test_get_page_by_exact_id(drop_home):
    p = storage.add_page(storage.Page(page_id="abcdef", source=Path("/tmp/x"),
                                       type="static"))
    assert storage.get_page("abcdef").page_id == "abcdef"


def test_get_page_by_prefix(drop_home):
    storage.add_page(storage.Page(page_id="abcdef", source=Path("/tmp/x"),
                                   type="static"))
    assert storage.get_page("abc").page_id == "abcdef"


def test_get_page_by_name(drop_home):
    storage.add_page(storage.Page(page_id="abcdef", source=Path("/tmp/x"),
                                   type="static", name="myslug"))
    assert storage.get_page("myslug").page_id == "abcdef"


def test_get_page_returns_none_when_missing(drop_home):
    assert storage.get_page("nope") is None


def test_get_page_ambiguous_prefix_returns_none(drop_home):
    storage.add_page(storage.Page(page_id="abc111", source=Path("/tmp/x"),
                                   type="static"))
    storage.add_page(storage.Page(page_id="abc222", source=Path("/tmp/y"),
                                   type="static"))
    # ambiguous prefix => None
    assert storage.get_page("abc") is None


# remove_page

def test_remove_page_by_id(drop_home):
    storage.add_page(storage.Page(page_id="abc", source=Path("/tmp/x"),
                                   type="static"))
    assert storage.remove_page("abc") is True
    assert storage.load_pages() == {}


def test_remove_page_by_name(drop_home):
    storage.add_page(storage.Page(page_id="abc", source=Path("/tmp/x"),
                                   type="static", name="myslug"))
    assert storage.remove_page("myslug") is True
    assert storage.load_pages() == {}


def test_remove_page_missing_returns_false(drop_home):
    assert storage.remove_page("nope") is False


# Migration

def test_migration_no_op_when_already_v2(drop_home):
    import json
    # Write fresh v2 file
    (drop_home / "pages.json").write_text(json.dumps({"version": 2, "pages": {}}))
    # maybe_migrate should be idempotent
    storage.maybe_migrate()
    raw = json.loads((drop_home / "pages.json").read_text())
    assert raw["version"] == 2


def test_migration_no_op_when_no_file(drop_home):
    # No pages.json — migration is silently noop
    storage.maybe_migrate()
    # File still doesn't exist
    assert not (drop_home / "pages.json").exists()


def test_migration_converts_v1_flat_dict(drop_home):
    import json
    # v1 schema: flat dict { page_id: {...all fields including runtime} }
    v1_data = {
        "abc12345": {
            "source": "/tmp/x",
            "is_dir": False,
            "password_hash": "ph",
            "created_at": "2026-01-01T00:00:00+00:00",
            "description": "old page",
            "name": "myslug",
            "type": "static",
            "run_cmd": "",
            "port": 0,
            "pid": 12345,
            "tunnel_url": "https://old.example.com",
            "tunnel_pid": 67890,
        }
    }
    (drop_home / "pages.json").write_text(json.dumps(v1_data))
    storage.maybe_migrate()
    # Backup exists
    assert (drop_home / "pages.json.v1.bak").exists()
    # New schema
    raw = json.loads((drop_home / "pages.json").read_text())
    assert raw["version"] == 2
    assert "abc12345" in raw["pages"]
    page_dict = raw["pages"]["abc12345"]
    assert page_dict["name"] == "myslug"
    assert page_dict["password_hash"] == "ph"
    # Runtime fields NOT in pages.json after migration
    assert "pid" not in page_dict
    assert "tunnel_pid" not in page_dict
    assert "tunnel_url" not in page_dict
    # Runtime file written with carried values
    runtime_raw = json.loads((drop_home / "runtime.json").read_text())
    assert runtime_raw["version"] == 2
    assert runtime_raw["runtimes"]["abc12345"]["app_pid"] == 12345
    assert runtime_raw["runtimes"]["abc12345"]["tunnel_url"] == "https://old.example.com"
    assert runtime_raw["runtimes"]["abc12345"]["tunnel_pid"] == 67890


def test_migration_converts_v1_app_with_auth_dict(drop_home):
    import json
    v1_data = {
        "appid": {
            "source": "/tmp/app",
            "is_dir": False,
            "password_hash": "",
            "created_at": "2026-01-01T00:00:00+00:00",
            "description": "",
            "name": "myapp",
            "type": "app",
            "run_cmd": "flask run",
            "port": 5000,
            "pid": 0,
            "tunnel_url": "",
            "tunnel_pid": 0,
            "auth": {"scheme": "basic", "user": "drop", "password_hash": "ph"},
            "public": False,
            "proxy_pid": 99,
            "proxy_port": 5001,
            "rewrite_host": True,
        }
    }
    (drop_home / "pages.json").write_text(json.dumps(v1_data))
    storage.maybe_migrate()
    pages = storage.load_pages()
    assert pages["appid"].auth.scheme == "basic"
    assert pages["appid"].auth.user == "drop"
    assert pages["appid"].rewrite_host is True
    assert pages["appid"].port == 5000
    # proxy_pid in runtime
    from drop import runtime
    rt = runtime.get_runtime("appid")
    assert rt.proxy_pid == 99
    assert rt.proxy_port == 5001
