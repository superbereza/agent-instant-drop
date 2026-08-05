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
# Authoritative public base URL for the static server, written by the deployment
# (drop-install-env / tailnet-publish) or set by hand. Read before any guessing,
# so the printed link is the real one (tailnet/cloudflare) — not a wrong IP:port
# guess. Overridable per-invocation via the DROP_PUBLIC_URL env var.
PUBLIC_URL_FILE = DROP_HOME / "base_url"

CLOUDFLARED_BIN_OVERRIDE = os.environ.get("DROP_CLOUDFLARED_BIN")

DEFAULT_SERVER_PORT = 8080
AUTH_REALM = "drop"
DEFAULT_AUTH_USER = "drop"
SCHEMA_VERSION = 2

# Default length of auto-generated static-page passwords (~57 bits at length 10).
STATIC_PASSWORD_LENGTH = 10

# Password-attempt rate limits (used by server.py).
RL_PER_IP_MAX = 5        # attempts per (remote_addr, page) per window
RL_GLOBAL_MAX = 30       # attempts per page per window, regardless of source IP
RL_WINDOW_SEC = 60
