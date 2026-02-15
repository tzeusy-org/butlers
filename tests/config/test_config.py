"""Tests for butler configuration loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from butlers.config import (
    DEFAULT_APPROVAL_RULE_PRECEDENCE,
    ApprovalConfig,
    ApprovalRiskTier,
    ButlerConfig,
    ConfigError,
    GatedToolConfig,
    LoggingConfig,
    RuntimeConfig,
    ScheduleConfig,
    load_config,
    parse_approval_config,
    validate_approval_config,
)

pytestmark = pytest.mark.unit
# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FULL_TOML = """\
[butler]
name = "jarvis"
port = 8100
description = "Personal assistant butler"

[butler.db]
name = "jarvis_db"

[butler.runtime]
model = "claude-sonnet-4-20250514"

[butler.env]
required = ["OPENAI_API_KEY", "PG_DSN"]
optional = ["SLACK_TOKEN"]

[[butler.schedule]]
name = "daily-digest"
cron = "0 8 * * *"
prompt = "Summarise overnight emails"

[[butler.schedule]]
name = "weekly-report"
cron = "0 9 * * 1"
prompt = "Generate weekly status report"

[modules.email]
max_threads = 50

[modules.telegram]
mode = "polling"

[modules.telegram.user]
enabled = false

[modules.telegram.bot]
token_env = "TG_TOKEN"

[modules.email.user]
enabled = false

[modules.email.bot]
address_env = "BOT_EMAIL_ADDRESS"
password_env = "BOT_EMAIL_PASSWORD"
"""

MINIMAL_TOML = """\
[butler]
name = "alfred"
port = 9000
"""


def _write_toml(tmp_path: Path, content: str, filename: str = "butler.toml") -> Path:
    """Write *content* to a TOML file inside *tmp_path* and return the directory."""
    (tmp_path / filename).write_text(content)
    return tmp_path


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


def test_load_full_config(tmp_path: Path):
    """All sections present — every field is parsed correctly."""
    config_dir = _write_toml(tmp_path, FULL_TOML)
    cfg = load_config(config_dir)

    assert isinstance(cfg, ButlerConfig)
    assert cfg.name == "jarvis"
    assert cfg.port == 8100
    assert cfg.description == "Personal assistant butler"
    assert cfg.db_name == "jarvis_db"

    # Runtime
    assert cfg.runtime.model == "claude-sonnet-4-20250514"

    # Schedules
    assert len(cfg.schedules) == 2
    assert cfg.schedules[0] == ScheduleConfig(
        name="daily-digest", cron="0 8 * * *", prompt="Summarise overnight emails"
    )
    assert cfg.schedules[1] == ScheduleConfig(
        name="weekly-report", cron="0 9 * * 1", prompt="Generate weekly status report"
    )

    # Modules
    assert "email" in cfg.modules
    assert cfg.modules["email"] == {
        "max_threads": 50,
        "user": {"enabled": False},
        "bot": {
            "address_env": "BOT_EMAIL_ADDRESS",
            "password_env": "BOT_EMAIL_PASSWORD",
        },
    }
    assert "telegram" in cfg.modules
    assert cfg.modules["telegram"] == {
        "mode": "polling",
        "user": {"enabled": False},
        "bot": {"token_env": "TG_TOKEN"},
    }

    # Env
    assert cfg.env_required == ["OPENAI_API_KEY", "PG_DSN"]
    assert cfg.env_optional == ["SLACK_TOKEN"]


def test_load_minimal_config(tmp_path: Path):
    """Only [butler] with name and port — defaults applied everywhere else."""
    config_dir = _write_toml(tmp_path, MINIMAL_TOML)
    cfg = load_config(config_dir)

    assert cfg.name == "alfred"
    assert cfg.port == 9000
    assert cfg.description is None
    assert cfg.runtime.model == "claude-haiku-4-5-20251001"
    assert cfg.schedules == []
    assert cfg.modules == {}
    assert cfg.env_required == []
    assert cfg.env_optional == []


def test_default_db_name(tmp_path: Path):
    """db_name defaults to butler_{name} when [butler.db] is omitted."""
    config_dir = _write_toml(tmp_path, MINIMAL_TOML)
    cfg = load_config(config_dir)

    assert cfg.db_name == "butler_alfred"


def test_env_section(tmp_path: Path):
    """Parses [butler.env] required and optional lists."""
    toml = """\
