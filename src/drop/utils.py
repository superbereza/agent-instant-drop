"""Pure helpers — IP detection, port allocation, systemd/cloudflared detection,
page-id generation.
"""

import ipaddress
import os
import platform
import secrets
import shutil
import socket
import string
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config


def _hex_to_ip(h: str) -> str | None:
    """Decode a /proc/net/tcp{,6} local-address hex field to an IP string."""
    try:
        if len(h) == 8:  # IPv4, stored little-endian
            return str(ipaddress.IPv4Address(bytes(reversed(bytes.fromhex(h)))))
        if len(h) == 32:  # IPv6, four little-endian 32-bit words
            out = bytearray()
            for i in range(0, 32, 8):
                out += bytes(reversed(bytes.fromhex(h[i:i + 8])))
            return str(ipaddress.IPv6Address(bytes(out)))
    except (ValueError, ipaddress.AddressValueError):
        return None
    return None


def _listening_ips(port: int) -> set[str] | None:
    """Local IPs with a LISTEN socket on `port`, via /proc/net/tcp{,6}.

    Returns None when the tables can't be read (e.g. macOS), so callers can
    fall back to a network probe.
    """
    found: set[str] = set()
    read_any = False
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(path).read_text().splitlines()[1:]
        except OSError:
            continue
        read_any = True
        for line in lines:
            parts = line.split()
            if len(parts) < 4 or parts[3] != "0A":  # 0A == TCP_LISTEN
                continue
            addr_hex, _, port_hex = parts[1].rpartition(":")
            try:
                if int(port_hex, 16) != port:
                    continue
            except ValueError:
                continue
            ip = _hex_to_ip(addr_hex)
            if ip:
                found.add(ip)
    return found if read_any else None


def app_binds_non_loopback(port: int) -> bool | None:
    """True if anything listens on `port` on a non-loopback address.

    0.0.0.0 / :: (wildcard) and any concrete LAN/public IP count as exposed;
    only 127.0.0.0/8 and ::1 are safe. Returns None if it can't introspect
    (caller should fall back to a network probe).
    """
    ips = _listening_ips(port)
    if ips is None:
        return None
    for ip in ips:
        addr = ipaddress.ip_address(ip)
        if addr.is_unspecified or not addr.is_loopback:
            return True
    return False


def atomic_write_text(path: Path, text: str) -> None:
    """Write text durably: temp file in the same dir, fsync, then os.replace.

    Prevents a crash mid-write from leaving a truncated file — which the JSON
    loaders treat as "empty registry", silently dropping every page.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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
