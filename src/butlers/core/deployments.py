"""The deployments ledger and serving provenance.

bu-9r3hd.2 (epic bu-9r3hd "Deploy spine"). See
``alembic/versions/core/core_163_deployments_ledger.py`` for the table and
its design rationale, and ``butlers.cli._start_all`` for the single call site
that records a boot (once per process, not once per butler daemon).

This module is deliberately narrow: it records a boot or deploy, the baked
git SHA, its migration head, and the serving provenance that explains what is
actually running. It is NOT the drift sentinel (bu-9r3hd.1 compares this
against the live per-schema Alembic state on an hourly cadence).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg

from butlers.migrations import get_chain_revision_ids

logger = logging.getLogger(__name__)

VALID_RESULTS = frozenset({"success", "failed"})
VALID_DEPLOYMENT_SOURCES = frozenset({"boot", "deploy"})
VALID_SERVING_MODES = frozenset({"image", "hotreload-worktree"})

_UNKNOWN_GIT_SHA = "unknown"
_DEFAULT_SOURCE_ROOT = Path("/app/src")
_DEFAULT_MOUNTINFO_PATH = Path("/proc/self/mountinfo")
_MOUNTINFO_ESCAPE = re.compile(r"\\([0-7]{3})")
_WORKTREE_LABEL = re.compile(r"\.worktrees/[^/]+")
_PATH_NAVIGATION_SEGMENTS = frozenset({".", ".."})


@dataclass(frozen=True)
class ServingProvenance:
    """Serving-mode evidence recorded alongside a process boot.

    ``serving_mode`` stays ``None`` when runtime inspection cannot honestly
    distinguish a baked image from some other mounted source. A linked
    worktree is deliberately represented by its stable, non-host-specific
    ``.worktrees/<name>`` suffix rather than an absolute host path.
    """

    serving_mode: str | None
    serving_worktree: str | None


def _decode_mountinfo_path(value: str) -> str:
    """Decode Linux mountinfo's octal path escapes without shell parsing."""

    return _MOUNTINFO_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), value)


def _is_canonical_worktree_label(value: str) -> bool:
    """Return whether *value* identifies one linked checkout without navigation."""

    return bool(
        _WORKTREE_LABEL.fullmatch(value)
        and value.rsplit("/", maxsplit=1)[-1] not in _PATH_NAVIGATION_SEGMENTS
    )


def _worktree_label(mount_root: str) -> str | None:
    """Extract ``.worktrees/<name>`` from a bind-mount source path."""

    # Do not use PurePosixPath here: it normalizes ``.`` segments, which can
    # turn ``.worktrees/./src`` into the false label ``.worktrees/src``.
    parts = mount_root.split("/")
    for index, part in enumerate(parts[:-1]):
        if part == ".worktrees":
            label = f".worktrees/{parts[index + 1]}"
            return label if _is_canonical_worktree_label(label) else None
    return None


