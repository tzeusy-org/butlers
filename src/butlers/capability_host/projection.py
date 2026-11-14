"""What the host reads from the projection itself (RFC 0039 D3, D16, D17; OD14).

Once per call, inside the call's snapshot: the replicated manifest digest, the applied record
the owner step left, and the replicated kill switches. The pending decision and its
narrowing/widening label reuse the owner step's own comparison (``manifest_transition``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import asyncpg

from butlers.capability_host.config import HostSettings
from butlers.knowledge_read.core import ReadError, projection_disabled_domains, published
from butlers.knowledge_read.manifest import (
    Manifest,
    ManifestError,
    decode_applied_manifest,
    manifest_transition,
)

READER_SERVER_SETTINGS = {"search_path": "pg_catalog"}


@dataclass(frozen=True)
class ProjectionStatus:
    replicated_digests: frozenset[str]
    applied_digest: str | None
    applied_canonical: str | None
    applied_at: datetime | None
    copy_complete: bool
    disabled: frozenset[str]


@dataclass(frozen=True)
class Verdict:
    """``code``: the refusal for every tool but ``describe``; ``change``: the pending label."""

    code: str | None
    change: str | None


async def read_status(conn: asyncpg.Connection) -> ProjectionStatus:
    """Read the serving preconditions; missing projection objects mean the step never ran."""
    _, state_view = published("mcp_surface.manifest_state", "describe")
    try:
        digests = await conn.fetch(f"SELECT fields->>'digest' AS digest FROM {state_view}")
        applied = await conn.fetchrow(
            "SELECT digest, canonical, applied_at, copy_complete FROM mcp_read.manifest_applied"
        )
        disabled = await projection_disabled_domains(conn)
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedFunctionError):
        raise ReadError("projection_apply_pending") from None
    return ProjectionStatus(
        replicated_digests=frozenset(r["digest"] for r in digests),
        applied_digest=None if applied is None else applied["digest"],
        applied_canonical=None if applied is None else applied["canonical"],
        applied_at=None if applied is None else applied["applied_at"],
        copy_complete=bool(applied and applied["copy_complete"]),
        disabled=disabled,
    )


def judge(status: ProjectionStatus, shipped: Manifest) -> Verdict:
    """D17 precedence between deploy skew and a pending owner step, plus the gauge label."""
    pending = (
        status.applied_digest is None
        or status.applied_digest != shipped.digest
        or not status.copy_complete
    )
    change = None
    if pending:
        try:
            applied = (
                None
                if status.applied_canonical is None
                else decode_applied_manifest(status.applied_canonical)
            )
            change = manifest_transition(applied, shipped).direction
        except ManifestError:
            change = "narrowing"
    if status.replicated_digests != {shipped.digest}:
        return Verdict("projection_version_mismatch", change)
    return Verdict("projection_apply_pending" if pending else None, change)


async def canary_probe(conn: asyncpg.Connection, manifest: Manifest, canaries: tuple[str, ...]):
    """Whether any published view row holds a canary value (D15 projection probe)."""
    if not canaries:
        return False
    for table in manifest.tables:
        _, view = published(table.source, table.tools[0])
        hit = await conn.fetchval(
            f"SELECT EXISTS(SELECT 1 FROM {view} v, unnest($1::text[]) c "
            "WHERE strpos(v::text, c) > 0)",
            list(canaries),
        )
        if hit:
            return True
    return False


async def create_pool(settings: HostSettings) -> asyncpg.Pool:
    """The reader pool as ``mcp_public_ro``; the password is file-mounted (no env fallback).

    ``search_path`` is pinned to ``pg_catalog`` (bu-y66o00.28): readers name every projection
    object by schema, so no database- or role-level setting can steer an unqualified name.
    """
    password = settings.db_password_path.read_text().strip()
    return await asyncpg.create_pool(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=password,
        database=settings.db_name,
        min_size=0,
        max_size=4,
        timeout=5,
        command_timeout=10,
        server_settings=dict(READER_SERVER_SETTINGS),
    )
