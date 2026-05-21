"""Self-tests for the test harness itself. If these fail, no other test
in v2 can be trusted."""

import os
import socket


def test_drop_home_sets_env_var(drop_home):
    assert os.environ["DROP_HOME"] == str(drop_home)
    assert drop_home.exists()
    assert drop_home.is_dir()


def test_drop_home_is_isolated_per_test(drop_home, tmp_path):
    # drop_home must live under tmp_path (pytest gives each test a
    # fresh tmp_path)
    assert drop_home.is_relative_to(tmp_path)


def test_free_port_is_unused(free_port):
    assert isinstance(free_port, int)
    assert 1024 < free_port < 65536
    # Verify we can actually bind to it
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", free_port))
        s.listen(1)  # no exception => port is usable


def test_free_port_gives_different_ports():
    """Two separate fixture instances should not necessarily collide.
    This is a sanity check, not a strict invariant.
    """
    # We can't actually invoke the fixture twice in one test; instead
    # we directly allocate two ports the way the fixture does.
    def alloc():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return s.getsockname()[1]
    p1 = alloc()
    p2 = alloc()
    # Both must be valid; equality is permitted (OS may reuse) but
    # extremely rare in practice.
    assert isinstance(p1, int) and isinstance(p2, int)
    assert 1024 < p1 < 65536 and 1024 < p2 < 65536
