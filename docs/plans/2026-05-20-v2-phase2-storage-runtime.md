# v2 Phase 2 — Storage + Runtime + Migration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the persistent registry (`Page` dataclass + JSON file with UNIQUE name constraint) and the volatile per-page runtime state (`PageRuntime` dataclass with PID-probe alive checks). Plus the one-time v1→v2 migration that converts old `pages.json` to the new schema.

**Architecture:** Two modules with clean separation. `storage.py` owns the config-side `Page` (immutable after registration) backed by `pages.json` with `{"version": 2, "pages": {...}}` envelope. `runtime.py` owns the volatile `PageRuntime` (pids, ports, tunnel_url) backed by `runtime.json`. Splitting them lets `clear runtime` / `drop list` operate cleanly without touching config fields.

**Tech Stack:** Python stdlib `dataclasses`, `json`, `os`. Phase 1 modules (`config`, `auth`, `utils`) already available.

**Reference spec:** `docs/2026-05-20-v2-greenfield-design.md` (Data model + Migration sections).

**Branch:** `v2`.

---

## Module boundaries

- **`storage.py`** — `Page` dataclass + `AuthConfig` dataclass. CRUD: `load_pages`, `save_pages`, `add_page` (raises on dup name), `get_page`, `remove_page`, `list_pages`. Migration: `maybe_migrate()` called on first read.
- **`runtime.py`** — `PageRuntime` dataclass with `is_app_alive` / `is_proxy_alive` / `is_tunnel_alive` methods. CRUD: `get_runtime`, `save_runtime`, `clear_runtime`. PID-probe via `os.kill(pid, 0)`.

---

## Task 1: `storage.py` — Page dataclass + CRUD with UNIQUE constraint

**Files:**
- Modify: `src/drop/storage.py`
- Create: `tests/unit/test_storage.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_storage.py`:

```python
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
```

- [ ] **Step 2: Run, verify failures**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest tests/unit/test_storage.py -v
```

Expected: collection errors / AttributeError.

- [ ] **Step 3: Implement `storage.py`**

Replace `src/drop/storage.py` with:

```python
"""Page CRUD over ~/.drop/pages.json with UNIQUE(name) constraint.

Persistent registry. Volatile runtime (pids, ports, tunnel URL) lives in
drop.runtime (separate file). Migration from v1's flat schema is done
once on first read.
"""

import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, UTC
from pathlib import Path
from typing import Literal

from . import config


@dataclass(frozen=True)
class AuthConfig:
    scheme: str
    user: str
    password_hash: str


@dataclass
class Page:
    page_id: str
    source: Path
    type: Literal["static", "app"]
    name: str = ""
    description: str = ""
    is_public: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    # static-only
    password_hash: str = ""
    # app-only
    run_cmd: str = ""
    port: int = 0
    auth: AuthConfig | None = None
    allow_side_door: bool = False
    rewrite_host: bool = False


def _ensure_dir() -> None:
    config.DROP_HOME.mkdir(parents=True, exist_ok=True)


def _page_to_dict(p: Page) -> dict:
    d = asdict(p)
    d["source"] = str(p.source)
    if p.auth is not None:
        d["auth"] = asdict(p.auth)
    return d


def _page_from_dict(d: dict) -> Page:
    auth_d = d.get("auth")
    auth = AuthConfig(**auth_d) if auth_d else None
    return Page(
        page_id=d["page_id"],
        source=Path(d["source"]),
        type=d["type"],
        name=d.get("name", ""),
        description=d.get("description", ""),
        is_public=d.get("is_public", False),
        created_at=d.get("created_at", ""),
        password_hash=d.get("password_hash", ""),
        run_cmd=d.get("run_cmd", ""),
        port=d.get("port", 0),
        auth=auth,
        allow_side_door=d.get("allow_side_door", False),
        rewrite_host=d.get("rewrite_host", False),
    )


def load_pages() -> dict[str, Page]:
    """Load registry. Runs migration on first call if file is v1 schema."""
    maybe_migrate()
    if not config.PAGES_FILE.exists():
        return {}
    try:
        raw = json.loads(config.PAGES_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict) or "pages" not in raw:
        return {}
    return {pid: _page_from_dict({**d, "page_id": pid})
            for pid, d in raw["pages"].items()}


