"""Tests for environment variable resolution in butler config values."""

from __future__ import annotations

from pathlib import Path

import pytest

from butlers.config import ConfigError, load_config, resolve_env_vars

pytestmark = pytest.mark.unit
# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_toml(tmp_path: Path, content: str) -> Path:
    """Write *content* to butler.toml inside *tmp_path* and return the directory."""
    (tmp_path / "butler.toml").write_text(content)
    return tmp_path


# ---------------------------------------------------------------------------
# resolve_env_vars — unit tests
# ---------------------------------------------------------------------------


class TestResolveEnvVars:
    """Direct tests for the resolve_env_vars() function."""

    def test_simple_string(self, monkeypatch):
        """A string containing ${VAR} is resolved."""
        monkeypatch.setenv("MY_SECRET", "hunter2")
        assert resolve_env_vars("${MY_SECRET}") == "hunter2"

    def test_partial_string(self, monkeypatch):
        """Env vars embedded in a larger string are replaced in-place."""
        monkeypatch.setenv("HOST", "db.example.com")
        monkeypatch.setenv("PORT", "5432")
        result = resolve_env_vars("postgresql://${HOST}:${PORT}/mydb")
        assert result == "postgresql://db.example.com:5432/mydb"

    def test_no_env_vars(self):
        """Strings without ${...} patterns pass through unchanged."""
        assert resolve_env_vars("plain string") == "plain string"

    def test_nested_dict(self, monkeypatch):
        """Env vars in nested dict values are resolved."""
        monkeypatch.setenv("DB_PASS", "s3cret")
        data = {"outer": {"inner": {"password": "${DB_PASS}"}}}
        result = resolve_env_vars(data)
        assert result == {"outer": {"inner": {"password": "s3cret"}}}

    def test_list_values(self, monkeypatch):
        """Env vars inside list elements are resolved."""
        monkeypatch.setenv("ITEM_A", "alpha")
        monkeypatch.setenv("ITEM_B", "beta")
        data = ["${ITEM_A}", "${ITEM_B}", "literal"]
        result = resolve_env_vars(data)
        assert result == ["alpha", "beta", "literal"]

    def test_mixed_list_in_dict(self, monkeypatch):
        """Env vars in lists nested inside dicts are resolved."""
        monkeypatch.setenv("TAG", "v1")
        data = {"tags": ["${TAG}", "static"]}
        result = resolve_env_vars(data)
        assert result == {"tags": ["v1", "static"]}

    def test_non_string_passthrough(self):
        """Non-string values (int, float, bool, None) pass through unchanged."""
        assert resolve_env_vars(42) == 42
        assert resolve_env_vars(3.14) == 3.14
        assert resolve_env_vars(True) is True
        assert resolve_env_vars(None) is None

    def test_dict_with_non_string_values(self, monkeypatch):
        """Dict with mixed string and non-string values — only strings resolved."""
        monkeypatch.setenv("NAME", "jarvis")
        data = {"name": "${NAME}", "port": 8100, "enabled": True}
        result = resolve_env_vars(data)
        assert result == {"name": "jarvis", "port": 8100, "enabled": True}

    def test_missing_env_var_raises(self):
        """Missing env var raises ConfigError with variable name."""
        with pytest.raises(ConfigError, match="NONEXISTENT_VAR"):
            resolve_env_vars("${NONEXISTENT_VAR}")

    def test_missing_env_var_error_includes_original(self):
        """Error message includes the original string for context."""
        with pytest.raises(ConfigError, match="original"):
            resolve_env_vars("prefix_${MISSING_VAR}_suffix")

    def test_multiple_missing_vars_in_one_string(self):
        """All missing vars in a single string are reported together."""
        with pytest.raises(ConfigError, match="MISS_A") as exc_info:
            resolve_env_vars("${MISS_A}:${MISS_B}")
        assert "MISS_B" in str(exc_info.value)

    def test_missing_var_in_nested_dict(self):
        """Missing var inside a nested dict raises ConfigError."""
        data = {"level1": {"level2": "${DOES_NOT_EXIST}"}}
        with pytest.raises(ConfigError, match="DOES_NOT_EXIST"):
            resolve_env_vars(data)

    def test_empty_string(self):
        """Empty string passes through unchanged."""
        assert resolve_env_vars("") == ""

    def test_empty_dict(self):
        """Empty dict passes through unchanged."""
        assert resolve_env_vars({}) == {}

    def test_empty_list(self):
        """Empty list passes through unchanged."""
        assert resolve_env_vars([]) == []

    def test_multiple_refs_same_var(self, monkeypatch):
        """Multiple references to the same var in one string are all resolved."""
        monkeypatch.setenv("X", "val")
        assert resolve_env_vars("${X}-${X}") == "val-val"

    def test_env_var_with_underscores_and_digits(self, monkeypatch):
        """Variable names with underscores and digits are valid."""
        monkeypatch.setenv("MY_VAR_2", "works")
        assert resolve_env_vars("${MY_VAR_2}") == "works"

    def test_dollar_without_braces_ignored(self):
        """Bare $VAR (without braces) is NOT resolved — only ${VAR} syntax."""
        assert resolve_env_vars("$NOT_A_REF") == "$NOT_A_REF"


