"""Butler daemon lifecycle helpers — startup and shutdown sequences.

Extracted from daemon.py to reduce its size.  The two entry points are:

* :func:`run_startup` — full startup sequence (see :func:`run_startup` for the step breakdown)
* :func:`run_shutdown` — graceful shutdown sequence

Both functions accept a :class:`~butlers.daemon.ButlerDaemon` instance typed as
``Any`` at runtime to avoid a circular import between ``daemon.py`` and this
module.  Callers should pass ``self`` from within ``ButlerDaemon.start()`` /
``ButlerDaemon.shutdown()``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from butlers.catalogue_bootstrap import upsert_provider_feature_catalogue
from butlers.config import (
    ButlerType,
    load_config,
)
from butlers.core.approvals_policy import (
    AttentionReadUnavailable,
    get_approvals_policy_quiet_hours,
)
from butlers.core.general_settings import resolve_general_timezone
from butlers.core.logging import resolve_log_root
from butlers.core.metrics import init_metrics
from butlers.core.runtimes import DEFAULT_RUNTIME_TYPE
from butlers.core.runtimes.registry import build_runtime_adapter
from butlers.core.scheduler import sync_schedules
from butlers.core.skills import get_skills_dir
from butlers.core.spawner import Spawner
from butlers.core.telemetry import init_telemetry
from butlers.credentials import (
    detect_secrets,
    validate_credentials,
    validate_module_credentials_async,
)
from butlers.daemon_utils import _flatten_config_for_secret_scan
from butlers.db import Database
from butlers.exceptions import RuntimeBinaryNotFoundError
from butlers.migrations import get_module_migration_chains, has_butler_chain, run_migrations
from butlers.module_state import ModuleStartupStatus
from butlers.owner_bootstrap import _ensure_owner_entity
from butlers.storage import BlobStorageStartupError, S3BlobStore

logger = logging.getLogger(__name__)


async def _sync_toml_schedules(daemon: Any, pool: Any, credential_store: Any) -> None:
    """Sync the butler's TOML schedules, with each schedule's timezone and the quiet policy."""
    # Staffer-typed agents skip daily_briefing_contribution schedule entries
    # per the staffer-archetype spec (briefing exclusion decision point).
    _is_staffer = daemon.config.type == ButlerType.STAFFER
    schedules = [
        s
        for s in daemon.config.schedules
        if not (_is_staffer and s.job_name == "daily_briefing_contribution")
    ]
    # Interpret hour-pinned crons in the owner's configured timezone (failing
    # open to UTC) so e.g. a daily "5 1 * * *" fires at 01:05 local, not 01:05
    # UTC. Resolved from the shared general settings via the credential store.
    default_timezone = await resolve_general_timezone(credential_store.shared_pool)
    # Owner quiet window, used only for the warn-only always-suppressed schedule lint.
    quiet_policy = None
    if credential_store.shared_pool is not None:
        try:
            quiet_policy = await get_approvals_policy_quiet_hours(credential_store.shared_pool)
        except AttentionReadUnavailable:
            # This optional lint never authorizes delivery; actual delivery
            # consumers retain the strict unavailable-read refusal.
            logger.warning("TOML schedule advisory quiet-window lint unavailable")
    await sync_schedules(
        pool,
        schedules,
        stagger_key=daemon.config.name,
        skills_dir=get_skills_dir(daemon.config_dir),
        default_timezone=default_timezone,
        quiet_policy=quiet_policy,
    )


@dataclass
class StartupState:
    """Values produced and consumed by the ordered startup phases.

    The daemon retains long-lived resources; this state carries only the locals
    shared between phases. Each run gets a fresh state, and a fatal error stops
    the sequence before any phase can consume an uninitialized prerequisite.
    """

    profile: Any = None
    log_root: Path | None = None
    module_creds: dict[str, list[str]] = field(default_factory=dict)
    pool: Any = None
    db_url: str = ""
    migration_schema: str | None = None
    credential_store: Any = None
    started_modules: list[Any] = field(default_factory=list)


Phase = Callable[[Any, StartupState], Awaitable[None]]


async def _startup_config(daemon: Any, state: StartupState) -> None:
    """Load config and logging; retain the selected control profile and log root."""
    # Only an explicitly installed startup selection activates this boundary.
    state.profile = vars(daemon).get("_control_profile")
    if state.profile is not None:
        from butlers.control import check_private_key_absent

        check_private_key_absent(state.profile)

    # 1. Load config (skip if pre-set, e.g. by e2e fixtures)
    if daemon.config is None:
        daemon.config = load_config(daemon.config_dir)

    if state.profile is not None and daemon.config.name != "switchboard":
        raise ValueError("control profile requires the switchboard config")

    # 1b. Configure structured logging for this butler
    from butlers.core.logging import configure_logging

    state.log_root = resolve_log_root(daemon.config.logging.log_root)
    configure_logging(
        level=daemon.config.logging.level,
        fmt=daemon.config.logging.format,
        log_root=state.log_root,
        butler_name=daemon.config.name,
    )
    logger.info("Loaded config for butler: %s", daemon.config.name)

    # 1c. Blob storage initialization is deferred to step 8c (after
    # CredentialStore is available) so S3 credentials can be resolved
    # from the database rather than requiring environment variables.


async def _startup_telemetry(daemon: Any, state: StartupState) -> None:
    """Initialize telemetry/metrics and warn about inline secrets."""
    # 2. Initialize telemetry and metrics
    init_telemetry(f"butler.{daemon.config.name}")
    init_metrics(f"butler.{daemon.config.name}")

    # 2.5. Detect inline secrets in config
    config_values = _flatten_config_for_secret_scan(daemon.config)
    secret_warnings = detect_secrets(config_values)
    for warning in secret_warnings:
        logger.warning(warning)


async def _startup_modules(daemon: Any, state: StartupState) -> None:
    """Select declared modules in dependency order."""
    # 3. Construct only declared modules and reject unknown names, missing
    # declared dependencies and cycles before provisioning or module effects.
    daemon._modules = daemon._registry.load_from_config(daemon.config.modules)


async def _startup_module_config(daemon: Any, state: StartupState) -> None:
    """Validate module configs, retaining isolated module failures."""
    # 4. Validate module config schemas (non-fatal per-module).
    daemon._module_configs = daemon._validate_module_configs()


async def _startup_credentials(daemon: Any, state: StartupState) -> None:
    """Collect module credential requirements and validate butler environment."""
    # 5. Validate butler.env credentials (env-only fast-fail for non-secret config).
    # Module credentials are validated later (step 8b) after the DB pool is
    # available, so DB-stored secrets are visible.
    state.module_creds = daemon._collect_module_credentials()
    validate_credentials(
        daemon.config.env_required,
        daemon.config.env_optional,
    )


async def _startup_database(daemon: Any, state: StartupState) -> None:
    """Acquire the database pool, establish owner identity and attach logging."""
    # 6. Provision database
    # If db was injected (e.g., for testing), skip provisioning
    if daemon.db is None:
        daemon.db = Database.from_env(daemon.config.db_name)
        daemon.db.set_schema(daemon.config.db_schema)
        if daemon.config.db_schema:
            daemon.db.role = f"butler_{daemon.config.db_schema}_rw"
        await daemon.db.provision()
        state.pool = await daemon.db.connect()
    else:
        # Database already provisioned and connected externally
        state.pool = daemon.db.pool
        if state.pool is None:
            raise RuntimeError("Injected Database must already be connected")
    daemon.db.owner_butler = daemon.config.name
    if state.profile is not None:
        from butlers.control import check_stored_private_key_absent

        await check_stored_private_key_absent(state.pool)

    # 6b. Attach the butler-scoped DB log handler so /api/butlers/{name}/logs
    # surfaces live application logs.  The handler filters by butler context
    # (set in step 1b via configure_logging), so multi-butler-in-process does
    # not cross-contaminate.  Migrations in step 7 will populate butler_logs
    # the first time the schema is provisioned; until then writes will fail
    # quietly and be swallowed by ButlerLogger.
    from butlers.core.butler_logging import ButlerDBLogHandler, ButlerLogger

    db_log_schema = daemon.config.db_schema or daemon.config.name
    butler_db_logger = ButlerLogger(pool=state.pool, schema=db_log_schema)
    daemon._db_log_handler = ButlerDBLogHandler(
        butler_logger=butler_db_logger,
        butler_name=daemon.config.name,
    )
    logging.getLogger().addHandler(daemon._db_log_handler)


async def _startup_core_migrations(daemon: Any, state: StartupState) -> None:
    """Set migration inputs and run the core and butler-specific chains."""
    # 7. Run core Alembic migrations
    state.db_url = daemon._build_db_url()
    state.migration_schema = daemon.config.db_schema or None
    await run_migrations(state.db_url, chain="core", schema=state.migration_schema)

    # 7b. Run butler-specific Alembic migrations (if chain exists)
    if has_butler_chain(daemon.config.name):
        logger.info("Running butler-specific migrations for: %s", daemon.config.name)
        await run_migrations(state.db_url, chain=daemon.config.name, schema=state.migration_schema)


async def _startup_module_migrations(daemon: Any, state: StartupState) -> None:
    """Run module migrations and cascade isolated migration failures."""
    # 8. Run module Alembic migrations (non-fatal per-module)
    chains = get_module_migration_chains(daemon._modules, daemon.config.name)
    for mod in daemon._modules:
        if mod.name in daemon._module_statuses:
            continue
        rev = chains.get(mod.name)
        if rev:
            # A module MAY declare a private schema (validated config field
            # ``memory_schema`` on the memory module) so its chain migrates into
            # a dedicated schema instead of the butler's own — e.g. chronicler
            # routes memory to ``chronicler_mem`` so the memory ``episodes``
            # table does not collide with the domain ``chronicler.episodes``
            # (bu-93y4rt / bu-w6jca). ``env.py`` auto-creates the target schema,
            # so no extra provisioning is needed. Falls back to the butler
            # schema for every other module.
            mod_cfg = daemon._module_configs.get(mod.name)
            module_migration_schema = (
                getattr(mod_cfg, "memory_schema", None) or state.migration_schema
            )
            try:
                await run_migrations(state.db_url, chain=rev, schema=module_migration_schema)
            except Exception as exc:
                error_msg = str(exc)
                daemon._module_statuses[mod.name] = ModuleStartupStatus(
                    status="failed", phase="migration", error=error_msg
                )
                logger.warning("Module '%s' disabled: migration failed: %s", mod.name, error_msg)
    daemon._cascade_module_failures()


async def _startup_credential_store(daemon: Any, state: StartupState) -> None:
    """Build credentials and validate the healthy modules' daemon requirements."""
    # 8b. Create layered CredentialStore and validate module credentials
    # (non-fatal per-module).
    # DB pool is now available so DB-stored credentials are visible to resolve().
    # Only validate credentials for modules that haven't already failed (e.g. from
    # migration errors), to avoid redundant DB queries and overwriting earlier failure
    # statuses with spurious credential failures.
    state.credential_store = await daemon._build_credential_store(state.pool)
    daemon._credential_store = state.credential_store
    active_module_creds_for_validation = {
        k: v
        for k, v in state.module_creds.items()
        if k.split(".")[0] not in daemon._module_statuses
    }
    module_cred_failures = await validate_module_credentials_async(
        active_module_creds_for_validation, state.credential_store
    )
    for mod_key, missing_vars in module_cred_failures.items():
        # mod_key may be "modname" or "modname.scope" — map to root module.
        root_mod = mod_key.split(".")[0]
        error_msg = f"Missing credential(s): {', '.join(missing_vars)}"
        daemon._module_statuses[root_mod] = ModuleStartupStatus(
            status="failed", phase="credentials", error=error_msg
        )
        logger.warning("Module '%s' disabled: %s", root_mod, error_msg)
    daemon._cascade_module_failures()