def save_pages(pages: dict[str, Page]) -> None:
    """Write registry."""
    _ensure_dir()
    envelope = {
        "version": config.SCHEMA_VERSION,
        "pages": {pid: _page_to_dict(p) for pid, p in pages.items()},
    }
    # Remove `page_id` from inner dict — it's the key, not duplicated content
    for pid, d in envelope["pages"].items():
        d.pop("page_id", None)
    config.PAGES_FILE.write_text(json.dumps(envelope, indent=2))


def add_page(page: Page) -> Page:
    """Persist a Page. Raises ValueError on duplicate non-empty name."""
    pages = load_pages()
    if page.name:
        for existing_id, existing in pages.items():
            if existing.name == page.name:
                raise ValueError(
                    f"name '{page.name}' already exists (page_id {existing_id[:8]})"
                )
    pages[page.page_id] = page
    save_pages(pages)
    return page


def get_page(identifier: str) -> Page | None:
    """Get by exact id, unique prefix, or name. None if missing/ambiguous."""
    pages = load_pages()
    if identifier in pages:
        return pages[identifier]
    prefix_matches = [pid for pid in pages if pid.startswith(identifier)]
    if len(prefix_matches) == 1:
        return pages[prefix_matches[0]]
    for p in pages.values():
        if p.name == identifier:
            return p
    return None


def remove_page(identifier: str) -> bool:
    """Remove by exact id, unique prefix, or name. Returns True if found."""
    pages = load_pages()
    target = None
    if identifier in pages:
        target = identifier
    else:
        prefix_matches = [pid for pid in pages if pid.startswith(identifier)]
        if len(prefix_matches) == 1:
            target = prefix_matches[0]
        else:
            for pid, p in pages.items():
                if p.name == identifier:
                    target = pid
                    break
    if target is None:
        return False
    del pages[target]
    save_pages(pages)
    # Also clear runtime for the removed page
    from . import runtime
    runtime.clear_runtime(target)
    return True


def list_pages() -> dict[str, Page]:
    """Alias for load_pages — explicit semantics for external callers."""
    return load_pages()


# ---- Migration (v1 flat dict -> v2 versioned envelope) ----

_RUNTIME_FIELDS_V1 = {"pid", "proxy_pid", "proxy_port", "tunnel_pid", "tunnel_url"}


