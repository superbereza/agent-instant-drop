# v2 Phase 1 — Pure Modules (config, auth, manifest, utils)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the four pure-logic modules of v2 with full unit-test coverage via TDD. After this phase, all stateless helpers (path safety, password hashing, IP detection, config constants) are available for Phase 2+ to depend on.

**Architecture:** Each module is single-responsibility and has zero I/O dependencies beyond fs/socket (already mockable via fixtures). Tests live in `tests/unit/test_<module>.py`. Reuse `drop_home` and `free_port` fixtures from Phase 0 `conftest.py`.

**Tech Stack:** Python stdlib only (no new deps). pytest from Phase 0. TDD per function: write failing test → implement → verify pass → commit.

**Reference spec:** `docs/2026-05-20-v2-greenfield-design.md` (data model, side-door, atomic lifecycle sections all rely on these primitives).

**Branch:** `v2`. Phase 0 already merged into v2 (skeleton stubs exist).

---

## Module Boundaries

- **`config.py`** — paths + constants + env overrides. ZERO functions, only module-level constants computed at import.
- **`auth.py`** — password gen/hash/verify, basic-auth header parsing, simple rate-limit dict.
- **`manifest.py`** — `safe_path`, `matches_manifest`, `load_manifest`, `is_env_file` (extracted from v1 `utils.py`).
- **`utils.py`** — IP detection, port allocation, port wait, has_systemd, find_cloudflared, generate_page_id.

Cross-deps allowed within Phase 1: `manifest.py` and `utils.py` may import from `config.py` for paths/constants. `auth.py` is self-contained.

---

## Task 1: `config.py` — paths + constants

**Files:**
- Modify: `src/drop/config.py` (currently a docstring stub)
- Create: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_config.py`:

```python
"""Tests for drop.config — paths + constants + env overrides."""

import importlib
from pathlib import Path

import pytest


def _reload_config():
    """Re-import config so env changes take effect."""
    import drop.config
    return importlib.reload(drop.config)


def test_drop_home_default_is_home_dot_drop(monkeypatch):
    monkeypatch.delenv("DROP_HOME", raising=False)
    config = _reload_config()
    assert config.DROP_HOME == Path.home() / ".drop"


def test_drop_home_overridden_by_env(drop_home):
    config = _reload_config()
    assert config.DROP_HOME == drop_home


def test_derived_paths_track_drop_home(drop_home):
    config = _reload_config()
    assert config.PAGES_FILE == drop_home / "pages.json"
    assert config.RUNTIME_FILE == drop_home / "runtime.json"
    assert config.LOGS_DIR == drop_home / "logs"
    assert config.BIN_DIR == drop_home / "bin"
    assert config.SERVER_PID_FILE == drop_home / "server.pid"
    assert config.SERVER_PORT_FILE == drop_home / "port"
    assert config.SERVER_HOST_FILE == drop_home / "host"
    assert config.SERVER_TUNNEL_FILE == drop_home / "tunnel.json"


def test_constants(drop_home):
    config = _reload_config()
    assert config.AUTH_REALM == "drop"
    assert config.DEFAULT_AUTH_USER == "drop"
    assert config.DEFAULT_SERVER_PORT == 8080
    assert config.SCHEMA_VERSION == 2


def test_cloudflared_bin_override(monkeypatch, tmp_path):
    fake = tmp_path / "cloudflared"
    fake.write_text("")
    monkeypatch.setenv("DROP_CLOUDFLARED_BIN", str(fake))
    config = _reload_config()
    assert config.CLOUDFLARED_BIN_OVERRIDE == str(fake)


def test_cloudflared_bin_override_unset(monkeypatch):
    monkeypatch.delenv("DROP_CLOUDFLARED_BIN", raising=False)
    config = _reload_config()
    assert config.CLOUDFLARED_BIN_OVERRIDE is None
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest tests/unit/test_config.py -v
```

Expected: failures (`AttributeError: module 'drop.config' has no attribute 'DROP_HOME'`).

- [ ] **Step 3: Implement `config.py`**

Replace `src/drop/config.py` with:

```python
"""Paths + constants + env overrides for drop.

All file paths are derived from DROP_HOME (defaulting to ~/.drop).
Tests override DROP_HOME via the `drop_home` pytest fixture to get
isolated state.
"""

