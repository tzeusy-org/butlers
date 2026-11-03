"""Operator-only demotion of laundered owner-authority memory (bu-dhloi1.1).

Before consolidation was partitioned by content authority, one session could
read an owner episode and a stranger's episode together and stamp a fact or
rule derived from both as ``owner``.  The owner decided (bu-7ry1sy q1) that the
owner-class facts and rules a butler consolidated after mem_013, when that
butler also consolidated third-party episodes, are demoted to ``mixed``: kept,
attributed, and no longer steering.

This module classifies and applies that decision for ONE memory schema.  It is
never called by a daemon, scheduler, MCP tool or API; the only caller is
``scripts/memory_authority_demotion.py``, run by an operator under the runbook
``docs/operations/memory-authority-demotion.md``.

The database cannot prove the historical facts the decision depends on (when
mem_013 and the partition fix were installed, which butlers consolidated
third-party content, whether ``derived_from`` links were written only by
consolidation).  An operator supplies those as a schema-specific *evidence
manifest*.  Anything the manifest does not evidence is UNKNOWN, never clean:

* **Dry run** (the default) is read-only and content-blind.  It reports per
  tenant/butler/type counts by disposition and reason, plus the candidate IDs
  with preimage digests and one candidate-set digest.
* **Apply** needs an approval bound to that exact manifest digest, candidate
  digest and count.  In one transaction per schema it re-classifies under row
  locks, refuses on any UNKNOWN, endorsement anomaly or drift, then changes
  only ``content_authority`` to ``mixed`` per candidate (compare-and-swap),
  disowns the matching ``public.memory_catalog`` row and appends one
  ``memory_events`` receipt per row.  Any failure rolls the whole schema back.
  Replaying an operation key returns its original receipts and writes nothing.

There is no restore, inverse promotion or delete here.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from butlers.modules.memory.content_authority import MIXED, OWNER_CLASS

PREDICATE_VERSION = "authority-demotion/v1"
MANIFEST_VERSION = 1
APPROVAL_VERSION = 1
RECEIPT_EVENT = "content_authority_demoted"
OPERATION_EVENT = "content_authority_demotion_operation"
ACTOR = "operator:authority_demotion"

CANDIDATE = "candidate"
EXCLUDED = "excluded"
UNCHANGED = "unchanged"
UNKNOWN = "unknown"
ANOMALY = "anomaly"

_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z")
_OPERATION_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")

# Columns the classifier and apply depend on.  Anything else is schema drift.
_REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "facts": frozenset(
        {"id", "tenant_id", "source_butler", "content_authority", "authority_entity_id"}
        | {"created_at", "validity", "content"}
    ),
    "rules": frozenset(
        {"id", "tenant_id", "source_butler", "content_authority", "authority_entity_id"}
        | {"created_at", "maturity", "retired_at", "metadata", "content", "endorsed_at"}
    ),
    "memory_links": frozenset({"source_type", "source_id", "target_type", "relation"}),
    "memory_events": frozenset(
        {"event_type", "actor", "tenant_id", "actor_butler", "request_id"}
        | {"memory_type", "memory_id", "payload", "created_at"}
    ),
}


class ManifestError(ValueError):
    """The evidence manifest or approval is malformed; nothing was read or written."""


class DemotionRefused(RuntimeError):
    """Apply refused before committing anything.  ``reason`` is a closed code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# Evidence manifest and approval
# ---------------------------------------------------------------------------


def _canonical_digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _evidence_ref(raw: Mapping[str, Any], key: str) -> str | None:
    ref = raw.get("evidence")
    if ref is None:
        return None
    if not isinstance(ref, str) or not ref.strip():
        raise ManifestError(f"{key}.evidence must be a non-empty string or null")
    return ref


def _strict_keys(raw: Any, key: str, allowed: set[str]) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise ManifestError(f"{key} must be an object")
    extra = set(raw) - allowed
    if extra:
        raise ManifestError(f"{key} has unsupported keys: {sorted(extra)}")
    return raw


