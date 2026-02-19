"""Tests for credential validation with aggregated error reporting."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from butlers.credentials import (
    CredentialError,
    validate_credentials,
    validate_module_credentials,
    validate_module_credentials_async,
)

pytestmark = pytest.mark.unit
# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Env vars used across tests.  Every test that isn't specifically testing for
# the absence of ANTHROPIC_API_KEY must set it via monkeypatch.
_ANTHROPIC_KEY = "ANTHROPIC_API_KEY"


def _set_anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure ANTHROPIC_API_KEY is present so it doesn't cause spurious failures."""
    monkeypatch.setenv(_ANTHROPIC_KEY, "sk-test-key")


# ---------------------------------------------------------------------------
# Happy-path
# ---------------------------------------------------------------------------


def test_all_present(monkeypatch: pytest.MonkeyPatch):
    """No error when every required variable is set."""
    _set_anthropic_key(monkeypatch)
    monkeypatch.setenv("PG_DSN", "postgres://localhost/test")
    monkeypatch.setenv("EMAIL_PASS", "secret")

    # Should not raise
    validate_credentials(
        env_required=["PG_DSN"],
        env_optional=[],
        module_credentials={"email": ["EMAIL_PASS"]},
    )


# ---------------------------------------------------------------------------
# Missing core credential
# ---------------------------------------------------------------------------


def test_missing_anthropic_key(monkeypatch: pytest.MonkeyPatch):
    """Missing ANTHROPIC_API_KEY raises CredentialError."""
    monkeypatch.delenv(_ANTHROPIC_KEY, raising=False)

    with pytest.raises(CredentialError, match="ANTHROPIC_API_KEY"):
        validate_credentials(env_required=[], env_optional=[])


# ---------------------------------------------------------------------------
# Missing butler.env required
# ---------------------------------------------------------------------------


def test_missing_butler_env_required(monkeypatch: pytest.MonkeyPatch):
    """Missing butler.env required var raises CredentialError."""
    _set_anthropic_key(monkeypatch)
    monkeypatch.delenv("PG_DSN", raising=False)

    with pytest.raises(CredentialError, match="PG_DSN"):
        validate_credentials(env_required=["PG_DSN"], env_optional=[])


# ---------------------------------------------------------------------------
# Missing module credentials
# ---------------------------------------------------------------------------


def test_missing_module_credentials(monkeypatch: pytest.MonkeyPatch):
    """Missing module credential raises CredentialError."""
    _set_anthropic_key(monkeypatch)
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)

    with pytest.raises(CredentialError, match="TG_BOT_TOKEN"):
        validate_credentials(
            env_required=[],
            env_optional=[],
            module_credentials={"telegram": ["TG_BOT_TOKEN"]},
        )


# ---------------------------------------------------------------------------
# Multiple missing — aggregated report
# ---------------------------------------------------------------------------


def test_multiple_missing_aggregated(monkeypatch: pytest.MonkeyPatch):
    """All missing vars are reported in a single CredentialError."""
    monkeypatch.delenv(_ANTHROPIC_KEY, raising=False)
    monkeypatch.delenv("PG_DSN", raising=False)
    monkeypatch.delenv("EMAIL_PASS", raising=False)

    with pytest.raises(CredentialError) as exc_info:
        validate_credentials(
            env_required=["PG_DSN"],
            env_optional=[],
            module_credentials={"email": ["EMAIL_PASS"]},
        )

    msg = str(exc_info.value)
    assert "ANTHROPIC_API_KEY" in msg
    assert "PG_DSN" in msg
    assert "EMAIL_PASS" in msg


# ---------------------------------------------------------------------------
# Optional vars
# ---------------------------------------------------------------------------


