"""Manifest matching + safe_path utilities for drop.

A directory page must include a `.drop-publish` manifest listing
allowed file patterns. `safe_path` enforces both path-traversal safety
and manifest membership.
"""

import fnmatch
from pathlib import Path


MANIFEST_FILE = ".drop-publish"


def is_env_file(name: str) -> bool:
    """Treat .env / .env.* / .envrc as secret, except .env.example."""
    lowered = name.lower()
    if lowered == ".env.example":
        return False
    if lowered == ".envrc":  # direnv — commonly holds secrets
        return True
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
    """Check if `relative_path` matches any pattern in `patterns`.

    Semantics:
      - `**`            recursive: matches everything under the prefix (bare
                        `**` matches every file).
      - `*.html`        single-segment glob: matches only same-depth files, it
                        does NOT cross `/` (so `*.html` will not silently
                        publish `private/secret.html`).
      - `dir` / `dir/`  directory prefix: matches everything beneath it.
    """
    for pattern in patterns:
        if "**" in pattern:
            prefix = pattern.split("**")[0].rstrip("/")
            if prefix == "":
                return True  # bare ** (or **/...) → match everything
            if relative_path == prefix or relative_path.startswith(prefix + "/"):
                return True
        elif fnmatch.fnmatch(relative_path, pattern) and \
                relative_path.count("/") == pattern.count("/"):
            # `*` must not cross a directory boundary: keep the glob anchored to
            # the same depth as the pattern.
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

        # full_path is already fully resolved, so any symlink (including
        # intermediate components) has been followed; the is_relative_to check
        # above is what catches an escape past `base`, symlinked or not.
        if not full_path.is_relative_to(base_resolved):
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
