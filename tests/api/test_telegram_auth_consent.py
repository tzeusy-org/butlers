"""Regression coverage for Telegram account-wide ingestion consent."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from butlers.api.routers import telegram_auth

pytestmark = pytest.mark.unit


def test_send_code_rejects_missing_or_false_account_wide_scope_consent() -> None:
    """The server must not rely on the browser to enforce acknowledgement."""
    base = {"api_id": 123, "api_hash": "hash", "phone": "+15551234567"}

    for payload in (base, {**base, "scope_consent": False}):
        with pytest.raises(ValidationError):
            telegram_auth.SendCodeRequest.model_validate(payload)


async def test_verified_session_persists_scope_grant_before_owner_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful setup records the accepted scope in connector control settings."""
    calls: list[str] = []
    pool = MagicMock()
    db = MagicMock()
    db.credential_shared_pool.return_value = pool
    db.pool.return_value = pool
    pending = {
        "api_id": 123,
        "api_hash": "hash",
        "phone": "+15551234567",
        "phone_code_hash": "code-hash",
        "session": "intermediate-session",
        "scope_consent": True,
    }

    class FakeStringSession:
        def __init__(self, value: str = "") -> None:
            self.value = value

        @staticmethod
        def save(session: object) -> str:
            return "final-session"

    class FakeTelegramClient:
        def __init__(self, session: object, api_id: int, api_hash: str) -> None:
            self.session = session

        async def connect(self) -> None:
            return None

        async def sign_in(self, **kwargs: object) -> None:
            return None

        async def get_me(self) -> object:
            return SimpleNamespace(username="owner", first_name="Owner", last_name="")

        async def disconnect(self) -> None:
            return None

    telethon_module = ModuleType("telethon")
    telethon_module.TelegramClient = FakeTelegramClient  # type: ignore[attr-defined]
    errors_module = ModuleType("telethon.errors")

    class SessionPasswordNeededError(Exception):
        pass

    errors_module.SessionPasswordNeededError = SessionPasswordNeededError  # type: ignore[attr-defined]
    sessions_module = ModuleType("telethon.sessions")
    sessions_module.StringSession = FakeStringSession  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "telethon", telethon_module)
    monkeypatch.setitem(sys.modules, "telethon.errors", errors_module)
    monkeypatch.setitem(sys.modules, "telethon.sessions", sessions_module)

    async def record_consent(*args: object, **kwargs: object) -> None:
        calls.append("consent")

    async def record_credential(*args: object, **kwargs: object) -> None:
        calls.append("credential")

    with (
        patch.object(
            telegram_auth,
            "_load_pending",
            new=AsyncMock(return_value=pending),
        ),
        patch.object(telegram_auth, "_delete_pending", new=AsyncMock()),
        patch.object(
            telegram_auth,
            "upsert_owner_entity_info",
            new=AsyncMock(side_effect=record_credential),
        ),
        patch(
            "butlers.connectors.cursor_store.save_connector_settings",
            new=AsyncMock(side_effect=record_consent),
        ) as save_consent,
    ):
        result = await telegram_auth.verify_code(
            telegram_auth.VerifyCodeRequest(session_token="token", code="12345"),
            db,
        )

    assert result.success is True
    save_consent.assert_awaited_once_with(
        pool,
        "telegram_user_client",
        "telegram:user:consent-control",
        {
            "account_wide_ingestion_consent": {
                "version": "telegram-user-client-account-wide-v1",
                "granted_at": ANY,
            }
        },
    )
    assert calls[0] == "consent"
    assert calls.count("credential") == 3
