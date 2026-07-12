"""The deployments ledger — one row per ``butlers up`` process boot.

bu-9r3hd.2 (epic bu-9r3hd "Deploy spine"). See
``alembic/versions/core/core_163_deployments_ledger.py`` for the table and
its design rationale, and ``butlers.cli._start_all`` for the single call site
that records a boot (once per process, not once per butler daemon).

This module is deliberately narrow: it records "a boot happened, from this
git SHA, against this migration head, with this coarse result" and reads it
back. It is NOT the drift sentinel (bu-9r3hd.1 compares this against the
live per-schema Alembic state on an hourly cadence) and it is NOT the
one-command deploy verb (bu-9r3hd.3 adds a build/migrate/verify-health
pipeline that will eventually call ``record_deployment`` with a real
success/failure verdict instead of "did every configured daemon start").
"""

from __future__ import annotations

import logging
import os
from typing import Any

import asyncpg

from butlers.migrations import get_chain_revision_ids

logger = logging.getLogger(__name__)

VALID_RESULTS = frozenset({"success", "failed"})

_UNKNOWN_GIT_SHA = "unknown"


def resolve_git_sha() -> str:
    """Return the git SHA this running image was built from.

    Sourced from the ``GIT_SHA`` environment variable, threaded in at Docker
    build time (see ``Dockerfile`` ``ARG GIT_SHA`` / ``scripts/compose.sh``).
    Falls back to ``"unknown"`` rather than raising when unset (e.g. a bare
    ``uv run`` dev invocation outside the built image).
    """
    return os.environ.get("GIT_SHA") or _UNKNOWN_GIT_SHA


async def read_migration_head(pool: asyncpg.Pool, schema: str) -> str | None:
    """Best-effort read of the CORE migration chain's applied revision for *schema*.

    ``{schema}.alembic_version`` legitimately holds more than one row: every
    independent Alembic chain ever applied to that schema (core, plus any
    module/butler-specific chain, e.g. ``memory``) shares one physical table —
    see ``butlers.migrations`` and the migration-drift sentinel's own
    ``_actual_revisions`` (``butlers.jobs.deploy_drift``) for the same shape.
    A bare ``SELECT ... LIMIT 1`` with no ``ORDER BY`` therefore returns
    whichever row Postgres happens to return first — observed in practice
    surfacing a stale module revision (e.g. ``mem_007``) on the /system
    Deployment card instead of the core chain's head. This intersects the
    schema's applied revisions against the known core-chain revision ids
    (``get_chain_revision_ids("core")``) to isolate the core chain
    specifically, mirroring ``compute_drift_report``'s own chain
    disambiguation.

    Returns ``None`` (rather than raising) if the table is missing,
    unreadable, or the core chain has never been applied to this schema, so a
    deploy still gets recorded with an honestly-absent migration_head instead
    of failing entirely.
    """
    try:
        rows = await pool.fetch(f'SELECT version_num FROM "{schema}".alembic_version')
    except Exception:
        logger.warning(
            "deployments: could not read alembic_version for schema=%s", schema, exc_info=True
        )
        return None

    applied = {row["version_num"] for row in rows}
    core_applied = applied & get_chain_revision_ids("core")
    if not core_applied:
        return None
    # The core chain is enforced (get_chain_head) to have exactly one head, so
    # this set should carry exactly one row in a non-drifted schema; sorting
    # is a deterministic best-effort tiebreaker for the (already-anomalous)
    # case where more than one core revision id is somehow present.
    return sorted(core_applied)[-1]


async def record_deployment(
    pool: asyncpg.Pool,
    *,
    git_sha: str,
    migration_head: str | None,
    result: str,
) -> str:
    """Insert one deployment-ledger row and return its id.

    Records the boot as already finished (``started_at`` == ``finished_at``
    == now) — this slice records a boot in one shot rather than a phased
    start/verify/finish pipeline. bu-9r3hd.3's `butlers deploy` verb will
    eventually own real phase timing once it exists.
    """
    if result not in VALID_RESULTS:
        raise ValueError(
            f"record_deployment: result must be one of {sorted(VALID_RESULTS)}, got {result!r}"
        )
    row_id = await pool.fetchval(
        """
        INSERT INTO public.deployments (git_sha, migration_head, started_at, finished_at, result)
        VALUES ($1, $2, now(), now(), $3)
        RETURNING id
        """,
        git_sha,
        migration_head,
        result,
    )
    return str(row_id)


async def get_current_deployment(pool: asyncpg.Pool) -> dict[str, Any] | None:
    """Return the most recent deployment row, or None if the ledger is empty."""
    row = await pool.fetchrow(
        """
        SELECT id, git_sha, migration_head, started_at, finished_at, result
        FROM public.deployments
        ORDER BY started_at DESC
        LIMIT 1
        """
    )
    return dict(row) if row is not None else None


async def list_recent_deployments(pool: asyncpg.Pool, limit: int = 10) -> list[dict[str, Any]]:
    """Return up to `limit` most recent deployment rows, newest first."""
    rows = await pool.fetch(
        """
        SELECT id, git_sha, migration_head, started_at, finished_at, result
        FROM public.deployments
        ORDER BY started_at DESC
        LIMIT $1
        """,
        limit,
    )
    return [dict(r) for r in rows]