async def _startup_blob_storage(daemon: Any, state: StartupState) -> None:
    """Initialize optional blob storage from the credential store."""
    # 8c. Initialize S3-compatible blob storage.
    # All S3 parameters are resolved from CredentialStore (DB-only, no env
    # fallback) — managed via the dashboard secrets UI at /secrets.
    s3_endpoint = await state.credential_store.resolve("BLOB_S3_ENDPOINT_URL", env_fallback=False)
    s3_bucket = await state.credential_store.resolve("BLOB_S3_BUCKET", env_fallback=False)
    s3_region = await state.credential_store.resolve("BLOB_S3_REGION", env_fallback=False)
    s3_access_key = await state.credential_store.resolve(
        "BLOB_S3_ACCESS_KEY_ID", env_fallback=False
    )
    s3_secret_key = await state.credential_store.resolve(
        "BLOB_S3_SECRET_ACCESS_KEY", env_fallback=False
    )
    if not s3_endpoint or not s3_bucket:
        logger.warning(
            "S3 blob storage not configured (missing BLOB_S3_ENDPOINT_URL / "
            "BLOB_S3_BUCKET). Blob operations will fail at runtime. Configure "
            "via the dashboard secrets UI (/secrets)."
        )
        daemon.blob_store = None
    else:
        blob_store = S3BlobStore(
            bucket=s3_bucket,
            butler_name=daemon.config.name,
            endpoint_url=s3_endpoint,
            access_key_id=s3_access_key,
            secret_access_key=s3_secret_key,
            region=s3_region or "us-east-1",
        )
        try:
            await blob_store.startup_check()
        except BlobStorageStartupError as exc:
            logger.warning(
                "S3 blob storage unavailable; blob operations will fail at runtime. "
                "Check /api/settings/blob-storage/test and the BLOB_S3_* secrets: %s",
                exc,
            )
            daemon.blob_store = None
        else:
            daemon.blob_store = blob_store


