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
    account: str | None = None

    @field_validator("type")
    @classmethod
    def _normalize_type(cls, value: str, info: ValidationInfo) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return normalized

    @field_validator("account", mode="before")
    @classmethod
    def _normalize_account(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized if normalized else None


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
        # Allow duplicate provider types only when each entry has a distinct non-None
        # account value (multi-account Google support).  Providers without account
        # disambiguation are still treated as singletons per type.
        type_accounts: list[tuple[str, str | None]] = [(p.type, p.account) for p in self.providers]

        # Check: multiple entries of the same type with no account = ambiguous.
        type_counts: dict[str, int] = {}
        for ptype, _ in type_accounts:
            type_counts[ptype] = type_counts.get(ptype, 0) + 1

        for ptype, count in type_counts.items():
            if count > 1:
                accounts_for_type = [paccount for t, paccount in type_accounts if t == ptype]
                # Raise if any entry lacks an account (ambiguous).
                if any(a is None for a in accounts_for_type):
                    raise ValueError(
                        f"Multiple '{ptype}' providers require distinct 'account' fields "
                        f"for disambiguation. Add 'account = \"email@example.com\"' to each "
                        f"'{ptype}' provider entry."
                    )
                # Raise if accounts are not all distinct.
                if len(set(accounts_for_type)) != len(accounts_for_type):
                    dupes = sorted({a for a in accounts_for_type if accounts_for_type.count(a) > 1})
                    raise ValueError(
                        f"Duplicate '{ptype}' provider entries with the same account: "
                        f"{', '.join(str(d) for d in dupes)}"
                    )
        return self

    @property
    def provider_types(self) -> list[str]:
        """Return list of configured provider type strings."""
        return [p.type for p in (self.providers or [])]


def _filter_runtimes(
    runtimes: dict[str, Any],
    *,
    provider: str | None,
    account: str | None,
) -> dict[str, Any]:
    """Filter the runtimes dict by optional provider type and account.

    Runtime keys are either ``"<type>"`` (single-instance) or
    ``"<type>:<account>"`` (multi-account).

    Filtering rules:
    - ``provider=None, account=None``: all runtimes.
    - ``provider="google", account=None``: all runtimes whose key starts with
      ``"google"``.
    - ``provider="google", account="work@gmail.com"``: the specific runtime
      keyed as ``"google:work@gmail.com"`` (or ``"google"`` if the account_id
      matches directly).
    - ``provider=None, account=X``: all runtimes whose runtime._account_id
      equals X.
    """
    if provider is None and account is None:
        return dict(runtimes)

    result: dict[str, Any] = {}
    for key, rt in runtimes.items():
        # Determine the type portion of the key.
        parts = key.split(":", 1)
        key_type = parts[0]
        key_account = parts[1] if len(parts) > 1 else None

        if provider is not None and key_type != provider:
            continue

        if account is not None:
            # Match by account: either the key suffix or the runtime's account_id.
            rt_account_id = getattr(rt, "_account_id", None)
            if key_account != account and rt_account_id != account:
                continue

        result[key] = rt

    return result


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

            summary: dict[str, Any] = {
                "fetched": result.fetched_contacts,
                "applied": result.applied_contacts,
                "skipped": result.skipped_contacts,
                "deleted": result.deleted_contacts,
            }
            if result.provider_total is not None:
                summary["provider_total"] = result.provider_total

            return {
                "provider": prov_name,
                "mode": result.mode,
                "summary": summary,
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
            account: str | None = None,
            mode: ContactsSyncMode = "incremental",
        ) -> dict[str, Any]:
            """Trigger an immediate contacts sync cycle.

            Args:
                provider: Provider to sync (e.g. 'google', 'telegram').
                          When omitted, syncs all configured providers.
                account: Optional Google account email to sync (e.g. 'work@gmail.com').
                         Only applicable when provider='google'.  When provider='google'
                         is specified without account, all Google account instances sync.
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

            # Resolve which runtimes to target.
            target_runtimes = _filter_runtimes(runtimes, provider=provider, account=account)

            if not target_runtimes and (provider is not None or account is not None):
                return {
                    "error": (
                        f"Provider '{provider}'"
                        + (f" account '{account}'" if account else "")
                        + " is not configured or failed to start. "
                        f"Configured providers: {configured}"
                    ),
                    "provider": provider,
                    "mode": mode,
                }

            # Sync all matched — return flat result for single, aggregated for multi
            if len(target_runtimes) == 1:
                prov_key, rt = next(iter(target_runtimes.items()))
                return await _sync_single_provider(rt, prov_key, mode)

            results: dict[str, Any] = {}
            for prov_key, rt in target_runtimes.items():
                results[prov_key] = await _sync_single_provider(rt, prov_key, mode)
            return {"results": results, "mode": mode}

        # ------------------------------------------------------------------
        # contacts_sync_status
        # ------------------------------------------------------------------

        @mcp.tool()
        async def contacts_sync_status(
            provider: str | None = None,
            account: str | None = None,
        ) -> dict[str, Any]:
            """Return the current contacts sync state.

            Args:
                provider: Provider to query (e.g. 'google', 'telegram').
                          When omitted, returns status for all configured providers.
                account: Optional Google account email to query (e.g. 'work@gmail.com').
                         Only applicable when provider='google'.

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

            # Resolve which runtimes to target.
            target_runtimes = _filter_runtimes(runtimes, provider=provider, account=account)

            if not target_runtimes and (provider is not None or account is not None):
                return {
                    "error": (
                        f"Provider '{provider}'"
                        + (f" account '{account}'" if account else "")
                        + " is not configured or failed to start. "
                        f"Configured providers: {configured}"
                    ),
                    "provider": provider,
                }

            # Status for all — flat for single, aggregated for multi
            if len(target_runtimes) == 1:
                prov_key, rt = next(iter(target_runtimes.items()))
                return await _status_single_provider(rt, prov_key)

            results: dict[str, Any] = {}
            for prov_key, rt in target_runtimes.items():
                results[prov_key] = await _status_single_provider(rt, prov_key)
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

            # If provider filter was specified but the provider type is not in
            # configured_types, return empty (not configured at all).
            if provider is not None and provider not in configured_types:
                return []

            # For configured but not-started providers (sync disabled), show entries.
            sources: list[dict[str, Any]] = []

            if provider is not None:
                # Check configured entries for this type.
                cfg_entries = [e for e in (cfg.providers or []) if e.type == provider]
                if not cfg_entries:
                    return []
                for entry in cfg_entries:
                    # Build the runtime key matching how on_startup keyed it.
                    is_multi = sum(1 for e in (cfg.providers or []) if e.type == provider) > 1
                    if is_multi and entry.account:
                        rt_key = f"{entry.type}:{entry.account}"
                    else:
                        rt_key = entry.type
                    acc_id = entry.account or _DEFAULT_ACCOUNT_ID
                    runtime = runtimes.get(rt_key)
                    if runtime is None:
                        sources.append(
                            {
                                "provider": entry.type,
                                "account_id": acc_id,
                                "sync_enabled": False,
                                "status": "sync_disabled",
                                "last_success_at": None,
                                "last_error": None,
                            }
                        )
                        continue
                    await _append_runtime_source(sources, runtime, rt_key)
            else:
                # All configured entries
                for entry in cfg.providers or []:
                    is_multi = sum(1 for e in (cfg.providers or []) if e.type == entry.type) > 1
                    if is_multi and entry.account:
                        rt_key = f"{entry.type}:{entry.account}"
                    else:
                        rt_key = entry.type
                    acc_id = entry.account or _DEFAULT_ACCOUNT_ID
                    runtime = runtimes.get(rt_key)
                    if runtime is None:
                        sources.append(
                            {
                                "provider": entry.type,
                                "account_id": acc_id,
                                "sync_enabled": False,
                                "status": "sync_disabled",
                                "last_success_at": None,
                                "last_error": None,
                            }
                        )
                        continue
                    await _append_runtime_source(sources, runtime, rt_key)

            return sources

        async def _append_runtime_source(
            sources: list[dict[str, Any]], runtime: Any, rt_key: str
        ) -> None:
            """Helper: load state and append a source entry."""
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
                    rt_key,
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

        # Count how many times each provider type appears to determine keying strategy.
        type_counts: dict[str, int] = {}
        for entry in self._config.providers:
            type_counts[entry.type] = type_counts.get(entry.type, 0) + 1

        for entry in self._config.providers:
            # Derive the runtime key and account_id for this entry.
            # For multi-account providers (type appears more than once), use
            # "<type>:<account>" as the key and the account email as account_id.
            # For single-instance providers, use just "<type>" and "default".
            account_id: str
            runtime_key: str
            if type_counts[entry.type] > 1:
                # Multi-account: account must be set (validated by ContactsConfig)
                account_id = entry.account or _DEFAULT_ACCOUNT_ID
                runtime_key = f"{entry.type}:{account_id}"
            else:
                account_id = entry.account or _DEFAULT_ACCOUNT_ID
                runtime_key = entry.type

            try:
                provider = await self._create_provider(
                    entry.type,
                    account=entry.account,
                    pool=pool,
                    credential_store=credential_store,
                )

                state_store = ContactsSyncStateStore(pool)
                backfill_engine = ContactBackfillEngine(
                    pool,
                    provider=provider.name,
                    account_id=account_id,
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
                    account_id=account_id,
                    incremental_interval=timedelta(minutes=self._config.sync.interval_minutes),
                    forced_full_interval=timedelta(days=self._config.sync.full_sync_interval_days),
                    on_cycle_complete=on_cycle_complete,
                )

                await runtime.start()
                self._providers[runtime_key] = provider
                self._runtimes[runtime_key] = runtime
                logger.info(
                    "ContactsModule: sync runtime started for %s (account=%s, "
                    "interval=%dm, full_sync_interval=%dd)",
                    entry.type,
                    account_id,
                    self._config.sync.interval_minutes,
                    self._config.sync.full_sync_interval_days,
                )
            except Exception:
                logger.exception(
                    "ContactsModule: failed to start provider '%s' (account=%s)",
                    entry.type,
                    entry.account,
                )
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
        account: str | None = None,
        pool: Any,
        credential_store: Any,
    ) -> ContactsProvider:
        """Create a contacts provider by type."""
        if provider_type == "telegram":
            return await self._create_telegram_provider(pool)
        else:
            client_id, client_secret, refresh_token = await self._resolve_google_credentials(
                account=account,
                pool=pool,
                credential_store=credential_store,
            )
            return GoogleContactsProvider(
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
            )

    async def _resolve_google_credentials(
        self,
        *,
        account: str | None = None,
        pool: Any = None,
        credential_store: Any,
    ) -> tuple[str, str, str]:
        """Resolve Google OAuth credentials from DB-backed credential store.

        When *account* is provided, credentials are resolved for that specific
        Google account from ``shared.google_accounts`` and its companion
        entity_info.  Otherwise, the primary account (via owner entity_info) is
        used for backward compatibility.

        Parameters
        ----------
        account:
            Optional email of the Google account to resolve credentials for.
        pool:
            asyncpg pool for direct DB lookups.  When ``None``, only the
            credential_store path is attempted.
        credential_store:
            A CredentialStore instance.  Required; raises if ``None``.

        Returns
        -------
        tuple[str, str, str]
            ``(client_id, client_secret, refresh_token)``
        """
        if credential_store is None:
            raise RuntimeError(
                "ContactsModule: Google OAuth credentials require a shared credential store. "
                f"Required keys: {_GOOGLE_OAUTH_CLIENT_ID_KEY}, {_GOOGLE_OAUTH_CLIENT_SECRET_KEY}, "
                "refresh token (contact_info). "
                "Store them via the dashboard OAuth flow (shared credential store)."
            )

        client_id = await credential_store.resolve(_GOOGLE_OAUTH_CLIENT_ID_KEY, env_fallback=False)
        client_secret = await credential_store.resolve(
            _GOOGLE_OAUTH_CLIENT_SECRET_KEY, env_fallback=False
        )

        refresh_token: str | None = None

        if pool is not None and account:
            # Account-aware: resolve refresh token from the companion entity
            # for the specific Google account row in shared.google_accounts.
            from butlers.google_account_registry import (
                GoogleAccountNotFoundError,
                get_google_account,
            )
            from butlers.google_account_registry import (
                MissingGoogleCredentialsError as _RegistryMissingError,
            )

            try:
                google_account = await get_google_account(pool, account)
            except (GoogleAccountNotFoundError, _RegistryMissingError) as exc:
                raise RuntimeError(
                    f"ContactsModule: Google account '{account}' is not connected. "
                    "Connect the account via the dashboard OAuth flow and re-start."
                ) from exc

            # Scope validation: the account must have granted contacts access.
            granted = google_account.granted_scopes or []
            has_contacts_scope = any("contacts" in s.lower() for s in granted)
            if not has_contacts_scope:
                raise RuntimeError(
                    f"ContactsModule: Google account '{account}' has not granted "
                    "Contacts scope. Re-authorize the account with Contacts access via "
                    "the dashboard OAuth flow."
                )

            # Fetch the refresh token from the companion entity's entity_info.
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT value FROM shared.entity_info
                    WHERE entity_id = $1 AND type = 'google_oauth_refresh'
                    LIMIT 1
                    """,
                    google_account.entity_id,
                )
                if row is not None:
                    refresh_token = row["value"]
        elif pool is not None:
            # Backward compat: primary account via owner entity_info.
            from butlers.credential_store import resolve_owner_entity_info

            refresh_token = await resolve_owner_entity_info(pool, _GOOGLE_CONTACT_INFO_REFRESH_TYPE)
        else:
            # No pool: fall back to owner entity_info via db.pool
            db_pool = getattr(self._db, "pool", None)
            if db_pool is not None:
                from butlers.credential_store import resolve_owner_entity_info

                refresh_token = await resolve_owner_entity_info(
                    db_pool, _GOOGLE_CONTACT_INFO_REFRESH_TYPE
                )

        if client_id and client_secret and refresh_token:
            logger.debug(
                "ContactsModule: resolved Google credentials (account=%s)",
                account or "primary",
            )
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
