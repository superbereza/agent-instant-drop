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