def maybe_migrate() -> None:
    """If pages.json is v1 (flat dict), back up and rewrite as v2."""
    if not config.PAGES_FILE.exists():
        return
    try:
        raw = json.loads(config.PAGES_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(raw, dict) and raw.get("version") == config.SCHEMA_VERSION:
        return  # already migrated
    if not isinstance(raw, dict):
        return  # unrecognized; leave alone

    # Backup
    backup = config.PAGES_FILE.with_suffix(".json.v1.bak")
    shutil.copy(config.PAGES_FILE, backup)

    pages: dict[str, Page] = {}
    runtimes_data: dict[str, dict] = {}
    for pid, v1 in raw.items():
        if not isinstance(v1, dict):
            continue
        # Carry runtime to runtime.json
        runtimes_data[pid] = {
            "page_id": pid,
            "app_pid": v1.get("pid", 0),
            "proxy_pid": v1.get("proxy_pid", 0),
            "proxy_port": v1.get("proxy_port", 0),
            "tunnel_pid": v1.get("tunnel_pid", 0),
            "tunnel_url": v1.get("tunnel_url", ""),
        }
        # Strip runtime keys from page dict
        page_dict = {k: v for k, v in v1.items() if k not in _RUNTIME_FIELDS_V1}
        # v1 used "public" -> map to is_public
        if "public" in page_dict:
            page_dict["is_public"] = page_dict.pop("public")
        page_dict.setdefault("page_id", pid)
        page_dict.setdefault("type", "static")
        try:
            pages[pid] = _page_from_dict(page_dict)
        except (KeyError, ValueError):
            # Skip malformed entries
            continue

    # Write v2
    save_pages(pages)

    # Write runtime file
    _ensure_dir()
    config.RUNTIME_FILE.write_text(json.dumps(
        {"version": config.SCHEMA_VERSION, "runtimes": runtimes_data},
        indent=2,
    ))
```

- [ ] **Step 4: Note: `storage.maybe_migrate` writes runtime.json. The `from . import runtime` inside `remove_page` is a deferred import to avoid circular dep. `runtime.py` must exist at least as a stub for these tests to import — it already does (Phase 0 skeleton).**

But the migration test imports `from drop import runtime` and calls `runtime.get_runtime`. That function doesn't exist until Task 2. We split — for Task 1 verification we should run only the tests that don't depend on runtime.get_runtime: skip `test_migration_converts_v1_app_with_auth_dict` until Task 2.

Run filtered:
```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest tests/unit/test_storage.py -v -k "not v1_app_with_auth_dict"
```

Expected: 19 passed (one deselected). The deselected test will pass after Task 2 (runtime).

- [ ] **Step 5: Commit**

```bash
git add src/drop/storage.py tests/unit/test_storage.py
git commit -m "feat(v2): storage module — Page dataclass, CRUD, UNIQUE name, v1->v2 migration"
```

---

## Task 2: `runtime.py` — PageRuntime + PID-probe + runtime.json

**Files:**
- Modify: `src/drop/runtime.py`
- Create: `tests/unit/test_runtime.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_runtime.py`:

```python
"""Tests for drop.runtime — PageRuntime dataclass + PID-probe + runtime.json CRUD."""

import json
import os
import subprocess
import time

import pytest

from drop import runtime


# Dataclass defaults

def test_runtime_defaults(drop_home):
    r = runtime.PageRuntime(page_id="abc")
    assert r.page_id == "abc"
    assert r.app_pid == 0
    assert r.proxy_pid == 0
    assert r.proxy_port == 0
    assert r.tunnel_pid == 0
    assert r.tunnel_url == ""


# Alive probes

def test_is_app_alive_zero_is_false(drop_home):
    r = runtime.PageRuntime(page_id="abc")
    assert r.is_app_alive() is False


def test_is_app_alive_real_process(drop_home):
    # Spawn a quick `sleep 30` and verify probe
    proc = subprocess.Popen(["sleep", "30"])
    try:
        r = runtime.PageRuntime(page_id="abc", app_pid=proc.pid)
        assert r.is_app_alive() is True
    finally:
        proc.terminate()
        proc.wait(timeout=2)
    # After termination, probe is False
    r2 = runtime.PageRuntime(page_id="abc", app_pid=proc.pid)
    assert r2.is_app_alive() is False


def test_is_proxy_alive_zero(drop_home):
    assert runtime.PageRuntime(page_id="x").is_proxy_alive() is False


def test_is_tunnel_alive_zero(drop_home):
    assert runtime.PageRuntime(page_id="x").is_tunnel_alive() is False


# get_runtime / save_runtime / clear_runtime

def test_get_runtime_missing_returns_empty(drop_home):
    r = runtime.get_runtime("nope")
    assert r.page_id == "nope"
    assert r.app_pid == 0


def test_save_and_get_runtime_round_trip(drop_home):
    r = runtime.PageRuntime(
        page_id="abc", app_pid=100, proxy_pid=200, proxy_port=8080,
        tunnel_pid=300, tunnel_url="https://x/",
    )
    runtime.save_runtime(r)
    loaded = runtime.get_runtime("abc")
    assert loaded.app_pid == 100
    assert loaded.proxy_pid == 200
    assert loaded.proxy_port == 8080
    assert loaded.tunnel_pid == 300
    assert loaded.tunnel_url == "https://x/"


def test_save_runtime_writes_versioned_envelope(drop_home):
    r = runtime.PageRuntime(page_id="abc", app_pid=1)
    runtime.save_runtime(r)
    raw = json.loads((drop_home / "runtime.json").read_text())
    assert raw["version"] == 2
    assert "runtimes" in raw
    assert raw["runtimes"]["abc"]["app_pid"] == 1


def test_clear_runtime_removes_entry(drop_home):
    runtime.save_runtime(runtime.PageRuntime(page_id="abc", app_pid=1))
    runtime.save_runtime(runtime.PageRuntime(page_id="def", app_pid=2))
    runtime.clear_runtime("abc")
    raw = json.loads((drop_home / "runtime.json").read_text())
    assert "abc" not in raw["runtimes"]
    assert "def" in raw["runtimes"]


def test_clear_runtime_missing_no_error(drop_home):
    runtime.clear_runtime("nope")  # should not raise


def test_load_runtimes_empty_when_no_file(drop_home):
    rtmap = runtime.load_runtimes()
    assert rtmap == {}
```

- [ ] **Step 2: Run, verify failures**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest tests/unit/test_runtime.py -v
```

- [ ] **Step 3: Implement `runtime.py`**

Replace `src/drop/runtime.py` with:

```python
"""Volatile per-page runtime state.

PageRuntime tracks pids and the tunnel URL. Stored in ~/.drop/runtime.json
separately from the config-side Page (which lives in pages.json). PID
liveness is verified via os.kill(pid, 0).
"""

import json
import os
from dataclasses import asdict, dataclass

from . import config


@dataclass
class PageRuntime:
    page_id: str
    app_pid: int = 0
    proxy_pid: int = 0
    proxy_port: int = 0
    tunnel_pid: int = 0
    tunnel_url: str = ""

    def _alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def is_app_alive(self) -> bool:
        return self._alive(self.app_pid)

    def is_proxy_alive(self) -> bool:
        return self._alive(self.proxy_pid)

    def is_tunnel_alive(self) -> bool:
        return self._alive(self.tunnel_pid)


def _ensure_dir() -> None:
    config.DROP_HOME.mkdir(parents=True, exist_ok=True)


def load_runtimes() -> dict[str, PageRuntime]:
    """Load all runtime state. Empty dict if no file."""
    if not config.RUNTIME_FILE.exists():
        return {}
    try:
        raw = json.loads(config.RUNTIME_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict) or "runtimes" not in raw:
        return {}
    return {pid: PageRuntime(**d) for pid, d in raw["runtimes"].items()}


def save_runtimes(rtmap: dict[str, PageRuntime]) -> None:
    """Write all runtime state."""
    _ensure_dir()
    envelope = {
        "version": config.SCHEMA_VERSION,
        "runtimes": {pid: asdict(r) for pid, r in rtmap.items()},
    }
    config.RUNTIME_FILE.write_text(json.dumps(envelope, indent=2))


def get_runtime(page_id: str) -> PageRuntime:
    """Get runtime for a page. Returns empty PageRuntime if missing."""
    rtmap = load_runtimes()
    return rtmap.get(page_id, PageRuntime(page_id=page_id))


def save_runtime(r: PageRuntime) -> None:
    """Persist one PageRuntime."""
    rtmap = load_runtimes()
    rtmap[r.page_id] = r
    save_runtimes(rtmap)


def clear_runtime(page_id: str) -> None:
    """Remove runtime entry for a page (no error if missing)."""
    rtmap = load_runtimes()
    if page_id in rtmap:
        del rtmap[page_id]
        save_runtimes(rtmap)
```

- [ ] **Step 4: Run all tests (storage + runtime — including the previously-deferred migration test)**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest tests/unit/test_runtime.py tests/unit/test_storage.py -v
```

Expected: 12 (runtime) + 20 (storage, now including the deferred one) = 32 passed.

- [ ] **Step 5: Commit**

```bash
git add src/drop/runtime.py tests/unit/test_runtime.py
git commit -m "feat(v2): runtime module — PageRuntime + PID-probe + runtime.json CRUD"
```

---

## Phase 2 Self-Review

After both tasks:

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest -v
```

Expected: all Phase 0 (4 harness) + Phase 1 (~64) + Phase 2 (~32) ≈ 100 tests, all passing.

Push:
```bash
git push origin v2
```

## What Phase 2 Does NOT Include (deferred)

- Lifecycle integration (Phase 3+).
- `drop list` command using these primitives — Phase 7 (CLI).
- Re-running migration multiple times — `maybe_migrate` is idempotent by the version-check.

## Phase 3 Readiness

After Phase 2 green, Phase 3 (`lifecycle/process.py`) can rely on:
- `runtime.save_runtime(PageRuntime(...))` to record spawned pids
- `runtime.get_runtime(page_id)` to query live state
- `storage.get_page(name_or_id)` to look up Page config before spawning