def _parse_boundary(raw: Any, key: str) -> datetime | None:
    """An evidenced install boundary, or None when the operator could not evidence it."""
    if raw is None:
        return None
    entry = _strict_keys(raw, key, {"at", "evidence"})
    if _evidence_ref(entry, key) is None:
        return None
    at = entry.get("at")
    if not isinstance(at, str):
        raise ManifestError(f"{key}.at must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(at)
    except ValueError as exc:
        raise ManifestError(f"{key}.at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ManifestError(f"{key}.at must carry a UTC offset")
    return parsed


@dataclass(frozen=True)
class CohortMember:
    """One (tenant, butler) the owner's affected-butler cohort names."""

    tenant_id: str
    source_butler: str
    third_party_evidenced: bool


@dataclass(frozen=True)
class EvidenceManifest:
    """Operator-supplied historical evidence for one memory schema.

    ``None`` boundaries and unevidenced members or derivation are deliberately
    representable: they classify affected rows as UNKNOWN instead of failing
    the parse, so a dry run still measures the unresolved remainder.
    """

    schema: str
    mem013_boundary: datetime | None
    partition_fix_boundary: datetime | None
    cohort: tuple[CohortMember, ...]
    derived_from_attested: bool
    digest: str

    @classmethod
    def parse(cls, raw: Any) -> EvidenceManifest:
        top = _strict_keys(
            raw,
            "manifest",
            {
                "manifest_version",
                "predicate_version",
                "schema",
                "mem013_boundary",
                "partition_fix_boundary",
                "affected_cohort",
                "derivation_evidence",
            },
        )
        if top.get("manifest_version") != MANIFEST_VERSION:
            raise ManifestError(f"manifest_version must be {MANIFEST_VERSION}")
        if top.get("predicate_version") != PREDICATE_VERSION:
            raise ManifestError(f"predicate_version must be {PREDICATE_VERSION!r}")
        schema = top.get("schema")
        if not isinstance(schema, str) or not _IDENTIFIER.fullmatch(schema):
            raise ManifestError("schema must be a lowercase SQL identifier")

        cohort_raw = top.get("affected_cohort")
        if not isinstance(cohort_raw, list) or not cohort_raw:
            raise ManifestError("affected_cohort must be a non-empty list")
        members: dict[tuple[str, str], CohortMember] = {}
        for index, entry_raw in enumerate(cohort_raw):
            key = f"affected_cohort[{index}]"
            entry = _strict_keys(entry_raw, key, {"tenant_id", "source_butler", "evidence"})
            tenant_id = entry.get("tenant_id")
            butler = entry.get("source_butler")
            if not isinstance(tenant_id, str) or not tenant_id:
                raise ManifestError(f"{key}.tenant_id must be a non-empty string")
            if not isinstance(butler, str) or not butler:
                raise ManifestError(f"{key}.source_butler must be a non-empty string")
            if (tenant_id, butler) in members:
                raise ManifestError(f"{key} duplicates an earlier cohort member")
            members[(tenant_id, butler)] = CohortMember(
                tenant_id, butler, _evidence_ref(entry, key) is not None
            )

        derivation = top.get("derivation_evidence")
        attested = False
        if derivation is not None:
            entry = _strict_keys(
                derivation,
                "derivation_evidence",
                {"derived_from_links_are_consolidation_only", "evidence"},
            )
            flag = entry.get("derived_from_links_are_consolidation_only")
            if not isinstance(flag, bool):
                raise ManifestError(
                    "derivation_evidence.derived_from_links_are_consolidation_only must be a bool"
                )
            attested = flag and _evidence_ref(entry, "derivation_evidence") is not None

        return cls(
            schema=schema,
            mem013_boundary=_parse_boundary(top.get("mem013_boundary"), "mem013_boundary"),
            partition_fix_boundary=_parse_boundary(
                top.get("partition_fix_boundary"), "partition_fix_boundary"
            ),
            cohort=tuple(sorted(members.values(), key=lambda m: (m.tenant_id, m.source_butler))),
            derived_from_attested=attested,
            digest=_canonical_digest(raw),
        )

    def member(self, tenant_id: str, source_butler: str | None) -> CohortMember | None:
        for member in self.cohort:
            if member.tenant_id == tenant_id and member.source_butler == source_butler:
                return member
        return None


@dataclass(frozen=True)
class Approval:
    """The owner's approval of one exact dry-run result, recorded by the operator.

    The code trusts nothing in ``approved_by``; the approval is valid only while
    the manifest digest, candidate digest and count it names still match.
    """

    schema: str
    operation_key: str
    manifest_digest: str
    candidate_digest: str
    candidate_count: int
    approved_by: str

    @classmethod
    def parse(cls, raw: Any) -> Approval:
        top = _strict_keys(
            raw,
            "approval",
            {
                "approval_version",
                "schema",
                "operation_key",
                "manifest_digest",
                "candidate_digest",
                "candidate_count",
                "approved_by",
            },
        )
        if top.get("approval_version") != APPROVAL_VERSION:
            raise ManifestError(f"approval_version must be {APPROVAL_VERSION}")
        schema = top.get("schema")
        op_key = top.get("operation_key")
        count = top.get("candidate_count")
        approved_by = top.get("approved_by")
        if not isinstance(schema, str) or not _IDENTIFIER.fullmatch(schema):
            raise ManifestError("approval.schema must be a lowercase SQL identifier")
        if not isinstance(op_key, str) or not _OPERATION_KEY.fullmatch(op_key):
            raise ManifestError("approval.operation_key must be 8-128 safe characters")
        for key in ("manifest_digest", "candidate_digest"):
            value = top.get(key)
            if not isinstance(value, str) or not _DIGEST.fullmatch(value):
                raise ManifestError(f"approval.{key} must be a sha256 hex digest")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ManifestError("approval.candidate_count must be a positive integer")
        if not isinstance(approved_by, str) or not approved_by.strip():
            raise ManifestError("approval.approved_by must name the recorded owner decision")
        return cls(
            schema=schema,
            operation_key=op_key,
            manifest_digest=top["manifest_digest"],
            candidate_digest=top["candidate_digest"],
            candidate_count=count,
            approved_by=approved_by,
        )


# ---------------------------------------------------------------------------
# Classification (pure)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryRow:
    """The content-blind slice of a fact or rule the classifier reads."""

    memory_type: str
    id: uuid.UUID
    tenant_id: str
    source_butler: str | None
    content_authority: str | None
    authority_entity_id: uuid.UUID | None
    created_at: datetime
    endorsed_at: datetime | None
    state: str
    content_digest: str
    derived_from_links: int

    @property
    def preimage_digest(self) -> str:
        return _canonical_digest(
            [
                self.memory_type,
                str(self.id),
                self.tenant_id,
                self.source_butler,
                self.content_authority,
                str(self.authority_entity_id) if self.authority_entity_id else None,
                self.created_at.isoformat(),
                self.endorsed_at.isoformat() if self.endorsed_at else None,
                self.state,
                self.content_digest,
                self.derived_from_links,
            ]
        )


def classify(row: MemoryRow, manifest: EvidenceManifest) -> tuple[str, str]:
    """Return ``(disposition, reason)`` for one row of the manifest's cohort.

    Order matters: a non-owner row is a no-op whatever the evidence says; every
    missing piece of evidence for an owner-class row yields UNKNOWN; only a row
    with every fact evidenced is a candidate.
    """
    if row.content_authority not in OWNER_CLASS:
        # Already mixed/third_party/system, or NULL legacy: never guessed upward.
        return UNCHANGED, "not_owner_class"
    member = manifest.member(row.tenant_id, row.source_butler)
    if member is None:
        return EXCLUDED, "outside_cohort"
    if manifest.mem013_boundary is None:
        return UNKNOWN, "mem013_boundary_unevidenced"
    if row.created_at < manifest.mem013_boundary:
        return EXCLUDED, "before_mem013"
    if manifest.partition_fix_boundary is None:
        return UNKNOWN, "partition_fix_boundary_unevidenced"
    if row.created_at >= manifest.partition_fix_boundary:
        return EXCLUDED, "after_partition_fix"
    if not member.third_party_evidenced:
        return UNKNOWN, "affected_butler_unevidenced"
    if row.derived_from_links == 0:
        # A direct owner write and a consolidation artifact whose evidence was
        # never linked look identical here.
        return UNKNOWN, "consolidation_derivation_unproven"
    if not manifest.derived_from_attested:
        return UNKNOWN, "derivation_evidence_unattested"
    if row.memory_type == "rule" and row.endorsed_at is not None:
        # Demoting would silently void a genuine owner endorsement.
        return ANOMALY, "endorsed_owner_class_rule"
    return CANDIDATE, "consolidation_derived_owner_class"


@dataclass(frozen=True)
class Candidate:
    memory_type: str
    id: uuid.UUID
    tenant_id: str
    source_butler: str | None
    prior_authority: str
    preimage_digest: str


@dataclass(frozen=True)
class DryRunReport:
    """A deterministic, content-blind classification of one schema."""

    schema: str
    manifest_digest: str
    catalog: str
    counts: Mapping[str, Mapping[str, int]]
    candidates: tuple[Candidate, ...]
    unknown_count: int
    anomaly_count: int
    candidate_digest: str = field(init=False)

    def __post_init__(self) -> None:
        digest = _canonical_digest(
            [[c.memory_type, str(c.id), c.preimage_digest] for c in self.candidates]
        )
        object.__setattr__(self, "candidate_digest", digest)

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)

    def to_json(self) -> dict[str, Any]:
        return {
            "predicate_version": PREDICATE_VERSION,
            "schema": self.schema,
            "manifest_digest": self.manifest_digest,
            "catalog": self.catalog,
            "candidate_count": self.candidate_count,
            "candidate_digest": self.candidate_digest,
            "unknown_count": self.unknown_count,
            "anomaly_count": self.anomaly_count,
            "complete": self.unknown_count == 0 and self.anomaly_count == 0,
            "counts": {scope: dict(c) for scope, c in self.counts.items()},
            "candidates": [
                {"memory_type": c.memory_type, "id": str(c.id), "preimage": c.preimage_digest}
                for c in self.candidates
            ],
        }


def build_report(
    rows: Iterable[MemoryRow], manifest: EvidenceManifest, *, catalog: str
) -> DryRunReport:
    counts: dict[str, Counter[str]] = {}
    candidates: list[Candidate] = []
    unknown = anomaly = 0
    for row in rows:
        disposition, reason = classify(row, manifest)
        scope = f"{row.tenant_id}/{row.source_butler}/{row.memory_type}"
        counts.setdefault(scope, Counter())[f"{disposition}:{reason}"] += 1
        if disposition == UNKNOWN:
            unknown += 1
        elif disposition == ANOMALY:
            anomaly += 1
        elif disposition == CANDIDATE:
            candidates.append(
                Candidate(
                    row.memory_type,
                    row.id,
                    row.tenant_id,
                    row.source_butler,
                    row.content_authority or "",
                    row.preimage_digest,
                )
            )
    candidates.sort(key=lambda c: (c.memory_type, c.id))
    return DryRunReport(
        schema=manifest.schema,
        manifest_digest=manifest.digest,
        catalog=catalog,
        counts={scope: dict(sorted(c.items())) for scope, c in sorted(counts.items())},
        candidates=tuple(candidates),
        unknown_count=unknown,
        anomaly_count=anomaly,
    )


def refusal_reason(
    report: DryRunReport, manifest: EvidenceManifest, approval: Approval
) -> str | None:
    """Why ``approval`` does not authorize applying ``report`` (None when it does)."""
    if approval.schema != manifest.schema:
        return "approval_schema_mismatch"
    if approval.manifest_digest != manifest.digest:
        return "manifest_drift"
    if report.unknown_count:
        return "unresolved_unknown"
    if report.anomaly_count:
        return "endorsement_anomaly"
    if report.candidate_count != approval.candidate_count:
        return "candidate_count_drift"
    if report.candidate_digest != approval.candidate_digest:
        return "candidate_drift"
    return None


# ---------------------------------------------------------------------------
# Database seam
# ---------------------------------------------------------------------------


def _q(schema: str, table: str) -> str:
    if not _IDENTIFIER.fullmatch(schema):
        raise ManifestError("unsafe schema identifier")
    return f'"{schema}"."{table}"'


def _select_rows_sql(schema: str, *, lock: bool) -> list[str]:
    links = _q(schema, "memory_links")
    lock_clause = " FOR UPDATE OF t" if lock else ""
    cohort = "(t.tenant_id, t.source_butler) IN (SELECT * FROM unnest($1::text[], $2::text[]))"
    fact_sql = f"""
        SELECT 'fact' AS memory_type, t.id, t.tenant_id, t.source_butler, t.content_authority,
               t.authority_entity_id, t.created_at, NULL::timestamptz AS endorsed_at,
               t.validity AS state, md5(t.content) AS content_digest,
               (SELECT count(*) FROM {links} l
                 WHERE l.source_type = 'fact' AND l.source_id = t.id
                   AND l.target_type = 'episode' AND l.relation = 'derived_from')
                   AS derived_from_links
        FROM {_q(schema, "facts")} t
        WHERE {cohort}
        ORDER BY t.id{lock_clause}
    """
    rule_sql = f"""
        SELECT 'rule' AS memory_type, t.id, t.tenant_id, t.source_butler, t.content_authority,
               t.authority_entity_id, t.created_at, t.endorsed_at,
               t.maturity || '|' || COALESCE(extract(epoch FROM t.retired_at)::text, '')
                   || '|' || COALESCE(t.metadata->>'forgotten', '') AS state,
               md5(t.content) AS content_digest,
               (SELECT count(*) FROM {links} l
                 WHERE l.source_type = 'rule' AND l.source_id = t.id
                   AND l.target_type = 'episode' AND l.relation = 'derived_from')
                   AS derived_from_links
        FROM {_q(schema, "rules")} t
        WHERE {cohort}
        ORDER BY t.id{lock_clause}
    """
    return [fact_sql, rule_sql]


async def _check_supported_schema(conn: Any, schema: str) -> str:
    """Refuse an unsupported schema; return the catalog state (present/not_enabled)."""
    rows = await conn.fetch(
        "SELECT table_name, column_name FROM information_schema.columns"
        " WHERE table_schema = $1 AND table_name = ANY($2::text[])",
        schema,
        list(_REQUIRED_COLUMNS),
    )
    present: dict[str, set[str]] = {}
    for row in rows:
        present.setdefault(row["table_name"], set()).add(row["column_name"])
    for table, required in _REQUIRED_COLUMNS.items():
        if not required <= present.get(table, set()):
            raise DemotionRefused("unsupported_schema")
    if await conn.fetchval("SELECT to_regclass('public.memory_catalog')") is None:
        return "not_enabled"
    has_column = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns"
        " WHERE table_schema = 'public' AND table_name = 'memory_catalog'"
        " AND column_name = 'content_authority')"
    )
    if not has_column:
        raise DemotionRefused("catalog_authority_unsupported")
    return "present"