import os
from pathlib import Path


DROP_HOME = Path(os.environ.get("DROP_HOME") or Path.home() / ".drop")

PAGES_FILE = DROP_HOME / "pages.json"
RUNTIME_FILE = DROP_HOME / "runtime.json"
LOGS_DIR = DROP_HOME / "logs"
BIN_DIR = DROP_HOME / "bin"
SERVER_PID_FILE = DROP_HOME / "server.pid"
SERVER_PORT_FILE = DROP_HOME / "port"
SERVER_HOST_FILE = DROP_HOME / "host"
SERVER_TUNNEL_FILE = DROP_HOME / "tunnel.json"

CLOUDFLARED_BIN_OVERRIDE = os.environ.get("DROP_CLOUDFLARED_BIN")

DEFAULT_SERVER_PORT = 8080
AUTH_REALM = "drop"
DEFAULT_AUTH_USER = "drop"
SCHEMA_VERSION = 2
```

- [ ] **Step 4: Run tests, verify all pass**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest tests/unit/test_config.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/drop/config.py tests/unit/test_config.py
git commit -m "feat(v2): config module — paths + constants + env overrides"
```

---

## Task 2: `auth.py` — password gen/hash/verify + basic-auth parse + rate-limit

**Files:**
- Modify: `src/drop/auth.py`
- Create: `tests/unit/test_auth.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_auth.py`:

```python
"""Tests for drop.auth — password gen/hash/verify, basic-auth parse, rate-limit."""

import base64
import time

import pytest

from drop import auth


# generate_password

def test_generate_password_default_length():
    pw = auth.generate_password()
    assert len(pw) == 6


def test_generate_password_custom_length():
    pw = auth.generate_password(12)
    assert len(pw) == 12


def test_generate_password_safe_alphabet():
    # No 0/O, 1/l/I confusable chars
    pw = auth.generate_password(1000)
    forbidden = "0Oo1lLiI"
    assert not any(c in forbidden for c in pw), f"Found confusable in {pw}"


# hash_password / verify_password

def test_hash_password_returns_prefixed_string():
    h = auth.hash_password("secret")
    assert h.startswith("sha256:")
    # 64 hex chars after "sha256:"
    assert len(h) == 7 + 64


def test_hash_password_deterministic():
    assert auth.hash_password("hello") == auth.hash_password("hello")


def test_hash_password_distinct_for_different_inputs():
    assert auth.hash_password("a") != auth.hash_password("b")


def test_verify_password_round_trip():
    h = auth.hash_password("right")
    assert auth.verify_password("right", h) is True


def test_verify_password_rejects_wrong():
    h = auth.hash_password("right")
    assert auth.verify_password("wrong", h) is False


def test_verify_password_empty_hash_allows_anything():
    # Convention from v1: empty hash means no password set.
    assert auth.verify_password("anything", "") is True


# generate_auth_creds

def test_generate_auth_creds_returns_user_drop_and_12char():
    user, pw = auth.generate_auth_creds()
    assert user == "drop"
    assert len(pw) == 12


# parse_basic_auth

def test_parse_basic_auth_well_formed():
    header = "Basic " + base64.b64encode(b"alice:s3cret").decode("ascii")
    assert auth.parse_basic_auth(header) == ("alice", "s3cret")


def test_parse_basic_auth_missing_prefix():
    assert auth.parse_basic_auth("alice:s3cret") is None


def test_parse_basic_auth_empty():
    assert auth.parse_basic_auth("") is None


def test_parse_basic_auth_bad_base64():
    assert auth.parse_basic_auth("Basic !!!not-base64!!!") is None


def test_parse_basic_auth_no_colon_in_decoded():
    header = "Basic " + base64.b64encode(b"nouser").decode("ascii")
    assert auth.parse_basic_auth(header) is None


def test_parse_basic_auth_password_may_contain_colon():
    header = "Basic " + base64.b64encode(b"u:p:with:colons").decode("ascii")
    assert auth.parse_basic_auth(header) == ("u", "p:with:colons")


def test_parse_basic_auth_non_utf8():
    # 0xff is not valid utf-8
    header = "Basic " + base64.b64encode(b"\xffuser:pw").decode("ascii")
    assert auth.parse_basic_auth(header) is None


# Rate limit

def test_rate_limiter_allows_under_limit():
    rl = auth.RateLimiter(max_attempts=3, window_sec=60)
    assert rl.check_and_record("1.2.3.4", "page1") is True
    assert rl.check_and_record("1.2.3.4", "page1") is True
    assert rl.check_and_record("1.2.3.4", "page1") is True


def test_rate_limiter_rejects_over_limit():
    rl = auth.RateLimiter(max_attempts=3, window_sec=60)
    for _ in range(3):
        rl.check_and_record("1.2.3.4", "page1")
    assert rl.check_and_record("1.2.3.4", "page1") is False


def test_rate_limiter_per_ip_isolation():
    rl = auth.RateLimiter(max_attempts=2, window_sec=60)
    for _ in range(2):
        rl.check_and_record("1.1.1.1", "page1")
    # different IP — fresh quota
    assert rl.check_and_record("2.2.2.2", "page1") is True


def test_rate_limiter_per_page_isolation():
    rl = auth.RateLimiter(max_attempts=2, window_sec=60)
    for _ in range(2):
        rl.check_and_record("1.1.1.1", "page1")
    # same IP, different page — fresh quota
    assert rl.check_and_record("1.1.1.1", "page2") is True


def test_rate_limiter_window_expires():
    rl = auth.RateLimiter(max_attempts=1, window_sec=0)  # immediate expiry
    rl.check_and_record("1.1.1.1", "page1")
    time.sleep(0.01)
    # window of 0 means previous attempts are already out — allow new
    assert rl.check_and_record("1.1.1.1", "page1") is True
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest tests/unit/test_auth.py -v
```