[butler]
name = "envbot"
port = 7000

[butler.env]
required = ["API_KEY"]
optional = ["DEBUG", "VERBOSE"]
"""
    config_dir = _write_toml(tmp_path, toml)
    cfg = load_config(config_dir)

    assert cfg.env_required == ["API_KEY"]
    assert cfg.env_optional == ["DEBUG", "VERBOSE"]


def test_schedule_parsing(tmp_path: Path):
    """Parses [[butler.schedule]] entries into ScheduleConfig objects."""
    toml = """\
[butler]
name = "cronbot"
port = 7001

[[butler.schedule]]
name = "tick"
cron = "*/10 * * * *"
prompt = "Do a tick"
"""
    config_dir = _write_toml(tmp_path, toml)
    cfg = load_config(config_dir)

    assert len(cfg.schedules) == 1
    sched = cfg.schedules[0]
    assert sched.name == "tick"
    assert sched.cron == "*/10 * * * *"
    assert sched.prompt == "Do a tick"


def test_modules_parsing(tmp_path: Path):
    """Parses [modules.*] sections into a dict of dicts."""
    toml = """\
[butler]
name = "modbot"
port = 7002

[modules.calendar]
provider = "google"

[modules.weather]
api_key_env = "WEATHER_KEY"
units = "metric"
"""
    config_dir = _write_toml(tmp_path, toml)
    cfg = load_config(config_dir)

    assert set(cfg.modules.keys()) == {"calendar", "weather"}
    assert cfg.modules["calendar"] == {"provider": "google"}
    assert cfg.modules["weather"] == {"api_key_env": "WEATHER_KEY", "units": "metric"}


# ---------------------------------------------------------------------------
# Runtime config tests
# ---------------------------------------------------------------------------


class TestRuntimeConfig:
    """Tests for [butler.runtime] section parsing."""

    def test_model_present(self, tmp_path: Path):
        """Model string is parsed from [butler.runtime] section."""
        toml = """\
[butler]
name = "modelbot"
port = 7010

[butler.runtime]
model = "claude-opus-4-20250514"
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        assert cfg.runtime.model == "claude-opus-4-20250514"

    def test_model_absent(self, tmp_path: Path):
        """Omitting [butler.runtime] entirely defaults model to Haiku."""
        toml = """\
[butler]
name = "nomodel"
port = 7011
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        assert cfg.runtime.model == "claude-haiku-4-5-20251001"

    def test_model_empty_string(self, tmp_path: Path):
        """Empty string model is normalised to Haiku default."""
        toml = """\
[butler]
name = "emptymodel"
port = 7012

[butler.runtime]
model = ""
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        assert cfg.runtime.model == "claude-haiku-4-5-20251001"

    def test_model_whitespace_only(self, tmp_path: Path):
        """Whitespace-only model is normalised to Haiku default."""
        toml = """\
[butler]
name = "wsmodel"
port = 7013

[butler.runtime]
model = "   "
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        assert cfg.runtime.model == "claude-haiku-4-5-20251001"

    def test_runtime_section_without_model(self, tmp_path: Path):
        """[butler.runtime] present but without model field defaults to Haiku."""
        toml = """\
[butler]
name = "nofield"
port = 7014

