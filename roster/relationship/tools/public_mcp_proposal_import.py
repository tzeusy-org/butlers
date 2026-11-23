"""The deterministic public MCP proposal importer (RFC 0039 D13 step 2; bu-y66o00.8).

``public_mcp_proposal_import`` is a Relationship ``dispatch_mode = "job"`` schedule that
runs every 5 minutes with no model and no session (D14 item 5). It is the quarantine
boundary between outside input and the owner's approvals queue. Each pass, in order:

1. **Switch.** Read the ``proposals`` and ``all`` rows of ``mcp_surface.controls`` on the
   primary (REQ-public-mcp-proposals-007). A missing row or a failed read counts as
   disabled. The projection's replicated copy is never consulted.
2. **Intake.** Log in to ``butlers_public`` as ``mcp_proposals_reader`` with the
   file-mounted password and read at most 100 inbox rows ordered by ``received_at``
   then ``proposal_id``. Copy them into ``relationship.external_fact_proposals`` in one
   transaction with ``ON CONFLICT DO NOTHING`` (state ``imported``), commit, and only
   then delete exactly the ids read from the inbox. A crash between the two steps
   leaves rows in both places; the next pass's copy is a no-op for them and its delete
   removes them, so nothing is lost or doubled. Intake runs while the switch is
   disabled too: copied rows stay ``imported`` and unparked. Without the password file
   intake is skipped with a warning and a metric; the other steps still run.
3. **Validate and park** (only while the switch is enabled). Each ``imported`` row
   received at least 30 s before the pass, oldest first, gets one transaction under a
   pass-wide advisory lock. The first failing check decides its terminal state:

   - ``invalid``: incomplete payload; predicate outside ``proposable.toml``; a client
     label or evidence URL the park path cannot represent; a subject or object entity
     that is missing, merged or deleted; or a subject that is the owner;
   - ``invalid``: the response DLP's counted (secret-shaped) rules match the note or the
     evidence URL;
   - ``duplicate``: an identical active fact exists;
   - ``conflict``: an earlier proposal, by ``(received_at, proposal_id)``, used the same
     ``(sub, cid, idempotency_key)``;
   - ``rate_limited``: the ``(sub, cid)`` already has 20 proposals *today* or 50
     *unresolved* (definitions below).

   A row passing every check is parked through ``park_external_fact_proposal`` in the
   same transaction that sets ``state = 'parked'`` and ``pending_action_id``.
4. **Expire and reconcile.** A parked action still ``pending`` past its ``expires_at``
   is expired (nothing else sweeps ordinary approvals on a schedule). Then each
   ``parked`` or ``apply_failed`` proposal takes its action's outcome: ``executed`` is
   ``applied``; ``rejected`` is ``owner_rejected``; ``expired`` (and ``cancelled``) is
   ``expired``; ``abandoned``, or ``approved`` with a recorded
   ``action_execution_failed`` event, is ``apply_failed``. A failed execution stays
   retryable in approvals, so an ``apply_failed`` proposal whose retry later executes
   moves on to ``applied``. ``decided_at`` is set at each transition.
5. **Retention.** 30 days after ``decided_at``, a terminal proposal's ``note`` and
   ``evidence_url`` are cleared; ids, state and timestamps stay.

Definitions (rate limits):

- **today** is the UTC calendar day of the proposal's own ``received_at``. A proposal is
  ``rate_limited`` when 20 or more proposals of the same ``(sub, cid)``, in any state,
  were received earlier on that day (ordered by ``received_at`` then ``proposal_id``).
  The count does not depend on when the importer runs.
- **unresolved** is ``state = 'parked'``: parked and not yet decided by the owner,
  executed or expired. A proposal is ``rate_limited`` when its ``(sub, cid)`` already has
  50 of them at its validation. Rows are validated oldest first, one at a time.

Privacy: logs, metrics and the job result carry counts, states and proposal ids only,
never a note, URL, subject, predicate or client value, and never a database error's
text (a CHECK violation's message can quote the row).
"""

from __future__ import annotations

import logging
import os
import uuid
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
from opentelemetry import metrics