async def _startup_cli_auth(daemon: Any, state: StartupState) -> None:
    """Restore CLI tokens and record the Codex baseline, best-effort."""
    # 8c2. Restore CLI auth tokens from DB to filesystem (non-fatal).
    #      Ensures LLM runtime CLIs have their auth files (e.g. OpenCode's
    #      auth.json) written to disk before the spawner tries to invoke them.
    try:
        from butlers.cli_auth.persistence import restore_tokens

        results = await restore_tokens(
            state.credential_store,
            codex_authority=state.credential_store,
        )
        restored = sum(1 for v in results.values() if v)
        if restored:
            logger.info("Restored %d CLI auth token(s) from DB", restored)

        # Record the auth.json baseline for the codex provider so that the
        # first post-startup invocation does not falsely detect a rotation.
        # The baseline must be recorded *after* restore_tokens writes the file.
        if results.get("codex"):
            try:
                from butlers.cli_auth.registry import PROVIDERS
                from butlers.core.runtimes._codex_auth_sync import record_auth_baseline

                codex_provider = PROVIDERS.get("codex")
                if codex_provider is not None and codex_provider.token_path is not None:
                    record_auth_baseline(codex_provider.token_path)
            except Exception:
                logger.debug("codex_auth_sync: baseline recording skipped", exc_info=True)
    except Exception:
        # A failed authority read can retain DB bind context.  Startup may
        # continue for non-Codex providers, but must not disclose it or
        # repopulate Codex from a local schema credential.
        logger.warning("CLI auth token restoration skipped safely")