[butler.runtime]
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        assert cfg.runtime.model == "claude-haiku-4-5-20251001"

    def test_model_opaque_string(self, tmp_path: Path):
        """Model string is opaque — any non-empty value is accepted."""
        toml = """\
[butler]
name = "opaque"
port = 7015

[butler.runtime]
model = "gpt-4o-2025-01-01"
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        assert cfg.runtime.model == "gpt-4o-2025-01-01"

    def test_runtime_config_dataclass_defaults(self):
        """RuntimeConfig defaults to Haiku model."""
        rc = RuntimeConfig()
        assert rc.model == "claude-haiku-4-5-20251001"

    def test_runtime_config_with_model(self):
        """RuntimeConfig can be constructed with a model string."""
        rc = RuntimeConfig(model="claude-opus-4-20250514")
        assert rc.model == "claude-opus-4-20250514"

    def test_backward_compat_no_runtime_section(self, tmp_path: Path):
        """Existing configs without [butler.runtime] still load correctly."""
        toml = """\
[butler]
name = "legacy"
port = 7016
description = "A legacy butler"

[butler.db]
name = "legacy_db"

[butler.env]
required = ["API_KEY"]
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        assert cfg.name == "legacy"
        assert cfg.port == 7016
        assert cfg.runtime.model == "claude-haiku-4-5-20251001"
        assert cfg.db_name == "legacy_db"
        assert cfg.env_required == ["API_KEY"]


# ---------------------------------------------------------------------------
# Validation / error tests
# ---------------------------------------------------------------------------


def test_missing_config_file(tmp_path: Path):
    """Raises ConfigError when butler.toml does not exist."""
    with pytest.raises(ConfigError, match="Config file not found"):
        load_config(tmp_path)


def test_invalid_toml(tmp_path: Path):
    """Raises ConfigError on malformed TOML with location info."""
    _write_toml(tmp_path, "[butler\nname = oops")
    with pytest.raises(ConfigError, match="Invalid TOML"):
        load_config(tmp_path)


def test_missing_name(tmp_path: Path):
    """Raises ConfigError when butler.name is absent."""
    _write_toml(tmp_path, "[butler]\nport = 8000\n")
    with pytest.raises(ConfigError, match="butler.name"):
        load_config(tmp_path)


def test_missing_port(tmp_path: Path):
    """Raises ConfigError when butler.port is absent."""
    _write_toml(tmp_path, '[butler]\nname = "noport"\n')
    with pytest.raises(ConfigError, match="butler.port"):
        load_config(tmp_path)


# ---------------------------------------------------------------------------
# Runtime config tests
# ---------------------------------------------------------------------------


def test_runtime_default_to_claude_code(tmp_path: Path):
    """When [runtime] section is missing, default to claude-code."""
    toml = """\
[butler]
name = "runtimebot"
port = 7003
"""
    config_dir = _write_toml(tmp_path, toml)
    cfg = load_config(config_dir)

    assert cfg.runtime.type == "claude-code"


def test_runtime_explicit_claude_code(tmp_path: Path):
    """Parse [runtime] section with explicit type = 'claude-code'."""
    toml = """\
[butler]
name = "ccbot"
port = 7004

[runtime]
type = "claude-code"
"""
    config_dir = _write_toml(tmp_path, toml)
    cfg = load_config(config_dir)

    assert cfg.runtime.type == "claude-code"


def test_runtime_codex(tmp_path: Path):
    """Parse [runtime] section with type = 'codex'."""
    toml = """\
[butler]
name = "codexbot"
port = 7005

[runtime]
type = "codex"
"""
    config_dir = _write_toml(tmp_path, toml)
    cfg = load_config(config_dir)

    assert cfg.runtime.type == "codex"


def test_runtime_gemini(tmp_path: Path):
    """Parse [runtime] section with type = 'gemini'."""
    toml = """\
[butler]
name = "geminibot"
port = 7006

[runtime]
type = "gemini"
"""
    config_dir = _write_toml(tmp_path, toml)
    cfg = load_config(config_dir)

    assert cfg.runtime.type == "gemini"


def test_runtime_invalid_type_raises_error(tmp_path: Path):
    """Invalid runtime type raises clear ConfigError at load time."""
    toml = """\
[butler]
name = "invalidbot"
port = 7007

[runtime]
type = "invalid-runtime"
"""
    config_dir = _write_toml(tmp_path, toml)

    with pytest.raises(ConfigError, match="Unknown runtime type 'invalid-runtime'"):
        load_config(config_dir)