Expected: all collection or import failures (module is empty).

- [ ] **Step 3: Implement `auth.py`**

Replace `src/drop/auth.py` with:

```python
"""Shared auth utilities for drop.

- generate_password / generate_auth_creds
- hash_password / verify_password (sha256-based, constant-time compare)
- parse_basic_auth (HTTP basic header → (user, password) or None)
- RateLimiter — in-memory per-(ip,page) attempt counter with sliding window
"""

import base64
import binascii
import hashlib
import secrets
import time

from .config import DEFAULT_AUTH_USER


_PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_password(length: int = 6) -> str:
    """Generate a random password from a confusable-char-free alphabet."""
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


def generate_auth_creds() -> tuple[str, str]:
    """Return (user, password) for basic auth. User is fixed (DEFAULT_AUTH_USER)."""
    return (DEFAULT_AUTH_USER, generate_password(12))


def hash_password(password: str) -> str:
    """SHA-256 hash with 'sha256:' prefix."""
    return "sha256:" + hashlib.sha256(password.encode("utf-8")).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time verify. Empty hash means 'no password required'."""
    if not password_hash:
        return True
    expected = hash_password(password)
    return secrets.compare_digest(expected, password_hash)


def parse_basic_auth(header: str) -> tuple[str, str] | None:
    """Decode an HTTP `Authorization: Basic ...` header.

    Returns (user, password) or None for any malformed input. password may
    contain colons (only the first colon is the separator).
    """
    if not header.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    user, sep, password = decoded.partition(":")
    if not sep:
        return None
    return (user, password)


class RateLimiter:
    """Per-(ip, key) sliding-window counter.

    In-memory only — resets on process restart. Suitable for drop's small
    scale; not for high-traffic deployments.
    """

    def __init__(self, max_attempts: int = 3, window_sec: int = 60):
        self.max_attempts = max_attempts
        self.window_sec = window_sec
        # {(ip, key): [timestamp, ...]}
        self._attempts: dict[tuple[str, str], list[float]] = {}

    def check_and_record(self, ip: str, key: str) -> bool:
        """Record an attempt and return True if it is within the limit."""
        now = time.time()
        bucket = (ip, key)
        attempts = self._attempts.get(bucket, [])
        # Drop attempts older than window
        attempts = [t for t in attempts if now - t <= self.window_sec]
        if len(attempts) >= self.max_attempts:
            self._attempts[bucket] = attempts
            return False
        attempts.append(now)
        self._attempts[bucket] = attempts
        return True
```

