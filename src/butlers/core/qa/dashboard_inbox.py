"""Owner-side durable dashboard custody (REQ-staffer-qa-003/004).

Deliberately not registered as an MCP tool or wired into the live patrol. The
released relay lacks the required QA peer identity/audience boundary. A future
transport owner must authenticate that peer before invoking these owner APIs;
neither these types nor a stored receipt authenticate a network request.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from butlers.core.goal_admission import digest
from butlers.core.healing.anonymizer import anonymize
from butlers.core.healing.fingerprint import compute_fingerprint_from_report
from butlers.core.qa.models import QaFinding
from butlers.core.queue_claim import ClaimQueue, claim_batch


class InboxRefused(ValueError):
    """Content-blind refusal; never include finding/context or driver diagnostics."""


@dataclass(frozen=True)
class DashboardIdentity:
    terminal_action_id: UUID
    terminal_effect_id: UUID
    terminal_effect_idempotency_key: str

    def __post_init__(self):
        if (
            not isinstance(self.terminal_action_id, UUID)
            or not isinstance(self.terminal_effect_id, UUID)
            or type(self.terminal_effect_idempotency_key) is not str
            or not 0 < len(self.terminal_effect_idempotency_key) <= 256
            or "\x00" in self.terminal_effect_idempotency_key
        ):
            raise InboxRefused("dashboard_identity_invalid")


@dataclass(frozen=True)
class DashboardReport:
    exception_type: str
    call_site: str
    severity: int
    event_summary: str
    context: str | None = None

    def __post_init__(self):
        if (
            type(self.severity) is not int
            or any(type(v) is not str or not v for v in (self.exception_type, self.call_site))
            or len(self.exception_type) > 256
            or len(self.call_site) > 512
            or type(self.event_summary) is not str
            or len(self.event_summary) > 200
            or (self.context is not None and type(self.context) is not str)
        ):
            raise InboxRefused("dashboard_report_invalid")
        if any(
            "\x00" in value
            for value in (
                self.exception_type,
                self.call_site,
                self.event_summary,
                self.context or "",
            )
        ):
            raise InboxRefused("dashboard_report_invalid")

    def canonical(self, repo_root: Path) -> dict:
        # Hash the canonical incoming bytes before anonymization too: two inputs
        # that redact to the same summary must still conflict at the receiver.
        severity = max(0, min(4, self.severity))
        hint = {0: "critical", 1: "high", 2: "medium", 3: "low", 4: "info"}[severity]
        result = compute_fingerprint_from_report(
            error_type=self.exception_type,
            error_message=self.event_summary,
            call_site=self.call_site,
            traceback_str=None,
            severity_hint=hint,
        )
        return {
            "fingerprint": result.fingerprint,
            "exception_type": self.exception_type,
            "call_site": self.call_site,
            "severity": result.severity,
            "event_summary": anonymize(self.event_summary, repo_root),
            "context": anonymize(self.context, repo_root) if self.context else None,
            "source_butler": "switchboard",
        }


@dataclass(frozen=True)
class DashboardClaim:
    inbox_id: UUID
    generation: int
    owner: str
    # A trusted process-config reader, never a report field or serialized flag.
    source_enabled: Callable[[], bool] = field(repr=False, compare=False)


_QUEUE = ClaimQueue(
    table="public.qa_dashboard_reports",
    key="id",
    eligible="q.inbox_state IN ('pending','claimed')",
    order_by="q.created_at, q.id",
    claim_set=(
        ("inbox_state", "'claimed'"),
        ("generation", "q.generation+1"),
        ("updated_at", "now()"),
    ),
    returning=("id", "generation", "lease_owner", "payload", "created_at"),
    reclaim_expired=True,
    lease_until="lease_until",
    lease_owner="lease_owner",
)


def _receipt(row) -> dict:
    result = {
        "terminal_action_id": str(row["terminal_action_id"]),
        "terminal_effect_id": str(row["terminal_effect_id"]),
        "inbox_state": row["inbox_state"],
        "created_at": row["created_at"].isoformat(),
    }
    if row["inbox_state"] == "acknowledged":
        result["finding_id"] = str(row["finding_id"])
    return result


def _finding(payload: dict, created_at: datetime, claim: DashboardClaim) -> QaFinding:
    return QaFinding(
        **payload,
        source_type="butler_reports",
        occurrence_count=1,
        first_seen=created_at,
        last_seen=created_at,
        timestamp=created_at,
        dashboard_claim=claim,
    )


class DashboardInbox:
    """QA-owned store. ``enabled`` comes from trusted source configuration.

    Receipt reads remain available when the source is disabled. Callers must
    reconcile uncertain acceptance by exact lookup, never by blindly retrying.
    No acceptance or receipt method here is a service authentication adapter.
    """

    def __init__(self, pool, *, enabled: Callable[[], bool], repo_root: Path):
        self.pool, self.enabled, self.repo_root = pool, enabled, repo_root

    async def accept(self, identity: DashboardIdentity, report: DashboardReport) -> dict:
        if not self.enabled():
            raise InboxRefused("butler_reports_disabled")
        payload = report.canonical(self.repo_root)
        payload_hash = digest({"report": asdict(report), "source_butler": "switchboard"})
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO public.qa_dashboard_reports "
                "(terminal_action_id,terminal_effect_id,idempotency_key,payload_hash,payload) "
                "VALUES ($1,$2,$3,$4,$5::jsonb) "
                "ON CONFLICT (terminal_action_id,terminal_effect_id) DO NOTHING",
                identity.terminal_action_id,
                identity.terminal_effect_id,
                identity.terminal_effect_idempotency_key,
                payload_hash,
                payload,
            )
            # A second statement sees a concurrent winner after the INSERT waits.
            row = await conn.fetchrow(
                "SELECT * FROM public.qa_dashboard_reports "
                "WHERE terminal_action_id=$1 AND terminal_effect_id=$2",
                identity.terminal_action_id,
                identity.terminal_effect_id,
            )
            if row["idempotency_key"] != identity.terminal_effect_idempotency_key or (
                row["payload_hash"] != payload_hash
            ):
                raise InboxRefused("dashboard_identity_conflict")
            return {"accepted": True, "delivery": "dashboard_durable", "receipt": _receipt(row)}

    async def receipt(self, action_id: UUID, effect_id: UUID) -> dict:
        if not isinstance(action_id, UUID) or not isinstance(effect_id, UUID):
            raise InboxRefused("dashboard_identity_invalid")
        try:
            row = await self.pool.fetchrow(
                "SELECT terminal_action_id,terminal_effect_id,inbox_state,created_at,finding_id "
                "FROM public.qa_dashboard_reports WHERE terminal_action_id=$1 "
                "AND terminal_effect_id=$2",
                action_id,
                effect_id,
            )
            return {"status": "found", "receipt": _receipt(row)} if row else {"status": "not_found"}
        except Exception:
            # No exception text: driver errors can contain sensitive payloads.
            return {"status": "unavailable"}

    async def claim(self, *, limit: int = 50, lease_seconds: int = 120) -> list[QaFinding]:
        if not self.enabled():
            return []
        owner = str(uuid4())
        async with self.pool.acquire() as conn, conn.transaction():
            rows = await claim_batch(
                conn, _QUEUE, limit=limit, owner=owner, lease_seconds=lease_seconds
            )
        return [
            _finding(
                row["payload"],
                row["created_at"],
                DashboardClaim(row["id"], row["generation"], owner, self.enabled),
            )
            for row in rows
        ]

    async def acknowledged(self, finding_id: UUID):
        """Owner-side snapshot for the future authenticated evidence reader.

        This is not a control-plane SQL reader or a provenance authenticator.
        No production caller is wired until the service owner releases that
        boundary. Disabled sources retain receipt inspection but refuse input
        for a new admission; existing decisions reconcile in the goal ledger.
        """
        from butlers.core.qa.staffer_source import DashboardFinding

        if not self.enabled():
            raise InboxRefused("butler_reports_disabled")
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM public.qa_dashboard_reports "
                "WHERE finding_id=$1 AND inbox_state='acknowledged'",
                finding_id,
            )
            if row is None:
                raise InboxRefused("dashboard_finding_unacknowledged")
            finding = await conn.fetchrow(
                "SELECT * FROM public.qa_findings WHERE id=$1", finding_id
            )
            if finding is None or any(
                finding[key] != row["payload"][key]
                for key in (
                    "fingerprint",
                    "exception_type",
                    "call_site",
                    "severity",
                    "event_summary",
                    "source_butler",
                )
            ):
                raise InboxRefused("dashboard_finding_mismatch")
            return DashboardFinding(
                DashboardIdentity(
                    row["terminal_action_id"], row["terminal_effect_id"], row["idempotency_key"]
                ),
                row["payload_hash"],
                row["generation"],
                finding_id,
                dict(finding),
            )

    @staticmethod
    async def persist_finding(conn, patrol_id, finding, dedup_reason, healing_attempt_id):
        """Insert and acknowledge in one transaction; fencing precedes every insert.

        Caller owns the transaction. Acknowledged same-generation retries return
        the original finding; an expired/superseded claimant cannot create one.
        """
        from butlers.core.qa.findings import insert_finding

        if not conn.is_in_transaction():
            raise RuntimeError("dashboard acknowledgement requires a transaction")
        claim = finding.dashboard_claim
        if not claim.source_enabled():
            raise InboxRefused("butler_reports_disabled")
        row = await conn.fetchrow(
            "SELECT *,lease_until>clock_timestamp() AS live FROM public.qa_dashboard_reports "
            "WHERE id=$1 FOR UPDATE",
            claim.inbox_id,
        )
        if not row or row["generation"] != claim.generation or row["lease_owner"] != claim.owner:
            raise InboxRefused("dashboard_claim_stale")
        if row["inbox_state"] == "acknowledged":
            return row["finding_id"]
        if row["inbox_state"] != "claimed" or not row["live"]:
            raise InboxRefused("dashboard_claim_stale")
        # Rebuild from stored canonical bytes, never from a caller's modified finding.
        canonical = _finding(row["payload"], row["created_at"], claim)
        finding_id = await insert_finding(
            conn,
            patrol_id,
            replace(canonical, dashboard_claim=None),
            dedup_reason,
            healing_attempt_id,
        )
        updated = await conn.fetchval(
            "UPDATE public.qa_dashboard_reports SET inbox_state='acknowledged',finding_id=$4, "
            "updated_at=now() WHERE id=$1 AND generation=$2 AND lease_owner=$3 "
            "AND inbox_state='claimed' AND lease_until>clock_timestamp() RETURNING id",
            claim.inbox_id,
            claim.generation,
            claim.owner,
            finding_id,
        )
        if updated is None:
            raise InboxRefused("dashboard_claim_stale")
        if not claim.source_enabled():
            # A disable during an awaited insert rolls the entire handoff back.
            raise InboxRefused("butler_reports_disabled")
        return finding_id