def test_runtime_config_accessible_from_butler_config(tmp_path: Path):
    """Verify runtime config is accessible via config.runtime.type."""
    toml = """\
[butler]
name = "accessbot"
port = 7008

[runtime]
type = "gemini"
"""
    config_dir = _write_toml(tmp_path, toml)
    cfg = load_config(config_dir)

    # Can access runtime.type directly
    assert cfg.runtime.type == "gemini"

    # Runtime config is a RuntimeConfig instance
    from butlers.config import RuntimeConfig

    assert isinstance(cfg.runtime, RuntimeConfig)


# ---------------------------------------------------------------------------
# Approval config tests
# ---------------------------------------------------------------------------


class TestApprovalConfig:
    """Tests for [modules.approvals] section parsing."""

    def test_approvals_minimal(self, tmp_path: Path):
        """Minimal approvals config with just enabled flag."""
        toml = """\
[butler]
name = "approvalsbot"
port = 8200

[modules.approvals]
enabled = true
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        assert "approvals" in cfg.modules
        approvals = cfg.modules["approvals"]
        assert approvals["enabled"] is True
        assert approvals.get("default_expiry_hours", 48) == 48
        assert approvals.get("gated_tools", {}) == {}

    def test_approvals_full_config(self, tmp_path: Path):
        """Full approvals config with gated tools and custom expiry."""
        toml = """\
[butler]
name = "approvalsbot"
port = 8200

[modules.approvals]
enabled = true
default_expiry_hours = 72

[modules.approvals.gated_tools]
email_send = {}
purchase_create = {expiry_hours = 24}
calendar_invite = {}
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        assert "approvals" in cfg.modules
        approvals = cfg.modules["approvals"]
        assert approvals["enabled"] is True
        assert approvals["default_expiry_hours"] == 72
        assert "gated_tools" in approvals
        gated_tools = approvals["gated_tools"]
        assert "email_send" in gated_tools
        assert gated_tools["email_send"] == {}
        assert "purchase_create" in gated_tools
        assert gated_tools["purchase_create"] == {"expiry_hours": 24}
        assert "calendar_invite" in gated_tools
        assert gated_tools["calendar_invite"] == {}

    def test_approvals_disabled(self, tmp_path: Path):
        """Approvals module can be disabled."""
        toml = """\
[butler]
name = "approvalsbot"
port = 8200

[modules.approvals]
enabled = false
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        assert "approvals" in cfg.modules
        assert cfg.modules["approvals"]["enabled"] is False

    def test_approvals_default_expiry(self, tmp_path: Path):
        """Default expiry hours defaults to 48."""
        toml = """\
[butler]
name = "approvalsbot"
port = 8200

[modules.approvals]
enabled = true
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        approvals = cfg.modules["approvals"]
        # Default should be absent (will be handled by ApprovalConfig dataclass)
        assert (
            approvals.get("default_expiry_hours") is None
            or approvals.get("default_expiry_hours") == 48
        )

    def test_approvals_no_gated_tools(self, tmp_path: Path):
        """Approvals config without gated_tools section."""
        toml = """\
[butler]
name = "approvalsbot"
port = 8200

[modules.approvals]
enabled = true
default_expiry_hours = 48
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        approvals = cfg.modules["approvals"]
        assert approvals.get("gated_tools", {}) == {}

    def test_approvals_gated_tool_with_custom_expiry(self, tmp_path: Path):
        """Gated tool can override default expiry."""
        toml = """\
[butler]
name = "approvalsbot"
port = 8200

[modules.approvals]
enabled = true
default_expiry_hours = 48

