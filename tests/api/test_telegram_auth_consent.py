"""Regression coverage for Telegram account-wide ingestion consent."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from butlers.api.routers import telegram_auth

pytestmark = pytest.mark.unit


def test_send_code_rejects_missing_or_false_account_wide_scope_consent() -> None:
    """The server must not rely on the browser to enforce acknowledgement."""
    base = {"api_id": 123, "api_hash": "hash", "phone": "+15551234567"}

    for payload in (base, {**base, "scope_consent": False}):
        with pytest.raises(ValidationError):
            telegram_auth.SendCodeRequest.model_validate(payload)


async def test_verified_session_commits_control_state_credentials_and_pending_delete_together() -> (
    None
):
    """All final setup writes share one connection and one transaction."""
    calls: list[str] = []
    pool = MagicMock()
    conn = MagicMock()
    conn.execute = AsyncMock()
    pending = {
        "api_id": 123,
        "api_hash": "hash",
    }

    @asynccontextmanager
    async def acquire():
        yield conn

    @asynccontextmanager
    async def transaction():
        yield

    pool.acquire = MagicMock(return_value=acquire())
    conn.transaction = MagicMock(return_value=transaction())

    async def record_consent(*args: object, **kwargs: object) -> None:
        calls.append("consent")

    async def record_credential(
        _conn: object,
        info_type: str,
        _value: str,
        *,
        secured: bool,
    ) -> bool:
        del secured
        calls.append(info_type)
        return True

    with (
        patch.object(
            telegram_auth,
            "upsert_owner_entity_info_on_connection",
            new=AsyncMock(side_effect=record_credential),
        ),
        patch.object(
            telegram_auth,
            "save_account_wide_ingestion_consent",
            new=AsyncMock(side_effect=record_consent),
        ) as save_consent,
    ):
        await telegram_auth._persist_verified_telegram_session(
            pool,
            token="token",
            pending=pending,
            session_string="final-session",
        )

    pool.acquire.assert_called_once_with()
    conn.transaction.assert_called_once_with()
    save_consent.assert_awaited_once()
    assert save_consent.await_args.args[0] is conn
    assert calls == [
        "consent",
        "telegram_api_id",
        "telegram_api_hash",
        "telegram_user_session",
    ]
    delete_query = conn.execute.await_args.args[0]
    assert "DELETE FROM butler_secrets" in delete_query


async def test_atomic_persistence_rolls_back_without_deleting_pending_on_false_upsert() -> None:
    """A false credential write aborts the entire transaction before pending deletion."""
    pool = MagicMock()
    conn = MagicMock()
    conn.execute = AsyncMock()
    rolled_back: list[bool] = []

    @asynccontextmanager
    async def acquire():
        yield conn

    @asynccontextmanager
    async def transaction():
        try:
            yield
        except Exception:
            rolled_back.append(True)
            raise

    pool.acquire = MagicMock(return_value=acquire())
    conn.transaction = MagicMock(return_value=transaction())
    upsert = AsyncMock(side_effect=[True, False])

    with (
        patch.object(telegram_auth, "save_account_wide_ingestion_consent", new=AsyncMock()),
        patch.object(telegram_auth, "upsert_owner_entity_info_on_connection", new=upsert),
    ):
        with pytest.raises(telegram_auth._CredentialPersistenceError):
            await telegram_auth._persist_verified_telegram_session(
                pool,
                token="token",
                pending={"api_id": 123, "api_hash": "hash"},
                session_string="final-session",
            )

    assert rolled_back == [True]
    assert upsert.await_count == 2
    conn.execute.assert_not_awaited()


async def test_verify_code_preserves_pending_auth_when_a_credential_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A false credential upsert is retriable, not a successful destructive completion."""
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

    save_pending = AsyncMock()
    delete_pending = AsyncMock()
    with (
        patch.object(telegram_auth, "_load_pending", new=AsyncMock(return_value=pending)),
        patch.object(telegram_auth, "_save_pending", new=save_pending),
        patch.object(telegram_auth, "_delete_pending", new=delete_pending),
        patch.object(
            telegram_auth,
            "_persist_verified_telegram_session",
            new=AsyncMock(
                side_effect=telegram_auth._CredentialPersistenceError("telegram_api_hash")
            ),
        ),
    ):
        with pytest.raises(HTTPException) as raised:
            await telegram_auth.verify_code(
                telegram_auth.VerifyCodeRequest(session_token="token", code="12345"),
                db,
            )

    assert raised.value.status_code == 503
    assert "retry" in str(raised.value.detail).lower()
    delete_pending.assert_not_awaited()
    assert any(
        call.args[2].get("verified_session") == "final-session"
        for call in save_pending.await_args_list
    )


async def test_verify_code_retries_staged_verified_session_without_consuming_another_otp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial credential write resumes from durable verified state idempotently."""
    pool = MagicMock()
    db = MagicMock()
    db.credential_shared_pool.return_value = pool
    pending = {
        "api_id": 123,
        "api_hash": "hash",
        "phone": "+15551234567",
        "phone_code_hash": "code-hash",
        "session": "intermediate-session",
        "verified_session": "final-session",
        "scope_consent": True,
    }
    sign_in_calls: list[dict[str, object]] = []

    class FakeStringSession:
        def __init__(self, value: str = "") -> None:
            self.value = value

        @staticmethod
        def save(session: object) -> str:
            return "unexpected-new-session"

    class FakeTelegramClient:
        def __init__(self, session: object, api_id: int, api_hash: str) -> None:
            self.session = session

        async def connect(self) -> None:
            return None

        async def sign_in(self, **kwargs: object) -> None:
            sign_in_calls.append(kwargs)

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

    save_pending = AsyncMock()
    delete_pending = AsyncMock()
    persist = AsyncMock()
    with (
        patch.object(telegram_auth, "_load_pending", new=AsyncMock(return_value=pending)),
        patch.object(telegram_auth, "_save_pending", new=save_pending),
        patch.object(telegram_auth, "_delete_pending", new=delete_pending),
        patch.object(
            telegram_auth,
            "_persist_verified_telegram_session",
            new=persist,
        ),
    ):
        result = await telegram_auth.verify_code(
            telegram_auth.VerifyCodeRequest(session_token="token", code="unused"),
            db,
        )

    assert result.success is True
    assert sign_in_calls == []
    save_pending.assert_not_awaited()
    delete_pending.assert_not_awaited()
    persist.assert_awaited_once()
    assert persist.await_args.kwargs["session_string"] == "final-session"


async def test_session_status_reads_shared_control_state_without_switchboard_registry() -> None:
    pool = MagicMock()
    db = MagicMock()
    db.credential_shared_pool.return_value = pool
    settings = {
        "account_wide_ingestion_consent": {
            "version": "telegram-user-client-account-wide-v1",
            "granted_at": "2026-07-17T00:00:00+00:00",
        }
    }

    with (
        patch.object(
            telegram_auth,
            "resolve_owner_entity_info",
            new=AsyncMock(side_effect=["123", "hash", "session"]),
        ),
        patch.object(
            telegram_auth,
            "load_account_wide_ingestion_consent",
            new=AsyncMock(return_value=settings),
        ) as load_consent,
    ):
        status = await telegram_auth.session_status(db)

    assert status.ready is True
    assert status.has_scope_consent is True
    load_consent.assert_awaited_once_with(pool)
    db.pool.assert_not_called()