async def _startup_shared_bootstrap(daemon: Any, state: StartupState) -> None:
    """Bootstrap owner, provider catalogue and domain-event contracts together."""
    # 8d. Bootstrap owner entity (idempotent; non-fatal).
    #     Ensures owner entity exists in public.entities.
    await _ensure_owner_entity(state.pool)

    # 8d2. Seed provider feature catalogue (idempotent; non-fatal).
    #      UPSERTs the canonical known-provider rows into
    #      public.provider_feature_catalogue so the WhatBreaks affordance on
    #      /secrets has server-side data from the first boot onward.
    await upsert_provider_feature_catalogue(state.pool)

    # 8d3. Materialize this butler's own domain-event contracts (idempotent;
    #      non-fatal). The declarations live in roster/<butler>/
    #      domain_events.toml and are the source of truth; this projects them
    #      into public.domain_event_contracts so other butlers and the
    #      dashboard can read what this namespace promises. Admission control
    #      reads git, never this table, so a skipped projection narrows what
    #      is *visible*, never what is permitted.
    try:
        from butlers.core.domain_event_contracts import materialize_own_contracts

        materialized = await materialize_own_contracts(state.pool, publisher=daemon.config.name)
        if materialized:
            logger.info(
                "Materialized %d domain-event contract(s) for %s",
                len(materialized),
                daemon.config.name,
            )
    except Exception:
        logger.warning(
            "materialize_own_contracts failed for butler=%s (best-effort, startup continues)",
            daemon.config.name,
            exc_info=True,
        )


async def _startup_session_recovery(daemon: Any, state: StartupState) -> None:
    """Recover orphaned sessions, best-effort."""
    # 8e. Recover orphaned sessions from a previous daemon run.
    #     Any sessions row with completed_at IS NULL at startup is necessarily
    #     orphaned — this daemon is the sole writer and is just booting. Without
    #     this sweep, chronicler projects orphans as open work episodes that
    #     never close (visible as multi-day-old "in-progress" sessions on the
    #     chronicles dashboard).  Best-effort: never blocks startup.
    try:
        from butlers.core.sessions import recover_orphaned_sessions

        await recover_orphaned_sessions(state.pool)
    except Exception:
        logger.warning(
            "recover_orphaned_sessions failed for butler=%s (best-effort, startup continues)",
            daemon.config.name,
            exc_info=True,
        )


async def _startup_runtime_config(daemon: Any, state: StartupState) -> None:
    """Resolve the required runtime configuration before schedules or modules."""
    # 9. Resolve runtime config (seed DB operational fields from toml on first boot).
    # Creates the RuntimeConfigAccessor and seeds the runtime_config table
    # if this is the first boot. Git remains authoritative for core_groups;
    # runtime_config may preserve only an explicitly reasoned strict subset.
    from butlers.core.runtime_config import RuntimeConfigAccessor

    schema = daemon.config.db_schema or daemon.config.name
    daemon._runtime_config_accessor = RuntimeConfigAccessor(state.pool, schema)
    effective_runtime = await daemon._runtime_config_accessor.seed_if_empty(
        daemon.config.runtime_seed, daemon.config.name
    )
    daemon.db.runtime_config_accessor = daemon._runtime_config_accessor
    if effective_runtime.seeded_at == effective_runtime.updated_at:
        logger.info("Seeded runtime config from butler.toml for %s", daemon.config.name)
    else:
        logger.info(
            "Using runtime config from DB for %s (seeded %s, updated %s)",
            daemon.config.name,
            effective_runtime.seeded_at,
            effective_runtime.updated_at,
        )


async def _startup_schedules(daemon: Any, state: StartupState) -> None:
    """Sync TOML schedule provenance before module-default recovery."""
    # 10. Sync TOML schedules before modules register their defaults. This makes
    # the TOML-orphan state observable to the module-default recovery boundary.
    await _sync_toml_schedules(daemon, state.pool, state.credential_store)


