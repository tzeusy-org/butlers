"""Unit tests for TelegramContactsProvider."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.contacts.telegram_provider import (
    TelegramContactsProvider,
    _make_sync_cursor,
    _user_to_canonical,
)

pytestmark = pytest.mark.unit


def _make_user(
    *,
    user_id: int = 12345,
    first_name: str | None = "Alice",
    last_name: str | None = "Smith",
    username: str | None = "alice",
    phone: str | None = "15551234567",
    bot: bool = False,
    deleted: bool = False,
    is_self: bool = False,
) -> MagicMock:
    """Create a mock Telethon User object."""
    user = MagicMock()
    user.id = user_id
    user.first_name = first_name
    user.last_name = last_name
    user.username = username
    user.phone = phone
    user.bot = bot
    user.deleted = deleted
    user.is_self = is_self
    return user


class TestUserToCanonical:
    def test_basic_conversion(self) -> None:
        user = _make_user()
        contact = _user_to_canonical(user)
        assert contact is not None
        assert contact.external_id == "12345"
        assert contact.first_name == "Alice"
        assert contact.last_name == "Smith"
        assert contact.display_name == "Alice Smith"
        assert len(contact.phones) == 1
        assert contact.phones[0].value == "+15551234567"
        assert contact.phones[0].label == "mobile"
        assert contact.phones[0].primary is True
        assert len(contact.usernames) == 1
        assert contact.usernames[0].value == "@alice"
        assert contact.usernames[0].service == "telegram"

    def test_phone_already_has_plus(self) -> None:
        user = _make_user(phone="+15551234567")
        contact = _user_to_canonical(user)
        assert contact is not None
        assert contact.phones[0].value == "+15551234567"

    def test_no_phone(self) -> None:
        user = _make_user(phone=None)
        contact = _user_to_canonical(user)
        assert contact is not None
        assert len(contact.phones) == 0

    def test_no_username(self) -> None:
        user = _make_user(username=None)
        contact = _user_to_canonical(user)
        assert contact is not None
        assert len(contact.usernames) == 0

    def test_skips_bots(self) -> None:
        user = _make_user(bot=True)
        assert _user_to_canonical(user) is None

    def test_skips_deleted(self) -> None:
        user = _make_user(deleted=True)
        assert _user_to_canonical(user) is None

    def test_skips_self(self) -> None:
        user = _make_user(is_self=True)
        assert _user_to_canonical(user) is None

    def test_none_user(self) -> None:
        assert _user_to_canonical(None) is None

    def test_first_name_only(self) -> None:
        user = _make_user(last_name=None)
        contact = _user_to_canonical(user)
        assert contact is not None
        assert contact.display_name == "Alice"
        assert contact.last_name is None

    def test_no_name_falls_back_to_username(self) -> None:
        user = _make_user(first_name=None, last_name=None, username="alice")
        contact = _user_to_canonical(user)
        assert contact is not None
        assert contact.display_name == "alice"

    def test_no_name_no_username_falls_back_to_id(self) -> None:
        user = _make_user(first_name=None, last_name=None, username=None)
        contact = _user_to_canonical(user)
        assert contact is not None
        assert contact.display_name == "12345"

    def test_raw_payload_populated(self) -> None:
        user = _make_user()
        contact = _user_to_canonical(user)
        assert contact is not None
        assert contact.raw is not None
        assert contact.raw["telegram_user_id"] == "12345"
        assert contact.raw["first_name"] == "Alice"
        assert contact.raw["username"] == "alice"

    def test_etag_is_none(self) -> None:
        """Telegram contacts use hash-based versioning, not etags."""
        user = _make_user()
        contact = _user_to_canonical(user)
        assert contact is not None
        assert contact.etag is None

    def test_empty_string_names_treated_as_none(self) -> None:
        user = _make_user(first_name="", last_name="")
        contact = _user_to_canonical(user)
        assert contact is not None
        assert contact.first_name is None
        assert contact.last_name is None


class TestMakeSyncCursor:
    def test_cursor_format(self) -> None:
        cursor = _make_sync_cursor()
        assert cursor.startswith("telegram:")
        # Should be parseable as ISO timestamp after prefix
        ts_part = cursor[len("telegram:"):]
        dt = datetime.fromisoformat(ts_part)
        assert dt.tzinfo is not None


class TestTelegramContactsProvider:
    def test_name(self) -> None:
        with patch("butlers.modules.contacts.telegram_provider.TELETHON_AVAILABLE", True):
            provider = TelegramContactsProvider(
                api_id=123, api_hash="abc", session_string="sess"
            )
            assert provider.name == "telegram"

    @pytest.mark.asyncio
    async def test_full_sync_returns_contacts(self) -> None:
        with patch("butlers.modules.contacts.telegram_provider.TELETHON_AVAILABLE", True):
            provider = TelegramContactsProvider(
                api_id=123, api_hash="abc", session_string="sess"
            )

        users = [_make_user(user_id=1, first_name="A"), _make_user(user_id=2, first_name="B")]
        mock_client = AsyncMock()
        mock_client.is_connected.return_value = True
        mock_client.is_user_authorized = AsyncMock(return_value=True)
        mock_client.get_contacts = AsyncMock(return_value=users)
        provider._client = mock_client

        batch = await provider.full_sync(account_id="default")
        assert len(batch.contacts) == 2
        assert batch.contacts[0].external_id == "1"
        assert batch.contacts[1].external_id == "2"
        assert batch.next_page_token is None
        assert batch.next_sync_cursor is not None
        assert batch.next_sync_cursor.startswith("telegram:")

    @pytest.mark.asyncio
    async def test_incremental_sync_returns_contacts(self) -> None:
        with patch("butlers.modules.contacts.telegram_provider.TELETHON_AVAILABLE", True):
            provider = TelegramContactsProvider(
                api_id=123, api_hash="abc", session_string="sess"
            )

        users = [_make_user(user_id=3)]
        mock_client = AsyncMock()
        mock_client.is_connected.return_value = True
        mock_client.is_user_authorized = AsyncMock(return_value=True)
        mock_client.get_contacts = AsyncMock(return_value=users)
        provider._client = mock_client

        batch = await provider.incremental_sync(
            account_id="default", cursor="telegram:old"
        )
        assert len(batch.contacts) == 1
        assert batch.next_sync_cursor is not None

    @pytest.mark.asyncio
    async def test_full_sync_filters_bots_and_deleted(self) -> None:
        with patch("butlers.modules.contacts.telegram_provider.TELETHON_AVAILABLE", True):
            provider = TelegramContactsProvider(
                api_id=123, api_hash="abc", session_string="sess"
            )

        users = [
            _make_user(user_id=1, first_name="Real"),
            _make_user(user_id=2, bot=True),
            _make_user(user_id=3, deleted=True),
            _make_user(user_id=4, is_self=True),
        ]
        mock_client = AsyncMock()
        mock_client.is_connected.return_value = True
        mock_client.is_user_authorized = AsyncMock(return_value=True)
        mock_client.get_contacts = AsyncMock(return_value=users)
        provider._client = mock_client

        batch = await provider.full_sync(account_id="default")
        assert len(batch.contacts) == 1
        assert batch.contacts[0].external_id == "1"

    @pytest.mark.asyncio
    async def test_validate_credentials_success(self) -> None:
        with patch("butlers.modules.contacts.telegram_provider.TELETHON_AVAILABLE", True):
            provider = TelegramContactsProvider(
                api_id=123, api_hash="abc", session_string="sess"
            )

        mock_me = MagicMock()
        mock_me.id = 999
        mock_client = AsyncMock()
        mock_client.is_connected.return_value = True
        mock_client.is_user_authorized = AsyncMock(return_value=True)
        mock_client.get_me = AsyncMock(return_value=mock_me)
        provider._client = mock_client

        await provider.validate_credentials()  # Should not raise

    @pytest.mark.asyncio
    async def test_validate_credentials_not_authorized(self) -> None:
        with patch("butlers.modules.contacts.telegram_provider.TELETHON_AVAILABLE", True):
            provider = TelegramContactsProvider(
                api_id=123, api_hash="abc", session_string="sess"
            )

        mock_client = AsyncMock()
        mock_client.is_connected.return_value = False
        mock_client.connect = AsyncMock()
        mock_client.is_user_authorized = AsyncMock(return_value=False)

        # We need to mock _ensure_client to use our mock
        provider._client = None

        with patch(
            "butlers.modules.contacts.telegram_provider.StringSession", return_value="session"
        ), patch(
            "butlers.modules.contacts.telegram_provider.TelegramClient",
            return_value=mock_client,
        ):
            from butlers.modules.contacts.sync import ContactsTokenRefreshError

            with pytest.raises(ContactsTokenRefreshError, match="not authorized"):
                await provider.validate_credentials()

    @pytest.mark.asyncio
    async def test_list_groups_returns_empty(self) -> None:
        with patch("butlers.modules.contacts.telegram_provider.TELETHON_AVAILABLE", True):
            provider = TelegramContactsProvider(
                api_id=123, api_hash="abc", session_string="sess"
            )

        batch = await provider.list_groups(account_id="default")
        assert len(batch.groups) == 0

    @pytest.mark.asyncio
    async def test_shutdown_disconnects_client(self) -> None:
        with patch("butlers.modules.contacts.telegram_provider.TELETHON_AVAILABLE", True):
            provider = TelegramContactsProvider(
                api_id=123, api_hash="abc", session_string="sess"
            )

        mock_client = AsyncMock()
        provider._client = mock_client

        await provider.shutdown()
        mock_client.disconnect.assert_called_once()
        assert provider._client is None

    @pytest.mark.asyncio
    async def test_shutdown_no_client(self) -> None:
        with patch("butlers.modules.contacts.telegram_provider.TELETHON_AVAILABLE", True):
            provider = TelegramContactsProvider(
                api_id=123, api_hash="abc", session_string="sess"
            )

        provider._client = None
        await provider.shutdown()  # Should not raise


class TestContactsModuleTelegramSupport:
    """Test that ContactsModule supports the 'telegram' provider."""

    def test_telegram_in_supported_providers(self) -> None:
        from butlers.modules.contacts import ContactsModule

        assert "telegram" in ContactsModule._SUPPORTED_PROVIDERS

    def test_config_accepts_telegram(self) -> None:
        from butlers.modules.contacts import ContactsConfig

        cfg = ContactsConfig(provider="telegram")
        assert cfg.provider == "telegram"

    def test_config_normalizes_provider(self) -> None:
        from butlers.modules.contacts import ContactsConfig

        cfg = ContactsConfig(provider="  Telegram  ")
        assert cfg.provider == "telegram"
