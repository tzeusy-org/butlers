"""Tests for butler configuration loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from butlers.config import (
    ApprovalConfig,
    ApprovalRiskTier,
    BufferConfig,
    ButlerType,
    ConfigError,
    GatedToolConfig,
    LoggingConfig,
    RuntimeConfig,
    ScheduleConfig,
    ScheduleDispatchMode,
    load_config,
    parse_approval_config,
    validate_approval_config,
)

pytestmark = pytest.mark.unit

FULL_TOML = """\
[butler]
name = "jarvis"
port = 41100
description = "Personal assistant butler"

[butler.db]
name = "jarvis_db"

[butler.runtime_seed]
model = "claude-sonnet-4-20250514"

[butler.env]
required = ["SMTP_PASSWORD", "PG_DSN"]
optional = ["SLACK_TOKEN"]

[[butler.schedule]]
name = "daily_digest"
cron = "0 8 * * *"
prompt = "Summarise overnight emails"

[[butler.schedule]]
name = "weekly_report"
cron = "0 9 * * 1"
prompt = "Generate weekly status report"

[modules.email]
max_threads = 50

[modules.telegram]
mode = "polling"
"""

MINIMAL_TOML = """\
[butler]
name = "alfred"
port = 9000
"""


def _write_toml(tmp_path: Path, content: str) -> Path:
    (tmp_path / "butler.toml").write_text(content)
    return tmp_path


# ---------------------------------------------------------------------------
# Happy-path loading + DB schema
# ---------------------------------------------------------------------------


def test_load_config_full_and_minimal(tmp_path: Path):
    """Full config parses all sections; minimal config applies defaults."""
    cfg = load_config(_write_toml(tmp_path, FULL_TOML))
    assert cfg.name == "jarvis" and cfg.port == 41100 and cfg.db_name == "jarvis_db"
    assert cfg.runtime_seed.model == "claude-sonnet-4-20250514"
    assert cfg.runtime.model == "claude-sonnet-4-20250514"
    assert len(cfg.schedules) == 2
    assert cfg.schedules[0] == ScheduleConfig(
        name="daily_digest", cron="0 8 * * *", prompt="Summarise overnight emails"
    )
    assert "email" in cfg.modules
    assert cfg.env_required == ["SMTP_PASSWORD", "PG_DSN"]

    cfg2 = load_config(_write_toml(tmp_path, MINIMAL_TOML))
    assert cfg2.name == "alfred" and cfg2.port == 9000
    assert cfg2.runtime.model == "claude-haiku-4-5-20251001"
    assert cfg2.runtime_seed.model is None  # no model set in seed -> None
    assert cfg2.schedules == [] and cfg2.modules == {}
    assert cfg2.db_name == "butlers" and cfg2.db_schema == "alfred"


def test_db_schema_defaults_and_rejects_invalid(tmp_path: Path):
    db_toml = '[butler]\nname = "general"\nport = 9002\n[butler.db]\nname = "butlers"\n'
    cfg = load_config(_write_toml(tmp_path, db_toml))
    assert cfg.db_schema == "general"
    invalid_toml = (
        '[butler]\nname = "general"\nport = 9003\n'
        '[butler.db]\nname = "butlers"\nschema = "general; drop schema public"\n'
    )
    with pytest.raises(ConfigError, match="Invalid butler.db.schema"):
        load_config(_write_toml(tmp_path, invalid_toml))


# ---------------------------------------------------------------------------
# Validation / error tests
# ---------------------------------------------------------------------------


def test_config_errors(tmp_path: Path):
    """Missing file, invalid TOML, and missing required fields all raise ConfigError."""
    with pytest.raises(ConfigError, match="Config file not found"):
        load_config(tmp_path)
    for toml, match in [
        ("[butler\nname = oops", "Invalid TOML"),
        ("[butler]\nport = 8000\n", "butler.name"),
        ('[butler]\nname = "noport"\n', "butler.port"),
    ]:
        _write_toml(tmp_path, toml)
        with pytest.raises(ConfigError, match=match):
            load_config(tmp_path)


# ---------------------------------------------------------------------------
# Schedule parsing
# ---------------------------------------------------------------------------


def test_schedule_parsing(tmp_path: Path):
    """Prompt-mode and job-mode schedules parse correctly; invalid schedules raise."""
    base = (
        '[butler]\nname = "cronbot"\nport = 7001\n\n'
        '[[butler.schedule]]\nname = "t"\ncron = "*/5 * * * *"\n'
    )

    sched = load_config(_write_toml(tmp_path, base + 'prompt = "Do a tick"\n')).schedules[0]
    assert sched.dispatch_mode == ScheduleDispatchMode.PROMPT and sched.job_name is None

    sched2 = load_config(
        _write_toml(tmp_path, base + 'dispatch_mode = "job"\njob_name = "sweep"\n')
    ).schedules[0]
    assert sched2.dispatch_mode == ScheduleDispatchMode.JOB and sched2.job_name == "sweep"

    for extra, match in [
        (
            'dispatch_mode = "native"\nprompt = "run"\n',
            r"Invalid butler\.schedule\[0\]\.dispatch_mode",
        ),  # noqa: E501
        ('dispatch_mode = "job"\n', r"dispatch_mode='job' requires non-empty job_name"),
    ]:
        with pytest.raises(ConfigError, match=match):
            load_config(_write_toml(tmp_path, base + extra))


# ---------------------------------------------------------------------------
# RuntimeConfig + BufferConfig + LoggingConfig
# ---------------------------------------------------------------------------


def test_runtime_config(tmp_path: Path):
    """Model, args, concurrency/queue defaults and validation."""
    assert RuntimeConfig().max_concurrent_sessions == 1
    assert RuntimeConfig().max_queued_sessions == 100
    assert RuntimeConfig().model == "claude-haiku-4-5-20251001"

    runtime_toml = (
        '[butler]\nname = "m"\nport = 7010\n'
        '[butler.runtime_seed]\nmodel = "claude-opus-4-20250514"\nmax_concurrent_sessions = 4\n'
    )
    cfg = load_config(_write_toml(tmp_path, runtime_toml))
    assert cfg.runtime.model == "claude-opus-4-20250514"
    assert cfg.runtime.max_concurrent_sessions == 4
    assert cfg.runtime_seed.model == "claude-opus-4-20250514"
    assert cfg.runtime_seed.max_concurrent_sessions == 4

    with pytest.raises(ConfigError, match="max_queued_sessions"):
        mqs_toml = (
            '[butler]\nname = "m"\nport = 7011\n[butler.runtime_seed]\nmax_queued_sessions = 0\n'
        )
        load_config(_write_toml(tmp_path, mqs_toml))

    with pytest.raises(ConfigError, match="expected an array of strings"):
        args_toml = '[butler]\nname = "m"\nport = 7012\n[butler.runtime_seed]\nargs = "flat"\n'
        load_config(_write_toml(tmp_path, args_toml))

    repo_root = Path(__file__).resolve().parents[2]
    for butler in ("switchboard", "general", "relationship", "health", "messenger"):
        assert load_config(repo_root / "roster" / butler).runtime.max_concurrent_sessions >= 3


def test_old_runtime_section_rejected(tmp_path: Path):
    """Old [butler.runtime] section is rejected with clear error."""
    old_toml = '[butler]\nname = "m"\nport = 7013\n[butler.runtime]\nmodel = "x"\n'
    with pytest.raises(ConfigError, match=r"\[butler\.runtime\] has been renamed"):
        load_config(_write_toml(tmp_path, old_toml))


def test_old_seed_configs_section_rejected(tmp_path: Path):
    """Old [butler.seed_configs] section is rejected with clear error."""
    old_toml = '[butler]\nname = "m"\nport = 7014\n[butler.seed_configs]\nmodel = "x"\n'
    with pytest.raises(ConfigError, match=r"\[butler\.seed_configs\] has been merged"):
        load_config(_write_toml(tmp_path, old_toml))


def test_missing_runtime_seed_section_defaults(tmp_path: Path):
    """Missing [butler.runtime_seed] section returns defaults."""
    minimal_toml = '[butler]\nname = "m"\nport = 7015\n'
    cfg = load_config(_write_toml(tmp_path, minimal_toml))
    assert cfg.runtime_seed.model is None
    assert cfg.runtime_seed.runtime_type == "codex"
    assert cfg.runtime_seed.max_concurrent_sessions == 3
    assert cfg.runtime_seed.max_queued_sessions == 10
    assert cfg.runtime_seed.session_timeout_s == 900
    assert cfg.runtime_seed.core_groups is None
    assert cfg.runtime_seed.args == ()
    assert cfg.runtime_seed.liveness_ttl_seconds == 300
    assert cfg.runtime_seed.route_contract_min == 1
    assert cfg.runtime_seed.route_contract_max == 1


def test_buffer_and_logging_config(tmp_path: Path):
    """BufferConfig and LoggingConfig default and validate correctly."""
    assert BufferConfig().queue_capacity == 100 and BufferConfig().worker_count == 1
    assert LoggingConfig().level == "INFO" and LoggingConfig().format == "text"

    buf_log_toml = (
        '[butler]\nname = "b"\nport = 9101\n'
        "[buffer]\nqueue_capacity = 200\nworker_count = 4\n"
        '[butler.logging]\nlevel = "debug"\nformat = "JSON"\n'
    )
    cfg = load_config(_write_toml(tmp_path, buf_log_toml))
    assert cfg.buffer.queue_capacity == 200 and cfg.buffer.worker_count == 4
    assert cfg.logging.level == "DEBUG" and cfg.logging.format == "json"

    with pytest.raises(ConfigError, match="Invalid butler.logging.format"):
        log_toml = '[butler]\nname = "b"\nport = 9102\n[butler.logging]\nformat = "yaml"\n'
        load_config(_write_toml(tmp_path, log_toml))

    repo_root = Path(__file__).resolve().parents[2]
    cfg_sw = load_config(repo_root / "roster" / "switchboard")
    assert cfg_sw.buffer.worker_count == cfg_sw.runtime.max_concurrent_sessions


# ---------------------------------------------------------------------------
# ButlerType + PermissionsConfig
# ---------------------------------------------------------------------------


def test_butler_type_and_permissions(tmp_path: Path):
    """Type defaults, staffer wildcard, invalid type, permissions validation."""
    cfg = load_config(_write_toml(tmp_path, MINIMAL_TOML))
    assert cfg.type is ButlerType.BUTLER and cfg.permissions.cross_butler_access == []

    staffer_toml = (
        '[butler]\nname = "switchboard"\nport = 41100\ntype = "staffer"\n'
        '[butler.permissions]\ncross_butler_access = ["*"]\n'
    )
    cfg_staffer = load_config(_write_toml(tmp_path, staffer_toml))
    assert cfg_staffer.type is ButlerType.STAFFER

    for toml_frag, match in [
        ('type = "robot"', "Invalid butler.type"),
        ("type = 42", "butler.type must be a string"),
    ]:
        with pytest.raises(ConfigError, match=match):
            load_config(
                _write_toml(tmp_path, f'[butler]\nname = "rogue"\nport = 9004\n{toml_frag}\n')
            )


# ---------------------------------------------------------------------------
# ApprovalConfig
# ---------------------------------------------------------------------------


def test_approval_config(tmp_path: Path):
    """ApprovalConfig defaults, parsing, validation, effective getters."""
    ac = ApprovalConfig(enabled=True)
    assert ac.default_expiry_hours == 48 and ac.default_risk_tier == ApprovalRiskTier.MEDIUM

    raw = {
        "enabled": True,
        "default_expiry_hours": 72,
        "default_risk_tier": "low",
        "gated_tools": {
            "email_send": {"risk_tier": "high"},
            "purchase_create": {"expiry_hours": 24, "risk_tier": "critical"},
        },
    }
    ac2 = parse_approval_config(raw)
    assert (
        ac2.default_expiry_hours == 72
        and ac2.gated_tools["email_send"].risk_tier == ApprovalRiskTier.HIGH
    )

    ac3 = ApprovalConfig(
        enabled=True,
        default_expiry_hours=48,
        default_risk_tier=ApprovalRiskTier.LOW,
        gated_tools={
            "purchase_create": GatedToolConfig(expiry_hours=24, risk_tier=ApprovalRiskTier.CRITICAL)
        },
    )
    assert ac3.get_effective_expiry("purchase_create") == 24
    assert ac3.get_effective_expiry("unknown_tool") == 48
    assert ac3.get_effective_risk_tier("purchase_create") == ApprovalRiskTier.CRITICAL

    with pytest.raises(ConfigError, match="default_risk_tier"):
        parse_approval_config({"enabled": True, "default_risk_tier": "veryhigh"})

    validate_approval_config(ac, {"email_send"})  # disabled, no raise
    validate_approval_config(None, {"email_send"})  # None, no raise
    ac4 = ApprovalConfig(enabled=True, gated_tools={"unknown_tool": GatedToolConfig()})
    with pytest.raises(ConfigError, match="unknown_tool.*not registered"):
        validate_approval_config(ac4, {"email_send"})


# ---------------------------------------------------------------------------
# Messenger config + Switchboard URL + memory module
# ---------------------------------------------------------------------------


def test_messenger_and_switchboard_and_memory_config(tmp_path: Path):
    """Messenger requires delivery module; switchboard URL defaults; memory module parses."""
    with pytest.raises(ConfigError, match="requires at least one delivery module"):
        load_config(_write_toml(tmp_path, '[butler]\nname = "messenger"\nport = 41104\n'))

    cfg_sw = load_config(_write_toml(tmp_path, '[butler]\nname = "switchboard"\nport = 41100\n'))
    assert cfg_sw.switchboard_url is None

    cfg_gen = load_config(_write_toml(tmp_path, '[butler]\nname = "general"\nport = 41101\n'))
    assert cfg_gen.switchboard_url == "http://localhost:41100/mcp"

    # Memory module config
    mem_toml = (
        '[butler]\nname = "membot"\nport = 41200\n\n[modules.memory]\n\n'
        "[modules.memory.retrieval]\ncontext_token_budget = 5000\n"
        'default_mode = "semantic"\n'
    )
    cfg_mem = load_config(_write_toml(tmp_path, mem_toml))
    assert "memory" in cfg_mem.modules
    assert cfg_mem.modules["memory"]["retrieval"]["context_token_budget"] == 5000

    cfg_nomem = load_config(_write_toml(tmp_path, '[butler]\nname = "nomem"\nport = 40201\n'))
    assert "memory" not in cfg_nomem.modules
    legacy_toml = '[butler]\nname = "legacy"\nport = 8202\n\n[butler.memory]\nenabled = true\n'
    cfg_legacy = load_config(_write_toml(tmp_path, legacy_toml))
    assert "memory" not in cfg_legacy.modules


# ---------------------------------------------------------------------------
# resolve_env_vars
# ---------------------------------------------------------------------------


def test_resolve_env_vars(monkeypatch, tmp_path: Path):
    """resolve_env_vars: interpolates vars, handles types, raises on missing."""
    from butlers.config import resolve_env_vars

    monkeypatch.setenv("MY_SECRET", "hunter2")
    monkeypatch.setenv("DB_PASS", "s3cret")
    assert resolve_env_vars("${MY_SECRET}") == "hunter2"
    assert resolve_env_vars("plain string") == "plain string"
    assert resolve_env_vars("$NOT_A_REF") == "$NOT_A_REF"
    assert resolve_env_vars({}) == {}
    assert resolve_env_vars(42) == 42
    assert resolve_env_vars({"outer": {"inner": {"password": "${DB_PASS}"}}}) == {
        "outer": {"inner": {"password": "s3cret"}}
    }
    with pytest.raises(ConfigError, match="NONEXISTENT_VAR"):
        resolve_env_vars("${NONEXISTENT_VAR}")
    monkeypatch.setenv("SOURCE_EMAIL_PASSWORD", "p@ssw0rd")
    email_toml = (
        '[butler]\nname = "mailbot"\nport = 41200\n\n'
        '[modules.email]\npassword = "${SOURCE_EMAIL_PASSWORD}"\n'
    )
    cfg = load_config(_write_toml(tmp_path, email_toml))
    assert cfg.modules["email"]["password"] == "p@ssw0rd"
