"""Tests for drop.runtime — PageRuntime dataclass + PID-probe + runtime.json CRUD."""

import json
import os
import subprocess
import time

import pytest

from drop import runtime


# Dataclass defaults

def test_runtime_defaults(drop_home):
    r = runtime.PageRuntime(page_id="abc")
    assert r.page_id == "abc"
    assert r.app_pid == 0
    assert r.proxy_pid == 0
    assert r.proxy_port == 0
    assert r.tunnel_pid == 0
    assert r.tunnel_url == ""


# Alive probes

def test_is_app_alive_zero_is_false(drop_home):
    r = runtime.PageRuntime(page_id="abc")
    assert r.is_app_alive() is False


def test_is_app_alive_real_process(drop_home):
    # Spawn a quick `sleep 30` and verify probe
    proc = subprocess.Popen(["sleep", "30"])
    try:
        r = runtime.PageRuntime(page_id="abc", app_pid=proc.pid)
        assert r.is_app_alive() is True
    finally:
        proc.terminate()
        proc.wait(timeout=2)
    # After termination, probe is False
    r2 = runtime.PageRuntime(page_id="abc", app_pid=proc.pid)
    assert r2.is_app_alive() is False


def test_is_proxy_alive_zero(drop_home):
    assert runtime.PageRuntime(page_id="x").is_proxy_alive() is False


def test_is_tunnel_alive_zero(drop_home):
    assert runtime.PageRuntime(page_id="x").is_tunnel_alive() is False


# get_runtime / save_runtime / clear_runtime

def test_get_runtime_missing_returns_empty(drop_home):
    r = runtime.get_runtime("nope")
    assert r.page_id == "nope"
    assert r.app_pid == 0


def test_save_and_get_runtime_round_trip(drop_home):
    r = runtime.PageRuntime(
        page_id="abc", app_pid=100, proxy_pid=200, proxy_port=8080,
        tunnel_pid=300, tunnel_url="https://x/",
    )
    runtime.save_runtime(r)
    loaded = runtime.get_runtime("abc")
    assert loaded.app_pid == 100
    assert loaded.proxy_pid == 200
    assert loaded.proxy_port == 8080
    assert loaded.tunnel_pid == 300
    assert loaded.tunnel_url == "https://x/"


def test_save_runtime_writes_versioned_envelope(drop_home):
    r = runtime.PageRuntime(page_id="abc", app_pid=1)
    runtime.save_runtime(r)
    raw = json.loads((drop_home / "runtime.json").read_text())
    assert raw["version"] == 2
    assert "runtimes" in raw
    assert raw["runtimes"]["abc"]["app_pid"] == 1


def test_clear_runtime_removes_entry(drop_home):
    runtime.save_runtime(runtime.PageRuntime(page_id="abc", app_pid=1))
    runtime.save_runtime(runtime.PageRuntime(page_id="def", app_pid=2))
    runtime.clear_runtime("abc")
    raw = json.loads((drop_home / "runtime.json").read_text())
    assert "abc" not in raw["runtimes"]
    assert "def" in raw["runtimes"]


def test_clear_runtime_missing_no_error(drop_home):
    runtime.clear_runtime("nope")  # should not raise


def test_load_runtimes_empty_when_no_file(drop_home):
    rtmap = runtime.load_runtimes()
    assert rtmap == {}