async def _startup_module_startup(daemon: Any, state: StartupState) -> None:
    """Start healthy modules and retain them for audit wiring."""
    # 11. Call module on_startup (non-fatal per-module)
    state.started_modules = []
    for mod in daemon._modules:
        if mod.name in daemon._module_statuses:
            continue
        try:
            validated_config = daemon._module_configs.get(mod.name)
            await mod.on_startup(
                validated_config, daemon.db, state.credential_store, blob_store=daemon.blob_store
            )
            state.started_modules.append(mod)
        except Exception as exc:
            error_msg = str(exc)
            daemon._module_statuses[mod.name] = ModuleStartupStatus(
                status="failed", phase="startup", error=error_msg
            )
            logger.warning("Module '%s' disabled: on_startup failed: %s", mod.name, error_msg)
            daemon._cascade_module_failures()


async def _startup_spawner(daemon: Any, state: StartupState) -> None:
    """Create the runtime and spawner, wire audit and connect Switchboard."""
    # 12. Create Spawner with runtime adapter (verify binary on PATH)
    runtime = build_runtime_adapter(
        DEFAULT_RUNTIME_TYPE,
        codex_authority=state.credential_store,
        butler_name=daemon.config.name,
        log_root=state.log_root,
    )

    binary = runtime.binary_name
    if not shutil.which(binary):
        raise RuntimeBinaryNotFoundError(
            f"Runtime binary {binary!r} not found on PATH. "
            f"The {DEFAULT_RUNTIME_TYPE!r} runtime requires {binary!r} to be installed."
        )

    # 12a. Set up audit pool for daemon-side audit logging
    audit_pool = await daemon._create_audit_pool(state.pool)
    # Expose the Switchboard-schema pool to the scheduler loop so it can gate
    # scheduled dispatch on butler_registry.eligibility_state (paused/quarantined
    # butlers must not fire cron/deadline ticks).
    daemon._audit_pool = audit_pool

    # 12a-ii. Wire audit pool into modules that emit egress audit entries
    # (telegram_send, gmail_send, google_calendar_write).  This is a post-startup
    # hook so modules receive the pool after it is created, without altering the
    # on_startup signature or ordering.
    for mod in state.started_modules:
        try:
            mod.wire_audit_pool(audit_pool)
        except Exception:
            logger.debug(
                "wire_audit_pool failed for module '%s' (non-fatal)", mod.name, exc_info=True
            )

    daemon.spawner = Spawner(
        config=daemon.config,
        config_dir=daemon.config_dir,
        pool=state.pool,
        runtime=runtime,
        audit_pool=audit_pool,
        credential_store=state.credential_store,
        runtime_config_accessor=daemon._runtime_config_accessor,
    )

    # 12b. Wire message classification pipeline for switchboard modules
    daemon._wire_pipelines(state.pool)

    # 12c. Open MCP client connection to Switchboard (non-switchboard butlers)
    await daemon._connect_switchboard()


async def _startup_core_tools(daemon: Any, state: StartupState) -> None:
    """Create FastMCP and register core tools."""
    # 13. Create FastMCP and register core tools
    from butlers.core.owner_creation import OwnerCreationMiddleware

    daemon.mcp = FastMCP(daemon.config.name)
    daemon.mcp.add_middleware(OwnerCreationMiddleware(daemon.config.name))
    daemon._register_core_tools()


async def _startup_module_tools(daemon: Any, state: StartupState) -> None:
    """Register module tools, gates and runtime state before serving requests."""
    # 14. Register module MCP tools (non-fatal per-module)
    await daemon._register_module_tools()

    # 14b. Apply approval gates to configured gated tools
    daemon._gated_tool_originals = await daemon._apply_approval_gates()

    # 14b.1. Snapshot only the final post-approval FastMCP definitions.
    await daemon._finalize_tool_catalog()

    # 14c. Wire calendar overlap-approval enqueuer when both modules are loaded
    daemon._wire_calendar_approval_enqueuer()

    # 14d. Wire spawner + switchboard_client into modules that define wire_runtime().
    # Must run after _connect_switchboard() (step 12c) so that switchboard_client
    # is already set, and after register_tools() (step 14) so that module state
    # is fully initialised before the runtime references are injected.
    daemon._wire_module_runtime()

    # Mark remaining modules as active
    for mod in daemon._modules:
        if mod.name not in daemon._module_statuses:
            daemon._module_statuses[mod.name] = ModuleStartupStatus(status="active")

    # 14e. Initialize module runtime states (enabled/disabled) from state store
    await daemon._init_module_runtime_states(state.pool)

    # 14f. The fenced approval-delivery worker is daemon-owned but remains
    # dormant until the trusted recovery-only Messenger runtime is installed.
    from butlers.core.approval_delivery_worker import start_approval_delivery_worker

    await start_approval_delivery_worker(daemon)