from butlers.capability_host.assertion import client_label
from butlers.capability_host.dlp import Scan
from butlers.db import db_params_from_env
from butlers.knowledge_read.proposable import proposable_predicates
from butlers.modules.approvals.delivery_lifecycle import transition_pending_action
from butlers.modules.approvals.external_proposals import (
    evidence_url_host,
    validate_client_label,
)
from butlers.modules.approvals.models import ActionStatus
from butlers.roster_imports import roster_module

_parking = roster_module("relationship", "tools/external_fact_proposals")

logger = logging.getLogger(__name__)

JOB_NAME = "public_mcp_proposal_import"
INBOX_BATCH = 100
VALIDATE_LIMIT = 500
SETTLE = timedelta(seconds=30)
DAY_CAP = 20
UNRESOLVED_CAP = 50
RETENTION = timedelta(days=30)
SWITCH_DOMAINS = ("proposals", "all")
TERMINAL_STATES = (
    "invalid",
    "duplicate",
    "conflict",
    "rate_limited",
    "applied",
    "owner_rejected",
    "expired",
    "apply_failed",
)
NOTE_MAX = 500

READER_ROLE = "mcp_proposals_reader"
DEFAULT_PASSWORD_FILE = "/etc/butlers/mcp-proposals-reader/password"
PASSWORD_FILE_ENV = "BUTLERS_MCP_PROPOSALS_READER_PASSWORD_FILE"
HOST_ENV = "BUTLERS_MCP_PROPOSALS_DB_HOST"
PORT_ENV = "BUTLERS_MCP_PROPOSALS_DB_PORT"
NAME_ENV = "BUTLERS_MCP_PROPOSALS_DB_NAME"
DEFAULT_DATABASE = "butlers_public"

_TABLE = "relationship.external_fact_proposals"
_LOCK_KEY = "relationship.public_mcp_proposal_import"
_ACTOR = "system:public_mcp_proposal_import"
_INBOX_COLUMNS = (
    "proposal_id",
    "sub",
    "cid",
    "idempotency_key",
    "subject_entity_id",
    "predicate",
    "object_entity_id",
    "evidence_url",
    "note",
    "received_at",
)

#: pending_actions.status -> proposal state; approved is decided by its failure events.
_ACTION_OUTCOMES = {
    ActionStatus.EXECUTED.value: "applied",
    ActionStatus.REJECTED.value: "owner_rejected",
    ActionStatus.EXPIRED.value: "expired",
    ActionStatus.CANCELLED.value: "expired",
    ActionStatus.ABANDONED.value: "apply_failed",
}


@dataclass(frozen=True)
class InboxSettings:
    """Where the inbox lives and where its reader password is mounted. Never the value."""

    host: str
    port: int
    database: str
    password_file: Path
    ssl: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> InboxSettings:
        """Defaults: the primary's host, port and sslmode, database ``butlers_public``."""
        env = os.environ if env is None else env
        primary = db_params_from_env()
        ssl = primary.get("ssl")
        return cls(
            host=env.get(HOST_ENV) or str(primary["host"]),
            port=int(env.get(PORT_ENV) or primary["port"] or 5432),
            database=env.get(NAME_ENV) or DEFAULT_DATABASE,
            password_file=Path(env.get(PASSWORD_FILE_ENV) or DEFAULT_PASSWORD_FILE),
            ssl=ssl if isinstance(ssl, str) else None,
        )

    def read_password(self) -> str | None:
        """The mounted password, or ``None`` when the optional Secret is not mounted."""
        try:
            value = self.password_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        return value or None


@dataclass
class PassReport:
    """Counts only: this is the job result and the metric source."""

    switch_enabled: bool = False
    intake: str = "not_run"
    inbox_depth: int | None = None
    copied: int = 0
    already_present: int = 0
    deleted: int = 0
    outcomes: Counter[str] = field(default_factory=Counter)
    expired_actions: int = 0
    reconciled: Counter[str] = field(default_factory=Counter)
    cleared: int = 0
    errors: Counter[str] = field(default_factory=Counter)

    def as_dict(self) -> dict[str, Any]:
        return {
            "switch_enabled": self.switch_enabled,
            "intake": self.intake,
            "inbox_depth": self.inbox_depth,
            "copied": self.copied,
            "already_present": self.already_present,
            "deleted": self.deleted,
            "outcomes": dict(self.outcomes),
            "expired_actions": self.expired_actions,
            "reconciled": dict(self.reconciled),
            "cleared": self.cleared,
            "errors": dict(self.errors),
        }


