"""Tests for ButlerType enum and PermissionsConfig config parsing (bu-8njj0.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from butlers.config import (
    ButlerConfig,
    ButlerType,
    ConfigError,
    PermissionsConfig,
    load_config,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_toml(tmp_path: Path, content: str, filename: str = "butler.toml") -> Path:
    """Write *content* to a TOML file inside *tmp_path* and return the directory."""
    (tmp_path / filename).write_text(content)
    return tmp_path


# ---------------------------------------------------------------------------
# ButlerType enum
# ---------------------------------------------------------------------------


def test_butler_type_enum_values():
    """ButlerType has BUTLER and STAFFER values with expected string representations."""
    assert ButlerType.BUTLER == "butler"
    assert ButlerType.STAFFER == "staffer"
    assert ButlerType("butler") is ButlerType.BUTLER
    assert ButlerType("staffer") is ButlerType.STAFFER


# ---------------------------------------------------------------------------
# ButlerConfig.type — default
# ---------------------------------------------------------------------------


def test_type_defaults_to_butler_when_absent(tmp_path: Path):
    """Config with no type field defaults to ButlerType.BUTLER."""
    toml = """\
[butler]
name = "alfred"
port = 9000
"""
    cfg = load_config(_write_toml(tmp_path, toml))

    assert cfg.type is ButlerType.BUTLER


def test_type_defaults_to_butler_in_dataclass():
    """ButlerConfig dataclass default for type is ButlerType.BUTLER."""
    cfg = ButlerConfig(name="test", port=1234)
    assert cfg.type is ButlerType.BUTLER


# ---------------------------------------------------------------------------
# ButlerConfig.type — explicit values
# ---------------------------------------------------------------------------


def test_type_butler_explicit(tmp_path: Path):
    """Explicit type = 'butler' parses to ButlerType.BUTLER."""
    toml = """\
[butler]
name = "general"
port = 9001
type = "butler"
"""
    cfg = load_config(_write_toml(tmp_path, toml))

    assert cfg.type is ButlerType.BUTLER


def test_type_staffer(tmp_path: Path):
    """type = 'staffer' parses to ButlerType.STAFFER."""
    toml = """\
[butler]
name = "infra-relay"
port = 9002
type = "staffer"
"""
    cfg = load_config(_write_toml(tmp_path, toml))

    assert cfg.type is ButlerType.STAFFER


def test_type_case_insensitive(tmp_path: Path):
    """type field is case-insensitive."""
    toml = """\
[butler]
name = "infra-relay"
port = 9003
type = "STAFFER"
"""
    cfg = load_config(_write_toml(tmp_path, toml))

    assert cfg.type is ButlerType.STAFFER


def test_type_invalid_value_raises(tmp_path: Path):
    """Unknown type value raises ConfigError with a useful message."""
    toml = """\
[butler]
name = "rogue"
port = 9004
type = "robot"
"""
    with pytest.raises(ConfigError, match="Invalid butler.type"):
        load_config(_write_toml(tmp_path, toml))


def test_type_non_string_raises(tmp_path: Path):
    """Non-string type value raises ConfigError."""
    toml = """\
[butler]
name = "rogue"
port = 9005
type = 42
"""
    with pytest.raises(ConfigError, match="butler.type must be a string"):
        load_config(_write_toml(tmp_path, toml))


# ---------------------------------------------------------------------------
# PermissionsConfig — defaults
# ---------------------------------------------------------------------------


def test_permissions_defaults_to_empty_list_when_absent(tmp_path: Path):
    """No [butler.permissions] section → cross_butler_access defaults to []."""
    toml = """\
[butler]
name = "general"
port = 9010
"""
    cfg = load_config(_write_toml(tmp_path, toml))

    assert isinstance(cfg.permissions, PermissionsConfig)
    assert cfg.permissions.cross_butler_access == []


def test_permissions_defaults_in_dataclass():
    """ButlerConfig dataclass default for permissions has empty cross_butler_access."""
    cfg = ButlerConfig(name="test", port=1234)
    assert cfg.permissions.cross_butler_access == []


# ---------------------------------------------------------------------------
# PermissionsConfig — explicit values
# ---------------------------------------------------------------------------


def test_permissions_wildcard_access(tmp_path: Path):
    """cross_butler_access = ['*'] parses correctly."""
    toml = """\
[butler]
name = "switchboard"
port = 41100
type = "staffer"

[butler.permissions]
cross_butler_access = ["*"]
"""
    cfg = load_config(_write_toml(tmp_path, toml))

    assert cfg.permissions.cross_butler_access == ["*"]


def test_permissions_scoped_access(tmp_path: Path):
    """cross_butler_access = ['general', 'health'] parses correctly."""
    toml = """\
[butler]
name = "notifier"
port = 41104
type = "staffer"

[butler.permissions]
cross_butler_access = ["general", "health"]
"""
    cfg = load_config(_write_toml(tmp_path, toml))

    assert cfg.permissions.cross_butler_access == ["general", "health"]


def test_permissions_empty_list_explicit(tmp_path: Path):
    """Explicitly empty cross_butler_access list parses correctly."""
    toml = """\
[butler]
name = "general"
port = 9011

[butler.permissions]
cross_butler_access = []
"""
    cfg = load_config(_write_toml(tmp_path, toml))

    assert cfg.permissions.cross_butler_access == []


def test_permissions_section_present_no_cross_butler_field(tmp_path: Path):
    """[butler.permissions] present but cross_butler_access absent → defaults to []."""
    toml = """\
[butler]
name = "general"
port = 9012

[butler.permissions]
"""
    cfg = load_config(_write_toml(tmp_path, toml))

    assert cfg.permissions.cross_butler_access == []


def test_permissions_cross_butler_non_list_raises(tmp_path: Path):
    """Non-list cross_butler_access raises ConfigError."""
    toml = """\
[butler]
name = "rogue"
port = 9013

[butler.permissions]
cross_butler_access = "*"
"""
    with pytest.raises(ConfigError, match="butler.permissions.cross_butler_access must be a list"):
        load_config(_write_toml(tmp_path, toml))


# ---------------------------------------------------------------------------
# Combined type + permissions in a realistic staffer config
# ---------------------------------------------------------------------------


def test_staffer_with_wildcard_permissions(tmp_path: Path):
    """Full staffer config with wildcard permissions parses correctly."""
    toml = """\
[butler]
name = "switchboard"
port = 41100
description = "Message router and entry point"
type = "staffer"

[butler.permissions]
cross_butler_access = ["*"]
"""
    cfg = load_config(_write_toml(tmp_path, toml))

    assert cfg.type is ButlerType.STAFFER
    assert cfg.permissions.cross_butler_access == ["*"]
    assert cfg.name == "switchboard"
    assert cfg.port == 41100


def test_butler_with_no_permissions_section(tmp_path: Path):
    """Standard butler config has type=BUTLER and empty permissions."""
    toml = """\
[butler]
name = "general"
port = 41101
description = "General purpose butler"
"""
    cfg = load_config(_write_toml(tmp_path, toml))

    assert cfg.type is ButlerType.BUTLER
    assert cfg.permissions.cross_butler_access == []