[modules.approvals.gated_tools]
high_risk_action = {expiry_hours = 1}
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        gated_tools = cfg.modules["approvals"]["gated_tools"]
        assert "high_risk_action" in gated_tools
        assert gated_tools["high_risk_action"]["expiry_hours"] == 1

    def test_approvals_multiple_gated_tools(self, tmp_path: Path):
        """Multiple gated tools with mixed configurations."""
        toml = """\
[butler]
name = "approvalsbot"
port = 8200

[modules.approvals]
enabled = true
default_expiry_hours = 48

[modules.approvals.gated_tools]
email_send = {}
purchase_create = {expiry_hours = 24}
database_delete = {expiry_hours = 6}
calendar_invite = {}
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        gated_tools = cfg.modules["approvals"]["gated_tools"]
        assert len(gated_tools) == 4
        assert gated_tools["email_send"] == {}
        assert gated_tools["purchase_create"]["expiry_hours"] == 24
        assert gated_tools["database_delete"]["expiry_hours"] == 6
        assert gated_tools["calendar_invite"] == {}

    def test_approvals_absent_from_config(self, tmp_path: Path):
        """Butler config without approvals module."""
        toml = """\
[butler]
name = "noapprovalsbot"
port = 8200
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        assert "approvals" not in cfg.modules


# ---------------------------------------------------------------------------
# ApprovalConfig dataclass tests
# ---------------------------------------------------------------------------


class TestApprovalConfigDataclass:
    """Tests for ApprovalConfig and GatedToolConfig dataclasses."""

    def test_gated_tool_config_defaults(self):
        """GatedToolConfig with no override uses None."""
        gtc = GatedToolConfig()
        assert gtc.expiry_hours is None
        assert gtc.risk_tier is None

    def test_gated_tool_config_with_override(self):
        """GatedToolConfig with expiry override."""
        gtc = GatedToolConfig(expiry_hours=12)
        assert gtc.expiry_hours == 12

    def test_approval_config_defaults(self):
        """ApprovalConfig defaults."""
        ac = ApprovalConfig(enabled=True)
        assert ac.enabled is True
        assert ac.default_expiry_hours == 48
        assert ac.default_risk_tier == ApprovalRiskTier.MEDIUM
        assert ac.rule_precedence == DEFAULT_APPROVAL_RULE_PRECEDENCE
        assert ac.gated_tools == {}

    def test_approval_config_custom_default_expiry(self):
        """ApprovalConfig with custom default expiry."""
        ac = ApprovalConfig(enabled=True, default_expiry_hours=72)
        assert ac.default_expiry_hours == 72

    def test_approval_config_with_gated_tools(self):
        """ApprovalConfig with gated tools."""
        gated_tools = {
            "email_send": GatedToolConfig(),
            "purchase_create": GatedToolConfig(expiry_hours=24),
        }
        ac = ApprovalConfig(enabled=True, gated_tools=gated_tools)
        assert len(ac.gated_tools) == 2
        assert ac.gated_tools["email_send"].expiry_hours is None
        assert ac.gated_tools["purchase_create"].expiry_hours == 24

    def test_parse_approval_config_minimal(self):
        """Parse minimal approval config dict."""
        raw = {"enabled": True}
        ac = parse_approval_config(raw)
        assert ac.enabled is True
        assert ac.default_expiry_hours == 48
        assert ac.default_risk_tier == ApprovalRiskTier.MEDIUM
        assert ac.gated_tools == {}

    def test_parse_approval_config_full(self):
        """Parse full approval config dict."""
        raw = {
            "enabled": True,
            "default_expiry_hours": 72,
            "default_risk_tier": "low",
            "gated_tools": {
                "email_send": {"risk_tier": "high"},
                "purchase_create": {"expiry_hours": 24, "risk_tier": "critical"},
            },
        }
        ac = parse_approval_config(raw)
        assert ac.enabled is True
        assert ac.default_expiry_hours == 72
        assert ac.default_risk_tier == ApprovalRiskTier.LOW
        assert len(ac.gated_tools) == 2
        assert ac.gated_tools["email_send"].expiry_hours is None
        assert ac.gated_tools["email_send"].risk_tier == ApprovalRiskTier.HIGH
        assert ac.gated_tools["purchase_create"].expiry_hours == 24
        assert ac.gated_tools["purchase_create"].risk_tier == ApprovalRiskTier.CRITICAL

    def test_parse_approval_config_disabled(self):
        """Parse disabled approval config."""
        raw = {"enabled": False}
        ac = parse_approval_config(raw)
        assert ac.enabled is False

    def test_parse_approval_config_invalid_default_risk_tier(self):
        raw = {"enabled": True, "default_risk_tier": "veryhigh"}
        with pytest.raises(ConfigError, match="default_risk_tier"):
            parse_approval_config(raw)

    def test_parse_approval_config_invalid_tool_risk_tier(self):
        raw = {"enabled": True, "gated_tools": {"email_send": {"risk_tier": "veryhigh"}}}
        with pytest.raises(ConfigError, match="risk_tier"):
            parse_approval_config(raw)

    def test_parse_approval_config_none_returns_none(self):
        """parse_approval_config with None returns None."""
        assert parse_approval_config(None) is None

    def test_approval_config_get_effective_expiry_default(self):
        """Get effective expiry for a tool without override."""
        ac = ApprovalConfig(
            enabled=True,
            default_expiry_hours=48,
            gated_tools={"email_send": GatedToolConfig()},
        )
        assert ac.get_effective_expiry("email_send") == 48

    def test_approval_config_get_effective_expiry_override(self):
        """Get effective expiry for a tool with override."""
        ac = ApprovalConfig(
            enabled=True,
            default_expiry_hours=48,
            gated_tools={"purchase_create": GatedToolConfig(expiry_hours=24)},
        )
        assert ac.get_effective_expiry("purchase_create") == 24

    def test_approval_config_get_effective_expiry_unknown_tool(self):
        """Get effective expiry for an unknown tool returns default."""
        ac = ApprovalConfig(enabled=True, default_expiry_hours=48)
        assert ac.get_effective_expiry("unknown_tool") == 48

    def test_approval_config_get_effective_risk_tier_default(self):
        ac = ApprovalConfig(enabled=True, default_risk_tier=ApprovalRiskTier.LOW)
        assert ac.get_effective_risk_tier("unknown_tool") == ApprovalRiskTier.LOW

    def test_approval_config_get_effective_risk_tier_tool_override(self):
        ac = ApprovalConfig(
            enabled=True,
            default_risk_tier=ApprovalRiskTier.MEDIUM,
            gated_tools={"purchase_create": GatedToolConfig(risk_tier=ApprovalRiskTier.CRITICAL)},
        )
        assert ac.get_effective_risk_tier("purchase_create") == ApprovalRiskTier.CRITICAL


