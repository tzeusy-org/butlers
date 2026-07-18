"""Database connection manager for the dashboard API.

Maintains one asyncpg pool per butler key and supports both legacy multi-DB
and one-DB/multi-schema topologies through schema-scoped search_path settings.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import asyncpg

from butlers.db import (
    pool_sizes_from_env,
    register_jsonb_codec,
    schema_search_path,
    should_retry_with_ssl_disable,
)

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages asyncpg connection pools for multiple butler DB contexts.

    Usage::

        mgr = DatabaseManager(host="localhost", port=5432, user="postgres", password="postgres")
        await mgr.add_butler("switchboard", db_name="butlers", db_schema="switchboard")
        await mgr.add_butler("atlas", db_name="butlers", db_schema="general")

        pool = mgr.pool("switchboard")
        results, failed = await mgr.fan_out_with_status("SELECT count(*) FROM sessions")
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5432,
        user: str = "postgres",
        password: str = "postgres",
        ssl: str | None = None,
        min_pool_size: int | None = None,
        max_pool_size: int | None = None,
    ) -> None:
        env_min_pool_size, env_max_pool_size = pool_sizes_from_env(
            "BUTLERS_API_DB_POOL",
            default_min=1,
            default_max=3,
        )
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._ssl = ssl
        self._min_pool_size = env_min_pool_size if min_pool_size is None else min_pool_size
        self._max_pool_size = env_max_pool_size if max_pool_size is None else max_pool_size
        self._pools: dict[str, asyncpg.Pool] = {}
        self._shared_pool: asyncpg.Pool | None = None
        self._butler_modules: dict[str, frozenset[str]] = {}
        # The explicit target schema for each butler pool.  Query paths that
        # own per-butler tables must use this rather than falling through to
        # ``public`` via the pool's search_path after local schema loss.
        self._butler_schemas: dict[str, str | None] = {}
        # Optional private memory-schema overrides.  Most butlers keep memory
        # tables in their domain schema, while Chronicler owns memory in
        # ``chronicler_mem`` so its domain ``episodes`` relation stays distinct.
        self._butler_memory_schema_overrides: dict[str, str | None] = {}
        # Relation presence captured when the dashboard pool becomes available.
        # Optional-schema readers use this lifecycle marker to distinguish a
        # deliberately uninstalled table from one that disappears later.
        self._relation_presence_at_start: dict[str, dict[str, bool]] = {}
        # role_enforcement_disabled: True when SET ROLE schema-isolation is NOT
        # active for any butler database managed by this instance.  Starts True
        # (conservative default — enforcement not yet confirmed) and may be
        # updated via set_role_enforcement_disabled() during startup.
        self._role_enforcement_disabled: bool = True

    async def _create_pool(
        self,
        *,
        database: str,
        log_name: str,
        schema: str | None = None,
    ) -> asyncpg.Pool:
        """Create an asyncpg pool with configured retry behavior."""
        pool_kwargs: dict[str, Any] = {
            "host": self._host,
            "port": self._port,
            "user": self._user,
            "password": self._password,
            "database": database,
            "min_size": self._min_pool_size,
            "max_size": self._max_pool_size,
            "init": register_jsonb_codec,
        }
        search_path = schema_search_path(schema)
        if search_path is not None:
            pool_kwargs["server_settings"] = {"search_path": search_path}
        if self._ssl is not None:
            pool_kwargs["ssl"] = self._ssl
        try:
            return await asyncpg.create_pool(**pool_kwargs)
        except Exception as exc:
            if not should_retry_with_ssl_disable(exc, self._ssl):
                raise
            retry_kwargs = dict(pool_kwargs)
            retry_kwargs["ssl"] = "disable"
            logger.info(
                "Retrying DB pool creation with ssl=disable for %s after SSL upgrade loss",
                log_name,
            )
            return await asyncpg.create_pool(**retry_kwargs)

    async def add_butler(
        self,
        butler_name: str,
        db_name: str | None = None,
        db_schema: str | None = None,
        modules: frozenset[str] | None = None,
        memory_schema: str | None = None,
    ) -> None:
        """Add a butler database connection pool.

        Parameters
        ----------
        butler_name:
            The butler's name (used as key for pool lookup).
        db_name:
            The database name. Defaults to butler_name if not provided.
        db_schema:
            Optional schema name for one-db multi-schema topology.
        memory_schema:
            Optional private schema that owns this butler's memory relations.
            When omitted, memory relations use ``db_schema`` (or legacy
            unqualified lookup when no schema is configured).
        modules:
            Optional set of module names enabled for this butler (e.g.
            ``frozenset({"calendar", "email"})``). Used by
            ``butlers_with_module()`` to filter fan_out targets.
        """
        if butler_name in self._pools:
            logger.warning("Butler %s already has a pool; skipping", butler_name)
            return

        effective_db = db_name or butler_name
        # ``schema_search_path`` validates and normalizes the configured
        # schema.  Retain its first component as the explicit local target for
        # table-owning dashboard queries; ``search_path`` itself intentionally
        # keeps ``public`` available for cross-butler reads.
        search_path = schema_search_path(db_schema)
        local_schema = search_path.split(",", maxsplit=1)[0] if search_path is not None else None
        memory_search_path = schema_search_path(memory_schema)
        local_memory_schema = (
            memory_search_path.split(",", maxsplit=1)[0] if memory_search_path is not None else None
        )
        pool = await self._create_pool(
            database=effective_db,
            log_name=f"butler {butler_name}",
            schema=db_schema,
        )
        self._pools[butler_name] = pool
        self._butler_schemas[butler_name] = local_schema
        self._butler_memory_schema_overrides[butler_name] = local_memory_schema
        if modules is not None:
            self._butler_modules[butler_name] = modules
        logger.info(
            "Added pool for butler: %s (db=%s, schema=%s, memory_schema=%s)",
            butler_name,
            effective_db,
            db_schema or "<default>",
            local_memory_schema or local_schema or "<legacy>",
        )

    async def set_credential_shared_pool(self, db_name: str, db_schema: str | None = None) -> None:
        """Set the dedicated shared credential DB pool."""
        if self._shared_pool is not None:
            await self._shared_pool.close()
            self._shared_pool = None
        self._shared_pool = await self._create_pool(
            database=db_name,
            log_name="shared credentials",
            schema=db_schema,
        )
        logger.info("Configured shared credential pool (db=%s, schema=%s)", db_name, db_schema)

    def credential_shared_pool(self) -> asyncpg.Pool:
        """Return dedicated shared credential pool or raise KeyError."""
        if self._shared_pool is None:
            raise KeyError("Shared credential pool is not configured")
        return self._shared_pool

    def pool(self, butler_name: str) -> asyncpg.Pool:
        """Get the connection pool for a specific butler.

        Raises KeyError if the butler hasn't been added.
        """
        if butler_name not in self._pools:
            raise KeyError(f"No pool for butler: {butler_name}")
        return self._pools[butler_name]

    def schema_for_butler(self, butler_name: str) -> str | None:
        """Return the explicit schema configured for a butler's pool.

        ``None`` denotes a legacy database without a schema-scoped pool.  The
        caller must then preserve the database's unqualified-table semantics.
        """
        if butler_name not in self._pools:
            raise KeyError(f"No pool for butler: {butler_name}")
        return self._butler_schemas.get(butler_name)

    def memory_schema_for_butler(self, butler_name: str) -> str | None:
        """Return the effective schema that owns a butler's memory relations.

        A configured ``[modules.memory] memory_schema`` override takes
        precedence over the domain pool schema.  Without an override, memory
        remains colocated with the butler's domain schema; only a legacy pool
        with neither schema configured keeps unqualified-table semantics.
        """
        if butler_name not in self._pools:
            raise KeyError(f"No pool for butler: {butler_name}")
        memory_schema = self._butler_memory_schema_overrides.get(butler_name)
        if memory_schema is not None:
            return memory_schema
        return self._butler_schemas.get(butler_name)

    async def snapshot_relation_presence(
        self,
        source_name: str,
        relation_names: tuple[str, ...],
        *,
        pool: asyncpg.Pool | None = None,
    ) -> None:
        """Record whether domain relations exist when API startup completes.

        Domain relations use the source pool's configured schema.  Use
        :meth:`snapshot_memory_relation_presence` for memory-owned relations,
        because those may live in a private schema distinct from the domain.

        ``to_regclass`` respects the pool's schema-scoped ``search_path``.  A
        later ``UndefinedTableError`` is a normal optional-schema absence only
        when this snapshot explicitly recorded the relation as absent.  A
        failed snapshot intentionally leaves the value unknown so callers
        fail closed rather than hiding a potentially dropped table.
        """
        await self._snapshot_relation_presence(
            source_name,
            relation_names,
            source_schema=self._butler_schemas.get(source_name),
            pool=pool,
        )

    async def snapshot_memory_relation_presence(
        self,
        source_name: str,
        relation_names: tuple[str, ...],
        *,
        pool: asyncpg.Pool | None = None,
    ) -> None:
        """Record memory relation presence against its effective owner schema."""
        await self._snapshot_relation_presence(
            source_name,
            relation_names,
            source_schema=self.memory_schema_for_butler(source_name),
            pool=pool,
        )

    async def _snapshot_relation_presence(
        self,
        source_name: str,
        relation_names: tuple[str, ...],
        *,
        source_schema: str | None,
        pool: asyncpg.Pool | None,
    ) -> None:
        """Record relation presence with an explicit source-schema decision.

        ``to_regclass`` respects the pool's schema-scoped ``search_path``.  A
        later ``UndefinedTableError`` is a normal optional-schema absence only
        when this snapshot explicitly recorded the relation as absent.  A
        failed snapshot intentionally leaves the value unknown so callers
        fail closed rather than hiding a potentially dropped table.
        """
        if not relation_names:
            return
        try:
            target_pool = pool if pool is not None else self.pool(source_name)
            if source_schema is None:
                rows = await target_pool.fetch(
                    "SELECT requested.relation_name, "
                    "to_regclass(requested.relation_name) IS NOT NULL AS present "
                    "FROM unnest($1::text[]) AS requested(relation_name)",
                    list(relation_names),
                )
            else:
                rows = await target_pool.fetch(
                    "SELECT requested.relation_name, "
                    "to_regclass(format('%I.%I', $2::text, requested.relation_name)) "
                    "IS NOT NULL AS present "
                    "FROM unnest($1::text[]) AS requested(relation_name)",
                    list(relation_names),
                    source_schema,
                )
        except Exception:
            logger.warning(
                "Failed to snapshot optional relation presence for %s",
                source_name,
                exc_info=True,
            )
            return

        observed = {str(row["relation_name"]): bool(row["present"]) for row in rows}
        self._relation_presence_at_start.setdefault(source_name, {}).update(
            {relation: observed[relation] for relation in relation_names if relation in observed}
        )

    def relation_observed_since_start(self, source_name: str, relation_name: str) -> bool | None:
        """Return the startup-presence marker for a relation, if it was recorded.

        ``True`` means the relation existed when this dashboard process
        started; ``False`` means the schema was deliberately absent then;
        ``None`` is unknown and must not be treated as a graceful absence.
        """
        return self._relation_presence_at_start.get(source_name, {}).get(relation_name)

    @property
    def butler_names(self) -> list[str]:
        """Return list of all registered butler names."""
        return list(self._pools.keys())

    def butlers_with_module(self, module_name: str) -> list[str] | None:
        """Return butler names that have *module_name* enabled, or None if unknown.

        Returns ``None`` when no module metadata has been registered (e.g. in
        tests or legacy deployments), so callers can fall back to querying all
        butlers rather than silently returning an empty list.

        Parameters
        ----------
        module_name:
            The module key to filter by (e.g. ``"calendar"``).

        Returns
        -------
        list[str] | None
            Sorted list of butler names with the module enabled, or ``None`` if
            module metadata is not available.
        """
        if not self._butler_modules:
            return None
        return sorted(name for name, mods in self._butler_modules.items() if module_name in mods)

    @property
    def role_enforcement_disabled(self) -> bool:
        """Return True when SET ROLE schema-isolation is NOT active.

        True when no DB role has been verified for any butler managed by this
        instance.  Starts True (conservative — enforcement not yet confirmed)
        and is updated by ``set_role_enforcement_disabled()`` during startup
        after role-existence verification.

        Intended for the dashboard health surface.
        """
        return self._role_enforcement_disabled

    def set_role_enforcement_disabled(self, disabled: bool) -> None:
        """Set the role-enforcement-disabled flag.

        Called during ``init_db_manager()`` after role verification to record
        whether SET ROLE enforcement is active across the managed databases.
        """
        self._role_enforcement_disabled = disabled

    async def fan_out_with_status(
        self,
        query: str,
        args: tuple[Any, ...] = (),
        butler_names: list[str] | None = None,
    ) -> tuple[dict[str, list[asyncpg.Record]], list[str]]:
        """Execute a query concurrently across multiple butler databases.

        Every targeted butler gets an entry in the returned ``results`` map
        (empty list on failure); ``failed_butler_names`` names the ones whose
        query raised. This is the *only* fan-out primitive: the failed list is
        returned rather than swallowed so callers must decide whether to
        distinguish "genuinely empty" from "this source errored" (e.g. to
        surface a degraded-source flag in a response envelope) instead of
        silently fabricating an all-clear from a partial result.

        Returns
        -------
        tuple[dict[str, list[asyncpg.Record]], list[str]]
            ``(results, failed_butler_names)``. ``results`` maps every
            targeted butler to its rows (empty list on failure). Every entry
            in ``failed_butler_names`` also has an empty-list entry in
            ``results``; the error is logged either way.
        """
        targets = butler_names if butler_names is not None else self.butler_names
        failed: list[str] = []

        async def _query_one(name: str) -> tuple[str, list[asyncpg.Record]]:
            try:
                p = self._pools[name]
                rows = await p.fetch(query, *args)
                return (name, rows)
            except Exception:
                logger.warning("fan_out query failed for butler %s", name, exc_info=True)
                failed.append(name)
                return (name, [])

        results = await asyncio.gather(*[_query_one(n) for n in targets])
        return dict(results), failed

    async def close(self) -> None:
        """Close all connection pools."""
        if self._shared_pool is not None:
            try:
                await self._shared_pool.close()
                logger.info("Closed shared credential pool")
            except Exception:
                logger.warning("Error closing shared credential pool", exc_info=True)
            self._shared_pool = None

        for name, p in self._pools.items():
            try:
                await p.close()
                logger.info("Closed pool for butler: %s", name)
            except Exception:
                logger.warning("Error closing pool for butler: %s", name, exc_info=True)
        self._pools.clear()
        self._butler_schemas.clear()
        self._butler_memory_schema_overrides.clear()
        self._relation_presence_at_start.clear()
