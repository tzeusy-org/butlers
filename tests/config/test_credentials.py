"""Tests for credential validation and secret detection."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from butlers.credentials import (
    CredentialError,
    validate_credentials,
    validate_module_credentials_async,
)

pytestmark = pytest.mark.unit


def _make_store(resolved: dict[str, str | None]) -> object:
    store = AsyncMock()

    async def _resolve(key, *, env_fallback=True):
        return resolved.get(key)

    store.resolve = _resolve
    return store


def test_validate_credentials(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    """All present: no error. Missing: aggregated CredentialError. Optional missing: warning."""
    monkeypatch.setenv("PG_DSN", "postgres://localhost/test")
    monkeypatch.setenv("EMAIL_PASS", "secret")
    validate_credentials(
        env_required=["PG_DSN"], env_optional=[], module_credentials={"email": ["EMAIL_PASS"]}
    )

    monkeypatch.delenv("PG_DSN", raising=False)
    monkeypatch.delenv("TG_TOKEN", raising=False)
    with pytest.raises(CredentialError) as exc_info:
        validate_credentials(
            env_required=["PG_DSN"], env_optional=[], module_credentials={"telegram": ["TG_TOKEN"]}
        )
    msg = str(exc_info.value)
    assert (
        "PG_DSN" in msg
        and "TG_TOKEN" in msg
        and "required by butler.env" in msg
        and "required by module:telegram" in msg
    )

    monkeypatch.delenv("SLACK_TOKEN", raising=False)
    with caplog.at_level(logging.WARNING, logger="butlers.credentials"):
        validate_credentials(env_required=[], env_optional=["SLACK_TOKEN"])
    assert any("SLACK_TOKEN" in r.message for r in caplog.records)


def test_detect_secrets():
    """detect_secrets: finds known patterns; ignores safe/short values."""
    from butlers.credentials import detect_secrets

    cases = [
        ("api_key", "sk-1234567890abcdef", "sk-"),
        ("github_token", "ghp_1234567890abcdefghij", "ghp_"),
        ("slack_bot", "xoxb-1234567890abcdef", "xoxb-"),
        ("cert", "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij1234", "base64"),
        ("password", "1234567890abcdef1234567890", "key name suggests secret"),
    ]
    for key, value, fragment in cases:
        warnings = detect_secrets({key: value})
        assert len(warnings) >= 1 and any(fragment in w for w in warnings), f"Failed for {key}"
    assert detect_secrets({"debug": "true", "port": "8080", "api_key": "short"}) == []


async def test_validate_module_credentials_async(monkeypatch: pytest.MonkeyPatch):
    """DB-stored passes; missing returns dict of module->missing keys."""
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    store = _make_store({"TG_BOT_TOKEN": "db-stored"})
    assert await validate_module_credentials_async({"telegram": ["TG_BOT_TOKEN"]}, store) == {}  # type: ignore[arg-type]

    store2 = _make_store({"CAL_CLIENT_ID": "present"})
    result = await validate_module_credentials_async(
        {"calendar": ["CAL_CLIENT_ID", "CAL_CLIENT_SECRET"]}, store2
    )  # type: ignore[arg-type]
    assert result == {"calendar": ["CAL_CLIENT_SECRET"]}
