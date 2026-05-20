"""Contact orphan resolver: backfill entity_id for orphan contacts.

Post-migration Python script (Migration bead 5.5).  Reads orphan rows from
``public.contacts_pre_migration_<YYYYMMDD>`` snapshot (contacts with
``entity_id IS NULL``) and resolves each one via one of two strategies:

  a) **Mint** — if the contact has a usable canonical-name signal (non-empty
     ``first_name``, ``last_name``, ``nickname``, or ``name`` that is not a
     generic placeholder), a new ``public.entities`` row is minted via direct
     SQL and ``public.contacts.entity_id`` is backfilled.  This is the
     documented carve-out from the "no direct SQL outside relationship butler"
     rule; it is justified by migration-time context where the relationship
     butler may not be live and MCP calls are unreachable.

  b) **Notify** — if the contact has no usable name signal, a Telegram
     notification is sent to the owner summarising the orphan row so the owner
     can decide manually.  The row is marked ``deferred`` in the report.

Spec anchor
-----------
``openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/specs/relationship-facts/spec.md``
§ "Requirement: Orphan contact handling" (Amendment 1.1.A.3 update).

Usage
-----
    # Dry-run (default) — enumerate plan, no writes:
    python -m butlers.scripts.contact_orphan_resolver --date 20260601

    # Apply — perform writes:
    python -m butlers.scripts.contact_orphan_resolver --date 20260601 --apply

    # Custom report path:
    python -m butlers.scripts.contact_orphan_resolver \\
        --date 20260601 --apply --report-path /tmp/orphans.md

Environment variables
---------------------
DATABASE_URL, POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD
control the DB connection (same env vars as the butler daemon).

BUTLER_TELEGRAM_TOKEN and the owner's ``telegram_chat_id`` stored in
``public.entity_info`` are used for notify-path escalations.  If either is
absent the notification is logged as a warning and the row is still recorded
in the report as ``deferred``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("contact_orphan_resolver")

# ---------------------------------------------------------------------------
# Repo-root discovery
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    """Return the repository root (three levels above this file in src layout)."""
    # src/butlers/scripts/<this file> → src/butlers → src → repo root
    return Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

_DATE_LABEL_RE = re.compile(r"^\d{8}$")


def _validate_date_label(date_label: str) -> None:
    """Raise ValueError if date_label is not a safe YYYYMMDD string."""
    if not _DATE_LABEL_RE.match(date_label):
        raise ValueError(f"Invalid date_label {date_label!r}: must be exactly 8 digits (YYYYMMDD).")


# ---------------------------------------------------------------------------
# DB connection
# ---------------------------------------------------------------------------


async def _create_pool() -> asyncpg.Pool:
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        pool = await asyncpg.create_pool(dsn=database_url)
    else:
        pool = await asyncpg.create_pool(
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            user=os.environ.get("POSTGRES_USER", "butlers"),
            password=os.environ.get("POSTGRES_PASSWORD", "butlers"),
            database=os.environ.get("POSTGRES_DB", "butlers"),
        )
    assert pool is not None
    return pool


# ---------------------------------------------------------------------------
# Resolution outcome types
# ---------------------------------------------------------------------------


@dataclass
class OrphanRow:
    """One row from the snapshot table with entity_id IS NULL."""

    id: uuid.UUID
    name: str
    first_name: str | None
    last_name: str | None
    nickname: str | None
    company: str | None
    roles: list[str]
    created_at: datetime

    @classmethod
    def from_record(cls, rec: Any) -> OrphanRow:
        return cls(
            id=rec["id"],
            name=rec["name"] or "",
            first_name=rec["first_name"] or None,
            last_name=rec["last_name"] or None,
            nickname=rec["nickname"] or None,
            company=rec["company"] or None,
            roles=list(rec["roles"]) if rec["roles"] else [],
            created_at=rec["created_at"],
        )

    def canonical_name_signal(self) -> str | None:
        """Return the best available canonical-name signal, or None.

        A contact has a usable name signal if it has a non-empty first_name,
        last_name, nickname, or a non-trivially generic ``name`` value.
        """
        if self.first_name and self.first_name.strip():
            parts = [self.first_name.strip()]
            if self.last_name and self.last_name.strip():
                parts.append(self.last_name.strip())
            return " ".join(parts)
        if self.last_name and self.last_name.strip():
            return self.last_name.strip()
        if self.nickname and self.nickname.strip():
            return self.nickname.strip()
        # Fall back to name only if it looks like a real name (not empty/generic)
        n = (self.name or "").strip()
        if n and n.lower() not in {"unknown", "unnamed", "contact", "new contact", ""}:
            return n
        return None

    def synthetic_canonical_name(self) -> str:
        """Return a synthetic canonical_name for nameless orphans (--force-nameless).

        Priority:
          1. Company name: "Contact at <company> (#{id[:8]})"
          2. Roles list:   "<role1, role2> contact (#{id[:8]})"
          3. ID fallback:  "Unnamed contact #{id[:8]}"

        The short UUID suffix keeps names unique-ish and makes it easy for a
        human reviewing the entity catalog to spot synthetic entries later.
        """
        short_id = str(self.id)[:8]
        if self.company and self.company.strip():
            return f"Contact at {self.company.strip()} (#{short_id})"
        if self.roles:
            role_str = ", ".join(self.roles)
            return f"{role_str} contact (#{short_id})"
        return f"Unnamed contact #{short_id}"


@dataclass
class ResolutionOutcome:
    """Per-row resolution outcome."""

    orphan: OrphanRow
    status: str  # "minted" | "deferred" | "dry-run-would-mint" | "dry-run-would-defer"
    entity_id: uuid.UUID | None = None  # set for "minted"
    notify_sent: bool = False
    note: str = ""


@dataclass
class ResolverStats:
    """Aggregate statistics from a resolver run."""

    total_orphans: int = 0
    minted: int = 0
    deferred: int = 0
    outcomes: list[ResolutionOutcome] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Snapshot existence check
# ---------------------------------------------------------------------------


async def _snapshot_exists(pool: asyncpg.Pool, table: str) -> bool:
    """Return True if *table* already exists in the public schema."""
    result = await pool.fetchval(
        "SELECT to_regclass($1::text)",
        f"public.{table}",
    )
    return result is not None


# ---------------------------------------------------------------------------
# Query: fetch orphan rows from snapshot
# ---------------------------------------------------------------------------


async def _fetch_orphans(pool: asyncpg.Pool, snapshot_table: str) -> list[OrphanRow]:
    """Return all rows in the snapshot where entity_id IS NULL."""
    rows = await pool.fetch(
        f"""
        SELECT id, name, first_name, last_name, nickname, company,
               COALESCE(roles, ARRAY[]::text[]) AS roles,
               created_at
        FROM public."{snapshot_table}"
        WHERE entity_id IS NULL
        ORDER BY created_at ASC
        """
    )
    return [OrphanRow.from_record(r) for r in rows]


# ---------------------------------------------------------------------------
# Mint: direct SQL entity creation + backfill
# ---------------------------------------------------------------------------


async def _mint_entity_and_backfill(
    pool: asyncpg.Pool,
    orphan: OrphanRow,
    canonical_name: str,
) -> uuid.UUID:
    """Mint a new entity for *orphan* and backfill public.contacts.entity_id.

    This is the documented carve-out for migration-time context (see module
    docstring).  Returns the new entity's UUID.

    Idempotent: if ``public.contacts.entity_id`` is already set for this
    contact (from a previous run), the existing entity_id is returned and no
    new entity row is created.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Guard against double-minting on re-runs.  The snapshot table has
            # a static entity_id=NULL, but public.contacts may have already
            # been backfilled by a prior --apply run.
            existing_entity_id: uuid.UUID | None = await conn.fetchval(
                "SELECT entity_id FROM public.contacts WHERE id = $1",
                orphan.id,
            )
            if existing_entity_id is not None:
                logger.info(
                    "Contact %s already has entity_id=%s — skipping mint (idempotent re-run)",
                    orphan.id,
                    existing_entity_id,
                )
                return existing_entity_id

            entity_id: uuid.UUID = await conn.fetchval(
                """
                INSERT INTO public.entities (canonical_name, entity_type, roles)
                VALUES ($1, 'person', ARRAY[]::text[])
                RETURNING id
                """,
                canonical_name,
            )
            await conn.execute(
                "UPDATE public.contacts SET entity_id = $1 WHERE id = $2",
                entity_id,
                orphan.id,
            )
    logger.info(
        "Minted entity %s for contact %s (canonical_name=%r)",
        entity_id,
        orphan.id,
        canonical_name,
    )
    return entity_id


