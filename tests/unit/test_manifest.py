"""Tests for drop.manifest — manifest patterns + safe_path + is_env_file."""

import os
from pathlib import Path

import pytest

from drop import manifest


# MANIFEST_FILE constant

def test_manifest_filename():
    assert manifest.MANIFEST_FILE == ".drop-publish"


# is_env_file

def test_is_env_file_blocks_dot_env():
    assert manifest.is_env_file(".env") is True


def test_is_env_file_blocks_dot_env_local():
    assert manifest.is_env_file(".env.local") is True


def test_is_env_file_blocks_dot_env_production():
    assert manifest.is_env_file(".env.production") is True


def test_is_env_file_blocks_dot_envrc():
    assert manifest.is_env_file(".envrc") is True


def test_is_env_file_allows_dot_env_example():
    assert manifest.is_env_file(".env.example") is False


def test_is_env_file_allows_normal_files():
    assert manifest.is_env_file("index.html") is False
    assert manifest.is_env_file("readme.md") is False


def test_is_env_file_case_insensitive():
    assert manifest.is_env_file(".ENV") is True
    assert manifest.is_env_file(".Env.LOCAL") is True


# load_manifest

def test_load_manifest_missing_file(tmp_path):
    assert manifest.load_manifest(tmp_path) is None


def test_load_manifest_reads_lines(tmp_path):
    (tmp_path / ".drop-publish").write_text("index.html\nassets/**\n")
    patterns = manifest.load_manifest(tmp_path)
    assert patterns == ["index.html", "assets/**"]


def test_load_manifest_strips_blank_lines_and_comments(tmp_path):
    (tmp_path / ".drop-publish").write_text(
        "# this is a comment\n"
        "index.html\n"
        "\n"
        "  # indented comment\n"
        "assets/**\n"
    )
    patterns = manifest.load_manifest(tmp_path)
    assert patterns == ["index.html", "assets/**"]


# matches_manifest

def test_matches_manifest_exact_file():
    assert manifest.matches_manifest("index.html", ["index.html"]) is True
    assert manifest.matches_manifest("other.html", ["index.html"]) is False


def test_matches_manifest_glob_extension():
    assert manifest.matches_manifest("a.html", ["*.html"]) is True
    assert manifest.matches_manifest("a.css", ["*.html"]) is False


def test_matches_manifest_single_glob_does_not_cross_slash():
    # Security: `*.html` must match only top-level files, never nested ones,
    # so it can't silently publish private/secret.html.
    assert manifest.matches_manifest("private/secret.html", ["*.html"]) is False
    assert manifest.matches_manifest("assets/sub/x.css", ["assets/*.css"]) is False
    assert manifest.matches_manifest("assets/x.css", ["assets/*.css"]) is True


def test_matches_manifest_bare_double_star_matches_all():
    assert manifest.matches_manifest("index.html", ["**"]) is True
    assert manifest.matches_manifest("deep/nested/file.js", ["**"]) is True


def test_matches_manifest_double_star_directory():
    patterns = ["assets/**"]
    assert manifest.matches_manifest("assets/css/main.css", patterns) is True
    assert manifest.matches_manifest("assets/index.html", patterns) is True
    assert manifest.matches_manifest("assets", patterns) is True
    assert manifest.matches_manifest("other/file", patterns) is False


def test_matches_manifest_directory_prefix_match():
    # Listing a directory name allows files inside it too
    assert manifest.matches_manifest("assets/main.css", ["assets/"]) is True
    assert manifest.matches_manifest("assets/main.css", ["assets"]) is True


def test_matches_manifest_no_patterns():
    assert manifest.matches_manifest("anything", []) is False


# safe_path

def test_safe_path_simple_resolves(tmp_path):
    (tmp_path / "index.html").write_text("ok")
    result = manifest.safe_path(tmp_path, "index.html")
    assert result == (tmp_path / "index.html").resolve()


def test_safe_path_blocks_traversal(tmp_path):
    (tmp_path / "x").mkdir()
    result = manifest.safe_path(tmp_path / "x", "../escape")
    assert result is None


def test_safe_path_blocks_absolute_outside(tmp_path):
    result = manifest.safe_path(tmp_path, "/etc/passwd")
    assert result is None


def test_safe_path_blocks_env_files(tmp_path):
    (tmp_path / ".env").write_text("SECRET=x")
    assert manifest.safe_path(tmp_path, ".env") is None


def test_safe_path_allows_env_example(tmp_path):
    (tmp_path / ".env.example").write_text("EXAMPLE=y")
    result = manifest.safe_path(tmp_path, ".env.example")
    assert result == (tmp_path / ".env.example").resolve()


def test_safe_path_blocks_symlink_outside(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret")
    inside = tmp_path / "link"
    inside.symlink_to(outside)
    assert manifest.safe_path(tmp_path, "link") is None


def test_safe_path_respects_manifest_allow(tmp_path):
    (tmp_path / "ok.html").write_text("ok")
    result = manifest.safe_path(tmp_path, "ok.html", manifest=["*.html"])
    assert result is not None


def test_safe_path_respects_manifest_deny(tmp_path):
    (tmp_path / "secret.json").write_text("{}")
    assert manifest.safe_path(tmp_path, "secret.json", manifest=["*.html"]) is None
