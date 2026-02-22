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
    ContactAddress,
    ContactBatch,
    ContactDate,
    ContactEmail,
    ContactOrganization,
    ContactPhone,
    ContactPhoto,
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
    ContactUrl,
    ContactUsername,
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
        """Register contacts MCP tools.

        Tools capture ``self`` via closure so they resolve ``_runtime`` at
        call-time, after ``on_startup()`` has wired the runtime.  All four
        tools return a clear error when the runtime is unavailable (sync
        disabled or not yet started).
        """
        self._config = self._coerce_config(config)
        self._db = db

        module = self  # capture for closures

        # ------------------------------------------------------------------
        # contacts_sync_now
        # ------------------------------------------------------------------

        @mcp.tool()
        async def contacts_sync_now(
            provider: str = "google",
            mode: ContactsSyncMode = "incremental",
        ) -> dict[str, Any]:
            """Trigger an immediate contacts sync cycle.

            Args:
                provider: Provider to sync (currently only 'google').
                mode: Sync mode — 'incremental' for routine refresh,
                      'full' for a complete backfill from the provider.

            Returns:
                A dict with the sync result summary including fetched,
                applied, skipped, and deleted contact counts.
            """
            runtime = module._runtime
            if runtime is None:
                return {
                    "error": (
                        "Contacts sync runtime is not running. "
                        "Ensure sync.enabled=true and Google credentials are configured."
                    ),
                    "provider": provider,
                    "mode": mode,
                }

            config_provider = module._config.provider if module._config else "google"
            if provider != config_provider:
                return {
                    "error": (
                        f"Provider '{provider}' is not the configured provider "
                        f"'{config_provider}'. Only the configured provider is supported."
                    ),
                    "provider": provider,
                    "mode": mode,
                }

            try:
                result: ContactsSyncResult = await runtime._sync_engine.sync(
                    account_id=runtime._account_id,
                    mode=mode,
                )
            except ContactsSyncError as exc:
                return {
                    "error": str(exc),
                    "provider": provider,
                    "mode": mode,
                }
            except Exception as exc:
                logger.warning("contacts_sync_now failed: %s", exc, exc_info=True)
                return {
                    "error": f"Sync failed: {exc}",
                    "provider": provider,
                    "mode": mode,
                }

            return {
                "provider": provider,
                "mode": result.mode,
                "summary": {
                    "fetched": result.fetched_contacts,
                    "applied": result.applied_contacts,
                    "skipped": result.skipped_contacts,
                    "deleted": result.deleted_contacts,
                },
                "next_sync_cursor": result.next_sync_cursor,
            }

        # ------------------------------------------------------------------
        # contacts_sync_status
        # ------------------------------------------------------------------

        @mcp.tool()
        async def contacts_sync_status(
            provider: str = "google",
        ) -> dict[str, Any]:
            """Return the current contacts sync state for a provider.

            Args:
                provider: Provider to query (currently only 'google').

            Returns:
                A dict with last sync timestamps, cursor age, last error,
                and approximate contact count.
            """
            runtime = module._runtime
            if runtime is None:
                return {
                    "error": (
                        "Contacts sync runtime is not running. "
                        "Ensure sync.enabled=true and Google credentials are configured."
                    ),
                    "provider": provider,
                    "sync_enabled": False,
                }

            try:
                state: ContactsSyncState = await runtime._state_store.load(
                    provider=runtime._provider_name,
                    account_id=runtime._account_id,
                )
            except Exception as exc:
                logger.warning("contacts_sync_status: state load failed: %s", exc, exc_info=True)
                return {
                    "error": f"Failed to load sync state: {exc}",
                    "provider": provider,
                }

            contact_count = len(state.contact_versions) if state.contact_versions else 0

            return {
                "provider": provider,
                "sync_enabled": True,
                "sync_cursor": state.sync_cursor is not None,
                "cursor_issued_at": state.cursor_issued_at,
                "last_full_sync_at": state.last_full_sync_at,
                "last_incremental_sync_at": state.last_incremental_sync_at,
                "last_success_at": state.last_success_at,
                "last_error": state.last_error,
                "contact_count": contact_count,
            }

        # ------------------------------------------------------------------
        # contacts_source_list
        # ------------------------------------------------------------------

        @mcp.tool()
        async def contacts_source_list(
            provider: str | None = None,
        ) -> list[dict[str, Any]]:
            """List connected contact source accounts with their status.

            Args:
                provider: Filter by provider name (e.g. 'google').
                          When omitted, all configured sources are listed.

            Returns:
                A list of source account dicts, each with provider name,
                account_id, sync enabled flag, and last sync timestamp.
            """
            runtime = module._runtime
            cfg = module._config

            configured_provider = cfg.provider if cfg else "unknown"

            # If a provider filter is given and doesn't match, return empty.
            if provider is not None and provider != configured_provider:
                return []

            if runtime is None:
                return [
                    {
                        "provider": configured_provider,
                        "account_id": _DEFAULT_ACCOUNT_ID,
                        "sync_enabled": False,
                        "status": "sync_disabled",
                        "last_success_at": None,
                        "last_error": None,
                    }
                ]

            try:
                state: ContactsSyncState = await runtime._state_store.load(
                    provider=runtime._provider_name,
                    account_id=runtime._account_id,
                )
                status = "active" if state.last_error is None else "error"
                if state.last_success_at is None:
                    status = "never_synced"
            except Exception as exc:
                logger.warning("contacts_source_list: state load failed: %s", exc, exc_info=True)
                state = ContactsSyncState()
                status = "unknown"

            return [
                {
                    "provider": runtime._provider_name,
                    "account_id": runtime._account_id,
                    "sync_enabled": True,
                    "status": status,
                    "last_success_at": state.last_success_at,
                    "last_error": state.last_error,
                }
            ]

        # ------------------------------------------------------------------
        # contacts_source_reconcile
        # ------------------------------------------------------------------

        @mcp.tool()
        async def contacts_source_reconcile(
            contact_id: str | None = None,
        ) -> dict[str, Any]:
            """Trigger re-evaluation of source links for a contact.

            Schedules an immediate incremental sync to pull the latest data
            from the provider and reconcile any stale or missing source links.

            Args:
                contact_id: Optional contact ID to reconcile.  When omitted,
                            triggers reconciliation for all contacts.

            Returns:
                A dict confirming the reconciliation request was queued.
            """
            runtime = module._runtime
            if runtime is None:
                return {
                    "error": (
                        "Contacts sync runtime is not running. "
                        "Ensure sync.enabled=true and Google credentials are configured."
                    ),
                    "queued": False,
                }

            # Signal the runtime poller to wake up and run a sync cycle.
            runtime.trigger_immediate_sync()

            return {
                "queued": True,
                "contact_id": contact_id,
                "message": (
                    "Reconciliation queued via immediate sync trigger. "
                    "The sync runtime will process source links on the next cycle."
                    if contact_id is None
                    else (
                        f"Reconciliation for contact '{contact_id}' queued via "
                        "immediate sync trigger. The sync runtime will refresh "
                        "source links on the next cycle."
                    )
                ),
            }

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
    "ContactAddress",
    "ContactBatch",
    "ContactDate",
    "ContactEmail",
    "ContactOrganization",
    "ContactPhone",
    "ContactPhoto",
    "ContactUrl",
    "ContactUsername",
    "ContactsProvider",
    "ContactsRequestError",
    "ContactsSyncEngine",
    "ContactsSyncError",
    "ContactsSyncMode",
    "ContactsSyncResult",
    "ContactsSyncRuntime",
    "ContactsSyncState",
    "ContactsSyncStateStore",
    "ContactsSyncTokenExpiredError",
    "GoogleContactsProvider",
]