# ---------------------------------------------------------------------------
# Notify: Telegram escalation to owner
# ---------------------------------------------------------------------------


async def _resolve_telegram_credentials(pool: asyncpg.Pool) -> tuple[str | None, str | None]:
    """Return (bot_token, chat_id) from CredentialStore + entity_info.

    Returns (None, None) if credentials are not configured.
    """
    try:
        from butlers.credential_store import CredentialStore, resolve_owner_entity_info

        token = await CredentialStore(pool).resolve("BUTLER_TELEGRAM_TOKEN")
        chat_id = await resolve_owner_entity_info(pool, "telegram_chat_id")
        return token, chat_id
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not resolve Telegram credentials: %s", exc)
        return None, None


async def _send_telegram_notification(
    pool: asyncpg.Pool,
    message: str,
) -> bool:
    """Send a Telegram message to the owner.  Returns True on success."""
    try:
        import httpx
    except ImportError:
        logger.warning("httpx not available — cannot send Telegram notification")
        return False

    token, chat_id = await _resolve_telegram_credentials(pool)
    if not token or not chat_id:
        logger.warning(
            "Telegram credentials not configured — notification not sent. "
            "Set BUTLER_TELEGRAM_TOKEN and owner telegram_chat_id in entity_info."
        )
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code >= 400:
                try:
                    detail = resp.json().get("description", resp.text[:200])
                except (ValueError, KeyError):
                    detail = resp.text[:200]
                logger.error(
                    "Telegram sendMessage failed: status=%d detail=%s",
                    resp.status_code,
                    detail,
                )
                return False
            logger.info("Telegram notification sent to chat_id=%r", chat_id)
            return True
    except Exception as exc:  # noqa: BLE001
        logger.error("Telegram request error: %s", exc)
        return False