# ---------------------------------------------------------------------------
# Pass
# ---------------------------------------------------------------------------


async def run_public_mcp_proposal_import(
    pool: Any,
    *,
    now: datetime | None = None,
    inbox: InboxSettings | None = None,
) -> dict[str, Any]:
    """One importer pass over the Relationship pool. Each step fails independently."""
    now = now or datetime.now(UTC)
    report = PassReport()
    report.switch_enabled = await _switch_enabled(pool, report)
    try:
        await _intake(pool, inbox or InboxSettings.from_env(), now, report)
    except Exception as exc:  # noqa: BLE001 -- the other steps still run
        report.intake = "failed"
        _error(report, "intake", exc)
    if report.switch_enabled:
        try:
            await _validate_imported(pool, now, report)
        except Exception as exc:  # noqa: BLE001
            _error(report, "validate", exc)
    try:
        await _reconcile(pool, now, report)
    except Exception as exc:  # noqa: BLE001
        _error(report, "reconcile", exc)
    try:
        await _retain(pool, now, report)
    except Exception as exc:  # noqa: BLE001
        _error(report, "retention", exc)
    _emit(report)
    logger.info(
        "public MCP proposal import: switch=%s intake=%s copied=%d deleted=%d outcomes=%s "
        "reconciled=%s cleared=%d errors=%s",
        "enabled" if report.switch_enabled else "disabled",
        report.intake,
        report.copied,
        report.deleted,
        dict(report.outcomes),
        dict(report.reconciled),
        report.cleared,
        dict(report.errors),
    )
    return report.as_dict()


def _error(report: PassReport, step: str, exc: BaseException, proposal_id: Any = None) -> None:
    report.errors[step] += 1
    # The class name only: a driver message can quote a row, and a row holds outside text.
    logger.warning(
        "public MCP proposal import: %s failed (%s)%s",
        step,
        type(exc).__name__,
        f" for proposal {proposal_id}" if proposal_id is not None else "",
    )


async def _switch_enabled(pool: Any, report: PassReport) -> bool:
    """Both rows present and ``enabled`` on the primary; anything else parks nothing."""
    try:
        rows = await pool.fetch(
            "SELECT domain, state FROM mcp_surface.controls WHERE domain = ANY($1::text[])",
            list(SWITCH_DOMAINS),
        )
    except Exception as exc:  # noqa: BLE001 -- unreadable switch fails closed
        _error(report, "switch", exc)
        return False
    states = {row["domain"]: row["state"] for row in rows}
    return all(states.get(domain) == "enabled" for domain in SWITCH_DOMAINS)


# ---------------------------------------------------------------------------
# Intake: copy, then delete
# ---------------------------------------------------------------------------


async def _intake(pool: Any, settings: InboxSettings, now: datetime, report: PassReport) -> None:
    password = settings.read_password()
    if password is None:
        report.intake = "skipped_no_credential"
        logger.warning(
            "public MCP proposal import: no %s password file is mounted; intake skipped",
            READER_ROLE,
        )
        return
    kwargs: dict[str, Any] = {}
    if settings.ssl is not None:
        kwargs["ssl"] = settings.ssl
    inbox = await asyncpg.connect(
        host=settings.host,
        port=settings.port,
        user=READER_ROLE,
        password=password,
        database=settings.database,
        timeout=10,
        server_settings={
            "search_path": "pg_catalog, pg_temp",
            "application_name": "butlers-proposal-importer",
        },
        **kwargs,
    )
    try:
        report.inbox_depth = await inbox.fetchval("SELECT count(*) FROM mcp_inbox.proposals")
        rows = await inbox.fetch(
            f"SELECT {', '.join(_INBOX_COLUMNS)} FROM mcp_inbox.proposals "
            "ORDER BY received_at, proposal_id LIMIT $1",
            INBOX_BATCH,
        )
        if not rows:
            report.intake = "empty"
            return
        ids = [row["proposal_id"] for row in rows]
        report.copied = await copy_to_primary(pool, rows, now)
        report.already_present = len(rows) - report.copied
        report.deleted = await delete_from_inbox(inbox, ids)
        report.intake = "copied"
    finally:
        await inbox.close()