async def _startup_mcp_server(daemon: Any, state: StartupState) -> None:
    """Bind the MCP server, schedule warmup and start the durable buffer."""
    # 15. Start FastMCP SSE server on configured port
    await daemon._start_mcp_server()

    # 15b. Warm up MCP endpoints (best-effort, non-blocking for daemon boot).
    # Fires initialize + tools/list against the butler's own endpoint (and any
    # extra endpoints) so the first real Codex spawn hits warm server-side
    # caches/pools instead of cold ones.  Failures are logged at WARNING level
    # and never propagate — the warmup task runs in the background so it does
    # not hold up the remaining startup steps.
    asyncio.create_task(
        _warmup_mcp_endpoints_best_effort(daemon),
        name=f"mcp-warmup-{daemon.config.name}",
    )

    # 15c. Start durable buffer workers and scanner (switchboard only)
    if daemon._buffer is not None:
        await daemon._buffer.start()


async def _startup_background_services(daemon: Any, state: StartupState) -> None:
    """Launch route recovery, Switchboard heartbeat and scheduler tasks."""
    # 16a. Recover eligible route_inbox rows (non-staffer butlers only).
    # Accepted work and stale processing leases are recovered in a background
    # task so long-running LLM sessions do not block startup. Reclaimed
    # dashboard processing turns reconcile their durable predecessor and
    # suppress automatic replay when it cannot be proven terminal.
    # Staffers (switchboard, messenger) have their own durable routing mechanisms
    # and do not use route_inbox for crash recovery.
    if daemon.config.type != ButlerType.STAFFER and daemon.spawner is not None:
        daemon._route_inbox_recovery_task = asyncio.create_task(
            daemon._recover_route_inbox(state.pool)
        )

    # 16b. Launch switchboard heartbeat (non-switchboard butlers only)
    if daemon.config.switchboard_url is not None:
        daemon._switchboard_heartbeat_task = asyncio.create_task(
            daemon._switchboard_heartbeat_loop()
        )

    # 16c. Start internal scheduler loop
    daemon._scheduler_loop_task = asyncio.create_task(daemon._scheduler_loop())


async def _startup_readiness(daemon: Any, state: StartupState) -> None:
    """Start legacy reporting and gate acceptance on committed boot registration."""
    # 17. Legacy reporting is retained for flag-off and rollback deployments.
    # Receiver-derived mode uses the independently supervised Dashboard observer;
    # an unauthenticated daemon POST cannot renew that receiver evidence.
    if os.environ.get("BUTLERS_RECEIVER_DERIVED_ROUTE_CUTOVER") != "1":
        daemon._liveness_reporter_task = asyncio.create_task(daemon._liveness_reporter_loop())

    # The port is bound and local services are ready, but receiver-visible
    # route acceptance requires a committed L1 epoch.  A daemon that started
    # before Switchboard seeded its registry row stays not-ready and retries
    # with the *same* process UUID; it never invents success from a failed DB
    # call or turns a later retry into a phantom successor.
    registered = await daemon._register_boot_epoch()
    daemon._accepting_connections = registered
    if not registered:
        daemon._boot_registration_task = asyncio.create_task(
            daemon._retry_boot_registration(), name=f"boot-register-{daemon.config.name}"
        )
    # Publish only the actual final wrapped registry after startup completed.
    # The administrative D4 control defaults OFF; no ingress is enabled here.
    from butlers.core.catalog_publication import start_catalog_publication

    if registered:
        await start_catalog_publication(daemon, state.pool)
    daemon._started_at = time.monotonic()

    failed_count = sum(1 for s in daemon._module_statuses.values() if s.status != "active")
    if failed_count:
        logger.warning(
            "Butler %s started on port %d with %d failed module(s)",
            daemon.config.name,
            daemon.config.port,
            failed_count,
        )
    else:
        logger.info("Butler %s started on port %d", daemon.config.name, daemon.config.port)


# RFC 0001's 17 stages, with phase 8 split at its existing dependency boundaries.
# Keep these functions in this module: startup dependencies are patched here by
# daemon tests, and the shared bootstrap phase is the future init-job boundary.
STARTUP_PHASES: list[Phase] = [
    _startup_config,
    _startup_telemetry,
    _startup_modules,
    _startup_module_config,
    _startup_credentials,
    _startup_database,
    _startup_core_migrations,
    _startup_module_migrations,
    _startup_credential_store,
    _startup_blob_storage,
    _startup_cli_auth,
    _startup_shared_bootstrap,
    _startup_session_recovery,
    _startup_runtime_config,
    _startup_schedules,
    _startup_module_startup,
    _startup_spawner,
    _startup_core_tools,
    _startup_module_tools,
    _startup_mcp_server,
    _startup_background_services,
    _startup_readiness,
]