# ---------------------------------------------------------------------------
# Approval config validation tests
# ---------------------------------------------------------------------------


class TestApprovalConfigValidation:
    """Tests for validating approval config against registered tools."""

    def test_validate_approval_config_all_tools_registered(self):
        """Validation passes when all gated tools are registered."""
        ac = ApprovalConfig(
            enabled=True,
            gated_tools={
                "email_send": GatedToolConfig(),
                "purchase_create": GatedToolConfig(expiry_hours=24),
            },
        )
        registered_tools = {"email_send", "purchase_create", "calendar_invite"}
        # Should not raise
        validate_approval_config(ac, registered_tools)

    def test_validate_approval_config_unregistered_tool(self):
        """Validation fails when a gated tool is not registered."""
        ac = ApprovalConfig(
            enabled=True,
            gated_tools={
                "email_send": GatedToolConfig(),
                "unknown_tool": GatedToolConfig(),
            },
        )
        registered_tools = {"email_send", "purchase_create"}

        with pytest.raises(ConfigError, match="Unknown gated tool.*unknown_tool.*not registered"):
            validate_approval_config(ac, registered_tools)

    def test_validate_approval_config_multiple_unregistered_tools(self):
        """Validation reports all unregistered tools."""
        ac = ApprovalConfig(
            enabled=True,
            gated_tools={
                "email_send": GatedToolConfig(),
                "unknown_tool_1": GatedToolConfig(),
                "unknown_tool_2": GatedToolConfig(),
            },
        )
        registered_tools = {"email_send"}

        with pytest.raises(ConfigError) as exc_info:
            validate_approval_config(ac, registered_tools)

        error_msg = str(exc_info.value)
        assert "unknown_tool_1" in error_msg
        assert "unknown_tool_2" in error_msg

    def test_validate_approval_config_disabled_skips_validation(self):
        """Validation is skipped when approvals are disabled."""
        ac = ApprovalConfig(
            enabled=False,
            gated_tools={"unknown_tool": GatedToolConfig()},
        )
        registered_tools = {"email_send"}
        # Should not raise even though unknown_tool is not registered
        validate_approval_config(ac, registered_tools)

    def test_validate_approval_config_none_is_noop(self):
        """Validation with None config is a no-op."""
        validate_approval_config(None, {"email_send"})

    def test_validate_approval_config_empty_gated_tools(self):
        """Validation passes with no gated tools."""
        ac = ApprovalConfig(enabled=True, gated_tools={})
        registered_tools = {"email_send", "purchase_create"}
        # Should not raise
        validate_approval_config(ac, registered_tools)