async def copy_to_primary(pool: Any, rows: list[Any], now: datetime) -> int:
    """Insert inbox rows as ``imported`` with no-op-on-conflict; return how many were new."""
    columns = {name: [row[name] for row in rows] for name in _INBOX_COLUMNS}
    async with pool.acquire() as conn, conn.transaction():
        inserted = await conn.fetch(
            f"""
            INSERT INTO {_TABLE} (proposal_id, sub, cid, idempotency_key, subject_entity_id,
                predicate, object_entity_id, evidence_url, note, received_at, state,
                imported_at)
            SELECT p.*, 'imported', $11::timestamptz
              FROM unnest($1::uuid[], $2::text[], $3::text[], $4::text[], $5::uuid[],
                          $6::text[], $7::uuid[], $8::text[], $9::text[], $10::timestamptz[])
                   AS p(proposal_id, sub, cid, idempotency_key, subject_entity_id, predicate,
                        object_entity_id, evidence_url, note, received_at)
            ON CONFLICT (proposal_id) DO NOTHING
            RETURNING proposal_id
            """,
            *(columns[name] for name in _INBOX_COLUMNS),
            now,
        )
    return len(inserted)


async def delete_from_inbox(inbox: Any, ids: list[uuid.UUID]) -> int:
    """Delete exactly the ids this pass read and recorded on the primary."""
    status = await inbox.execute(
        "DELETE FROM mcp_inbox.proposals WHERE proposal_id = ANY($1::uuid[])", ids
    )
    return int(str(status).rsplit(" ", 1)[-1])


# ---------------------------------------------------------------------------
# Validation and parking
# ---------------------------------------------------------------------------


async def _validate_imported(pool: Any, now: datetime, report: PassReport) -> None:
    ids = await pool.fetch(
        f"SELECT proposal_id FROM {_TABLE} WHERE state = 'imported' AND received_at <= $1 "
        "ORDER BY received_at, proposal_id LIMIT $2",
        now - SETTLE,
        VALIDATE_LIMIT,
    )
    for row in ids:
        proposal_id = row["proposal_id"]
        try:
            outcome = await validate_and_park(pool, proposal_id, now)
        except Exception as exc:  # noqa: BLE001 -- the row stays imported for the next pass
            _error(report, "park", exc, proposal_id)
            continue
        if outcome is not None:
            report.outcomes[outcome] += 1


