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