async def run_startup(daemon: Any) -> None:
    """Execute the ordered startup phases with fresh per-run state.

    Fatal errors propagate and prevent later phases. Each module phase retains
    its own non-fatal failure handling and dependent-module cascading.
    """
    state = StartupState()
    for phase in STARTUP_PHASES:
        await phase(daemon, state)


async def _warmup_mcp_endpoints_best_effort(daemon: Any) -> None:
    """Background task: warm up MCP endpoints after server is listening.

    Runs after ``_start_mcp_server()`` completes.  Fires initialize + tools/list
    against the butler's own endpoint.  Switchboard-exposed endpoints are not
    yet wired at the time the daemon starts, so only the butler's own endpoint
    is targeted here.

    All failures are swallowed — this task must never surface exceptions that
    would propagate to unhandled task machinery.
    """
    try:
        from butlers.core.mcp_warmup import warmup_mcp_endpoints

        await warmup_mcp_endpoints(
            daemon.config.name,
            butler_port=daemon.config.port,
        )
    except Exception:
        logger.warning(
            "MCP endpoint warmup failed for butler=%s (best-effort, startup continues)",
            daemon.config.name,
            exc_info=True,
        )


async def run_shutdown(daemon: Any) -> None:
    """Retain shutdown across caller cancellation when publication owns resources.

    Retirement has its existing single five-second absolute bound. Subsequent
    existing owners retain their own bounds; repeated cancellation never starts
    another retirement or abandons their cleanup. Propagate cancellation after
    the retained lifetime settles, even if retirement also fails.
    """
    task = getattr(daemon, "_catalog_shutdown_task", None)
    if task is None:
        if (
            getattr(daemon, "_catalog_publication", None) is None
            and vars(daemon).get("_chronicler_refresh_service") is None
            and vars(daemon).get("_finance_warning_control_owner") is None
        ):
            await _run_shutdown(daemon)
            return
        task = asyncio.create_task(_run_shutdown(daemon), name="catalog-owned-shutdown")
        daemon._catalog_shutdown_task = task
    interrupted = False
    try:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                interrupted = True
        task.result()
    finally:
        if interrupted:
            raise asyncio.CancelledError


async def _run_shutdown(daemon: Any) -> None:
    """Execute the graceful shutdown sequence.

    This is the implementation body of :meth:`ButlerDaemon.shutdown`.  It is
    extracted here so that ``daemon.py`` remains a thinner orchestration file.

    0. Stop advertising route acceptance and cancel registration retry
    1. Stop MCP server
    2. Stop durable buffer (drain queue, cancel workers)
    2b. Cancel in-flight route_inbox background tasks
    3. Stop accepting new triggers and drain in-flight runtime sessions
    4. Cancel switchboard heartbeat
    5. Close Switchboard MCP client
    5b. Cancel internal scheduler loop (wait for in-progress tick() to finish)
    6. Module on_shutdown in reverse topological order
    7. Close DB pool
    """
    logger.info(
        "Shutting down butler: %s",
        daemon.config.name if daemon.config else "unknown",
    )

    finance_owner = vars(daemon).get("_finance_warning_control_owner")
    if finance_owner is not None:
        finance_owner.stop_admission()
    daemon._shutting_down = True
    daemon._accepting_connections = False
    if daemon._boot_registration_task is not None:
        daemon._boot_registration_task.cancel()
        try:
            await daemon._boot_registration_task
        except asyncio.CancelledError:
            pass
        daemon._boot_registration_task = None

    # Retire only this boot's registration before stopping its real registry.
    from butlers.core.catalog_publication import close_catalog_publication

    retirement_error = None
    try:
        await close_catalog_publication(daemon)
    except BaseException as exc:
        retirement_error = exc

    try:
        await _shutdown_resources(daemon)
    finally:
        if retirement_error is not None:
            raise retirement_error


