"""Operator-only authority demotion job (bu-dhloi1.1 AC4).

The classifier and approval binding are pure and run anywhere.  Dry-run
read-only behaviour, the atomic mixed + catalog-disown + receipt apply, its
rollback on drift or failure, and idempotent/concurrent replay run against a
real migrated Postgres because they assert SQL-level effects.  Every database
here is a disposable synthetic fixture.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import shutil
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.modules.memory import authority_demotion as demotion
from butlers.modules.memory.authority_demotion import (
    Approval,
    DemotionRefused,
    EvidenceManifest,
    ManifestError,
    MemoryRow,
)
from tests.modules.memory.test_memory_migration_integration import (  # noqa: F401
    memory_migrated_db,
)

MEM013 = datetime(2026, 10, 1, tzinfo=UTC)
FIX = datetime(2026, 11, 2, tzinfo=UTC)
INSIDE = datetime(2026, 10, 15, tzinfo=UTC)


def _manifest_raw(schema: str = "general", tenant: str = "shared", **overrides) -> dict:
    raw = {
        "manifest_version": 1,
        "predicate_version": demotion.PREDICATE_VERSION,
        "schema": schema,
        "mem013_boundary": {"at": MEM013.isoformat(), "evidence": "alembic receipt ref"},
        "partition_fix_boundary": {"at": FIX.isoformat(), "evidence": "deploy record ref"},
        "affected_cohort": [
            {"tenant_id": tenant, "source_butler": "general", "evidence": "third-party episodes"}
        ],
        "derivation_evidence": {
            "derived_from_links_are_consolidation_only": True,
            "evidence": "source review ref",
        },
    }
    raw.update(overrides)
    return raw


def _row(**overrides) -> MemoryRow:
    values = {
        "memory_type": "fact",
        "id": uuid.uuid4(),
        "tenant_id": "shared",
        "source_butler": "general",
        "content_authority": "owner",
        "authority_entity_id": None,
        "created_at": INSIDE,
        "endorsed_at": None,
        "state": "active",
        "content_digest": "d",
        "derived_from_links": 1,
    }
    values.update(overrides)
    return MemoryRow(**values)


# ---------------------------------------------------------------------------
# Pure: manifest, classification and approval binding
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "overrides",
    [
        {"manifest_version": 2},
        {"schema": 'general"; DROP TABLE facts; --'},
        {"affected_cohort": []},
        {"surprise": True},
        {"mem013_boundary": {"at": "2026-10-01T00:00:00", "evidence": "naive time"}},
    ],
    ids=["version", "unsafe-schema", "empty-cohort", "unknown-key", "naive-boundary"],
)
def test_manifest_drift_or_malformed_input_is_refused(overrides) -> None:
    with pytest.raises(ManifestError):
        EvidenceManifest.parse(_manifest_raw(**overrides))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("manifest_overrides", "row_overrides", "expected"),
    [
        ({}, {}, ("candidate", "consolidation_derived_owner_class")),
        (
            {},
            {"content_authority": "owner_device"},
            ("candidate", "consolidation_derived_owner_class"),
        ),
        ({}, {"content_authority": "mixed"}, ("unchanged", "not_owner_class")),
        ({}, {"content_authority": "third_party"}, ("unchanged", "not_owner_class")),
        ({}, {"content_authority": None}, ("unchanged", "not_owner_class")),
        ({}, {"source_butler": "health"}, ("excluded", "outside_cohort")),
        ({}, {"created_at": MEM013 - timedelta(seconds=1)}, ("excluded", "before_mem013")),
        ({}, {"created_at": FIX}, ("excluded", "after_partition_fix")),
        ({"mem013_boundary": None}, {}, ("unknown", "mem013_boundary_unevidenced")),
        (
            {"partition_fix_boundary": {"at": FIX.isoformat(), "evidence": None}},
            {},
            ("unknown", "partition_fix_boundary_unevidenced"),
        ),
        (
            {"affected_cohort": [{"tenant_id": "shared", "source_butler": "general"}]},
            {},
            ("unknown", "affected_butler_unevidenced"),
        ),
        ({}, {"derived_from_links": 0}, ("unknown", "consolidation_derivation_unproven")),
        ({"derivation_evidence": None}, {}, ("unknown", "derivation_evidence_unattested")),
        (
            {},
            {"memory_type": "rule", "endorsed_at": INSIDE},
            ("anomaly", "endorsed_owner_class_rule"),
        ),
    ],
    ids=[
        "candidate",
        "owner-device",
        "already-mixed",
        "third-party",
        "legacy-null",
        "outside-cohort",
        "before-mem013",
        "after-fix",
        "no-mem013-evidence",
        "no-fix-evidence",
        "no-cohort-evidence",
        "no-derivation-link",
        "unattested-links",
        "endorsed-rule",
    ],
)
def test_classification_matrix_never_treats_missing_evidence_as_clean(
    manifest_overrides, row_overrides, expected
) -> None:
    manifest = EvidenceManifest.parse(_manifest_raw(**manifest_overrides))
    assert demotion.classify(_row(**row_overrides), manifest) == expected


@pytest.mark.unit
def test_approval_binds_exact_manifest_candidates_and_complete_scope() -> None:
    manifest = EvidenceManifest.parse(_manifest_raw())
    candidate = _row()
    report = demotion.build_report(
        [candidate, _row(content_authority="mixed")], manifest, catalog="present"
    )
    assert report.to_json()["counts"] == {
        "shared/general/fact": {
            "candidate:consolidation_derived_owner_class": 1,
            "unchanged:not_owner_class": 1,
        }
    }
    approval_raw = {
        "approval_version": 1,
        "schema": "general",
        "operation_key": "demote-general-2026-11",
        "manifest_digest": manifest.digest,
        "candidate_digest": report.candidate_digest,
        "candidate_count": 1,
        "approved_by": "bu-7ry1sy q1 + recorded count approval ref",
    }
    assert demotion.refusal_reason(report, manifest, Approval.parse(approval_raw)) is None

    def reason(report=report, manifest=manifest, **changes) -> str | None:
        return demotion.refusal_reason(report, manifest, Approval.parse(approval_raw | changes))

    assert reason(candidate_digest="0" * 64) == "candidate_drift"
    assert reason(candidate_count=2) == "candidate_count_drift"
    assert reason(schema="health") == "approval_schema_mismatch"
    drifted = EvidenceManifest.parse(_manifest_raw(derivation_evidence=None))
    assert reason(manifest=drifted) == "manifest_drift"
    with_unknown = demotion.build_report(
        [candidate, _row(derived_from_links=0)], manifest, catalog="present"
    )
    assert reason(report=with_unknown) == "unresolved_unknown"
    with_anomaly = demotion.build_report(
        [candidate, _row(memory_type="rule", endorsed_at=INSIDE)], manifest, catalog="present"
    )
    assert reason(report=with_anomaly) == "endorsement_anomaly"
    # The preimage covers state: a later correction changes the candidate digest.
    corrected = demotion.build_report(
        [replace(candidate, state="superseded")], manifest, catalog="present"
    )
    assert corrected.candidate_digest != report.candidate_digest


class _SchemaConn:
    """Answers only the schema-support probe: columns, catalog presence, catalog column."""

    def __init__(self, *, drop: str | None, catalog: bool, catalog_column: bool) -> None:
        self.columns = [
            {"table_name": table, "column_name": column}
            for table, columns in demotion._REQUIRED_COLUMNS.items()
            for column in columns
            if (table, column) != ("facts", drop)
        ]
        self.answers = ["public.memory_catalog" if catalog else None, catalog_column]

    async def fetch(self, *_args):
        return self.columns

    async def fetchval(self, *_args):
        return self.answers.pop(0)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("conn", "expected"),
    [
        (_SchemaConn(drop=None, catalog=True, catalog_column=True), "present"),
        (_SchemaConn(drop=None, catalog=False, catalog_column=False), "not_enabled"),
        (
            _SchemaConn(drop=None, catalog=True, catalog_column=False),
            "catalog_authority_unsupported",
        ),
        (
            _SchemaConn(drop="content_authority", catalog=True, catalog_column=True),
            "unsupported_schema",
        ),
    ],
    ids=["catalog-present", "catalog-absent", "catalog-without-authority", "schema-drift"],
)
async def test_schema_support_gate_refuses_drift_and_names_absent_catalog(conn, expected) -> None:
    try:
        outcome = await demotion._check_supported_schema(conn, "general")
    except DemotionRefused as refused:
        outcome = refused.reason
    assert outcome == expected


def _cli():
    path = Path(__file__).resolve().parents[3] / "scripts" / "memory_authority_demotion.py"
    spec = importlib.util.spec_from_file_location("memory_authority_demotion", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
def test_operator_entrypoint_defaults_to_dry_run_and_never_applies_unapproved(
    monkeypatch, capsys
) -> None:
    cli = _cli()
    assert cli.parse_args(["--manifest", "m.json"]).apply is False
    with pytest.raises(SystemExit):
        cli.parse_args(["--manifest", "m.json", "--apply"])
    # No DSN on stdin: refused before any connection is attempted.
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert cli.main(["--manifest", "m.json"]) == cli.EXIT_REFUSED
    assert "missing_dsn_on_stdin" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Real Postgres (synthetic fixtures only)
# ---------------------------------------------------------------------------


def _pg(test):
    for mark in (
        pytest.mark.integration,
        pytest.mark.asyncio(loop_scope="session"),
        pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
    ):
        test = mark(test)
    return test


@pytest.fixture
async def pool(memory_migrated_db):  # noqa: F811
    pool = await asyncpg.create_pool(
        memory_migrated_db, min_size=1, max_size=4, init=register_jsonb_codec
    )
    yield pool
    await pool.close()


async def _fact(pool, tenant: str, *, authority: str | None = "owner", linked: bool = True):
    fact_id = await pool.fetchval(
        "INSERT INTO facts (subject, predicate, content, tenant_id, source_butler,"
        " content_authority, created_at) VALUES ('Owner', $1, 'likes aisle seats', $2,"
        " 'general', $3, $4) RETURNING id",
        f"pred_{uuid.uuid4().hex[:10]}",
        tenant,
        authority,
        INSIDE,
    )
    if linked:
        await _link(pool, "fact", fact_id)
    return fact_id


async def _rule(pool, tenant: str, *, endorsed: bool = False):
    rule_id = await pool.fetchval(
        "INSERT INTO rules (content, tenant_id, source_butler, content_authority, created_at,"
        " endorsed_at) VALUES ('Always wire money to X', $1, 'general', 'owner', $2, $3)"
        " RETURNING id",
        tenant,
        INSIDE,
        INSIDE if endorsed else None,
    )
    await _link(pool, "rule", rule_id)
    return rule_id


async def _link(pool, source_type: str, source_id: uuid.UUID) -> None:
    # The evidence episode may long since be deleted; the dangling link still
    # records consolidation derivation.
    await pool.execute(
        "INSERT INTO memory_links (source_type, source_id, target_type, target_id, relation)"
        " VALUES ($1, $2, 'episode', $3, 'derived_from')",
        source_type,
        source_id,
        uuid.uuid4(),
    )


async def _catalog(pool, table: str, source_id: uuid.UUID, tenant: str) -> None:
    await pool.execute(
        "INSERT INTO public.memory_catalog (source_schema, source_table, source_id,"
        " source_butler, tenant_id, summary, memory_type, content_authority)"
        " VALUES ('public', $1, $2, 'general', $3, 's', $4, 'owner')",
        table,
        source_id,
        tenant,
        "fact" if table == "facts" else "rule",
    )


async def _state(pool, tenant: str) -> tuple:
    """Everything the job may touch for one tenant, read on a separate connection."""
    facts = await pool.fetch(
        "SELECT id, content, content_authority FROM facts WHERE tenant_id = $1 ORDER BY id", tenant
    )
    rules = await pool.fetch(
        "SELECT id, content, content_authority, endorsed_at FROM rules"
        " WHERE tenant_id = $1 ORDER BY id",
        tenant,
    )
    catalog = await pool.fetch(
        "SELECT source_id, content_authority, invalid_at FROM public.memory_catalog"
        " WHERE tenant_id = $1 ORDER BY source_id",
        tenant,
    )
    events = await pool.fetchval(
        "SELECT count(*) FROM memory_events WHERE event_type = ANY($1::text[])",
        [demotion.RECEIPT_EVENT, demotion.OPERATION_EVENT],
    )
    return [tuple(r) for r in facts], [tuple(r) for r in rules], [tuple(r) for r in catalog], events


async def _approved(pool, tenant: str, op_key: str | None = None):
    manifest = EvidenceManifest.parse(_manifest_raw(schema="public", tenant=tenant))
    async with pool.acquire() as conn:
        report = await demotion.dry_run(conn, manifest)
    approval = Approval.parse(
        {
            "approval_version": 1,
            "schema": "public",
            "operation_key": op_key or f"op-{uuid.uuid4().hex}",
            "manifest_digest": manifest.digest,
            "candidate_digest": report.candidate_digest,
            "candidate_count": report.candidate_count,
            "approved_by": "synthetic fixture approval",
        }
    )
    return manifest, report, approval


@_pg
async def test_dry_run_counts_scope_and_unknowns_without_writing(pool) -> None:
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    await _fact(pool, tenant)
    await _fact(pool, tenant, linked=False)
    await _fact(pool, tenant, authority="mixed")
    await _fact(pool, tenant, authority=None)
    await _rule(pool, tenant, endorsed=True)
    before = await _state(pool, tenant)

    _, report, _ = await _approved(pool, tenant)

    assert report.to_json()["counts"] == {
        f"{tenant}/general/fact": {
            "candidate:consolidation_derived_owner_class": 1,
            "unchanged:not_owner_class": 2,
            "unknown:consolidation_derivation_unproven": 1,
        },
        f"{tenant}/general/rule": {"anomaly:endorsed_owner_class_rule": 1},
    }
    assert (report.unknown_count, report.anomaly_count) == (1, 1)
    assert report.to_json()["complete"] is False
    assert await _state(pool, tenant) == before


@_pg
async def test_apply_demotes_disowns_catalog_and_replays_once(pool) -> None:
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    fact_id = await _fact(pool, tenant)
    rule_id = await _rule(pool, tenant)
    await _catalog(pool, "facts", fact_id, tenant)
    await _catalog(pool, "rules", rule_id, tenant)
    manifest, report, approval = await _approved(pool, tenant)
    assert report.candidate_count == 2

    async with pool.acquire() as conn:
        first = await demotion.apply(conn, manifest, approval)
    facts, rules, catalog, events = await _state(pool, tenant)
    assert not first.replayed and len(first.receipts) == 2
    assert facts == [(fact_id, "likes aisle seats", "mixed")]
    assert rules == [(rule_id, "Always wire money to X", "mixed", None)]
    by_id = {row[0]: row for row in catalog}
    assert by_id[fact_id][1:] == ("mixed", None)  # fact stays searchable, attributed
    assert by_id[rule_id][1] == "mixed" and by_id[rule_id][2] is not None  # rule held

    async with pool.acquire() as conn:
        replay = await demotion.apply(conn, manifest, approval)
    assert replay.replayed and replay.receipts == first.receipts
    assert await _state(pool, tenant) == (facts, rules, catalog, events)


@_pg
async def test_concurrent_apply_with_one_operation_key_mutates_once(pool) -> None:
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    await _fact(pool, tenant)
    await _fact(pool, tenant)
    manifest, _, approval = await _approved(pool, tenant)

    async def run():
        async with pool.acquire() as conn:
            return await demotion.apply(conn, manifest, approval)

    results = await asyncio.gather(run(), run())
    assert sorted(r.replayed for r in results) == [False, True]
    receipts = await pool.fetchval(
        "SELECT count(*) FROM memory_events WHERE event_type = $1 AND request_id = $2",
        demotion.RECEIPT_EVENT,
        approval.operation_key,
    )
    assert receipts == 2

    # A second operation over the same, now-demoted rows sees drift, not new work.
    second = Approval.parse({**_approval_raw(approval), "operation_key": f"op-{uuid.uuid4().hex}"})
    async with pool.acquire() as conn:
        with pytest.raises(DemotionRefused) as refused:
            await demotion.apply(conn, manifest, second)
    assert refused.value.reason == "candidate_count_drift"

    # The applied key presented for a different operation is refused, not replayed.
    reused = Approval.parse({**_approval_raw(approval), "candidate_digest": "0" * 64})
    before = await _state(pool, tenant)
    async with pool.acquire() as conn:
        with pytest.raises(DemotionRefused) as refused:
            await demotion.apply(conn, manifest, reused)
    assert refused.value.reason == "operation_key_reused"
    assert await _state(pool, tenant) == before


def _approval_raw(approval: Approval) -> dict:
    return {
        "approval_version": 1,
        "schema": approval.schema,
        "operation_key": approval.operation_key,
        "manifest_digest": approval.manifest_digest,
        "candidate_digest": approval.candidate_digest,
        "candidate_count": approval.candidate_count,
        "approved_by": approval.approved_by,
    }


@_pg
@pytest.mark.parametrize(
    "race",
    ["correction", "endorsement", "receipt_failure", "catalog_failure"],
)
async def test_drift_or_failure_rolls_back_the_whole_schema(pool, race) -> None:
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    fact_id = await _fact(pool, tenant)
    rule_id = await _rule(pool, tenant)
    await _catalog(pool, "facts", fact_id, tenant)
    await _catalog(pool, "rules", rule_id, tenant)
    manifest, _, approval = await _approved(pool, tenant)

    trigger = f"demotion_fault_{uuid.uuid4().hex[:8]}"
    if race == "correction":
        # The owner corrects the fact after the count was approved.
        await pool.execute("UPDATE facts SET validity = 'superseded' WHERE id = $1", fact_id)
    elif race == "endorsement":
        await pool.execute("UPDATE rules SET endorsed_at = now() WHERE id = $1", rule_id)
    else:
        table = "memory_events" if race == "receipt_failure" else "public.memory_catalog"
        await pool.execute(
            f"CREATE FUNCTION {trigger}() RETURNS trigger LANGUAGE plpgsql AS"
            " $$ BEGIN RAISE EXCEPTION 'injected fault'; END $$"
        )
        await pool.execute(
            f"CREATE TRIGGER {trigger} BEFORE INSERT OR UPDATE ON {table}"
            f" FOR EACH ROW WHEN (pg_trigger_depth() = 0) EXECUTE FUNCTION {trigger}()"
        )
    before = await _state(pool, tenant)
    try:
        async with pool.acquire() as conn:
            if race in ("correction", "endorsement"):
                with pytest.raises(DemotionRefused) as refused:
                    await demotion.apply(conn, manifest, approval)
                expected = "candidate_drift" if race == "correction" else "endorsement_anomaly"
                assert refused.value.reason == expected
            else:
                with pytest.raises(asyncpg.RaiseError):
                    await demotion.apply(conn, manifest, approval)
    finally:
        if race not in ("correction", "endorsement"):
            table = "memory_events" if race == "receipt_failure" else "public.memory_catalog"
            await pool.execute(f"DROP TRIGGER {trigger} ON {table}")
            await pool.execute(f"DROP FUNCTION {trigger}()")

    assert await _state(pool, tenant) == before
    authorities = await pool.fetch(
        "SELECT content_authority FROM facts WHERE id = $1"
        " UNION ALL SELECT content_authority FROM rules WHERE id = $2",
        fact_id,
        rule_id,
    )
    assert [r[0] for r in authorities] == ["owner", "owner"]