async def _load_rows(conn: Any, manifest: EvidenceManifest, *, lock: bool) -> list[MemoryRow]:
    tenants = [m.tenant_id for m in manifest.cohort]
    butlers = [m.source_butler for m in manifest.cohort]
    rows: list[MemoryRow] = []
    for sql in _select_rows_sql(manifest.schema, lock=lock):
        for r in await conn.fetch(sql, tenants, butlers):
            rows.append(
                MemoryRow(
                    memory_type=r["memory_type"],
                    id=r["id"],
                    tenant_id=r["tenant_id"],
                    source_butler=r["source_butler"],
                    content_authority=r["content_authority"],
                    authority_entity_id=r["authority_entity_id"],
                    created_at=r["created_at"],
                    endorsed_at=r["endorsed_at"],
                    state=r["state"],
                    content_digest=r["content_digest"],
                    derived_from_links=int(r["derived_from_links"]),
                )
            )
    return rows


async def dry_run(conn: Any, manifest: EvidenceManifest) -> DryRunReport:
    """Classify one schema in a read-only snapshot; writes nothing."""
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        catalog = await _check_supported_schema(conn, manifest.schema)
        rows = await _load_rows(conn, manifest, lock=False)
    return build_report(rows, manifest, catalog=catalog)


@dataclass(frozen=True)
class ApplyResult:
    operation_key: str
    replayed: bool
    receipts: tuple[Mapping[str, Any], ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "operation_key": self.operation_key,
            "replayed": self.replayed,
            "demoted": len(self.receipts),
            "receipts": [dict(r) for r in self.receipts],
        }