def _build_notify_message(orphan: OrphanRow) -> str:
    """Build the owner notification message for an orphan contact."""
    lines = [
        "<b>Orphan contact needs review</b>",
        f"ID: <code>{orphan.id}</code>",
        f"Name: {orphan.name or '(none)'}",
    ]
    if orphan.first_name:
        lines.append(f"First name: {orphan.first_name}")
    if orphan.last_name:
        lines.append(f"Last name: {orphan.last_name}")
    if orphan.nickname:
        lines.append(f"Nickname: {orphan.nickname}")
    if orphan.company:
        lines.append(f"Company: {orphan.company}")
    if orphan.roles:
        lines.append(f"Roles: {', '.join(orphan.roles)}")
    lines.append(f"Created: {orphan.created_at.strftime('%Y-%m-%d')}")
    lines.append(
        "\nThis contact has no entity_id and no canonical-name signal. "
        "Run the orphan resolver with --apply after assigning an entity manually."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core resolver logic
# ---------------------------------------------------------------------------


async def _resolve_orphan(
    pool: asyncpg.Pool,
    orphan: OrphanRow,
    *,
    apply: bool,
    force_nameless: bool = False,
) -> ResolutionOutcome:
    """Resolve a single orphan row.  Returns a ResolutionOutcome.

    When ``force_nameless=True`` and the orphan has no canonical-name signal,
    a synthetic canonical_name is derived from company/roles/id instead of
    deferring to owner notification.  Default behavior (``force_nameless=False``)
    is unchanged.
    """
    canonical = orphan.canonical_name_signal()

    # When force_nameless is set and there is no real name signal, synthesise one
    # so the orphan can follow the mint path instead of the notify path.
    if canonical is None and force_nameless:
        canonical = orphan.synthetic_canonical_name()
        synthetic = True
    else:
        synthetic = False

    if canonical is not None:
        # --- Mint path ---
        note_suffix = "; synthetic canonical_name (--force-nameless)" if synthetic else ""
        if apply:
            entity_id = await _mint_entity_and_backfill(pool, orphan, canonical)
            return ResolutionOutcome(
                orphan=orphan,
                status="minted",
                entity_id=entity_id,
                note=f"canonical_name={canonical!r}{note_suffix}",
            )
        else:
            logger.info(
                "[DRY-RUN] Would mint entity for contact %s (canonical_name=%r%s)",
                orphan.id,
                canonical,
                ", synthetic" if synthetic else "",
            )
            return ResolutionOutcome(
                orphan=orphan,
                status="dry-run-would-mint",
                note=f"canonical_name={canonical!r}{note_suffix}",
            )
    else:
        # --- Notify path ---
        if apply:
            msg = _build_notify_message(orphan)
            sent = await _send_telegram_notification(pool, msg)
            return ResolutionOutcome(
                orphan=orphan,
                status="deferred",
                notify_sent=sent,
                note="no canonical-name signal; owner notified"
                if sent
                else "no canonical-name signal; notification not sent (credentials missing)",
            )
        else:
            logger.info(
                "[DRY-RUN] Would notify owner about contact %s (no name signal)",
                orphan.id,
            )
            return ResolutionOutcome(
                orphan=orphan,
                status="dry-run-would-defer",
                note="no canonical-name signal",
            )


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

_REPORT_TEMPLATE = """\
# Contact Migration Orphan Resolver Report

**Generated:** {generated_at}
**Date label:** {date_label}
**Script:** `src/butlers/scripts/contact_orphan_resolver.py`
**Epic:** bu-uhjxr (entity-redesign — contacts → triples migration)
**Bead:** bu-yxdzq (Migration bead 5.5 — orphan resolver)
**Mode:** {mode}

---

## Summary

| Metric | Count |
|--------|-------|
| Total orphan contacts examined | {total_orphans} |
| Entities minted (backfilled) | {minted} |
| Deferred (owner notified) | {deferred} |
| Remaining unresolved orphans | {remaining} |

---

## Per-row outcomes

{per_row_table}

---

## Next steps

{next_steps}
"""

_DRY_RUN_NEXT_STEPS = """\
This was a **dry-run** (no writes performed).  To apply the resolution plan:

    python -m butlers.scripts.contact_orphan_resolver --date {date_label} --apply

Review the per-row table above before running with `--apply`.  For rows
marked `dry-run-would-defer`, assign an entity manually and update
`public.contacts.entity_id` before proceeding to parity tests (Migration
bead 6).
"""

_APPLY_NEXT_STEPS = """\
Resolution complete.  Verify with:

    SELECT COUNT(*) FROM public.contacts WHERE entity_id IS NULL;

Expected result: **{remaining}** unresolved contacts.

If `deferred` rows remain, assign entities manually and re-run the resolver
or update `public.contacts.entity_id` directly before proceeding to parity
tests (Migration bead 6).
"""


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a simple Markdown table."""
    sep = " | ".join("---" for _ in headers)
    header_line = " | ".join(headers)
    lines = [f"| {header_line} |", f"| {sep} |"]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def _render_report(
    *,
    date_label: str,
    stats: ResolverStats,
    apply: bool,
) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    mode = "APPLY" if apply else "DRY-RUN"

    remaining = stats.total_orphans - stats.minted

    per_row_headers = ["Contact ID", "Name", "Status", "Entity ID", "Note"]
    per_row_rows = []
    for outcome in stats.outcomes:
        per_row_rows.append(
            [
                f"`{outcome.orphan.id}`",
                outcome.orphan.name or "(none)",
                f"`{outcome.status}`",
                f"`{outcome.entity_id}`" if outcome.entity_id else "—",
                outcome.note,
            ]
        )

    if not per_row_rows:
        per_row_table = "_No orphan contacts found in the snapshot._"
    else:
        per_row_table = _md_table(per_row_headers, per_row_rows)

    if apply:
        next_steps = _APPLY_NEXT_STEPS.format(remaining=remaining)
    else:
        next_steps = _DRY_RUN_NEXT_STEPS.format(date_label=date_label)

    return _REPORT_TEMPLATE.format(
        generated_at=now,
        date_label=date_label,
        mode=mode,
        total_orphans=stats.total_orphans,
        minted=stats.minted,
        deferred=stats.deferred,
        remaining=remaining,
        per_row_table=per_row_table,
        next_steps=next_steps,
    )


# ---------------------------------------------------------------------------
# Main resolution loop
# ---------------------------------------------------------------------------


async def _run_resolver_with_pool(
    pool: asyncpg.Pool,
    *,
    date_label: str,
    report_path: Path,
    apply: bool,
    force_nameless: bool = False,
) -> int:
    """Execute the resolver using an already-open pool.  Does NOT close the pool."""
    _validate_date_label(date_label)

    snapshot_table = f"contacts_pre_migration_{date_label}"

    # Verify snapshot table exists
    if not await _snapshot_exists(pool, snapshot_table):
        logger.error(
            "Snapshot table public.%s does not exist. "
            "Run the pre-migration snapshot script first "
            "(src/butlers/scripts/contact_migration_snapshot.py --date %s).",
            snapshot_table,
            date_label,
        )
        return 1

    orphans = await _fetch_orphans(pool, snapshot_table)
    logger.info("Found %d orphan contact(s) in public.%s", len(orphans), snapshot_table)

    if not apply:
        logger.info("[DRY-RUN] No writes will be performed. Pass --apply to enable writes.")
    if force_nameless:
        logger.info(
            "--force-nameless: nameless orphans will be auto-minted with synthetic canonical_name."
        )

    stats = ResolverStats(total_orphans=len(orphans))

    for orphan in orphans:
        outcome = await _resolve_orphan(pool, orphan, apply=apply, force_nameless=force_nameless)
        stats.outcomes.append(outcome)
        if outcome.status == "minted":
            stats.minted += 1
        elif outcome.status == "deferred":
            stats.deferred += 1

    logger.info(
        "Resolution complete: total=%d minted=%d deferred=%d remaining=%d",
        stats.total_orphans,
        stats.minted,
        stats.deferred,
        stats.total_orphans - stats.minted,
    )

    report_text = _render_report(date_label=date_label, stats=stats, apply=apply)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text, encoding="utf-8")
    logger.info("Report written to: %s", report_path)

    return 0


async def run_resolver(
    *,
    date_label: str,
    report_path: Path,
    apply: bool,
    force_nameless: bool = False,
    _pool: asyncpg.Pool | None = None,
) -> int:
    """Run the orphan resolver.  Returns 0 on success, 1 on error.

    Args:
        date_label:     YYYYMMDD label matching the snapshot table to process.
        report_path:    Where to write the Markdown report.
        apply:          If False (default), performs a dry-run with no writes.
        force_nameless: When True, orphans with no canonical-name signal are
                        auto-minted using a synthetic name (company/roles/id)
                        instead of being deferred to owner notification.
                        When False (default), behavior is unchanged.
        _pool:          For testing only — callers should omit this so the
                        function creates and closes its own pool.
    """
    own_pool = _pool is None
    pool = _pool if _pool is not None else await _create_pool()
    try:
        return await _run_resolver_with_pool(
            pool,
            date_label=date_label,
            report_path=report_path,
            apply=apply,
            force_nameless=force_nameless,
        )
    except Exception:
        logger.exception("Resolver failed")
        return 1
    finally:
        if own_pool:
            await pool.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    today = datetime.now(UTC).strftime("%Y%m%d")
    default_report = (
        _repo_root()
        / "docs"
        / "reports"
        / f"contact-migration-orphans-{datetime.now(UTC).strftime('%Y-%m-%d')}.md"
    )

    parser = argparse.ArgumentParser(
        description=(
            "Resolve orphan contacts (entity_id IS NULL) in the pre-migration snapshot. "
            "Defaults to dry-run; pass --apply to perform writes."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--date",
        default=today,
        metavar="YYYYMMDD",
        help=f"Date label of the snapshot table to read (default: today = {today}).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help=(
            "Perform writes: mint entities, backfill contacts.entity_id, "
            "and send owner notifications. Default is dry-run (no writes)."
        ),
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=default_report,
        metavar="PATH",
        help=f"Where to write the orphan-resolver report (default: {default_report}).",
    )
    parser.add_argument(
        "--force-nameless",
        action="store_true",
        default=False,
        help=(
            "Auto-mint nameless orphans using a synthetic canonical_name "
            "(derived from company, roles, or contact ID) instead of deferring "
            "to owner notification. Has no effect on orphans that already have a "
            "canonical-name signal."
        ),
    )
    return parser.parse_args(argv)


def main() -> None:
    args = _parse_args()
    rc = asyncio.run(
        run_resolver(
            date_label=args.date,
            report_path=args.report_path,
            apply=args.apply,
            force_nameless=args.force_nameless,
        )
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()