- [ ] **Step 4: Run tests, verify all pass**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest tests/unit/test_auth.py -v
```

Expected: ~20 passed.

- [ ] **Step 5: Commit**

```bash
git add src/drop/auth.py tests/unit/test_auth.py
git commit -m "feat(v2): auth module — password gen/hash/verify, basic-auth parse, rate-limit"
```

---

## Task 3: `manifest.py` — safe_path + matches_manifest + load_manifest + is_env_file

**Files:**
- Modify: `src/drop/manifest.py`
- Create: `tests/unit/test_manifest.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_manifest.py`:

```python
"""Tests for drop.manifest — manifest patterns + safe_path + is_env_file."""

import os
from pathlib import Path

import pytest

from drop import manifest


# MANIFEST_FILE constant

def test_manifest_filename():
    assert manifest.MANIFEST_FILE == ".drop-publish"


# is_env_file

def test_is_env_file_blocks_dot_env():
    assert manifest.is_env_file(".env") is True


def test_is_env_file_blocks_dot_env_local():
    assert manifest.is_env_file(".env.local") is True


def test_is_env_file_blocks_dot_env_production():
    assert manifest.is_env_file(".env.production") is True


def test_is_env_file_allows_dot_env_example():
    assert manifest.is_env_file(".env.example") is False


def test_is_env_file_allows_normal_files():
    assert manifest.is_env_file("index.html") is False
    assert manifest.is_env_file("readme.md") is False


def test_is_env_file_case_insensitive():
    assert manifest.is_env_file(".ENV") is True
    assert manifest.is_env_file(".Env.LOCAL") is True


# load_manifest

def test_load_manifest_missing_file(tmp_path):
    assert manifest.load_manifest(tmp_path) is None


def test_load_manifest_reads_lines(tmp_path):
    (tmp_path / ".drop-publish").write_text("index.html\nassets/**\n")
    patterns = manifest.load_manifest(tmp_path)
    assert patterns == ["index.html", "assets/**"]


def test_load_manifest_strips_blank_lines_and_comments(tmp_path):
    (tmp_path / ".drop-publish").write_text(
        "# this is a comment\n"
        "index.html\n"
        "\n"
        "  # indented comment\n"
        "assets/**\n"
    )
    patterns = manifest.load_manifest(tmp_path)
    assert patterns == ["index.html", "assets/**"]


# matches_manifest

def test_matches_manifest_exact_file():
    assert manifest.matches_manifest("index.html", ["index.html"]) is True
    assert manifest.matches_manifest("other.html", ["index.html"]) is False


def test_matches_manifest_glob_extension():
    assert manifest.matches_manifest("a.html", ["*.html"]) is True
    assert manifest.matches_manifest("a.css", ["*.html"]) is False


def test_matches_manifest_double_star_directory():
    patterns = ["assets/**"]
    assert manifest.matches_manifest("assets/css/main.css", patterns) is True
    assert manifest.matches_manifest("assets/index.html", patterns) is True
    assert manifest.matches_manifest("assets", patterns) is True
    assert manifest.matches_manifest("other/file", patterns) is False


def test_matches_manifest_directory_prefix_match():
    # Listing a directory name allows files inside it too
    assert manifest.matches_manifest("assets/main.css", ["assets/"]) is True
    assert manifest.matches_manifest("assets/main.css", ["assets"]) is True


def test_matches_manifest_no_patterns():
    assert manifest.matches_manifest("anything", []) is False


# safe_path

def test_safe_path_simple_resolves(tmp_path):
    (tmp_path / "index.html").write_text("ok")
    result = manifest.safe_path(tmp_path, "index.html")
    assert result == (tmp_path / "index.html").resolve()


def test_safe_path_blocks_traversal(tmp_path):
    (tmp_path / "x").mkdir()
    result = manifest.safe_path(tmp_path / "x", "../escape")
    assert result is None


def test_safe_path_blocks_absolute_outside(tmp_path):
    result = manifest.safe_path(tmp_path, "/etc/passwd")
    assert result is None


def test_safe_path_blocks_env_files(tmp_path):
    (tmp_path / ".env").write_text("SECRET=x")
    assert manifest.safe_path(tmp_path, ".env") is None