async def _shutdown_resources(daemon: Any) -> None:
    """Existing resource teardown, also reached after catalog close refusal."""

    refresh_owner = vars(daemon).get("_chronicler_refresh_service")
    finance_owner = vars(daemon).get("_finance_warning_control_owner")
    deadline = asyncio.get_running_loop().time() + (
        daemon.config.shutdown_timeout_s if daemon.config else 30.0
    )
    if refresh_owner is not None:
        refresh_owner.closed = True

    # 1. Stop MCP server
    if daemon._server is not None:
        daemon._server.should_exit = True
    if daemon._server_task is not None:
        try:
            await daemon._server_task
        except Exception:
            logger.exception("Error while stopping MCP server")
        daemon._server_task = None
        daemon._server = None
    if daemon._mcp_socket is not None:
        try:
            daemon._mcp_socket.close()
        except Exception:
            logger.exception("Error while closing MCP socket")
        daemon._mcp_socket = None

    # 2. Stop durable buffer — drain remaining queue then cancel workers/scanner
    if daemon._buffer is not None:
        shutdown_timeout = daemon.config.shutdown_timeout_s if daemon.config else 30.0
        await daemon._buffer.stop(drain_timeout_s=shutdown_timeout)
        daemon._buffer = None

    # 2b. Cancel in-flight route_inbox background tasks.
    # These tasks hold references to the spawner; cancel them before draining.
    # Rows remain in 'accepted'/'processing' state in DB and will be recovered
    # on next startup via _recover_route_inbox().
    if daemon._route_inbox_tasks:
        logger.info("Cancelling %d in-flight route_inbox task(s)", len(daemon._route_inbox_tasks))
        for task in list(daemon._route_inbox_tasks):
            task.cancel()
        # Allow tasks to handle CancelledError
        await asyncio.gather(*daemon._route_inbox_tasks, return_exceptions=True)
        daemon._route_inbox_tasks.clear()

    # 3. Stop accepting new triggers and drain in-flight runtime sessions
    daemon._accepting_connections = False
    if daemon.spawner is not None:
        daemon.spawner.stop_accepting()
        timeout = daemon.config.shutdown_timeout_s if daemon.config else 30.0
        await daemon.spawner.drain(timeout=timeout)

    # 4. Cancel switchboard heartbeat
    if daemon._switchboard_heartbeat_task is not None:
        daemon._switchboard_heartbeat_task.cancel()
        try:
            await daemon._switchboard_heartbeat_task
        except asyncio.CancelledError:
            pass
        daemon._switchboard_heartbeat_task = None

    # 5. Close Switchboard MCP client
    await daemon._disconnect_switchboard()

    # 5b. Cancel internal scheduler loop and wait for any in-progress tick() to finish
    if daemon._scheduler_loop_task is not None:
        daemon._scheduler_loop_task.cancel()
        try:
            await daemon._scheduler_loop_task
        except asyncio.CancelledError:
            pass
        daemon._scheduler_loop_task = None

    # 5c. Cancel route_inbox recovery task
    if daemon._route_inbox_recovery_task is not None:
        daemon._route_inbox_recovery_task.cancel()
        try:
            await daemon._route_inbox_recovery_task
        except asyncio.CancelledError:
            pass
        daemon._route_inbox_recovery_task = None

    # 5d. Cancel liveness reporter loop
    if daemon._liveness_reporter_task is not None:
        daemon._liveness_reporter_task.cancel()
        try:
            await daemon._liveness_reporter_task
        except asyncio.CancelledError:
            pass
        daemon._liveness_reporter_task = None

    owners = [owner for owner in (refresh_owner, finance_owner) if owner is not None]

    async def close_owned(owner):
        return await owner.close(max(0, deadline - asyncio.get_running_loop().time()))

    settled = await asyncio.gather(
        *(close_owned(owner) for owner in owners), return_exceptions=True
    )
    if any(result is not True for result in settled):
        # One retained tail fences ALL physical-pool owners, not two independent closers.
        async def finish():
            await asyncio.gather(*(owner.settle() for owner in owners))
            await _shutdown_chronicler_dependents(daemon)

        existing = vars(daemon).get("_chronicler_cleanup_task")
        if existing is None:
            task = asyncio.create_task(finish(), name="retained-owned-resource-release")
            daemon._chronicler_cleanup_task = task
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        raise TimeoutError("Owned request cleanup incomplete; resources retained")
    await _shutdown_chronicler_dependents(daemon)


async def _shutdown_chronicler_dependents(daemon: Any) -> None:
    """Original dependent teardown, reached once after owned work settles."""
    # 6. Module shutdown in reverse topological order (active modules only)
    from butlers.core.approval_delivery_worker import stop_approval_delivery_worker

    await stop_approval_delivery_worker(daemon)

    active_set = {m.name for m in daemon._active_modules}
    for mod in reversed(daemon._modules):
        if mod.name not in active_set:
            continue
        try:
            await mod.on_shutdown()
        except Exception:
            logger.exception("Error during shutdown of module: %s", mod.name)

    # 6b. Close S3 blob store
    if daemon.blob_store is not None:
        await daemon.blob_store.close()
        daemon.blob_store = None

    # 6c. Detach the butler DB log handler before tearing down the pool so
    # in-flight fire-and-forget writes can finish and no new writes are
    # scheduled against a closing pool.
    if daemon._db_log_handler is not None:
        try:
            logging.getLogger().removeHandler(daemon._db_log_handler)
        except Exception:
            logger.exception("Error while detaching DB log handler")
        daemon._db_log_handler = None

    # 7. Close credential-layer DB pools
    if daemon._shared_credentials_db is not None:
        await daemon._shared_credentials_db.close()
        daemon._shared_credentials_db = None

    # 8. Close DB pool
    if daemon.db:
        await daemon.db.close()

    logger.info("Butler shutdown complete")
