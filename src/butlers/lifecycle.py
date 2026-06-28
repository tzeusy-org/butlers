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
import shutil
import time
from typing import Any

from fastmcp import FastMCP

from butlers.catalogue_bootstrap import upsert_provider_feature_catalogue
from butlers.config import (
    ButlerType,
    load_config,
)
from butlers.core.general_settings import resolve_general_timezone
from butlers.core.logging import resolve_log_root
from butlers.core.metrics import init_metrics
from butlers.core.runtimes import DEFAULT_RUNTIME_TYPE, get_adapter
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
from butlers.migrations import has_butler_chain, run_migrations
from butlers.module_state import ModuleStartupStatus
from butlers.owner_bootstrap import _ensure_owner_entity
from butlers.storage import BlobStorageStartupError, S3BlobStore

logger = logging.getLogger(__name__)


async def run_startup(daemon: Any) -> None:
    """Execute the full butler startup sequence.

    This is the implementation body of :meth:`ButlerDaemon.start`.  It is
    extracted here so that ``daemon.py`` remains a thinner orchestration file.

    Steps execute in order.  A failure at any step prevents subsequent steps.
    Module-specific steps (config validation, credentials, migrations,
    on_startup, tool registration) are non-fatal per-module: a failing module is
    recorded as failed and skipped in later phases while the butler continues to
    start with the remaining healthy modules.
    """
    # 1. Load config (skip if pre-set, e.g. by e2e fixtures)
    if daemon.config is None:
        daemon.config = load_config(daemon.config_dir)

    # 1b. Configure structured logging for this butler
    from butlers.core.logging import configure_logging

    log_root = resolve_log_root(daemon.config.logging.log_root)
    configure_logging(
        level=daemon.config.logging.level,
        fmt=daemon.config.logging.format,
        log_root=log_root,
        butler_name=daemon.config.name,
    )
    logger.info("Loaded config for butler: %s", daemon.config.name)

    # 1c. Blob storage initialization is deferred to step 8c (after
    # CredentialStore is available) so S3 credentials can be resolved
    # from the database rather than requiring environment variables.

    # 2. Initialize telemetry and metrics
    init_telemetry(f"butler.{daemon.config.name}")
    init_metrics(f"butler.{daemon.config.name}")

    # 2.5. Detect inline secrets in config
    config_values = _flatten_config_for_secret_scan(daemon.config)
    secret_warnings = detect_secrets(config_values)
    for warning in secret_warnings:
        logger.warning(warning)

    # 3. Initialize modules (topological order). The registry instantiates
    # every built-in module, then startup filters out modules that require
    # explicit config but are omitted from [modules.*].
    daemon._modules = daemon._select_startup_modules(
        daemon._registry.load_all(daemon.config.modules)
    )

    # 4. Validate module config schemas (non-fatal per-module).
    daemon._module_configs = daemon._validate_module_configs()

    # 5. Validate butler.env credentials (env-only fast-fail for non-secret config).
    # Module credentials are validated later (step 8b) after the DB pool is
    # available, so DB-stored secrets are visible.
    module_creds = daemon._collect_module_credentials()
    validate_credentials(
        daemon.config.env_required,
        daemon.config.env_optional,
    )

    # 6. Provision database
    # If db was injected (e.g., for testing), skip provisioning
    if daemon.db is None:
        daemon.db = Database.from_env(daemon.config.db_name)
        daemon.db.set_schema(daemon.config.db_schema)
        if daemon.config.db_schema:
            daemon.db.role = f"butler_{daemon.config.db_schema}_rw"
        await daemon.db.provision()
        pool = await daemon.db.connect()
    else:
        # Database already provisioned and connected externally
        pool = daemon.db.pool
        if pool is None:
            raise RuntimeError("Injected Database must already be connected")

    # 6b. Attach the butler-scoped DB log handler so /api/butlers/{name}/logs
    # surfaces live application logs.  The handler filters by butler context
    # (set in step 1b via configure_logging), so multi-butler-in-process does
    # not cross-contaminate.  Migrations in step 7 will populate butler_logs
    # the first time the schema is provisioned; until then writes will fail
    # quietly and be swallowed by ButlerLogger.
    from butlers.core.butler_logging import ButlerDBLogHandler, ButlerLogger

    db_log_schema = daemon.config.db_schema or daemon.config.name
    butler_db_logger = ButlerLogger(pool=pool, schema=db_log_schema)
    daemon._db_log_handler = ButlerDBLogHandler(
        butler_logger=butler_db_logger,
        butler_name=daemon.config.name,
    )
    logging.getLogger().addHandler(daemon._db_log_handler)

    # 7. Run core Alembic migrations
    db_url = daemon._build_db_url()
    migration_schema = daemon.config.db_schema or None
    await run_migrations(db_url, chain="core", schema=migration_schema)

    # 7b. Run butler-specific Alembic migrations (if chain exists)
    if has_butler_chain(daemon.config.name):
        logger.info("Running butler-specific migrations for: %s", daemon.config.name)
        await run_migrations(db_url, chain=daemon.config.name, schema=migration_schema)

    # 8. Run module Alembic migrations (non-fatal per-module)
    for mod in daemon._modules:
        if mod.name in daemon._module_statuses:
            continue
        rev = mod.migration_revisions()
        if rev:
            try:
                await run_migrations(db_url, chain=rev, schema=migration_schema)
            except Exception as exc:
                error_msg = str(exc)
                daemon._module_statuses[mod.name] = ModuleStartupStatus(
                    status="failed", phase="migration", error=error_msg
                )
                logger.warning("Module '%s' disabled: migration failed: %s", mod.name, error_msg)
    daemon._cascade_module_failures()

    # 8b. Create layered CredentialStore and validate module credentials
    # (non-fatal per-module).
    # DB pool is now available so DB-stored credentials are visible to resolve().
    # Only validate credentials for modules that haven't already failed (e.g. from
    # migration errors), to avoid redundant DB queries and overwriting earlier failure
    # statuses with spurious credential failures.
    credential_store = await daemon._build_credential_store(pool)
    daemon._credential_store = credential_store
    active_module_creds_for_validation = {
        k: v for k, v in module_creds.items() if k.split(".")[0] not in daemon._module_statuses
    }
    module_cred_failures = await validate_module_credentials_async(
        active_module_creds_for_validation, credential_store
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

    # Filter module_creds to exclude failed modules for spawner.
    active_module_creds = {
        k: v
        for k, v in module_creds.items()
        if k.split(".")[0] not in daemon._module_statuses
        or daemon._module_statuses[k.split(".")[0]].status == "active"
    }

    # 8c. Initialize S3-compatible blob storage.
    # All S3 parameters are resolved from CredentialStore (DB-only, no env
    # fallback) — managed via the dashboard secrets UI at /secrets.
    s3_endpoint = await credential_store.resolve("BLOB_S3_ENDPOINT_URL", env_fallback=False)
    s3_bucket = await credential_store.resolve("BLOB_S3_BUCKET", env_fallback=False)
    s3_region = await credential_store.resolve("BLOB_S3_REGION", env_fallback=False)
    s3_access_key = await credential_store.resolve("BLOB_S3_ACCESS_KEY_ID", env_fallback=False)
    s3_secret_key = await credential_store.resolve("BLOB_S3_SECRET_ACCESS_KEY", env_fallback=False)
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

    # 8c2. Restore CLI auth tokens from DB to filesystem (non-fatal).
    #      Ensures LLM runtime CLIs have their auth files (e.g. OpenCode's
    #      auth.json) written to disk before the spawner tries to invoke them.
    try:
        from butlers.cli_auth.persistence import restore_tokens

        results = await restore_tokens(credential_store)
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
        logger.debug("CLI auth token restoration skipped", exc_info=True)

    # 8d. Bootstrap owner entity (idempotent; non-fatal).
    #     Ensures owner entity exists in public.entities.
    await _ensure_owner_entity(pool)

    # 8d2. Seed provider feature catalogue (idempotent; non-fatal).
    #      UPSERTs the canonical known-provider rows into
    #      public.provider_feature_catalogue so the WhatBreaks affordance on
    #      /secrets has server-side data from the first boot onward.
    await upsert_provider_feature_catalogue(pool)

    # 8e. Recover orphaned sessions from a previous daemon run.
    #     Any sessions row with completed_at IS NULL at startup is necessarily
    #     orphaned — this daemon is the sole writer and is just booting. Without
    #     this sweep, chronicler projects orphans as open work episodes that
    #     never close (visible as multi-day-old "in-progress" sessions on the
    #     chronicles dashboard).  Best-effort: never blocks startup.
    try:
        from butlers.core.sessions import recover_orphaned_sessions

        await recover_orphaned_sessions(pool)
    except Exception:
        logger.warning(
            "recover_orphaned_sessions failed for butler=%s (best-effort, startup continues)",
            daemon.config.name,
            exc_info=True,
        )

    # 9b. Resolve runtime config from DB (seed from toml on first boot).
    # Creates the RuntimeConfigAccessor and seeds the runtime_config table
    # if this is the first boot. The effective RuntimeConfig from DB is used
    # for tool registration and spawner construction.
    from butlers.core.runtime_config import RuntimeConfigAccessor

    schema = daemon.config.db_schema or daemon.config.name
    daemon._runtime_config_accessor = RuntimeConfigAccessor(pool, schema)
    effective_runtime = await daemon._runtime_config_accessor.seed_if_empty(
        daemon.config.runtime_seed, daemon.config.name
    )
    if effective_runtime.seeded_at == effective_runtime.updated_at:
        logger.info("Seeded runtime config from butler.toml for %s", daemon.config.name)
    else:
        logger.info(
            "Using runtime config from DB for %s (seeded %s, updated %s)",
            daemon.config.name,
            effective_runtime.seeded_at,
            effective_runtime.updated_at,
        )

    # 9. Call module on_startup (non-fatal per-module)
    started_modules: list[Any] = []
    for mod in daemon._modules:
        if mod.name in daemon._module_statuses:
            continue
        try:
            validated_config = daemon._module_configs.get(mod.name)
            await mod.on_startup(
                validated_config, daemon.db, credential_store, blob_store=daemon.blob_store
            )
            started_modules.append(mod)
        except Exception as exc:
            error_msg = str(exc)
            daemon._module_statuses[mod.name] = ModuleStartupStatus(
                status="failed", phase="startup", error=error_msg
            )
            logger.warning("Module '%s' disabled: on_startup failed: %s", mod.name, error_msg)
            daemon._cascade_module_failures()

    # 10. Create Spawner with runtime adapter (verify binary on PATH)
    adapter_cls = get_adapter(DEFAULT_RUNTIME_TYPE)
    # ClaudeCodeAdapter accepts butler_name/log_root for CC stderr capture.
    # CodexAdapter accepts credential_store/butler_name for auth.json rotation sync.
    if DEFAULT_RUNTIME_TYPE == "claude":
        runtime = adapter_cls(butler_name=daemon.config.name, log_root=log_root)
    elif DEFAULT_RUNTIME_TYPE == "codex":
        runtime = adapter_cls(
            credential_store=credential_store,
            butler_name=daemon.config.name,
        )
    else:
        runtime = adapter_cls()

    binary = runtime.binary_name
    if not shutil.which(binary):
        raise RuntimeBinaryNotFoundError(
            f"Runtime binary {binary!r} not found on PATH. "
            f"The {DEFAULT_RUNTIME_TYPE!r} runtime requires {binary!r} to be installed."
        )

    # 10a. Set up audit pool for daemon-side audit logging
    audit_pool = await daemon._create_audit_pool(pool)
    # Expose the Switchboard-schema pool to the scheduler loop so it can gate
    # scheduled dispatch on butler_registry.eligibility_state (paused/quarantined
    # butlers must not fire cron/deadline ticks).
    daemon._audit_pool = audit_pool

    # 10a-ii. Wire audit pool into modules that emit egress audit entries
    # (telegram_send, gmail_send, google_calendar_write).  This is a post-startup
    # hook so modules receive the pool after it is created, without altering the
    # on_startup signature or ordering.
    for mod in started_modules:
        try:
            mod.wire_audit_pool(audit_pool)
        except Exception:
            logger.debug(
                "wire_audit_pool failed for module '%s' (non-fatal)", mod.name, exc_info=True
            )

    daemon.spawner = Spawner(
        config=daemon.config,
        config_dir=daemon.config_dir,
        pool=pool,
        module_credentials_env=active_module_creds,
        runtime=runtime,
        audit_pool=audit_pool,
        credential_store=credential_store,
        runtime_config_accessor=daemon._runtime_config_accessor,
    )

    # 10b. Wire message classification pipeline for switchboard modules
    daemon._wire_pipelines(pool)

    # 11. Sync TOML schedules to DB
    # Staffer-typed agents skip daily_briefing_contribution schedule entries
    # per the staffer-archetype spec (briefing exclusion decision point).
    _is_staffer = daemon.config.type == ButlerType.STAFFER
    schedules = [
        {
            "name": s.name,
            "cron": s.cron,
            "dispatch_mode": s.dispatch_mode.value,
            "prompt": s.prompt,
            "job_name": s.job_name,
            "job_args": s.job_args,
            "max_token_budget": s.max_token_budget,
            "complexity": s.complexity,
        }
        for s in daemon.config.schedules
        if not (_is_staffer and s.job_name == "daily_briefing_contribution")
    ]
    # Interpret hour-pinned crons in the owner's configured timezone (failing
    # open to UTC) so e.g. a daily "5 1 * * *" fires at 01:05 local, not 01:05
    # UTC.  Resolved from the shared general settings via the credential store.
    default_timezone = await resolve_general_timezone(credential_store.shared_pool)
    await sync_schedules(
        pool,
        schedules,
        stagger_key=daemon.config.name,
        skills_dir=get_skills_dir(daemon.config_dir),
        default_timezone=default_timezone,
    )

    # 11b. Open MCP client connection to Switchboard (non-switchboard butlers)
    await daemon._connect_switchboard()

    # 12. Create FastMCP and register core tools
    daemon.mcp = FastMCP(daemon.config.name)
    daemon._register_core_tools()

    # 13. Register module MCP tools (non-fatal per-module)
    await daemon._register_module_tools()

    # 13b. Apply approval gates to configured gated tools
    daemon._gated_tool_originals = await daemon._apply_approval_gates()

    # 13c. Wire calendar overlap-approval enqueuer when both modules are loaded
    daemon._wire_calendar_approval_enqueuer()

    # 13d. Wire spawner + switchboard_client into modules that define wire_runtime().
    # Must run after _connect_switchboard() (step 11b) so that switchboard_client
    # is already set, and after register_tools() (step 13) so that module state
    # is fully initialised before the runtime references are injected.
    daemon._wire_module_runtime()

    # Mark remaining modules as active
    for mod in daemon._modules:
        if mod.name not in daemon._module_statuses:
            daemon._module_statuses[mod.name] = ModuleStartupStatus(status="active")

    # 13e. Initialize module runtime states (enabled/disabled) from state store
    await daemon._init_module_runtime_states(pool)

    # 14. Start FastMCP SSE server on configured port
    await daemon._start_mcp_server()

    # 14b. Warm up MCP endpoints (best-effort, non-blocking for daemon boot).
    # Fires initialize + tools/list against the butler's own endpoint (and any
    # extra endpoints) so the first real Codex spawn hits warm server-side
    # caches/pools instead of cold ones.  Failures are logged at WARNING level
    # and never propagate — the warmup task runs in the background so it does
    # not hold up the remaining startup steps.
    asyncio.create_task(
        _warmup_mcp_endpoints_best_effort(daemon),
        name=f"mcp-warmup-{daemon.config.name}",
    )

    # 14c. Start durable buffer workers and scanner (switchboard only)
    if daemon._buffer is not None:
        await daemon._buffer.start()

    # 14d. Recover unprocessed route_inbox rows (non-staffer butlers only)
    # Rows that were accepted but never processed due to a crash are re-dispatched
    # as a background task so that long-running LLM sessions don't block startup
    # (and therefore don't prevent other butlers from starting in `butlers up`).
    # Staffers (switchboard, messenger) have their own durable routing mechanisms
    # and do not use route_inbox for crash recovery.
    if daemon.config.type != ButlerType.STAFFER and daemon.spawner is not None:
        daemon._route_inbox_recovery_task = asyncio.create_task(daemon._recover_route_inbox(pool))

    # 15. Launch switchboard heartbeat (non-switchboard butlers only)
    if daemon.config.switchboard_url is not None:
        daemon._switchboard_heartbeat_task = asyncio.create_task(
            daemon._switchboard_heartbeat_loop()
        )

    # 16. Start internal scheduler loop
    daemon._scheduler_loop_task = asyncio.create_task(daemon._scheduler_loop())

    # 17. Start liveness reporter (all butlers, including switchboard)
    daemon._liveness_reporter_task = asyncio.create_task(daemon._liveness_reporter_loop())

    # Mark as accepting connections and record startup time
    daemon._accepting_connections = True
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
    """Execute the graceful shutdown sequence.

    This is the implementation body of :meth:`ButlerDaemon.shutdown`.  It is
    extracted here so that ``daemon.py`` remains a thinner orchestration file.

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

    # 6. Module shutdown in reverse topological order (active modules only)
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

    # 7. Close audit DB pool (if separate from main DB)
    if daemon._audit_db is not None:
        await daemon._audit_db.close()
        daemon._audit_db = None

    # 8. Close credential-layer DB pools
    if daemon._shared_credentials_db is not None:
        await daemon._shared_credentials_db.close()
        daemon._shared_credentials_db = None

    # 9. Close DB pool
    if daemon.db:
        await daemon.db.close()

    logger.info("Butler shutdown complete")