def test_safe_path_allows_env_example(tmp_path):
    (tmp_path / ".env.example").write_text("EXAMPLE=y")
    result = manifest.safe_path(tmp_path, ".env.example")
    assert result == (tmp_path / ".env.example").resolve()


def test_safe_path_blocks_symlink_outside(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret")
    inside = tmp_path / "link"
    inside.symlink_to(outside)
    assert manifest.safe_path(tmp_path, "link") is None


def test_safe_path_respects_manifest_allow(tmp_path):
    (tmp_path / "ok.html").write_text("ok")
    result = manifest.safe_path(tmp_path, "ok.html", manifest=["*.html"])
    assert result is not None


def test_safe_path_respects_manifest_deny(tmp_path):
    (tmp_path / "secret.json").write_text("{}")
    assert manifest.safe_path(tmp_path, "secret.json", manifest=["*.html"]) is None
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest tests/unit/test_manifest.py -v
```

Expected: all fail/error (`AttributeError: module 'drop.manifest' has no attribute 'MANIFEST_FILE'`).

- [ ] **Step 3: Implement `manifest.py`**

Replace `src/drop/manifest.py` with:

```python
"""Manifest matching + safe_path utilities for drop.

A directory page must include a `.drop-publish` manifest listing
allowed file patterns. `safe_path` enforces both path-traversal safety
and manifest membership.
"""

import fnmatch
from pathlib import Path


MANIFEST_FILE = ".drop-publish"


def is_env_file(name: str) -> bool:
    """Treat .env / .env.* as secret, except .env.example."""
    lowered = name.lower()
    if lowered == ".env.example":
        return False
    if lowered == ".env" or lowered.startswith(".env."):
        return True
    return False


def load_manifest(directory: Path) -> list[str] | None:
    """Read patterns from `<directory>/.drop-publish`.

    Returns None if the manifest file is missing. Empty lines and lines
    starting with `#` are ignored.
    """
    path = directory / MANIFEST_FILE
    if not path.exists():
        return None
    try:
        raw = path.read_text().splitlines()
    except OSError:
        return None
    return [line.strip() for line in raw if line.strip() and not line.strip().startswith("#")]


def matches_manifest(relative_path: str, patterns: list[str]) -> bool:
    """Check if `relative_path` matches any pattern in `patterns`."""
    for pattern in patterns:
        if "**" in pattern:
            prefix = pattern.split("**")[0].rstrip("/")
            if relative_path.startswith(prefix + "/") or relative_path == prefix:
                return True
        elif fnmatch.fnmatch(relative_path, pattern):
            return True
        elif relative_path.startswith(pattern.rstrip("/") + "/"):
            return True
    return False


def safe_path(base: Path, requested: str, manifest: list[str] | None = None) -> Path | None:
    """Resolve `requested` under `base`, returning None for any unsafe path.

    Unsafe = traversal escape, symlink escape, .env file, or (if manifest
    is provided) not matching any pattern.
    """
    try:
        base_resolved = base.resolve()
        full_path = (base_resolved / requested).resolve()

        if not full_path.is_relative_to(base_resolved):
            return None

        # Symlink-target safety: even if final component isn't a symlink,
        # an intermediate one was followed by resolve(); is_relative_to
        # check above already catches escape. Belt-and-braces:
        if full_path.is_symlink():
            target = full_path.resolve()
            if not target.is_relative_to(base_resolved):
                return None

        if is_env_file(full_path.name):
            return None

        if manifest is not None:
            rel = str(full_path.relative_to(base_resolved))
            if not matches_manifest(rel, manifest):
                return None

        return full_path
    except (OSError, ValueError):
        return None
```

- [ ] **Step 4: Run tests, verify all pass**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest tests/unit/test_manifest.py -v
```

Expected: ~22 passed.

- [ ] **Step 5: Commit**

```bash
git add src/drop/manifest.py tests/unit/test_manifest.py
git commit -m "feat(v2): manifest module — safe_path, matches_manifest, is_env_file"
```

---

## Task 4: `utils.py` — IP detection + port helpers + has_systemd + find_cloudflared + generate_page_id

**Files:**
- Modify: `src/drop/utils.py`
- Create: `tests/unit/test_utils.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_utils.py`:

```python
"""Tests for drop.utils — pure helpers (IP, port, systemd, cloudflared, page-id)."""

import os
import socket
import string
from pathlib import Path

import pytest

from drop import utils


# generate_page_id

def test_generate_page_id_default_length():
    pid = utils.generate_page_id()
    assert len(pid) == 16


def test_generate_page_id_custom_length():
    assert len(utils.generate_page_id(8)) == 8


def test_generate_page_id_alphabet():
    # lowercase letters + digits
    valid = set(string.ascii_lowercase + string.digits)
    pid = utils.generate_page_id(100)
    assert set(pid).issubset(valid)


def test_generate_page_id_uniqueness():
    ids = {utils.generate_page_id() for _ in range(100)}
    assert len(ids) == 100  # extremely unlikely to collide


# allocate_free_port

def test_allocate_free_port_returns_valid_port():
    p = utils.allocate_free_port()
    assert isinstance(p, int)
    assert 1024 < p < 65536


def test_allocate_free_port_is_bindable():
    p = utils.allocate_free_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", p))
        s.listen(1)


# wait_for_port

def test_wait_for_port_returns_true_when_listening(free_port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", free_port))
    s.listen(1)
    try:
        assert utils.wait_for_port("127.0.0.1", free_port, timeout=1.0) is True
    finally:
        s.close()


def test_wait_for_port_returns_false_on_timeout(free_port):
    # No one is listening — should time out and return False
    assert utils.wait_for_port("127.0.0.1", free_port, timeout=0.3) is False


# has_systemd — purely OS-dependent. Just verify it returns a bool and
# doesn't raise on the test host.

def test_has_systemd_returns_bool():
    result = utils.has_systemd()
    assert isinstance(result, bool)


# find_cloudflared — env override + ~/.drop/bin path

def test_find_cloudflared_env_override(monkeypatch, tmp_path):
    fake = tmp_path / "cloudflared"
    fake.write_text("#!/bin/sh\necho fake")
    fake.chmod(0o755)
    monkeypatch.setenv("DROP_CLOUDFLARED_BIN", str(fake))
    import importlib
    import drop.config
    importlib.reload(drop.config)
    import drop.utils
    importlib.reload(drop.utils)
    assert drop.utils.find_cloudflared() == str(fake)


def test_find_cloudflared_returns_none_when_missing(monkeypatch, tmp_path):
    # No override, no ~/.drop/bin/cloudflared, and pretend PATH is empty
    monkeypatch.delenv("DROP_CLOUDFLARED_BIN", raising=False)
    # Point DROP_HOME at empty tmp so ~/.drop/bin/cloudflared check fails
    monkeypatch.setenv("DROP_HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "")
    import importlib
    import drop.config
    importlib.reload(drop.config)
    import drop.utils
    importlib.reload(drop.utils)
    assert drop.utils.find_cloudflared() is None


# get_local_ip — returns a string IP (cannot easily assert exact value).

def test_get_local_ip_returns_string():
    ip = utils.get_local_ip()
    assert isinstance(ip, str)
    parts = ip.split(".")
    assert len(parts) == 4
    assert all(p.isdigit() for p in parts)


# detect_ip — host_override branch is deterministic.

def test_detect_ip_host_override():
    assert utils.detect_ip("1.2.3.4") == "1.2.3.4"


# get_external_ip — network-dependent; just assert it returns either str or None.

@pytest.mark.integration
def test_get_external_ip_returns_str_or_none():
    result = utils.get_external_ip(timeout=2.0)
    assert result is None or isinstance(result, str)


# is_behind_nat — composite of external+local; assert bool.

@pytest.mark.integration
def test_is_behind_nat_returns_bool():
    assert isinstance(utils.is_behind_nat(), bool)
```

- [ ] **Step 2: Run tests, verify they fail (unit-only, skipping integration)**

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest tests/unit/test_utils.py -v -m "not integration"
```

Expected: many failures (`AttributeError: module 'drop.utils' has no attribute 'generate_page_id'`).

- [ ] **Step 3: Implement `utils.py`**

Replace `src/drop/utils.py` with:

```python
"""Pure helpers — IP detection, port allocation, systemd/cloudflared detection,
page-id generation.
"""

import platform
import secrets
import shutil
import socket
import string
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config


def generate_page_id(length: int = 16) -> str:
    """Generate cryptographically secure random page ID (lowercase + digits)."""
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def allocate_free_port() -> int:
    """Allocate a free TCP port from the OS."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def wait_for_port(host: str, port: int, timeout: float = 5.0) -> bool:
    """Block until host:port accepts connections or timeout."""
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


def get_external_ip(timeout: float = 2.0) -> str | None:
    """Best-effort external IP via ifconfig.me (stdlib HTTP, no curl)."""
    try:
        with urllib.request.urlopen("https://ifconfig.me/ip", timeout=timeout) as resp:
            ip = resp.read().decode("ascii", errors="replace").strip()
            if all(c in "0123456789." for c in ip) and ip.count(".") == 3:
                return ip
    except (urllib.error.URLError, OSError, ValueError):
        pass
    return None


def get_local_ip() -> str:
    """Local LAN IP (best-effort via UDP connect trick). Falls back to 127.0.0.1."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def detect_ip(host_override: str | None = None) -> str:
    """Best IP for URLs: explicit override > external > local."""
    if host_override:
        return host_override
    external = get_external_ip()
    if external:
        return external
    return get_local_ip()


def is_behind_nat() -> bool:
    """Heuristic: external IP differs from local IP → behind NAT."""
    external = get_external_ip()
    if not external:
        return False
    return external != get_local_ip()


def has_systemd() -> bool:
    """True if `systemctl --user` is callable (Linux with user systemd)."""
    if platform.system() != "Linux":
        return False
    try:
        subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            capture_output=True,
            timeout=2,
        )
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def find_cloudflared() -> str | None:
    """Locate cloudflared. Priority: DROP_CLOUDFLARED_BIN env > PATH > ~/.drop/bin/."""
    override = config.CLOUDFLARED_BIN_OVERRIDE
    if override and Path(override).exists():
        return override
    path = shutil.which("cloudflared")
    if path:
        return path
    bundled = config.BIN_DIR / "cloudflared"
    if bundled.exists() and bundled.is_file():
        return str(bundled)
    return None
