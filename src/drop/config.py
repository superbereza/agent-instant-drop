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
