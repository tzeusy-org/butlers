"""Contacts module API: config/module scaffold plus sync primitives."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from butlers.google_credentials import (
    KEY_CLIENT_ID as _GOOGLE_OAUTH_CLIENT_ID_KEY,
)
from butlers.google_credentials import (
    KEY_CLIENT_SECRET as _GOOGLE_OAUTH_CLIENT_SECRET_KEY,
)
from butlers.google_credentials import (
    KEY_REFRESH_TOKEN as _GOOGLE_REFRESH_TOKEN_KEY,
)
from butlers.modules.base import Module

from .sync import (
    DEFAULT_FORCED_FULL_SYNC_DAYS,
    DEFAULT_GOOGLE_PERSON_FIELDS,
    DEFAULT_INCREMENTAL_SYNC_INTERVAL_MINUTES,
    GOOGLE_OAUTH_TOKEN_URL,
    GOOGLE_PEOPLE_API_CONNECTIONS_URL,
    CanonicalContact,
    ContactBatch,
    ContactEmail,
    ContactPhone,
    ContactsProvider,
    ContactsRequestError,
    ContactsSyncEngine,
    ContactsSyncError,
    ContactsSyncMode,
    ContactsSyncResult,
    ContactsSyncRuntime,
    ContactsSyncState,
    ContactsSyncStateStore,
    ContactsSyncTokenExpiredError,
    GoogleContactsProvider,
)

logger = logging.getLogger(__name__)

_DEFAULT_ACCOUNT_ID = "default"


class ContactsSyncConfig(BaseModel):
    """Scheduler defaults for incremental and forced full contact sync."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    run_on_startup: bool = True
    interval_minutes: int = Field(default=15, ge=1)
    full_sync_interval_days: int = Field(default=6, ge=1)


