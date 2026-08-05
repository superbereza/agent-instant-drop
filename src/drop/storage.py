"""Page CRUD over ~/.drop/pages.json with UNIQUE(name) constraint.

Persistent registry. Volatile runtime (pids, ports, tunnel URL) lives in
drop.runtime (separate file). Migration from v1's flat schema is done
once on first read.
"""

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, UTC
from pathlib import Path
from typing import Literal

from . import config


def _drop_home() -> Path:
    """Return DROP_HOME, re-reading env at call time so tests can override."""
    return Path(os.environ.get("DROP_HOME") or Path.home() / ".drop")


def _pages_file() -> Path:
    return _drop_home() / "pages.json"


def _runtime_file() -> Path:
    return _drop_home() / "runtime.json"


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
    _drop_home().mkdir(parents=True, exist_ok=True)


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
    pages_file = _pages_file()
    if not pages_file.exists():
        return {}
    try:
        raw = json.loads(pages_file.read_text())
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
    from . import utils
    utils.atomic_write_text(_pages_file(), json.dumps(envelope, indent=2))


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


def matching_page_ids(identifier: str) -> list[str]:
    """All page_ids an identifier could refer to (exact id > prefix > name).

    Lets callers tell 'ambiguous' apart from 'not found' (get_page collapses
    both to None).
    """
    pages = load_pages()
    if identifier in pages:
        return [identifier]
    prefix_matches = [pid for pid in pages if pid.startswith(identifier)]
    if prefix_matches:
        return prefix_matches
    return [pid for pid, p in pages.items() if p.name == identifier]


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


def update_page(page: Page) -> Page:
    """Overwrite an existing page in place (same page_id → same URL). Enforces
    the same name-uniqueness rule as add_page (ignoring the page's own row)."""
    pages = load_pages()
    if page.name:
        for existing_id, existing in pages.items():
            if existing_id != page.page_id and existing.name == page.name:
                raise ValueError(
                    f"name '{page.name}' already exists (page_id {existing_id[:8]})"
                )
    pages[page.page_id] = page
    save_pages(pages)
    return page


def find_by_source(source: Path, page_type: str) -> tuple[str, Page] | None:
    """First (page_id, Page) whose source path and type match — used to dedupe
    re-publishes of the same file so `drop add` stays idempotent."""
    for pid, p in load_pages().items():
        if p.type == page_type and p.source == source:
            return (pid, p)
    return None


# ---- Migration (v1 flat dict -> v2 versioned envelope) ----

_RUNTIME_FIELDS_V1 = {"pid", "proxy_pid", "proxy_port", "tunnel_pid", "tunnel_url"}


def maybe_migrate() -> None:
    """If pages.json is v1 (flat dict), back up and rewrite as v2."""
    pages_file = _pages_file()
    if not pages_file.exists():
        return
    try:
        raw = json.loads(pages_file.read_text())
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(raw, dict) and raw.get("version") == config.SCHEMA_VERSION:
        return  # already migrated
    if not isinstance(raw, dict):
        return  # unrecognized; leave alone

    # Backup
    backup = pages_file.with_suffix(".json.v1.bak")
    shutil.copy(pages_file, backup)

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
    _runtime_file().write_text(json.dumps(
        {"version": config.SCHEMA_VERSION, "runtimes": runtimes_data},
        indent=2,
    ))
