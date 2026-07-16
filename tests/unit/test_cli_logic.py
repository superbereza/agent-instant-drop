"""Unit tests for CLI logic that doesn't need a subprocess: ambiguous-id
resolution (O8) and the degraded app status (O7)."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from drop import cli, runtime, storage, utils


def _dead_pid() -> int:
    """A pid that is reliably not alive."""
    pid = 2147483647
    try:
        os.kill(pid, 0)
        pytest.skip("chosen dead pid happens to be alive")
    except ProcessLookupError:
        return pid
    except PermissionError:
        pytest.skip("cannot probe chosen pid")
    return pid


# O8 — ambiguous identifier

def test_matching_page_ids_disambiguates(drop_home):
    for pid in ("abc111aaaaaaaaaa", "abc222bbbbbbbbbb", "zzz333cccccccccc"):
        storage.add_page(storage.Page(page_id=pid, source=Path("/tmp/x"), type="static"))
    assert set(storage.matching_page_ids("abc")) == {"abc111aaaaaaaaaa", "abc222bbbbbbbbbb"}
    assert storage.matching_page_ids("abc111aaaaaaaaaa") == ["abc111aaaaaaaaaa"]
    assert storage.matching_page_ids("nope") == []


def test_cmd_remove_reports_ambiguous(drop_home, capsys):
    for pid in ("abc111aaaaaaaaaa", "abc222bbbbbbbbbb"):
        storage.add_page(storage.Page(page_id=pid, source=Path("/tmp/x"), type="static"))
    rc = cli.cmd_remove(SimpleNamespace(id="abc"))
    out = capsys.readouterr()
    assert rc == 1
    assert "ambiguous" in out.err.lower()
    # nothing was removed
    assert len(storage.list_pages()) == 2


# O7 — degraded status when the app lives but its proxy died

def test_cmd_list_shows_degraded(drop_home, capsys, monkeypatch):
    monkeypatch.setattr(utils, "detect_ip", lambda *a, **k: "1.2.3.4")
    storage.add_page(storage.Page(
        page_id="app1", source=Path("/tmp/x"), type="app", name="app1",
        port=12345, auth=storage.AuthConfig(scheme="basic", user="drop",
                                            password_hash="sha256:deadbeef"),
    ))
    rt = runtime.PageRuntime(page_id="app1", app_pid=os.getpid(),
                             proxy_pid=_dead_pid(), proxy_port=54321)
    runtime.save_runtime(rt)
    cli.cmd_list(SimpleNamespace(all=True))
    out = capsys.readouterr().out
    assert "degraded" in out.lower() and "proxy" in out.lower()