class ContactsConfig(BaseModel):
    """Configuration for the Contacts module."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    include_other_contacts: bool = False
    sync: ContactsSyncConfig = Field(default_factory=ContactsSyncConfig)

    @field_validator("provider")
    @classmethod
    def _normalize_provider(cls, value: str, info: ValidationInfo) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return normalized


class ContactsModule(Module):
    """Contacts module scaffold with strict config and provider gating."""

    _SUPPORTED_PROVIDERS = {"google"}

    def __init__(self) -> None:
        self._config: ContactsConfig | None = None
        self._db: Any = None
        self._provider: ContactsProvider | None = None
        self._runtime: ContactsSyncRuntime | None = None

    @property
    def name(self) -> str:
        return "contacts"

    @property
    def config_schema(self) -> type[BaseModel]:
        return ContactsConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    @property
    def credentials_env(self) -> list[str]:
        # Contacts uses shared Google OAuth credentials from DB-backed secrets.
        return []

    def migration_revisions(self) -> str | None:
        return "contacts"

    @staticmethod
    def _coerce_config(config: Any) -> ContactsConfig:
        return config if isinstance(config, ContactsConfig) else ContactsConfig(**(config or {}))

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        # Tools are intentionally added in sync-engine follow-up work.
        self._config = self._coerce_config(config)
        self._db = db

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        """Initialize the contacts provider and start the sync runtime.

        Parameters
        ----------
        config:
            Module configuration (``ContactsConfig`` or raw dict).
        db:
            Butler database instance.
        credential_store:
            Optional :class:`~butlers.credential_store.CredentialStore`.
            When provided, Google OAuth credentials are resolved from
            ``butler_secrets``.
        """
        self._config = self._coerce_config(config)
        self._db = db

        if self._config.provider not in self._SUPPORTED_PROVIDERS:
            supported = ", ".join(sorted(self._SUPPORTED_PROVIDERS))
            raise RuntimeError(
                f"Unsupported contacts provider '{self._config.provider}'. "
                f"Supported providers: {supported}"
            )

        if not self._config.sync.enabled:
            logger.info("ContactsModule: sync is disabled; skipping runtime startup")
            return

        client_id, client_secret, refresh_token = await self._resolve_credentials(
            credential_store=credential_store
        )

        self._provider = GoogleContactsProvider(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
        )

        pool = getattr(db, "pool", None) if db is not None else None
        state_store = ContactsSyncStateStore(pool)

        async def _noop_apply(contact: CanonicalContact) -> None:
            # Placeholder apply callback until tool layer is wired in.
            pass

        sync_engine = ContactsSyncEngine(
            provider=self._provider,
            state_store=state_store,
            apply_contact=_noop_apply,
        )

        self._runtime = ContactsSyncRuntime(
            sync_engine=sync_engine,
            state_store=state_store,
            provider_name=self._provider.name,
            account_id=_DEFAULT_ACCOUNT_ID,
            incremental_interval=timedelta(minutes=self._config.sync.interval_minutes),
            forced_full_interval=timedelta(days=self._config.sync.full_sync_interval_days),
        )

        await self._runtime.start()
        logger.info(
            "ContactsModule: sync runtime started (interval=%dm, full_sync_interval=%dd)",
            self._config.sync.interval_minutes,
            self._config.sync.full_sync_interval_days,
        )

    async def on_shutdown(self) -> None:
        """Stop the sync runtime and release provider resources."""
        if self._runtime is not None:
            await self._runtime.stop()
            self._runtime = None
            logger.info("ContactsModule: sync runtime stopped")

        if self._provider is not None:
            await self._provider.shutdown()
            self._provider = None
            logger.info("ContactsModule: provider shut down")

        self._config = None
        self._db = None

    async def _resolve_credentials(
        self,
        *,
        credential_store: Any,
    ) -> tuple[str, str, str]:
        """Resolve Google OAuth credentials from DB-backed credential store.

        Parameters
        ----------
        credential_store:
            A :class:`~butlers.credential_store.CredentialStore` instance.
            When ``None``, an actionable ``RuntimeError`` is raised.

        Returns
        -------
        tuple[str, str, str]
            ``(client_id, client_secret, refresh_token)``

        Raises
        ------
        RuntimeError
            If credentials cannot be resolved from the credential store.
        """
        if credential_store is not None:
            client_id = await credential_store.resolve(
                _GOOGLE_OAUTH_CLIENT_ID_KEY, env_fallback=False
            )
            client_secret = await credential_store.resolve(
                _GOOGLE_OAUTH_CLIENT_SECRET_KEY, env_fallback=False
            )
            refresh_token = await credential_store.resolve(
                _GOOGLE_REFRESH_TOKEN_KEY, env_fallback=False
            )
            if client_id and client_secret and refresh_token:
                logger.debug("ContactsModule: resolved Google credentials from CredentialStore")
                return client_id, client_secret, refresh_token

        raise RuntimeError(
            "ContactsModule: Google OAuth credentials are not available in butler_secrets. "
            f"Required keys: {_GOOGLE_OAUTH_CLIENT_ID_KEY}, {_GOOGLE_OAUTH_CLIENT_SECRET_KEY}, "
            f"{_GOOGLE_REFRESH_TOKEN_KEY}. "
            "Store them via the dashboard OAuth flow (shared credential store)."
        )


__all__ = [
    "ContactsConfig",
    "ContactsModule",
    "ContactsSyncConfig",
    "DEFAULT_FORCED_FULL_SYNC_DAYS",
    "DEFAULT_GOOGLE_PERSON_FIELDS",
    "DEFAULT_INCREMENTAL_SYNC_INTERVAL_MINUTES",
    "GOOGLE_OAUTH_TOKEN_URL",
    "GOOGLE_PEOPLE_API_CONNECTIONS_URL",
    "CanonicalContact",
    "ContactBatch",
    "ContactEmail",
    "ContactPhone",
    "ContactsProvider",
    "ContactsRequestError",
    "ContactsSyncEngine",
    "ContactsSyncError",
    "ContactsSyncMode",
    "ContactsSyncRuntime",
    "ContactsSyncResult",
    "ContactsSyncState",
    "ContactsSyncStateStore",
    "ContactsSyncTokenExpiredError",
    "GoogleContactsProvider",
]
