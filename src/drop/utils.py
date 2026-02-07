"""Utility functions for drop."""

import fnmatch
import hashlib
import platform
import secrets
import socket
import string
import subprocess
from pathlib import Path


def generate_page_id(length: int = 16) -> str:
    """Generate cryptographically secure random page ID."""
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_password(length: int = 6) -> str:
    """Generate random password (easy to type/dictate)."""
    # Avoid confusing characters: 0/O, 1/l/I
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def hash_password(password: str) -> str:
    """Hash password with SHA256."""
    return "sha256:" + hashlib.sha256(password.encode()).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    """Verify password against hash."""
    if not password_hash:
        return True  # No password required
    expected = hash_password(password)
    return secrets.compare_digest(expected, password_hash)


def get_external_ip(timeout: float = 2.0) -> str | None:
    """Get external IP via ifconfig.me."""
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", str(timeout), "ifconfig.me"],
            capture_output=True,
            text=True,
            timeout=timeout + 1,
        )
        if result.returncode == 0 and result.stdout.strip():
            ip = result.stdout.strip()
            # Basic validation
            if all(c in "0123456789." for c in ip):
                return ip
    except Exception:
        pass
    return None


def get_local_ip() -> str:
    """Get local network IP (not 127.0.0.1)."""
    try:
        # Connect to external address to find local IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def detect_ip(host_override: str | None = None) -> str:
    """Detect best IP to use for URLs."""
    if host_override:
        return host_override

    # Try external IP first
    external = get_external_ip()
    if external:
        return external

    # Fall back to local IP
    return get_local_ip()


MANIFEST_FILE = ".drop-publish"


def is_env_file(name: str) -> bool:
    """Check if filename is a .env file (but allow .env.example)."""
    name = name.lower()
    if name == ".env.example":
        return False
    if name == ".env" or name.startswith(".env."):
        return True
    return False


def load_manifest(directory: Path) -> list[str] | None:
    """Load manifest patterns from .drop-publish file."""
    manifest_path = directory / MANIFEST_FILE
    if not manifest_path.exists():
        return None
    try:
        lines = manifest_path.read_text().strip().split("\n")
        # Filter empty lines and comments
        return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
    except Exception:
        return None


def matches_manifest(relative_path: str, patterns: list[str]) -> bool:
    """Check if relative path matches any manifest pattern."""
    for pattern in patterns:
        # Handle ** for recursive matching
        if "**" in pattern:
            # Convert ** to match any path segments
            # e.g., "assets/**" matches "assets/css/main.css"
            prefix = pattern.split("**")[0].rstrip("/")
            if relative_path.startswith(prefix + "/") or relative_path == prefix:
                return True
        elif fnmatch.fnmatch(relative_path, pattern):
            return True
        # Also check if pattern matches a parent directory
        elif relative_path.startswith(pattern.rstrip("/") + "/"):
            return True
    return False


def safe_path(base: Path, requested: str, manifest: list[str] | None = None) -> Path | None:
    """
    Resolve path and ensure it's within base directory and allowed by manifest.
    Returns None if path is unsafe.
    """
    try:
        base = base.resolve()
        # Normalize and resolve the requested path
        full_path = (base / requested).resolve()

        # Check path is within base
        if not full_path.is_relative_to(base):
            return None

        # Check symlinks don't escape
        if full_path.is_symlink():
            target = full_path.resolve()
            if not target.is_relative_to(base):
                return None

        # Always block .env files (except .env.example)
        if is_env_file(full_path.name):
            return None

        # Check manifest if provided
        if manifest is not None:
            relative = str(full_path.relative_to(base))
            if not matches_manifest(relative, manifest):
                return None

        return full_path
    except Exception:
        return None


def has_systemd() -> bool:
    """Check if systemd is available (Linux with systemd user services)."""
    if platform.system() != "Linux":
        return False
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            capture_output=True,
            timeout=2,
        )
        # Returns 0 for running, or other codes but command exists
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def is_behind_nat() -> bool:
    """Check if we're behind NAT (external IP != local IP)."""
    external = get_external_ip()
    if not external:
        # Can't determine external IP, assume not behind NAT
        return False
    local = get_local_ip()
    return external != local