async def _existing_receipts(
    conn: Any, schema: str, approval: Approval
) -> tuple[Mapping[str, Any], ...] | None:
    events = _q(schema, "memory_events")
    operation = await conn.fetchrow(
        f"SELECT payload FROM {events} WHERE event_type = $1 AND request_id = $2",
        OPERATION_EVENT,
        approval.operation_key,
    )
    if operation is None:
        return None
    payload = _as_dict(operation["payload"])
    if payload.get("candidate_digest") != approval.candidate_digest:
        raise DemotionRefused("operation_key_reused")
    rows = await conn.fetch(
        f"SELECT payload FROM {events} WHERE event_type = $1 AND request_id = $2"
        " ORDER BY memory_type, memory_id",
        RECEIPT_EVENT,
        approval.operation_key,
    )
    return tuple(_as_dict(r["payload"]) for r in rows)


def _as_dict(payload: Any) -> dict[str, Any]:
    if isinstance(payload, str):
        return json.loads(payload)
    return dict(payload)


async def apply(conn: Any, manifest: EvidenceManifest, approval: Approval) -> ApplyResult:
    """Demote the approved candidates atomically, or refuse with nothing written.

    One transaction: a schema-scoped advisory lock serializes demotions, a
    replayed operation key returns its original receipts, and every candidate
    is re-classified under ``FOR UPDATE`` locks (deterministic id order)
    before any write.  Each row change is a compare-and-swap on its prior
    authority; a lost swap, a failing catalog write or a failing receipt
    insert raises and rolls back the whole schema.
    """
    schema = manifest.schema
    events = _q(schema, "memory_events")
    async with conn.transaction():
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
            f"{PREDICATE_VERSION}:{schema}",
        )
        replay = await _existing_receipts(conn, schema, approval)
        if replay is not None:
            return ApplyResult(approval.operation_key, True, replay)

        catalog = await _check_supported_schema(conn, schema)
        report = build_report(
            await _load_rows(conn, manifest, lock=True), manifest, catalog=catalog
        )
        reason = refusal_reason(report, manifest, approval)
        if reason is not None:
            raise DemotionRefused(reason)

        receipts: list[Mapping[str, Any]] = []
        for candidate in report.candidates:
            table = "facts" if candidate.memory_type == "fact" else "rules"
            swapped = await conn.fetchval(
                f"UPDATE {_q(schema, table)} SET content_authority = $3"
                " WHERE id = $1 AND content_authority = $2 RETURNING id",
                candidate.id,
                candidate.prior_authority,
                MIXED,
            )
            if swapped is None:
                raise DemotionRefused("compare_and_swap_lost")
            catalog_rows = 0
            if catalog == "present":
                catalog_rows = await _disown_catalog_row(conn, schema, table, candidate.id)
            receipt = {
                "operation_key": approval.operation_key,
                "predicate_version": PREDICATE_VERSION,
                "manifest_digest": manifest.digest,
                "candidate_digest": report.candidate_digest,
                "approved_by": approval.approved_by,
                "memory_type": candidate.memory_type,
                "memory_id": str(candidate.id),
                "prior_authority": candidate.prior_authority,
                "new_authority": MIXED,
                "preimage_digest": candidate.preimage_digest,
                "catalog": catalog,
                "catalog_rows_disowned": catalog_rows,
            }
            await conn.execute(
                f"INSERT INTO {events} (event_type, actor, tenant_id, actor_butler, request_id,"
                " memory_type, memory_id, payload) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)",
                RECEIPT_EVENT,
                ACTOR,
                candidate.tenant_id,
                candidate.source_butler,
                approval.operation_key,
                candidate.memory_type,
                candidate.id,
                # A dict, not json.dumps(...): the connection's JSONB codec serializes
                # it, so a pre-dumped str would land as a JSON string scalar.
                receipt,
            )
            receipts.append(receipt)

        await conn.execute(
            f"INSERT INTO {events} (event_type, actor, request_id, payload)"
            " VALUES ($1, $2, $3, $4::jsonb)",
            OPERATION_EVENT,
            ACTOR,
            approval.operation_key,
            {
                "operation_key": approval.operation_key,
                "predicate_version": PREDICATE_VERSION,
                "schema": schema,
                "manifest_digest": manifest.digest,
                "candidate_digest": report.candidate_digest,
                "approved_by": approval.approved_by,
                "demoted": len(receipts),
                "catalog": catalog,
            },
        )
    return ApplyResult(approval.operation_key, False, tuple(receipts))


async def _disown_catalog_row(conn: Any, schema: str, table: str, source_id: uuid.UUID) -> int:
    """Carry the demotion to the catalog in the same transaction (never best effort).

    A fact's catalog row keeps serving search with its new ``mixed`` stamp,
    which readers attribute.  A rule's catalog row is also staled: a demoted,
    unendorsed rule is held and must stop steering other butlers.
    """
    stale = ", confidence = 0, invalid_at = COALESCE(invalid_at, now())" if table == "rules" else ""
    status = await conn.execute(
        f"UPDATE public.memory_catalog SET content_authority = $4, updated_at = now(){stale}"
        " WHERE source_schema = $1 AND source_table = $2 AND source_id = $3",
        schema,
        table,
        source_id,
        MIXED,
    )
    return int(status.rsplit(" ", 1)[-1])


def load_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestError("not valid JSON") from exc
