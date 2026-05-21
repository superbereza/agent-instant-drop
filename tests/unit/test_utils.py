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