async def validate_and_park(pool: Any, proposal_id: uuid.UUID, now: datetime) -> str | None:
    """Decide one ``imported`` proposal in one transaction; ``None`` if it was not imported."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", _LOCK_KEY)
        row = await conn.fetchrow(
            f"SELECT * FROM {_TABLE} WHERE proposal_id = $1 AND state = 'imported' FOR UPDATE",
            proposal_id,
        )
        if row is None:
            return None
        outcome = await first_failure(conn, row)
        if outcome is None:
            try:
                async with conn.transaction():
                    admission = await _parking.park_external_fact_proposal(
                        conn,
                        proposal_id=row["proposal_id"],
                        subject=row["subject_entity_id"],
                        predicate=row["predicate"],
                        object_entity_id=row["object_entity_id"],
                        client_label=client_label(row["cid"]) or "",
                        evidence_url=row["evidence_url"],
                        received_at=row["received_at"],
                    )
            except ValueError:
                # The park path refused the input itself (for example a predicate the
                # registry does not know); the savepoint rolled the park back.
                outcome = "invalid"
            else:
                await conn.execute(
                    f"UPDATE {_TABLE} SET state = 'parked', pending_action_id = $2 "
                    "WHERE proposal_id = $1",
                    proposal_id,
                    admission.action_id,
                )
                return "parked"
        await conn.execute(
            f"UPDATE {_TABLE} SET state = $2, decided_at = $3 WHERE proposal_id = $1",
            proposal_id,
            outcome,
            now,
        )
        return outcome


async def first_failure(conn: Any, row: Any) -> str | None:
    """The first failing check's state, in REQ-public-mcp-proposals-004 order, or ``None``."""
    if not await _well_formed(conn, row):
        return "invalid"
    if _secret_shaped(row["note"]) or _secret_shaped(row["evidence_url"]):
        return "invalid"
    if await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM relationship.entity_facts WHERE subject = $1 "
        "AND predicate = $2 AND object_entity_id = $3 AND validity = 'active')",
        row["subject_entity_id"],
        row["predicate"],
        row["object_entity_id"],
    ):
        return "duplicate"
    if await conn.fetchval(
        f"SELECT EXISTS (SELECT 1 FROM {_TABLE} WHERE sub = $1 AND cid = $2 "
        "AND idempotency_key = $3 AND (received_at, proposal_id) < ($4, $5))",
        row["sub"],
        row["cid"],
        row["idempotency_key"],
        row["received_at"],
        row["proposal_id"],
    ):
        return "conflict"
    if await _rate_limited(conn, row):
        return "rate_limited"
    return None


async def _well_formed(conn: Any, row: Any) -> bool:
    """Schema, allowlist, representable label and URL, live entities, non-owner subject."""
    if any(row[name] is None for name in _INBOX_COLUMNS if name not in ("evidence_url", "note")):
        return False
    if row["predicate"] not in proposable_predicates():
        return False
    if row["note"] is not None and len(row["note"]) > NOTE_MAX:
        return False
    try:
        validate_client_label(client_label(row["cid"]) or "")
        if row["evidence_url"] is not None:
            evidence_url_host(row["evidence_url"])
    except ValueError:
        return False
    entities = await conn.fetch(
        "SELECT id, 'owner' = ANY(COALESCE(roles, ARRAY[]::text[])) AS owner FROM public.entities "
        "WHERE id = ANY($1::uuid[]) AND metadata->>'merged_into' IS NULL "
        "AND metadata->>'deleted_at' IS NULL",
        [row["subject_entity_id"], row["object_entity_id"]],
    )
    live = {entity["id"]: entity["owner"] for entity in entities}
    if row["subject_entity_id"] not in live or row["object_entity_id"] not in live:
        return False
    return not live[row["subject_entity_id"]]


def _secret_shaped(value: str | None) -> bool:
    """The response DLP's counted rules (credentials and identifiers), not contact rules."""
    if value is None:
        return False
    scan = Scan(shared=False)
    scan.text(value)
    return scan.counted > 0


async def _rate_limited(conn: Any, row: Any) -> bool:
    received_at: datetime = row["received_at"]
    day_start = received_at.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    earlier_today = await conn.fetchval(
        f"SELECT count(*) FROM {_TABLE} WHERE sub = $1 AND cid = $2 AND received_at >= $3 "
        "AND (received_at, proposal_id) < ($4, $5)",
        row["sub"],
        row["cid"],
        day_start,
        received_at,
        row["proposal_id"],
    )
    if earlier_today >= DAY_CAP:
        return True
    unresolved = await conn.fetchval(
        f"SELECT count(*) FROM {_TABLE} WHERE sub = $1 AND cid = $2 AND state = 'parked'",
        row["sub"],
        row["cid"],
    )
    return unresolved >= UNRESOLVED_CAP


# ---------------------------------------------------------------------------
# Expiry, reconciliation and retention
# ---------------------------------------------------------------------------