# ---------------------------------------------------------------------------
# Integration with load_config
# ---------------------------------------------------------------------------


class TestLoadConfigEnvVars:
    """Env var resolution integrated into the full config loading pipeline."""

    def test_module_config_resolved(self, tmp_path, monkeypatch):
        """Module config values with ${VAR} are resolved before being returned."""
        monkeypatch.setenv("SOURCE_EMAIL_PASSWORD", "p@ssw0rd")
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        toml = """\
[butler]
name = "mailbot"
port = 40200

[modules.email]
password = "${SOURCE_EMAIL_PASSWORD}"
host = "${SMTP_HOST}"
port = 587
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        assert cfg.modules["email"]["password"] == "p@ssw0rd"
        assert cfg.modules["email"]["host"] == "smtp.example.com"
        assert cfg.modules["email"]["port"] == 587  # non-string preserved

    def test_description_resolved(self, tmp_path, monkeypatch):
        """Env vars in the butler description are resolved."""
        monkeypatch.setenv("ENV_NAME", "production")
        toml = """\
[butler]
name = "bot"
port = 8000
description = "Running in ${ENV_NAME}"
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        assert cfg.description == "Running in production"

    def test_schedule_prompt_resolved(self, tmp_path, monkeypatch):
        """Env vars in schedule prompts are resolved."""
        monkeypatch.setenv("TEAM_NAME", "engineering")
        toml = """\
[butler]
name = "schedbot"
port = 8300

[[butler.schedule]]
name = "report"
cron = "0 9 * * *"
prompt = "Generate report for ${TEAM_NAME}"
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        assert cfg.schedules[0].prompt == "Generate report for engineering"

    def test_missing_var_in_module_config_raises(self, tmp_path):
        """Missing env var in module config raises ConfigError at load time."""
        toml = """\
[butler]
name = "failbot"
port = 8400

[modules.email]
password = "${UNDEFINED_PASSWORD}"
"""
        config_dir = _write_toml(tmp_path, toml)
        with pytest.raises(ConfigError, match="UNDEFINED_PASSWORD"):
            load_config(config_dir)

    def test_db_name_resolved(self, tmp_path, monkeypatch):
        """Env vars in db name are resolved."""
        monkeypatch.setenv("DB_SUFFIX", "prod")
        toml = """\
[butler]
name = "dbbot"
port = 8500

[butler.db]
name = "butler_${DB_SUFFIX}"
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        assert cfg.db_name == "butler_prod"

    def test_db_schema_resolved(self, tmp_path, monkeypatch):
        """Env vars in db schema are resolved."""
        monkeypatch.setenv("BUTLER_SCHEMA", "general")
        toml = """\
[butler]
name = "dbbot"
port = 8501

[butler.db]
name = "butlers"
schema = "${BUTLER_SCHEMA}"
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        assert cfg.db_name == "butlers"
        assert cfg.db_schema == "general"
