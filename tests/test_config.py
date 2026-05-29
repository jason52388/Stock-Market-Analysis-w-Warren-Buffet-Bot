"""Tests for the settings loader and env-var expansion."""
from __future__ import annotations

import os

import pytest

from warren_bot.config import _expand, load_settings, repo_root


class TestEnvExpand:
    def test_simple_string(self, monkeypatch):
        monkeypatch.setenv("MY_VAR", "hello")
        assert _expand("${MY_VAR}") == "hello"

    def test_missing_var_becomes_empty(self, monkeypatch):
        monkeypatch.delenv("UNSET_VAR", raising=False)
        assert _expand("${UNSET_VAR}") == ""

    def test_recursive_dict(self, monkeypatch):
        monkeypatch.setenv("KEY", "secret")
        result = _expand({"a": {"b": "${KEY}", "c": [1, "${KEY}"]}})
        assert result == {"a": {"b": "secret", "c": [1, "secret"]}}

    def test_non_string_passthrough(self):
        assert _expand(42) == 42
        assert _expand(None) is None
        assert _expand(True) is True

    def test_no_var_pattern_unchanged(self):
        assert _expand("no vars here") == "no vars here"


class TestRepoRoot:
    def test_finds_pyproject(self):
        root = repo_root()
        assert (root / "pyproject.toml").exists()


class TestLoadSettings:
    def test_resolves_relative_path_against_repo_root(self):
        # The default "config/settings.yaml" should work from any cwd
        original_cwd = os.getcwd()
        try:
            os.chdir("/tmp")
            settings = load_settings()
            assert "weights" in settings
            assert "criteria" in settings
        finally:
            os.chdir(original_cwd)

    def test_absolute_path_used_verbatim(self, tmp_path):
        f = tmp_path / "custom.yaml"
        f.write_text("weights:\n  moat: 1.0\n")
        settings = load_settings(f)
        assert settings == {"weights": {"moat": 1.0}}

    def test_env_expansion_in_loaded_yaml(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_SECRET", "abc123")
        f = tmp_path / "with_env.yaml"
        f.write_text("api_key: ${TEST_SECRET}\n")
        settings = load_settings(f)
        assert settings["api_key"] == "abc123"