def test_optional_missing_warns(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    """Missing optional var produces a warning but no error."""
    _set_anthropic_key(monkeypatch)
    monkeypatch.delenv("SLACK_TOKEN", raising=False)

    with caplog.at_level(logging.WARNING, logger="butlers.credentials"):
        validate_credentials(env_required=[], env_optional=["SLACK_TOKEN"])

    assert any("SLACK_TOKEN" in record.message for record in caplog.records)


def test_optional_present_no_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """Present optional var produces no warning."""
    _set_anthropic_key(monkeypatch)
    monkeypatch.setenv("SLACK_TOKEN", "xoxb-test")

    with caplog.at_level(logging.WARNING, logger="butlers.credentials"):
        validate_credentials(env_required=[], env_optional=["SLACK_TOKEN"])

    assert not any("SLACK_TOKEN" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# Error message identifies sources
# ---------------------------------------------------------------------------


def test_error_message_identifies_sources(monkeypatch: pytest.MonkeyPatch):
    """Error message names the source component for each missing variable."""
    monkeypatch.delenv(_ANTHROPIC_KEY, raising=False)
    monkeypatch.delenv("PG_DSN", raising=False)
    monkeypatch.delenv("TG_TOKEN", raising=False)

    with pytest.raises(CredentialError) as exc_info:
        validate_credentials(
            env_required=["PG_DSN"],
            env_optional=[],
            module_credentials={"telegram": ["TG_TOKEN"]},
        )

    msg = str(exc_info.value)
    assert "required by core" in msg
    assert "required by butler.env" in msg
    assert "required by module:telegram" in msg


def test_error_message_identifies_identity_scoped_module_source(
    monkeypatch: pytest.MonkeyPatch,
):
    """Identity-scoped module sources are preserved in the aggregated message."""
    _set_anthropic_key(monkeypatch)
    monkeypatch.delenv("BOT_EMAIL_ADDRESS", raising=False)

    with pytest.raises(CredentialError) as exc_info:
        validate_credentials(
            env_required=[],
            env_optional=[],
            module_credentials={"email.bot": ["BOT_EMAIL_ADDRESS"]},
        )

    msg = str(exc_info.value)
    assert "required by module:email.bot" in msg


# ---------------------------------------------------------------------------
# Tests for detect_secrets function
# ---------------------------------------------------------------------------


def test_detect_secrets_finds_sk_prefix():
    """Detect OpenAI sk- prefix in config values."""
    from butlers.credentials import detect_secrets

    config = {"api_key": "sk-1234567890abcdef"}
    warnings = detect_secrets(config)

    assert len(warnings) == 1
    assert "api_key" in warnings[0]
    assert "sk-" in warnings[0]
    assert "prefix" in warnings[0]


def test_detect_secrets_finds_ghp_prefix():
    """Detect GitHub Personal Access Token ghp_ prefix."""
    from butlers.credentials import detect_secrets

    config = {"github_token": "ghp_1234567890abcdefghij1234567890"}
    warnings = detect_secrets(config)

    assert len(warnings) == 1
    assert "github_token" in warnings[0]
    assert "ghp_" in warnings[0]


def test_detect_secrets_finds_slack_prefixes():
    """Detect various Slack token prefixes."""
    from butlers.credentials import detect_secrets

    # Slack Bot token
    config1 = {"slack_token": "xoxb-1234567890abcdefghij"}
    warnings1 = detect_secrets(config1)
    assert len(warnings1) == 1
    assert "xoxb-" in warnings1[0]

    # Slack User token
    config2 = {"slack_token": "xoxp-1234567890abcdefghij"}
    warnings2 = detect_secrets(config2)
    assert len(warnings2) == 1
    assert "xoxp-" in warnings2[0]

    # Slack Workspace token
    config3 = {"slack_token": "xoxs-1234567890abcdefghij"}
    warnings3 = detect_secrets(config3)
    assert len(warnings3) == 1
    assert "xoxs-" in warnings3[0]

    # Slack App token
    config4 = {"slack_token": "xoxa-1234567890abcdefghij"}
    warnings4 = detect_secrets(config4)
    assert len(warnings4) == 1
    assert "xoxa-" in warnings4[0]


def test_detect_secrets_finds_github_pat_prefix():
    """Detect GitHub Personal Access Token github_pat_ prefix."""
    from butlers.credentials import detect_secrets

    config = {"github_pat": "github_pat_1234567890abcdefghij1234567890"}
    warnings = detect_secrets(config)

    assert len(warnings) == 1
    assert "github_pat" in warnings[0]
    assert "github_pat_" in warnings[0]


def test_detect_secrets_finds_gho_prefix():
    """Detect GitHub OAuth token gho_ prefix."""
    from butlers.credentials import detect_secrets

    config = {"github_oauth": "gho_1234567890abcdefghij1234567890"}
    warnings = detect_secrets(config)

    assert len(warnings) == 1
    assert "gho_" in warnings[0]


def test_detect_secrets_ignores_short_values():
    """Skip short values to avoid false positives."""
    from butlers.credentials import detect_secrets

    config = {
        "api_key": "short",
        "token": "abc123",
        "secret": "s3cr",
    }
    warnings = detect_secrets(config)

    assert len(warnings) == 0


def test_detect_secrets_ignores_normal_config():
    """No warnings for normal non-secret config values."""
    from butlers.credentials import detect_secrets

    config = {
        "debug": "true",
        "port": "8080",
        "host": "localhost:8080",
        "database": "postgresql",
        "version": "1.0.0",
        "app_name": "my_butler",
    }
    warnings = detect_secrets(config)

    assert len(warnings) == 0


def test_detect_secrets_finds_long_base64():
    """Detect long base64-like strings (40+ chars)."""
    from butlers.credentials import detect_secrets

    # Exactly 40 chars of base64-like content
    config1 = {"cert": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij1234"}
    warnings1 = detect_secrets(config1)
    assert len(warnings1) == 1
    assert "base64" in warnings1[0]

    # 50 chars of base64-like content
    config2 = {"certificate": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij/+=1234567890"}
    warnings2 = detect_secrets(config2)
    assert len(warnings2) == 1
    assert "base64" in warnings2[0]

    # Less than 40 chars should not trigger
    config3 = {"certificate": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij123"}
    warnings3 = detect_secrets(config3)
    assert len(warnings3) == 0


def test_detect_secrets_key_name_heuristic():
    """Detect secret key names with long values."""
    from butlers.credentials import detect_secrets

    # Key name "password" with long value (16+ chars)
    config1 = {"password": "1234567890abcdef1234567890"}
    warnings1 = detect_secrets(config1)
    assert len(warnings1) == 1
    assert "password" in warnings1[0]
    assert "key name suggests secret" in warnings1[0]

    # Key name "secret" with long value
    config2 = {"secret": "abcdefghijklmnop1234567890"}
    warnings2 = detect_secrets(config2)
    assert len(warnings2) == 1
    assert "secret" in warnings2[0]

    # Key name "api_key" with long value
    config3 = {"api_key": "1234567890abcdef1234567890"}
    warnings3 = detect_secrets(config3)
    assert len(warnings3) == 1
    assert "api_key" in warnings3[0]

    # Key name "token" with long value
    config4 = {"token": "1234567890abcdef1234567890"}
    warnings4 = detect_secrets(config4)
    assert len(warnings4) == 1
    assert "token" in warnings4[0]

    # Key name "key" with long value
    config5 = {"key": "1234567890abcdef1234567890"}
    warnings5 = detect_secrets(config5)
    assert len(warnings5) == 1
    assert "key" in warnings5[0]


def test_detect_secrets_key_heuristic_short_value():
    """No warning for heuristic key names with short values."""
    from butlers.credentials import detect_secrets

    config = {
        "password": "1234567890",  # Less than 16 chars
        "api_key": "12345",  # Less than 16 chars
        "secret": "short",  # Less than 16 chars
    }
    warnings = detect_secrets(config)

    assert len(warnings) == 0


def test_detect_secrets_ignores_urls():
    """Skip URL-like values to avoid false positives."""
    from butlers.credentials import detect_secrets

    config = {
        "database_url": "postgresql://user:password@localhost:5432/db",
        "api_endpoint": "https://api.example.com/v1/secret",
        "webhook": "http://localhost:8080/webhook",
    }
    warnings = detect_secrets(config)

    assert len(warnings) == 0


def test_detect_secrets_ignores_file_paths():
    """Skip file path values to avoid false positives."""
    from butlers.credentials import detect_secrets

    config = {
        "cert_path": "/etc/certs/server.crt",
        "key_path": "/home/user/.ssh/id_rsa",
        "config_dir": "./config/settings",
    }
    warnings = detect_secrets(config)

    assert len(warnings) == 0


def test_detect_secrets_returns_empty_for_clean_config():
    """Return empty list for config with no suspected secrets."""
    from butlers.credentials import detect_secrets

    config = {
        "app_name": "my_butler",
        "version": "1.2.3",
        "debug_mode": "false",
        "max_workers": "10",
        "timeout": "30",
    }
    warnings = detect_secrets(config)

    assert isinstance(warnings, list)
    assert len(warnings) == 0


def test_detect_secrets_skips_non_string_values():
    """Skip non-string values in config dict."""
    from butlers.credentials import detect_secrets

    config = {
        "debug": True,
        "port": 8080,
        "timeout": 30.5,
        "workers": [1, 2, 3],
        "settings": {"key": "sk-secret123456789"},
        "text": "normal_text",
    }
    warnings = detect_secrets(config)

    # Should only check the "text" key, which is safe
    assert len(warnings) == 0


def test_detect_secrets_multiple_warnings():
    """Return multiple warnings for multiple suspected secrets."""
    from butlers.credentials import detect_secrets

    config = {
        "openai_key": "sk-1234567890abcdef",
        "github_token": "ghp_1234567890abcdefghij",
        "slack_bot": "xoxb-1234567890abcdef",
    }
    warnings = detect_secrets(config)

    assert len(warnings) == 3
    assert any("openai_key" in w for w in warnings)
    assert any("github_token" in w for w in warnings)
    assert any("slack_bot" in w for w in warnings)


def test_detect_secrets_case_sensitivity_keys():
    """Key name heuristics should be case-insensitive."""
    from butlers.credentials import detect_secrets

    config = {
        "PASSWORD": "1234567890abcdef1234567890",
        "Api_Key": "1234567890abcdef1234567890",
        "SECRET": "1234567890abcdef1234567890",
    }
    warnings = detect_secrets(config)

    assert len(warnings) == 3


# ---------------------------------------------------------------------------
# Tests for validate_module_credentials
# ---------------------------------------------------------------------------


def test_validate_module_credentials_returns_failures(monkeypatch: pytest.MonkeyPatch):
    """Returns dict of module → missing vars when env vars are absent."""
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("CAL_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("CAL_CLIENT_ID", "present")

    result = validate_module_credentials(
        {
            "telegram": ["TG_BOT_TOKEN"],
            "calendar": ["CAL_CLIENT_ID", "CAL_CLIENT_SECRET"],
        }
    )

    assert "telegram" in result
    assert result["telegram"] == ["TG_BOT_TOKEN"]
    assert "calendar" in result
    assert result["calendar"] == ["CAL_CLIENT_SECRET"]


def test_validate_module_credentials_empty_when_all_present(monkeypatch: pytest.MonkeyPatch):
    """Returns empty dict when all module env vars are set."""
    monkeypatch.setenv("TG_BOT_TOKEN", "tok-123")
    monkeypatch.setenv("EMAIL_PASS", "pass-456")

    result = validate_module_credentials(
        {
            "telegram": ["TG_BOT_TOKEN"],
            "email": ["EMAIL_PASS"],
        }
    )

    assert result == {}


def test_validate_module_credentials_empty_input():
    """Returns empty dict for empty input."""
    result = validate_module_credentials({})
    assert result == {}


# ---------------------------------------------------------------------------
# Tests for validate_module_credentials_async
# ---------------------------------------------------------------------------


def _make_credential_store(resolved: dict[str, str | None]) -> object:
    """Create a mock CredentialStore that resolves keys from ``resolved`` dict."""
    store = AsyncMock()

    async def _resolve(key: str, *, env_fallback: bool = True) -> str | None:
        return resolved.get(key)

    store.resolve = _resolve
    return store


async def test_validate_module_credentials_async_returns_failures(
    monkeypatch: pytest.MonkeyPatch,
):
    """Returns dict of module → missing keys when neither DB nor env has them."""
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("CAL_CLIENT_SECRET", raising=False)

    # DB has CAL_CLIENT_ID only; everything else not in DB either
    store = _make_credential_store({"CAL_CLIENT_ID": "db-value"})

    result = await validate_module_credentials_async(
        {
            "telegram": ["TG_BOT_TOKEN"],
            "calendar": ["CAL_CLIENT_ID", "CAL_CLIENT_SECRET"],
        },
        store,  # type: ignore[arg-type]
    )

    assert "telegram" in result
    assert result["telegram"] == ["TG_BOT_TOKEN"]
    assert "calendar" in result
    assert result["calendar"] == ["CAL_CLIENT_SECRET"]


async def test_validate_module_credentials_async_db_stored_passes(
    monkeypatch: pytest.MonkeyPatch,
):
    """Credentials stored in DB pass validation even when env var is absent."""
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)

    # DB has the token
    store = _make_credential_store({"TG_BOT_TOKEN": "db-stored-token"})

    result = await validate_module_credentials_async(
        {"telegram": ["TG_BOT_TOKEN"]},
        store,  # type: ignore[arg-type]
    )

    # No failures because DB resolved the credential
    assert result == {}


async def test_validate_module_credentials_async_env_fallback_passes(
    monkeypatch: pytest.MonkeyPatch,
):
    """Credentials in env pass even when not in DB (CredentialStore env_fallback)."""
    monkeypatch.setenv("TG_BOT_TOKEN", "env-token")

    # DB has nothing; but store.resolve delegates to env fallback via the real store
    # Here we simulate the store returning the env value
    store = _make_credential_store({"TG_BOT_TOKEN": "env-token"})

    result = await validate_module_credentials_async(
        {"telegram": ["TG_BOT_TOKEN"]},
        store,  # type: ignore[arg-type]
    )

    assert result == {}


async def test_validate_module_credentials_async_empty_input():
    """Returns empty dict for empty input."""
    store = _make_credential_store({})

    result = await validate_module_credentials_async({}, store)  # type: ignore[arg-type]
    assert result == {}


async def test_validate_module_credentials_async_all_missing():
    """Returns all modules when none of their credentials are resolvable."""
    store = _make_credential_store({})  # nothing in DB; env also empty

    result = await validate_module_credentials_async(
        {
            "email": ["EMAIL_PASS"],
            "telegram": ["TG_BOT_TOKEN"],
        },
        store,  # type: ignore[arg-type]
    )

    assert "email" in result
    assert "telegram" in result


async def test_validate_module_credentials_async_multiple_keys_partial_missing():
    """Only missing keys are listed per module."""
    store = _make_credential_store({"CAL_CLIENT_ID": "present"})

    result = await validate_module_credentials_async(
        {"calendar": ["CAL_CLIENT_ID", "CAL_CLIENT_SECRET", "CAL_REFRESH_TOKEN"]},
        store,  # type: ignore[arg-type]
    )

    # CAL_CLIENT_ID is resolved; CAL_CLIENT_SECRET and CAL_REFRESH_TOKEN are missing
    assert "calendar" in result
    assert result["calendar"] == ["CAL_CLIENT_SECRET", "CAL_REFRESH_TOKEN"]