async def _reconcile(pool: Any, now: datetime, report: PassReport) -> None:
    stale = await pool.fetch(
        f"SELECT a.id FROM {_TABLE} p JOIN pending_actions a ON a.id = p.pending_action_id "
        "WHERE p.state = 'parked' AND a.status = 'pending' AND a.expires_at IS NOT NULL "
        "AND a.expires_at < $1",
        now,
    )
    for row in stale:
        transition = await transition_pending_action(
            pool,
            action_id=row["id"],
            target_status=ActionStatus.EXPIRED,
            decided_by=_ACTOR,
            event_actor=_ACTOR,
            event_reason="approval window elapsed",
            event_metadata={"tool_name": "relationship_assert_fact"},
            now=now,
        )
        report.expired_actions += int(transition.changed)
    decidable = await pool.fetch(
        f"""
        SELECT p.proposal_id, p.state, a.status,
               EXISTS (SELECT 1 FROM approval_events e WHERE e.action_id = a.id
                         AND e.event_type = 'action_execution_failed') AS failed
          FROM {_TABLE} p JOIN pending_actions a ON a.id = p.pending_action_id
         WHERE p.state IN ('parked', 'apply_failed')
        """
    )
    for row in decidable:
        target = proposal_state_for(row["status"], failed=row["failed"])
        if target is None or target == row["state"]:
            continue
        status = await pool.execute(
            f"UPDATE {_TABLE} SET state = $2, decided_at = $3 WHERE proposal_id = $1 "
            "AND state = $4",
            row["proposal_id"],
            target,
            now,
            row["state"],
        )
        if str(status).endswith(" 1"):
            report.reconciled[target] += 1


def proposal_state_for(action_status: str, *, failed: bool) -> str | None:
    """Map a parked action's status (and its failure events) to a proposal state."""
    if action_status in _ACTION_OUTCOMES:
        return _ACTION_OUTCOMES[action_status]
    if action_status == ActionStatus.APPROVED.value and failed:
        return "apply_failed"
    return None


async def _retain(pool: Any, now: datetime, report: PassReport) -> None:
    status = await pool.execute(
        f"UPDATE {_TABLE} SET note = NULL, evidence_url = NULL "
        "WHERE decided_at < $1 AND state = ANY($2::text[]) "
        "AND (note IS NOT NULL OR evidence_url IS NOT NULL)",
        now - RETENTION,
        list(TERMINAL_STATES),
    )
    report.cleared = int(str(status).rsplit(" ", 1)[-1])


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _emit(report: PassReport) -> None:
    """Stable labels only. ``apply_failed`` outcomes feed the RFC 0039 D16 alert."""
    meter = metrics.get_meter("butlers")
    outcomes = meter.create_counter(
        "butlers.public_mcp.proposal_import.outcomes",
        description="Proposal state transitions made by the importer, by resulting state.",
    )
    for state, count in (report.outcomes + report.reconciled).items():
        outcomes.add(count, {"state": state})
    if report.copied:
        meter.create_counter(
            "butlers.public_mcp.proposal_import.copied",
            description="Inbox rows newly copied to the primary.",
        ).add(report.copied)
    errors = meter.create_counter(
        "butlers.public_mcp.proposal_import.errors",
        description="Importer step failures, by step.",
    )
    for step, count in report.errors.items():
        errors.add(count, {"step": step})
    if report.intake == "skipped_no_credential":
        meter.create_counter(
            "butlers.public_mcp.proposal_import.intake_skipped",
            description="Passes that skipped intake because no reader password is mounted.",
        ).add(1, {"reason": "no_credential"})
    if report.inbox_depth is not None:
        meter.create_gauge(
            "butlers.public_mcp.proposal_import.inbox_depth",
            description="Undrained inbox rows seen at the start of the last intake.",
        ).set(report.inbox_depth)
    meter.create_gauge(
        "butlers.public_mcp.proposal_import.switch_enabled",
        description="1 while the proposals kill switch lets the importer park.",
    ).set(int(report.switch_enabled))


__all__ = [
    "DAY_CAP",
    "INBOX_BATCH",
    "JOB_NAME",
    "RETENTION",
    "UNRESOLVED_CAP",
    "InboxSettings",
    "PassReport",
    "copy_to_primary",
    "delete_from_inbox",
    "first_failure",
    "proposal_state_for",
    "run_public_mcp_proposal_import",
    "validate_and_park",
]