# ---------------------------------------------------------------------------
# Switchboard URL config tests
# ---------------------------------------------------------------------------


class TestSwitchboardUrlConfig:
    """Tests for [butler.switchboard] section parsing."""

    def test_default_switchboard_url_for_non_switchboard(self, tmp_path: Path):
        """Non-switchboard butlers default to http://localhost:8100/sse."""
        toml = """\
[butler]
name = "general"
port = 8101
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        assert cfg.switchboard_url == "http://localhost:8100/sse"


class TestMessengerConfigValidation:
    """Tests for messenger-specific config guardrails."""

    def test_messenger_requires_at_least_one_delivery_module(self, tmp_path: Path):
        """Messenger without telegram/email modules should fail config validation."""
        toml = """\
[butler]
name = "messenger"
port = 8104
"""
        config_dir = _write_toml(tmp_path, toml)

        with pytest.raises(ConfigError, match="requires at least one delivery module"):
            load_config(config_dir)

    def test_messenger_accepts_telegram_only(self, tmp_path: Path):
        """Messenger with only telegram module should load."""
        toml = """\
[butler]
name = "messenger"
port = 8104

[modules.telegram]
mode = "polling"
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        assert cfg.name == "messenger"
        assert "telegram" in cfg.modules

    def test_messenger_accepts_email_only(self, tmp_path: Path):
        """Messenger with only email module should load."""
        toml = """\
[butler]
name = "messenger"
port = 8104

[modules.email]
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        assert cfg.name == "messenger"
        assert "email" in cfg.modules

    def test_messenger_requires_enabled_bot_scope(self, tmp_path: Path):
        """Messenger with both bot scopes disabled should fail config validation."""
        toml = """\
[butler]
name = "messenger"
port = 8104

[modules.telegram]

[modules.telegram.bot]
enabled = false

[modules.email]

[modules.email.bot]
enabled = false
"""
        config_dir = _write_toml(tmp_path, toml)

        with pytest.raises(ConfigError, match="requires at least one enabled bot credential scope"):
            load_config(config_dir)

    def test_non_messenger_is_not_subject_to_messenger_delivery_requirements(self, tmp_path: Path):
        """Other butlers can load without delivery modules."""
        toml = """\
[butler]
name = "general"
port = 8101
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        assert cfg.name == "general"

    def test_switchboard_butler_has_no_url(self, tmp_path: Path):
        """The switchboard butler itself should have switchboard_url=None."""
        toml = """\
[butler]
name = "switchboard"
port = 8100
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        assert cfg.switchboard_url is None

    def test_explicit_switchboard_url(self, tmp_path: Path):
        """An explicit [butler.switchboard] url overrides the default."""
        toml = """\
[butler]
name = "health"
port = 8103

[butler.switchboard]
url = "http://switchboard.internal:9000/sse"
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        assert cfg.switchboard_url == "http://switchboard.internal:9000/sse"

    def test_explicit_switchboard_url_overrides_for_switchboard_name(self, tmp_path: Path):
        """Even the switchboard can have an explicit URL (edge case)."""
        toml = """\
[butler]
name = "switchboard"
port = 8100

[butler.switchboard]
url = "http://other-switchboard:8100/sse"
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        assert cfg.switchboard_url == "http://other-switchboard:8100/sse"

    def test_switchboard_url_env_var_resolution(self, tmp_path: Path, monkeypatch):
        """switchboard_url supports ${ENV_VAR} resolution."""
        monkeypatch.setenv("SWITCHBOARD_HOST", "sb.prod.internal")
        toml = """\
