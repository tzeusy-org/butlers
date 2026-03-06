"""Contacts module API: config/module scaffold plus sync primitives."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from butlers.google_credentials import (
    CONTACT_INFO_REFRESH_TOKEN as _GOOGLE_CONTACT_INFO_REFRESH_TYPE,
)
from butlers.google_credentials import (
    KEY_CLIENT_ID as _GOOGLE_OAUTH_CLIENT_ID_KEY,
)
from butlers.google_credentials import (
    KEY_CLIENT_SECRET as _GOOGLE_OAUTH_CLIENT_SECRET_KEY,
)
from butlers.modules.base import Module

from .backfill import ContactBackfillEngine
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
from .telegram_provider import TelegramContactsProvider

logger = logging.getLogger(__name__)

_DEFAULT_ACCOUNT_ID = "default"


class ContactsSyncConfig(BaseModel):
    """Scheduler defaults for incremental and forced full contact sync."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    run_on_startup: bool = True
    interval_minutes: int = Field(default=15, ge=1)
    full_sync_interval_days: int = Field(default=6, ge=1)


class ProviderEntry(BaseModel):
    """Configuration for a single contacts provider."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1)

    @field_validator("type")
    @classmethod
    def _normalize_type(cls, value: str, info: ValidationInfo) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return normalized


class ContactsConfig(BaseModel):
    """Configuration for the Contacts module.

    Supports both legacy single-provider and multi-provider configuration:

    Legacy (single provider)::

        provider = "google"

    Multi-provider::

        providers = [{type = "google"}, {type = "telegram"}]

    Exactly one of ``provider`` or ``providers`` must be specified.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    providers: list[ProviderEntry] | None = None
    include_other_contacts: bool = False
    sync: ContactsSyncConfig = Field(default_factory=ContactsSyncConfig)

    @field_validator("provider")
    @classmethod
    def _normalize_provider(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return normalized

    @model_validator(mode="after")
    def _resolve_providers(self) -> ContactsConfig:
        if self.provider is not None and self.providers is not None:
            raise ValueError(
                "Cannot specify both 'provider' and 'providers'. "
                "Use 'providers' for multi-provider configuration."
            )
        if self.provider is None and self.providers is None:
            raise ValueError("Either 'provider' or 'providers' must be specified.")
        if self.provider is not None and self.providers is None:
            self.providers = [ProviderEntry(type=self.provider)]
        types = [p.type for p in self.providers]
        if len(types) != len(set(types)):
            dupes = sorted({t for t in types if types.count(t) > 1})
            raise ValueError(f"Duplicate provider types: {', '.join(dupes)}")
        return self

    @property
    def provider_types(self) -> list[str]:
        """Return list of configured provider type strings."""
        return [p.type for p in (self.providers or [])]


class ContactsModule(Module):
    """Contacts module scaffold with strict config and multi-provider support."""

    _SUPPORTED_PROVIDERS = {"google", "telegram"}

    def __init__(self) -> None:
        self._config: ContactsConfig | None = None
        self._db: Any = None
        self._providers: dict[str, ContactsProvider] = {}
        self._runtimes: dict[str, ContactsSyncRuntime] = {}

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
        return []

    def migration_revisions(self) -> str | None:
        return "contacts"

    @staticmethod
    def _coerce_config(config: Any) -> ContactsConfig:
        return config if isinstance(config, ContactsConfig) else ContactsConfig(**(config or {}))

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register contacts MCP tools.

        Tools capture ``self`` via closure so they resolve ``_runtimes`` at
        call-time, after ``on_startup()`` has wired the runtimes.  All tools
        return a clear error when no runtime is available.
        """
        self._config = self._coerce_config(config)
        self._db = db

        module = self  # capture for closures

        # ------------------------------------------------------------------
        # Helper: sync a single provider
        # ------------------------------------------------------------------

        async def _sync_single_provider(
            runtime: ContactsSyncRuntime, prov_name: str, mode: ContactsSyncMode
        ) -> dict[str, Any]:
            try:
                result: ContactsSyncResult = await runtime._sync_engine.sync(
                    account_id=runtime._account_id,
                    mode=mode,
                )
            except ContactsSyncError as exc:
                return {"error": str(exc), "provider": prov_name, "mode": mode}
            except Exception as exc:
                logger.exception("contacts_sync_now failed for %s", prov_name)
                return {"error": f"Sync failed: {exc}", "provider": prov_name, "mode": mode}

            return {
                "provider": prov_name,
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
        # Helper: status for a single provider
        # ------------------------------------------------------------------

        async def _status_single_provider(
            runtime: ContactsSyncRuntime, prov_name: str
        ) -> dict[str, Any]:
            try:
                state: ContactsSyncState = await runtime._state_store.load(
                    provider=runtime._provider_name,
                    account_id=runtime._account_id,
                )
            except Exception as exc:
                logger.warning(
                    "contacts_sync_status: state load failed for %s: %s",
                    prov_name,
                    exc,
                    exc_info=True,
                )
                return {"error": f"Failed to load sync state: {exc}", "provider": prov_name}

            return {
                "provider": prov_name,
                "sync_enabled": True,
                "sync_cursor": state.sync_cursor is not None,
                "cursor_issued_at": state.cursor_issued_at,
                "last_full_sync_at": state.last_full_sync_at,
                "last_incremental_sync_at": state.last_incremental_sync_at,
                "last_success_at": state.last_success_at,
                "last_error": state.last_error,
                "contact_count": len(state.contact_versions),
            }

        # ------------------------------------------------------------------
        # contacts_sync_now
        # ------------------------------------------------------------------

        @mcp.tool()
        async def contacts_sync_now(
            provider: str | None = None,
            mode: ContactsSyncMode = "incremental",
        ) -> dict[str, Any]:
            """Trigger an immediate contacts sync cycle.

            Args:
                provider: Provider to sync (e.g. 'google', 'telegram').
                          When omitted, syncs all configured providers.
                mode: Sync mode — 'incremental' for routine refresh,
                      'full' for a complete backfill from the provider.

            Returns:
                A dict with the sync result summary including fetched,
                applied, skipped, and deleted contact counts.
            """
            runtimes = module._runtimes
            if not runtimes:
                return {
                    "error": (
                        "Contacts sync runtime is not running. "
                        "Ensure sync.enabled=true and provider credentials are configured."
                    ),
                    "provider": provider,
                    "mode": mode,
                }

            configured = sorted(runtimes.keys())

            if provider is not None:
                if provider not in runtimes:
                    return {
                        "error": (
                            f"Provider '{provider}' is not configured or failed to start. "
                            f"Configured providers: {configured}"
                        ),
                        "provider": provider,
                        "mode": mode,
                    }
                return await _sync_single_provider(runtimes[provider], provider, mode)

            # Sync all — return flat result for single provider, aggregated for multi
            if len(runtimes) == 1:
                prov_name = configured[0]
                return await _sync_single_provider(runtimes[prov_name], prov_name, mode)

            results: dict[str, Any] = {}
            for prov_name, runtime in runtimes.items():
                results[prov_name] = await _sync_single_provider(runtime, prov_name, mode)
            return {"results": results, "mode": mode}

        # ------------------------------------------------------------------
        # contacts_sync_status
        # ------------------------------------------------------------------

        @mcp.tool()
        async def contacts_sync_status(
            provider: str | None = None,
        ) -> dict[str, Any]:
            """Return the current contacts sync state.

            Args:
                provider: Provider to query (e.g. 'google', 'telegram').
                          When omitted, returns status for all configured providers.

            Returns:
                A dict with last sync timestamps, cursor age, last error,
                and approximate contact count.
            """
            runtimes = module._runtimes
            if not runtimes:
                return {
                    "error": (
                        "Contacts sync runtime is not running. "
                        "Ensure sync.enabled=true and provider credentials are configured."
                    ),
                    "provider": provider,
                    "sync_enabled": False,
                }

            configured = sorted(runtimes.keys())

            if provider is not None:
                if provider not in runtimes:
                    return {
                        "error": (
                            f"Provider '{provider}' is not configured or failed to start. "
                            f"Configured providers: {configured}"
                        ),
                        "provider": provider,
                    }
                return await _status_single_provider(runtimes[provider], provider)

            # Status for all — flat for single, aggregated for multi
            if len(runtimes) == 1:
                prov_name = configured[0]
                return await _status_single_provider(runtimes[prov_name], prov_name)

            results: dict[str, Any] = {}
            for prov_name, runtime in runtimes.items():
                results[prov_name] = await _status_single_provider(runtime, prov_name)
            return {"providers": results}

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
            runtimes = module._runtimes
            cfg = module._config
            configured_types = cfg.provider_types

            target_types = [provider] if provider is not None else configured_types

            sources: list[dict[str, Any]] = []
            for prov_type in target_types:
                if prov_type not in configured_types:
                    continue

                runtime = runtimes.get(prov_type)
                if runtime is None:
                    sources.append(
                        {
                            "provider": prov_type,
                            "account_id": _DEFAULT_ACCOUNT_ID,
                            "sync_enabled": False,
                            "status": "sync_disabled",
                            "last_success_at": None,
                            "last_error": None,
                        }
                    )
                    continue

                try:
                    state: ContactsSyncState = await runtime._state_store.load(
                        provider=runtime._provider_name,
                        account_id=runtime._account_id,
                    )
                    status = "active" if state.last_error is None else "error"
                    if state.last_success_at is None:
                        status = "never_synced"
                except Exception as exc:
                    logger.warning(
                        "contacts_source_list: state load failed for %s: %s",
                        prov_type,
                        exc,
                        exc_info=True,
                    )
                    state = ContactsSyncState()
                    status = "unknown"

                sources.append(
                    {
                        "provider": runtime._provider_name,
                        "account_id": runtime._account_id,
                        "sync_enabled": True,
                        "status": status,
                        "last_success_at": state.last_success_at,
                        "last_error": state.last_error,
                    }
                )

            return sources

        # ------------------------------------------------------------------
        # contacts_source_reconcile
        # ------------------------------------------------------------------

        @mcp.tool()
        async def contacts_source_reconcile(
            contact_id: str | None = None,
        ) -> dict[str, Any]:
            """Trigger re-evaluation of source links for a contact.

            Schedules an immediate incremental sync across all providers
            to reconcile any stale or missing source links.

            Args:
                contact_id: Optional contact ID to reconcile.  When omitted,
                            triggers reconciliation for all contacts.

            Returns:
                A dict confirming the reconciliation request was queued.
            """
            runtimes = module._runtimes
            if not runtimes:
                return {
                    "error": (
                        "Contacts sync runtime is not running. "
                        "Ensure sync.enabled=true and provider credentials are configured."
                    ),
                    "queued": False,
                }

            for runtime in runtimes.values():
                runtime.trigger_immediate_sync()

            return {
                "queued": True,
                "contact_id": contact_id,
                "providers_triggered": sorted(runtimes.keys()),
                "message": (
                    "Reconciliation queued via immediate sync trigger for all providers. "
                    "The sync runtimes will process all source links on the next cycle."
                    if contact_id is None
                    else (
                        f"Reconciliation queued for contact '{contact_id}' via "
                        "immediate sync trigger. Note: the sync engine currently "
                        "reconciles all source links per cycle; per-contact scoping "
                        "is not yet supported at the engine level."
                    )
                ),
            }

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        """Initialize contacts providers and start sync runtimes.

        Creates one provider and sync runtime per configured provider entry.
        For multi-provider configs, provider failures are isolated: if one
        provider fails to start, others still initialize.  For single-provider
        configs, startup errors propagate for backwards compatibility.

        Parameters
        ----------
        config:
            Module configuration (``ContactsConfig`` or raw dict).
        db:
            Butler database instance.
        credential_store:
            Optional :class:`~butlers.credential_store.CredentialStore`.
        """
        self._config = self._coerce_config(config)
        self._db = db

        for entry in self._config.providers:
            if entry.type not in self._SUPPORTED_PROVIDERS:
                supported = ", ".join(sorted(self._SUPPORTED_PROVIDERS))
                raise RuntimeError(
                    f"Unsupported contacts provider '{entry.type}'. "
                    f"Supported providers: {supported}"
                )

        if not self._config.sync.enabled:
            logger.info("ContactsModule: sync is disabled; skipping runtime startup")
            return

        pool = getattr(db, "pool", None) if db is not None else None

        for entry in self._config.providers:
            try:
                provider = await self._create_provider(
                    entry.type, pool=pool, credential_store=credential_store
                )

                state_store = ContactsSyncStateStore(pool)
                backfill_engine = ContactBackfillEngine(
                    pool,
                    provider=provider.name,
                    account_id=_DEFAULT_ACCOUNT_ID,
                )
                sync_engine = ContactsSyncEngine(
                    provider=provider,
                    state_store=state_store,
                    apply_contact=backfill_engine,
                )

                # For telegram, enrich chat IDs after each sync cycle
                on_cycle_complete = None
                if entry.type == "telegram":
                    tg_provider = provider
                    tg_pool = pool

                    async def _telegram_post_sync(result: ContactsSyncResult) -> None:
                        await _enrich_telegram_chat_ids(tg_provider, tg_pool)

                    on_cycle_complete = _telegram_post_sync

                runtime = ContactsSyncRuntime(
                    sync_engine=sync_engine,
                    state_store=state_store,
                    provider_name=provider.name,
                    account_id=_DEFAULT_ACCOUNT_ID,
                    incremental_interval=timedelta(minutes=self._config.sync.interval_minutes),
                    forced_full_interval=timedelta(days=self._config.sync.full_sync_interval_days),
                    on_cycle_complete=on_cycle_complete,
                )

                await runtime.start()
                self._providers[entry.type] = provider
                self._runtimes[entry.type] = runtime
                logger.info(
                    "ContactsModule: sync runtime started for %s "
                    "(interval=%dm, full_sync_interval=%dd)",
                    entry.type,
                    self._config.sync.interval_minutes,
                    self._config.sync.full_sync_interval_days,
                )
            except Exception:
                logger.exception("ContactsModule: failed to start provider '%s'", entry.type)
                if len(self._config.providers) == 1:
                    raise  # Single provider: propagate for backwards compat

    async def on_shutdown(self) -> None:
        """Stop all sync runtimes and release provider resources."""
        for prov_name, runtime in list(self._runtimes.items()):
            try:
                await runtime.stop()
                logger.info("ContactsModule: sync runtime stopped for %s", prov_name)
            except Exception:
                logger.exception("ContactsModule: error stopping runtime for %s", prov_name)
        self._runtimes.clear()

        for prov_name, provider in list(self._providers.items()):
            try:
                await provider.shutdown()
                logger.info("ContactsModule: provider shut down for %s", prov_name)
            except Exception:
                logger.exception("ContactsModule: error shutting down provider %s", prov_name)
        self._providers.clear()

        self._config = None
        self._db = None

    async def _create_provider(
        self,
        provider_type: str,
        *,
        pool: Any,
        credential_store: Any,
    ) -> ContactsProvider:
        """Create a contacts provider by type."""
        if provider_type == "telegram":
            return await self._create_telegram_provider(pool)
        else:
            client_id, client_secret, refresh_token = await self._resolve_google_credentials(
                credential_store=credential_store
            )
            return GoogleContactsProvider(
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
            )

    async def _resolve_google_credentials(
        self,
        *,
        credential_store: Any,
    ) -> tuple[str, str, str]:
        """Resolve Google OAuth credentials from DB-backed credential store.

        Returns
        -------
        tuple[str, str, str]
            ``(client_id, client_secret, refresh_token)``
        """
        if credential_store is not None:
            client_id = await credential_store.resolve(
                _GOOGLE_OAUTH_CLIENT_ID_KEY, env_fallback=False
            )
            client_secret = await credential_store.resolve(
                _GOOGLE_OAUTH_CLIENT_SECRET_KEY, env_fallback=False
            )

            # Refresh token from shared.contact_info (exclusively)
            refresh_token: str | None = None
            pool = getattr(self._db, "pool", None)
            if pool is not None:
                from butlers.credential_store import resolve_owner_entity_info

                refresh_token = await resolve_owner_entity_info(
                    pool, _GOOGLE_CONTACT_INFO_REFRESH_TYPE
                )

            if client_id and client_secret and refresh_token:
                logger.debug("ContactsModule: resolved Google credentials from CredentialStore")
                return client_id, client_secret, refresh_token

        raise RuntimeError(
            "ContactsModule: Google OAuth credentials are not available in database. "
            f"Required keys: {_GOOGLE_OAUTH_CLIENT_ID_KEY}, {_GOOGLE_OAUTH_CLIENT_SECRET_KEY}, "
            f"refresh token (contact_info). "
            "Store them via the dashboard OAuth flow (shared credential store)."
        )

    async def _create_telegram_provider(self, pool: Any) -> TelegramContactsProvider:
        """Create and validate a TelegramContactsProvider from owner entity_info.

        Resolves telegram_api_id, telegram_api_hash, and telegram_user_session
        from the owner entity's shared.entity_info entries.
        """
        if pool is None:
            raise RuntimeError(
                "ContactsModule: Telegram provider requires a database connection "
                "to resolve credentials from owner entity_info."
            )

        from butlers.credential_store import resolve_owner_entity_info

        _TELEGRAM_CI_TYPES = {
            "telegram_api_id": "API ID",
            "telegram_api_hash": "API hash",
            "telegram_user_session": "user session",
        }

        creds: dict[str, str] = {}
        for ci_type, label in _TELEGRAM_CI_TYPES.items():
            value = await resolve_owner_entity_info(pool, ci_type)
            if value:
                creds[ci_type] = value

        missing = [label for ci_type, label in _TELEGRAM_CI_TYPES.items() if ci_type not in creds]
        if missing:
            raise RuntimeError(
                f"ContactsModule: Telegram credentials missing from owner entity_info: "
                f"{', '.join(missing)}. Configure telegram_api_id, telegram_api_hash, "
                f"and telegram_user_session on the owner entity via the dashboard."
            )

        try:
            api_id = int(creds["telegram_api_id"])
        except ValueError as exc:
            raise RuntimeError(
                f"ContactsModule: invalid telegram_api_id in contact_info: {exc}"
            ) from exc

        provider = TelegramContactsProvider(
            api_id=api_id,
            api_hash=creds["telegram_api_hash"],
            session_string=creds["telegram_user_session"],
        )

        await provider.validate_credentials()
        logger.info("ContactsModule: Telegram provider credentials validated")
        return provider


async def _enrich_telegram_chat_ids(provider: TelegramContactsProvider, pool: Any) -> None:
    """Post-sync enrichment: resolve private chat IDs and write to contact_info.

    Calls provider.enrich_chat_ids() to get {user_id: chat_id} mapping from
    Telegram dialogs, then upserts telegram_chat_id entries in shared.contact_info
    for each contact matched via contacts_source_links.
    """
    try:
        user_to_chat = await provider.enrich_chat_ids(pool)
    except Exception as exc:
        logger.warning("Telegram chat ID enrichment failed: %s", exc, exc_info=True)
        return

    if not user_to_chat:
        return

    enriched = 0
    for user_id, chat_id in user_to_chat.items():
        # Find the local contact via source link
        row = await pool.fetchrow(
            """
            SELECT sl.local_contact_id FROM contacts_source_links sl
            WHERE sl.provider = 'telegram' AND sl.external_contact_id = $1
              AND sl.deleted_at IS NULL
            """,
            str(user_id),
        )
        if row is None:
            continue

        local_contact_id = row["local_contact_id"]
        chat_id_str = str(chat_id)

        # Upsert telegram_chat_id in shared.contact_info
        await pool.execute(
            """
            INSERT INTO shared.contact_info (contact_id, type, value, label, is_primary)
            VALUES ($1, 'telegram_chat_id', $2, NULL, false)
            ON CONFLICT DO NOTHING
            """,
            local_contact_id,
            chat_id_str,
        )
        enriched += 1

    if enriched:
        logger.info("Telegram chat ID enrichment: wrote %d entries", enriched)


__all__ = [
    "ContactBackfillEngine",
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
    "ProviderEntry",
    "TelegramContactsProvider",
]