```

- [ ] **Step 4: Run tests, verify all pass (unit + integration)**

Unit:
```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest tests/unit/test_utils.py -v -m "not integration"
```
Expected: ~11 passed.

Integration:
```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest tests/unit/test_utils.py -v -m integration
```
Expected: 2 passed (requires internet for `get_external_ip`; if no internet, skip via `-m "not integration"`).

- [ ] **Step 5: Commit**

```bash
git add src/drop/utils.py tests/unit/test_utils.py
git commit -m "feat(v2): utils module — IP detection, port helpers, has_systemd, find_cloudflared, generate_page_id"
```

---

## Phase 1 Self-Review

After all 4 tasks pass, run:

```bash
cd /home/superbereza/dev/agent-instant-drop
/home/superbereza/dev/agent-instant-drop/.venv/bin/pytest -v
```

Expected: ~63 tests passed (4 harness + 6 config + 20 auth + 22 manifest + 11 utils + 2 integration-marked). Exact count may differ slightly with refactor.

```bash
/home/superbereza/dev/agent-instant-drop/.venv/bin/python3 -c "
from drop import config, auth, manifest, utils
print('config:', config.SCHEMA_VERSION, config.DROP_HOME)
print('auth:', auth.generate_auth_creds())
print('manifest:', manifest.MANIFEST_FILE)
print('utils:', utils.generate_page_id())
"
```

Should print without errors.

Push v2 branch to fire CI:
```bash
git push origin v2
```

After CI green, Phase 1 is done.

## What Phase 1 Does NOT Include (deferred)

- `drop_home` fixture override of config: tests already exercise this via importlib.reload — that's the pattern Phase 2+ should follow.
- v1→v2 migration logic: Phase 2 (storage).
- Use of any of these helpers by other modules: each subsequent phase imports them as needed.
- macOS-specific systemd alternatives: out of scope.

## Phase 2 Readiness

After Phase 1 green, Phase 2 (`storage.py` + `runtime.py` + migration) can rely on:
- `config.PAGES_FILE`, `config.RUNTIME_FILE`, `config.SCHEMA_VERSION`
- `auth.hash_password` (for storing password hashes)
- `utils.generate_page_id`
