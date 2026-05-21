"""Tests for drop.auth — password gen/hash/verify, basic-auth parse, rate-limit."""

import base64
import time

import pytest

from drop import auth


# generate_password

def test_generate_password_default_length():
    pw = auth.generate_password()
    assert len(pw) == 6


def test_generate_password_custom_length():
    pw = auth.generate_password(12)
    assert len(pw) == 12


def test_generate_password_safe_alphabet():
    # No 0/O, 1/l/I confusable chars
    pw = auth.generate_password(1000)
    forbidden = "0Oo1lLiI"
    assert not any(c in forbidden for c in pw), f"Found confusable in {pw}"


# hash_password / verify_password

def test_hash_password_returns_prefixed_string():
    h = auth.hash_password("secret")
    assert h.startswith("sha256:")
    # 64 hex chars after "sha256:"
    assert len(h) == 7 + 64


def test_hash_password_deterministic():
    assert auth.hash_password("hello") == auth.hash_password("hello")


def test_hash_password_distinct_for_different_inputs():
    assert auth.hash_password("a") != auth.hash_password("b")


def test_verify_password_round_trip():
    h = auth.hash_password("right")
    assert auth.verify_password("right", h) is True


def test_verify_password_rejects_wrong():
    h = auth.hash_password("right")
    assert auth.verify_password("wrong", h) is False


def test_verify_password_empty_hash_allows_anything():
    # Convention from v1: empty hash means no password set.
    assert auth.verify_password("anything", "") is True


# generate_auth_creds

def test_generate_auth_creds_returns_user_drop_and_12char():
    user, pw = auth.generate_auth_creds()
    assert user == "drop"
    assert len(pw) == 12


# parse_basic_auth

def test_parse_basic_auth_well_formed():
    header = "Basic " + base64.b64encode(b"alice:s3cret").decode("ascii")
    assert auth.parse_basic_auth(header) == ("alice", "s3cret")


def test_parse_basic_auth_missing_prefix():
    assert auth.parse_basic_auth("alice:s3cret") is None


def test_parse_basic_auth_empty():
    assert auth.parse_basic_auth("") is None


def test_parse_basic_auth_bad_base64():
    assert auth.parse_basic_auth("Basic !!!not-base64!!!") is None


def test_parse_basic_auth_no_colon_in_decoded():
    header = "Basic " + base64.b64encode(b"nouser").decode("ascii")
    assert auth.parse_basic_auth(header) is None


def test_parse_basic_auth_password_may_contain_colon():
    header = "Basic " + base64.b64encode(b"u:p:with:colons").decode("ascii")
    assert auth.parse_basic_auth(header) == ("u", "p:with:colons")


def test_parse_basic_auth_non_utf8():
    # 0xff is not valid utf-8
    header = "Basic " + base64.b64encode(b"\xffuser:pw").decode("ascii")
    assert auth.parse_basic_auth(header) is None


# Rate limit

def test_rate_limiter_allows_under_limit():
    rl = auth.RateLimiter(max_attempts=3, window_sec=60)
    assert rl.check_and_record("1.2.3.4", "page1") is True
    assert rl.check_and_record("1.2.3.4", "page1") is True
    assert rl.check_and_record("1.2.3.4", "page1") is True


def test_rate_limiter_rejects_over_limit():
    rl = auth.RateLimiter(max_attempts=3, window_sec=60)
    for _ in range(3):
        rl.check_and_record("1.2.3.4", "page1")
    assert rl.check_and_record("1.2.3.4", "page1") is False


def test_rate_limiter_per_ip_isolation():
    rl = auth.RateLimiter(max_attempts=2, window_sec=60)
    for _ in range(2):
        rl.check_and_record("1.1.1.1", "page1")
    # different IP — fresh quota
    assert rl.check_and_record("2.2.2.2", "page1") is True


def test_rate_limiter_per_page_isolation():
    rl = auth.RateLimiter(max_attempts=2, window_sec=60)
    for _ in range(2):
        rl.check_and_record("1.1.1.1", "page1")
    # same IP, different page — fresh quota
    assert rl.check_and_record("1.1.1.1", "page2") is True


def test_rate_limiter_window_expires():
    rl = auth.RateLimiter(max_attempts=1, window_sec=0)  # immediate expiry
    rl.check_and_record("1.1.1.1", "page1")
    time.sleep(0.01)
    # window of 0 means previous attempts are already out — allow new
    assert rl.check_and_record("1.1.1.1", "page1") is True