def detect_boot_serving_provenance(
    *,
    source_root: Path = _DEFAULT_SOURCE_ROOT,
    mountinfo_path: Path = _DEFAULT_MOUNTINFO_PATH,
) -> ServingProvenance:
    """Detect a source bind mount without trusting ambient compose profile.

    A hotreload service bind-mounts the host checkout's ``src`` directory at
    ``/app/src``. Linux exposes that fact in ``/proc/self/mountinfo``: an
    exact mount at the source root carries the host-side path in field four.
    We only classify it as ``hotreload-worktree`` when that host path includes
    a linked ``.worktrees/<name>`` checkout. A different source mount is not
    silently labelled ``image``; it remains unknown until a future serving
    mode models it explicitly. This keeps the dashboard from granting image
    authority to a mounted tree it cannot identify.
    """

    try:
        mountinfo_lines = mountinfo_path.read_text().splitlines()
    except OSError:
        logger.debug("deployments: mountinfo unavailable; serving mode is unknown")
        return ServingProvenance(serving_mode=None, serving_worktree=None)

    source_root_text = str(source_root)
    for line in mountinfo_lines:
        pre_separator, separator, _post_separator = line.partition(" - ")
        if not separator:
            continue
        fields = pre_separator.split()
        # mount ID, parent ID, major:minor, root, mount point, options, ...
        if len(fields) < 5 or _decode_mountinfo_path(fields[4]) != source_root_text:
            continue

        worktree = _worktree_label(_decode_mountinfo_path(fields[3]))
        if worktree is not None:
            return ServingProvenance(
                serving_mode="hotreload-worktree",
                serving_worktree=worktree,
            )

        logger.info(
            "deployments: source root %s is bind-mounted but not a linked worktree; "
            "recording serving mode as unknown",
            source_root,
        )
        return ServingProvenance(serving_mode=None, serving_worktree=None)

    if source_root.is_dir():
        return ServingProvenance(serving_mode="image", serving_worktree=None)

    logger.debug("deployments: source root %s is absent; serving mode is unknown", source_root)
    return ServingProvenance(serving_mode=None, serving_worktree=None)


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
    of failing entirely. A *missing* ``alembic_version`` table is the expected
    case for a schema that tracks nothing (e.g. ``public`` on the live DB,
    which holds cross-butler tables but no migration chain — the core chain is
    applied per butler schema): it is classified as legitimately-absent and
    logged at ``debug`` with no traceback, mirroring the fleet convention in
    ``memory.py::_is_missing_memory_schema_error``. Only a *genuine* failure
    (dropped connection, permission error, malformed query) still logs loudly.
    """
    try:
        rows = await pool.fetch(f'SELECT version_num FROM "{schema}".alembic_version')
    except asyncpg.UndefinedTableError:
        # Expected-absent, not a failure: this schema simply has no migration
        # chain (e.g. ``public``). No traceback — that noise is exactly what
        # spooked the bu-hmdqz.1 redeploy into logging a full stack for a
        # benign missing table.
        logger.debug("deployments: no alembic_version in schema=%s (expected-absent)", schema)
        return None
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


async def _schemas_with_alembic_version(pool: asyncpg.Pool) -> list[str]:
    """Return every schema that physically carries an ``alembic_version`` table.

    The core migration chain is applied *per butler schema*, never to a single
    canonical schema — on the live DB ``alembic_version`` exists in each butler
    schema (``chronicler`` .. ``travel``) and NOT in ``public``. Discover the
    schemas that actually track migrations from the Postgres catalog rather
    than assuming any one schema (the old ``read_migration_head(pool, "public")``
    assumption is exactly what recorded ``migration_head=None``). Reads
    ``pg_catalog.pg_class``/``pg_namespace`` directly (a plain relation,
    ``relkind = 'r'``) rather than ``information_schema.tables``, which is a
    slower permission-checked view over the same data.
    """
    rows = await pool.fetch(
        """
        SELECT n.nspname AS table_schema
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relname = 'alembic_version'
          AND c.relkind = 'r'
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
        ORDER BY n.nspname
        """
    )
    return [row["table_schema"] for row in rows]


async def resolve_core_migration_head(pool: asyncpg.Pool) -> str | None:
    """Resolve the CORE chain's applied head across every schema that tracks it.

    Iterates the schemas that physically carry an ``alembic_version`` table
    (see :func:`_schemas_with_alembic_version`), reads each one's core-chain
    head (see :func:`read_migration_head`), and collapses them to a single
    representative value for the deployments ledger.

    Divergence semantics ([decision], per decision-autonomy): the drift
    sentinel (``butlers.jobs.deploy_drift``) owns *per-schema* drift reporting;
    this ledger field only needs one honest representative head. When schemas
    agree (the healthy case) that shared head is returned. When they disagree
    — a genuinely anomalous mid-migration or half-applied state — the
    **newest** head (``max`` over the zero-padded ``core_NNN`` ids, matching
    :func:`read_migration_head`'s own lexical tiebreak) is recorded, since a
    successful ``migrate`` phase advances every schema toward the code head, so
    the furthest-advanced value is the most honest "we got at least this far".
    The divergence is logged at ``warning`` with the full per-schema map so it
    is never silently smoothed over.

    Returns ``None`` — never raises for the expected-absent case — when no
    schema tracks the core chain at all, so a deploy is still recorded with an
    honestly-absent ``migration_head`` (rendered as an explicit "unknown" on
    the /system Deployment tile) instead of failing.
    """
    schemas = await _schemas_with_alembic_version(pool)
    heads: dict[str, str] = {}
    for schema in schemas:
        head = await read_migration_head(pool, schema)
        if head is not None:
            heads[schema] = head
    if not heads:
        return None
    distinct = set(heads.values())
    if len(distinct) > 1:
        logger.warning(
            "deployments: core migration head diverges across schemas %s; "
            "recording newest for the ledger (drift sentinel owns per-schema reporting)",
            heads,
        )
    return max(heads.values())


async def record_deployment(
    pool: asyncpg.Pool,
    *,
    git_sha: str,
    migration_head: str | None,
    result: str,
    source: str,
    serving_mode: str | None,
    serving_worktree: str | None,
) -> str:
    """Insert one deployment-ledger row with provenance and return its id.

    Records a boot or deploy as already finished (``started_at`` ==
    ``finished_at`` == now). ``source`` and ``serving_mode`` are required at
    every current writer, while the database deliberately permits null values
    in old rows that predate this provenance contract.
    """
    if result not in VALID_RESULTS:
        raise ValueError(
            f"record_deployment: result must be one of {sorted(VALID_RESULTS)}, got {result!r}"
        )
    if source not in VALID_DEPLOYMENT_SOURCES:
        raise ValueError(
            "record_deployment: source must be one of "
            f"{sorted(VALID_DEPLOYMENT_SOURCES)}, got {source!r}"
        )
    if serving_mode is not None and serving_mode not in VALID_SERVING_MODES:
        raise ValueError(
            "record_deployment: serving_mode must be one of "
            f"{sorted(VALID_SERVING_MODES)} or None, got {serving_mode!r}"
        )
    if serving_mode == "hotreload-worktree":
        if source != "boot":
            raise ValueError("record_deployment: hotreload-worktree requires source='boot'")
        if serving_worktree is None:
            raise ValueError(
                "record_deployment: hotreload-worktree requires "
                "serving_worktree='.worktrees/<name>'"
            )
    elif serving_worktree is not None:
        raise ValueError(
            "record_deployment: serving_worktree requires serving_mode='hotreload-worktree'"
        )
    if serving_worktree is not None and not _is_canonical_worktree_label(serving_worktree):
        raise ValueError(
            "record_deployment: serving_worktree must be '.worktrees/<name>', "
            f"got {serving_worktree!r}"
        )
    if source == "deploy" and (serving_mode != "image" or serving_worktree is not None):
        raise ValueError(
            "record_deployment: deploy source must record image serving with no worktree"
        )

    row_id = await pool.fetchval(
        """
        INSERT INTO public.deployments (
            git_sha,
            migration_head,
            started_at,
            finished_at,
            result,
            source,
            serving_mode,
            serving_worktree
        )
        VALUES ($1, $2, now(), now(), $3, $4, $5, $6)
        RETURNING id
        """,
        git_sha,
        migration_head,
        result,
        source,
        serving_mode,
        serving_worktree,
    )
    return str(row_id)


async def get_current_deployment(pool: asyncpg.Pool) -> dict[str, Any] | None:
    """Return the most recent deployment row, or None if the ledger is empty."""
    row = await pool.fetchrow(
        """
        SELECT
            id,
            git_sha,
            migration_head,
            started_at,
            finished_at,
            result,
            source,
            serving_mode,
            serving_worktree
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
        SELECT
            id,
            git_sha,
            migration_head,
            started_at,
            finished_at,
            result,
            source,
            serving_mode,
            serving_worktree
        FROM public.deployments
        ORDER BY started_at DESC
        LIMIT $1
        """,
        limit,
    )
    return [dict(r) for r in rows]
