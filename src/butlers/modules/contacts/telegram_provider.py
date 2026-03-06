"""Telegram contacts sync provider via Telethon.

Implements ContactsProvider for syncing contacts from the user's Telegram
account. Since Telegram has no incremental sync token API, both full_sync
and incremental_sync fetch the complete contact list; the sync engine's
contact_versions hash comparison handles dedup/change detection.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from .sync import (
    CanonicalContact,
    ContactBatch,
    ContactPhone,
    ContactsProvider,
    ContactsTokenRefreshError,
    ContactUsername,
    GroupBatch,
)

# Telethon is an optional dependency — handle import gracefully
try:
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.types import User

    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False
    TelegramClient = None  # type: ignore[assignment,misc]
    StringSession = None  # type: ignore[assignment,misc]
    User = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)


def _user_to_canonical(user: Any) -> CanonicalContact | None:
    """Convert a Telethon User to a CanonicalContact.

    Returns None for bots, deleted accounts, and the user themselves (is_self).
    """
    if user is None:
        return None
    if getattr(user, "bot", False) or getattr(user, "deleted", False):
        return None
    if getattr(user, "is_self", False):
        return None

    user_id = str(user.id)
    first_name = getattr(user, "first_name", None) or None
    last_name = getattr(user, "last_name", None) or None
    username = getattr(user, "username", None) or None
    phone = getattr(user, "phone", None) or None

    # Build display name
    name_parts = [p for p in [first_name, last_name] if p]
    display_name = " ".join(name_parts).strip() or username or user_id

    phones: list[ContactPhone] = []
    if phone:
        # Telegram stores phone numbers without '+' prefix
        normalized = f"+{phone}" if not phone.startswith("+") else phone
        phones.append(ContactPhone(value=normalized, label="mobile", primary=True))

    usernames: list[ContactUsername] = []
    if username:
        usernames.append(ContactUsername(value=f"@{username}", service="telegram"))

    # Build raw payload for hash-based versioning (no etag from Telegram)
    raw: dict[str, Any] = {
        "telegram_user_id": user_id,
        "first_name": first_name,
        "last_name": last_name,
        "username": username,
        "phone": phone,
    }

    return CanonicalContact(
        external_id=user_id,
        display_name=display_name,
        first_name=first_name,
        last_name=last_name,
        phones=phones,
        usernames=usernames,
        raw=raw,
    )


class TelegramContactsProvider(ContactsProvider):
    """Telegram contacts provider using Telethon user-client API.

    Fetches the user's Telegram contact list via ``client.get_contacts()``.
    Since Telegram has no incremental sync token, both full and incremental
    sync fetch the complete contact list. The sync engine's contact_versions
    hash comparison handles dedup and change detection.

    Chat ID enrichment (resolving private chat IDs for each contact) is done
    as a post-sync step by iterating dialogs.
    """

    def __init__(
        self,
        *,
        api_id: int,
        api_hash: str,
        session_string: str,
    ) -> None:
        if not TELETHON_AVAILABLE:
            raise RuntimeError("Telethon is not installed. Install with: uv pip install telethon")
        self._api_id = api_id
        self._api_hash = api_hash
        self._session_string = session_string
        self._client: TelegramClient | None = None

    @property
    def name(self) -> str:
        return "telegram"

    async def _ensure_client(self) -> TelegramClient:
        """Create and connect the Telethon client if not already connected."""
        if self._client is not None and self._client.is_connected():
            return self._client

        session = StringSession(self._session_string)
        self._client = TelegramClient(session, self._api_id, self._api_hash)
        await self._client.connect()

        if not await self._client.is_user_authorized():
            raise ContactsTokenRefreshError(
                "Telegram session is not authorized. "
                "Re-authenticate via the dashboard to generate a new session string."
            )

        return self._client

    async def full_sync(self, *, account_id: str, page_token: str | None = None) -> ContactBatch:
        """Fetch all Telegram contacts.

        Telegram returns the full contact list in one call (no pagination).
        A timestamp-based cursor is returned for the sync engine to track.
        """
        del page_token  # Telegram doesn't paginate contacts

        client = await self._ensure_client()
        result = await client.get_contacts()

        contacts: list[CanonicalContact] = []
        for user in result:
            canonical = _user_to_canonical(user)
            if canonical is not None:
                contacts.append(canonical)

        cursor = _make_sync_cursor()

        logger.info(
            "Telegram full_sync: fetched %d contacts (account=%s)",
            len(contacts),
            account_id,
        )

        return ContactBatch(
            contacts=contacts,
            next_page_token=None,
            next_sync_cursor=cursor,
        )

    async def incremental_sync(
        self,
        *,
        account_id: str,
        cursor: str,
        page_token: str | None = None,
    ) -> ContactBatch:
        """Fetch all contacts for incremental comparison.

        Telegram has no delta/sync token API. We fetch the full list every time
        and rely on the sync engine's contact_versions hash to detect actual
        additions, changes, and deletions.
        """
        del cursor, page_token  # Not used — Telegram has no incremental API

        client = await self._ensure_client()
        result = await client.get_contacts()

        contacts: list[CanonicalContact] = []
        for user in result:
            canonical = _user_to_canonical(user)
            if canonical is not None:
                contacts.append(canonical)

        new_cursor = _make_sync_cursor()

        logger.info(
            "Telegram incremental_sync: fetched %d contacts (account=%s)",
            len(contacts),
            account_id,
        )

        return ContactBatch(
            contacts=contacts,
            next_page_token=None,
            next_sync_cursor=new_cursor,
        )

    async def validate_credentials(self) -> None:
        """Verify Telegram session credentials are valid.

        Connects to Telegram and checks authorization status.
        """
        client = await self._ensure_client()
        me = await client.get_me()
        if me is None:
            raise ContactsTokenRefreshError("Telegram session returned no user identity")
        logger.debug("Telegram credentials validated: user_id=%s", me.id)

    async def list_groups(
        self,
        *,
        account_id: str,
        page_token: str | None = None,
    ) -> GroupBatch:
        """Telegram contacts don't have groups/labels. Return empty batch."""
        del account_id, page_token
        return GroupBatch(groups=[])

    async def shutdown(self) -> None:
        """Disconnect the Telethon client."""
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                logger.debug("Error disconnecting Telegram client", exc_info=True)
            self._client = None

    async def enrich_chat_ids(self, pool: Any) -> dict[int, int]:
        """Post-sync enrichment: resolve private chat IDs from dialogs.

        Iterates the user's dialogs and matches by user_id to find the
        private chat_id for each contact. Returns a mapping of
        {user_id: chat_id} for contacts that have private chats.

        This should be called after sync to populate telegram_chat_id entries
        in shared.contact_info.
        """
        client = await self._ensure_client()
        user_to_chat: dict[int, int] = {}

        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            if entity is None:
                continue
            # Only match private user chats (not groups/channels)
            if not getattr(entity, "is_self", False) and hasattr(entity, "id"):
                is_user = not getattr(entity, "megagroup", False) and not getattr(
                    entity, "broadcast", False
                )
                if is_user and not getattr(entity, "bot", False):
                    user_to_chat[entity.id] = dialog.id

        return user_to_chat


def _make_sync_cursor() -> str:
    """Generate a timestamp-based sync cursor for Telegram.

    Since Telegram has no real sync tokens, we use a timestamp as a cursor
    marker. The sync engine uses contact_versions hashes for actual change
    detection — the cursor just satisfies the interface contract.
    """
    return f"telegram:{datetime.now(UTC).isoformat()}"
