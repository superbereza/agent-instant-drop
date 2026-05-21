"""Shared fixtures for drop v2 tests.

Fixtures:
    drop_home   — tmp dir set as DROP_HOME env so v2 modules see an isolated
                  ~/.drop. Restored after the test via monkeypatch.
    free_port   — int, a free local TCP port allocated from the OS.
"""

import socket

import pytest


@pytest.fixture
def drop_home(tmp_path, monkeypatch):
    """Isolated DROP_HOME for one test.

    v2 modules read `os.environ.get("DROP_HOME")` and fall back to
    `~/.drop` when unset. By setting DROP_HOME to a tmp dir here, each
    test gets a fresh storage root and cleanup is automatic.
    """
    home = tmp_path / ".drop"
    home.mkdir()
    monkeypatch.setenv("DROP_HOME", str(home))
    return home


@pytest.fixture
def free_port():
    """Allocate a free TCP port from the OS.

    Note: the port is released as soon as the socket goes out of scope.
    A second caller within the same test may receive a different (also
    free) port. Race-on-bind is possible if another process binds in
    the gap; in practice this is rare on a developer machine.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]