[butler]
name = "health"
port = 8103

[butler.switchboard]
url = "http://${SWITCHBOARD_HOST}:8100/sse"
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        assert cfg.switchboard_url == "http://sb.prod.internal:8100/sse"

    def test_switchboard_url_in_butler_config_dataclass(self):
        """ButlerConfig dataclass defaults switchboard_url to None."""
        cfg = ButlerConfig(name="test", port=9000)
        assert cfg.switchboard_url is None

    def test_switchboard_section_without_url(self, tmp_path: Path):
        """[butler.switchboard] section present but without url falls back to default."""
        toml = """\
[butler]
name = "health"
port = 8103

[butler.switchboard]
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)

        assert cfg.switchboard_url == "http://localhost:8100/sse"


# ---------------------------------------------------------------------------
# Logging config tests
# ---------------------------------------------------------------------------


class TestLoggingConfig:
    """Tests for [butler.logging] section parsing."""

    def test_logging_defaults(self):
        """LoggingConfig dataclass defaults."""
        lc = LoggingConfig()
        assert lc.level == "INFO"
        assert lc.format == "text"
        assert lc.log_root is None

    def test_logging_defaults_from_minimal_toml(self, tmp_path: Path):
        """Minimal config gets default LoggingConfig."""
        config_dir = _write_toml(tmp_path, MINIMAL_TOML)
        cfg = load_config(config_dir)
        assert cfg.logging.level == "INFO"
        assert cfg.logging.format == "text"
        assert cfg.logging.log_root is None

    def test_logging_section_parsed(self, tmp_path: Path):
        """[butler.logging] section is parsed correctly."""
        toml = """\
[butler]
name = "logbot"
port = 7100

[butler.logging]
level = "DEBUG"
format = "json"
log_root = "/var/log/butlers"
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)
        assert cfg.logging.level == "DEBUG"
        assert cfg.logging.format == "json"
        assert cfg.logging.log_root == "/var/log/butlers"

    def test_logging_level_case_insensitive(self, tmp_path: Path):
        """Level is uppercased during parsing."""
        toml = """\
[butler]
name = "logbot"
port = 7100

[butler.logging]
level = "debug"
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)
        assert cfg.logging.level == "DEBUG"

    def test_logging_format_case_insensitive(self, tmp_path: Path):
        """Format is lowercased during parsing."""
        toml = """\
[butler]
name = "logbot"
port = 7100

[butler.logging]
format = "JSON"
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)
        assert cfg.logging.format == "json"

    def test_logging_invalid_format_raises(self, tmp_path: Path):
        """Invalid format value raises ConfigError."""
        toml = """\
[butler]
name = "logbot"
port = 7100

[butler.logging]
format = "yaml"
"""
        config_dir = _write_toml(tmp_path, toml)
        with pytest.raises(ConfigError, match="Invalid butler.logging.format"):
            load_config(config_dir)

    def test_logging_env_var_in_log_root(self, tmp_path: Path, monkeypatch):
        """log_root supports ${ENV_VAR} resolution."""
        monkeypatch.setenv("LOG_DIR", "/tmp/butler-logs")
        toml = """\
[butler]
name = "logbot"
port = 7100

[butler.logging]
log_root = "${LOG_DIR}"
"""
        config_dir = _write_toml(tmp_path, toml)
        cfg = load_config(config_dir)
        assert cfg.logging.log_root == "/tmp/butler-logs"

    def test_butler_config_includes_logging(self):
        """ButlerConfig includes logging field with defaults."""
        cfg = ButlerConfig(name="test", port=9000)
        assert isinstance(cfg.logging, LoggingConfig)
        assert cfg.logging.level == "INFO"
