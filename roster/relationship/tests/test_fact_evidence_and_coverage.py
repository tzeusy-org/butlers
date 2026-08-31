"""Evidence ledger and predicate coverage for relationship.entity_facts (bu-6jv4m.9).

Two gaps this covers:

1. A fact recorded *that* something is believed and nothing about *why*. Typed
   evidence handed to ``relationship_assert_fact()`` was forwarded to the
   approvals dossier and then discarded, so the moment the fact became active
   its justification was gone.

2. An empty fact list could not be told apart from an unsearched one. "No email
   for Alice" and "nobody ever looked for Alice's email" rendered identically,
   which is how a butler ends up asserting an absence it never checked.

The tests below hold the load-bearing properties: evidence lands in the same
transaction as the fact, is never rewritten, survives supersession, and an
approved write replays the owner's decision rather than trusting its caller.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from butlers.testing.schema_standins import ENTITY_PREDICATE_REGISTRY, PENDING_ACTIONS
from butlers.tools.relationship.fact_coverage import (
    compose_state,
    predicate_coverage,
    record_coverage,
)
from butlers.tools.relationship.fact_evidence import (
    EvidencePacket,
    coerce_session_id,
    read_fact_evidence,
    validate_evidence,
)
from butlers.tools.relationship.relationship_assert_fact import (
    AssertOutcome,
    relationship_assert_fact,
)
from roster.relationship.tests.evidence_schema import (
    MAX_TEXT_CHARS,
    apply_evidence_schema,
)

_PRED_HAS_EMAIL = "has-email"
_PRED_HAS_PHONE = "has-phone"

_docker = pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")

# The Postgres testcontainer and its pool are session-scoped; a per-test loop
# would hand asyncpg futures from another loop and every DB test would fail on
# "attached to a different loop" rather than on anything it asserts.
_session_loop = pytest.mark.asyncio(loop_scope="session")


# ---------------------------------------------------------------------------
# Pure composition — no database, no Docker
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestComposeState:
    """``compose_state`` is the whole point: four outcomes, no ambiguity."""

    def test_no_receipts_is_unknown_not_absent(self):
        """The load-bearing case. Silence is never proof of absence."""
        assert (
            compose_state(target_available=True, active_value_count=0, receipt_outcomes=[])
            == "unknown"
        )

    def test_absent_receipt_proves_absence(self):
        assert (
            compose_state(target_available=True, active_value_count=0, receipt_outcomes=["absent"])
            == "absent_proven"
        )

    def test_live_value_is_present(self):
        assert (
            compose_state(target_available=True, active_value_count=1, receipt_outcomes=[])
            == "present"
        )

    def test_all_sources_unreachable_is_unavailable(self):
        assert (
            compose_state(
                target_available=True,
                active_value_count=0,
                receipt_outcomes=["unavailable", "unavailable"],
            )
            == "unavailable"
        )

    def test_unavailable_receipt_does_not_outrank_absent(self):
        """One source proved absence; another was down. Absence still stands."""
        assert (
            compose_state(
                target_available=True,
                active_value_count=0,
                receipt_outcomes=["unavailable", "absent"],
            )
            == "absent_proven"
        )

    def test_expired_target_is_unavailable_even_with_receipts(self):
        assert (
            compose_state(
                target_available=False, active_value_count=3, receipt_outcomes=["present"]
            )
            == "unavailable"
        )

    def test_stale_present_receipt_without_a_value_is_unknown(self):
        """A source once saw a value; none is active now. That is not proof."""
        assert (
            compose_state(target_available=True, active_value_count=0, receipt_outcomes=["present"])
            == "unknown"
        )

    def test_every_combination_maps_into_the_state_set(self):
        from butlers.tools.relationship.fact_coverage import COVERAGE_STATES

        outcomes = ["present", "absent", "unavailable"]
        combos = [[], *[[o] for o in outcomes], *[[a, b] for a in outcomes for b in outcomes]]
        for available in (True, False):
            for count in (0, 1, 5):
                for receipts in combos:
                    state = compose_state(
                        target_available=available,
                        active_value_count=count,
                        receipt_outcomes=receipts,
                    )
                    assert state in COVERAGE_STATES


@pytest.mark.unit
class TestEvidenceValidation:
    """The ledger holds references. Bounds are the structural guarantee."""

    def test_accepts_a_well_formed_reference(self):
        validate_evidence([{"type": "url", "ref": "https://example.test/x", "note": "Source."}])

    def test_rejects_unknown_type(self):
        with pytest.raises(ValueError, match="not a supported evidence type"):
            validate_evidence([{"type": "screenshot", "ref": "x", "note": ""}])

    def test_rejects_missing_keys(self):
        with pytest.raises(ValueError, match="typed reference"):
            validate_evidence([{"type": "url", "ref": "x"}])

    def test_rejects_empty_ref(self):
        with pytest.raises(ValueError, match="non-empty string"):
            validate_evidence([{"type": "url", "ref": "", "note": ""}])

    def test_rejects_source_content_smuggled_as_a_ref(self):
        with pytest.raises(ValueError, match="references, not source content"):
            validate_evidence([{"type": "text", "ref": "x" * (MAX_TEXT_CHARS + 1), "note": ""}])

    def test_rejects_source_content_smuggled_as_a_note(self):
        with pytest.raises(ValueError, match="references, not source content"):
            validate_evidence([{"type": "text", "ref": "x", "note": "y" * (MAX_TEXT_CHARS + 1)}])

    def test_rejects_an_unbounded_packet(self):
        with pytest.raises(ValueError, match="at most"):
            validate_evidence(
                [{"type": "text", "ref": f"r{i}", "note": ""} for i in range(33)],
            )

    def test_packet_rejects_an_unknown_origin(self):
        with pytest.raises(ValueError, match="origin must be one of"):
            EvidencePacket(items=(), src="test", origin="inferred")

    def test_non_uuid_session_is_recorded_as_unknown_not_fatal(self):
        assert coerce_session_id("not-a-uuid") is None
        assert coerce_session_id(None) is None
        known = uuid.uuid4()
        assert coerce_session_id(str(known)) == known


# ---------------------------------------------------------------------------
# DB-backed
# ---------------------------------------------------------------------------


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Relationship schema plus the rel_034 evidence ledger and coverage table."""
    async with provisioned_postgres_pool() as p:
        await p.execute("""
            CREATE TABLE IF NOT EXISTS public.entities (
                id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                canonical_name TEXT        NOT NULL DEFAULT '',
                name           TEXT        NOT NULL DEFAULT '',
                entity_type    TEXT        NOT NULL DEFAULT 'person',
                aliases        TEXT[]      NOT NULL DEFAULT '{}',
                metadata       JSONB       DEFAULT '{}'::jsonb,
                roles          TEXT[]      NOT NULL DEFAULT '{}',
                created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("CREATE SCHEMA IF NOT EXISTS relationship")
        await p.execute(ENTITY_PREDICATE_REGISTRY.ddl(schema="relationship"))
        await p.execute("""
            INSERT INTO relationship.entity_predicate_registry
                (predicate, kind, object_kind, description)
            VALUES
                ('has-email', 'contact', 'literal', 'Email address for the entity.'),
                ('has-phone', 'contact', 'literal', 'Phone number for the entity.')
            ON CONFLICT (predicate) DO NOTHING
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS relationship.entity_facts (
                id          UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
                subject     UUID        NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
                predicate   TEXT        NOT NULL,
                object      TEXT        NOT NULL,
                object_kind TEXT        NOT NULL CHECK (object_kind IN ('literal', 'entity')),
                src         TEXT        NOT NULL,
                conf        FLOAT       NOT NULL DEFAULT 1.0
                                CHECK (conf >= 0.0 AND conf <= 1.0),
                last_seen   TIMESTAMPTZ,
                observed_at TIMESTAMPTZ,
                metadata    JSONB,
                weight      INT,
                verified    BOOL        NOT NULL DEFAULT false,
                "primary"   BOOL,
                validity    TEXT        NOT NULL DEFAULT 'active'
                                CHECK (validity IN ('active', 'retracted', 'superseded')),
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_ef_spo_active
                ON relationship.entity_facts (subject, predicate, object)
                WHERE validity = 'active'
        """)
        await p.execute(PENDING_ACTIONS.ddl())
        await apply_evidence_schema(p)
        yield p


@pytest.fixture
async def entity(pool: asyncpg.Pool) -> uuid.UUID:
    return await pool.fetchval(
        """
        INSERT INTO public.entities (canonical_name, entity_type, roles)
        VALUES ('Alice Foo', 'person', '{}')
        RETURNING id
        """
    )


@pytest.fixture
async def owner_entity(pool: asyncpg.Pool) -> uuid.UUID:
    return await pool.fetchval(
        """
        INSERT INTO public.entities (canonical_name, entity_type, roles)
        VALUES ('Owner User', 'person', '{owner}')
        RETURNING id
        """
    )


_EVIDENCE = [
    {"type": "url", "ref": "https://example.test/thread/1", "note": "Signature block."},
    {"type": "entity", "ref": "sender", "note": "Message sender."},
]


@pytest.mark.integration
@_docker
@_session_loop
class TestDirectWriteEvidence:
    async def test_direct_write_persists_evidence_and_provenance(self, pool, entity):
        result = await relationship_assert_fact(
            pool,
            entity,
            _PRED_HAS_EMAIL,
            "alice@example.test",
            src="test",
            evidence=list(_EVIDENCE),
        )
        assert result.outcome == AssertOutcome.inserted

        packet = await read_fact_evidence(pool, result.fact_id)
        assert packet["provenance"]["origin"] == "direct"
        assert packet["provenance"]["action_id"] is None
        assert [(e["type"], e["ref"]) for e in packet["evidence"]] == [
            (e["type"], e["ref"]) for e in _EVIDENCE
        ]
        assert {e["origin"] for e in packet["evidence"]} == {"direct"}
        assert {e["src"] for e in packet["evidence"]} == {"test"}
        assert all(e["carried_from"] is None for e in packet["evidence"])

    async def test_evidence_is_visible_only_if_the_fact_committed(self, pool, entity):
        """Atomicity, held from the failing side: a rolled-back write leaves no ledger."""
        async with pool.acquire() as conn:
            tx = conn.transaction()
            await tx.start()
            result = await relationship_assert_fact(
                pool,
                entity,
                _PRED_HAS_EMAIL,
                "rolled-back@example.test",
                src="test",
                evidence=list(_EVIDENCE),
                conn=conn,
            )
            assert result.fact_id is not None
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM relationship.fact_evidence WHERE fact_id = $1",
                    result.fact_id,
                )
                == 2
            )
            await tx.rollback()

        assert (
            await pool.fetchval(
                "SELECT count(*) FROM relationship.fact_evidence WHERE fact_id = $1",
                result.fact_id,
            )
            == 0
        )
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM relationship.entity_facts WHERE id = $1", result.fact_id
            )
            == 0
        )

    async def test_ledger_rows_cannot_be_rewritten(self, pool, entity):
        result = await relationship_assert_fact(
            pool,
            entity,
            _PRED_HAS_EMAIL,
            "alice@example.test",
            src="test",
            evidence=list(_EVIDENCE),
        )
        with pytest.raises(asyncpg.PostgresError, match="append-only"):
            await pool.execute(
                "UPDATE relationship.fact_evidence SET note = 'edited' WHERE fact_id = $1",
                result.fact_id,
            )

    async def test_recited_reference_does_not_duplicate(self, pool, entity):
        first = await relationship_assert_fact(
            pool,
            entity,
            _PRED_HAS_EMAIL,
            "alice@example.test",
            src="test",
            evidence=list(_EVIDENCE),
        )
        again = await relationship_assert_fact(
            pool,
            entity,
            _PRED_HAS_EMAIL,
            "alice@example.test",
            src="test",
            evidence=list(_EVIDENCE),
        )
        assert again.outcome == AssertOutcome.unchanged
        assert again.fact_id == first.fact_id
        packet = await read_fact_evidence(pool, first.fact_id)
        assert len(packet["evidence"]) == len(_EVIDENCE)

    async def test_reassertion_appends_new_references(self, pool, entity):
        first = await relationship_assert_fact(
            pool,
            entity,
            _PRED_HAS_EMAIL,
            "alice@example.test",
            src="test",
            evidence=list(_EVIDENCE),
        )
        await relationship_assert_fact(
            pool,
            entity,
            _PRED_HAS_EMAIL,
            "alice@example.test",
            src="test",
            evidence=[{"type": "url", "ref": "https://example.test/thread/2", "note": "Again."}],
        )
        packet = await read_fact_evidence(pool, first.fact_id)
        assert len(packet["evidence"]) == 3
        assert [e["seq"] for e in packet["evidence"]] == [1, 2, 3]

    async def test_supersession_carries_evidence_onto_the_new_row(self, pool, entity):
        first = await relationship_assert_fact(
            pool,
            entity,
            _PRED_HAS_EMAIL,
            "alice@example.test",
            src="test",
            evidence=list(_EVIDENCE),
        )
        # Different provenance (src) forces supersession rather than an update.
        second = await relationship_assert_fact(
            pool,
            entity,
            _PRED_HAS_EMAIL,
            "alice@example.test",
            src="other",
            evidence=[{"type": "url", "ref": "https://example.test/thread/9", "note": "New."}],
        )
        assert second.outcome == AssertOutcome.superseded
        assert second.fact_id != first.fact_id

        old = await read_fact_evidence(pool, first.fact_id)
        new = await read_fact_evidence(pool, second.fact_id)

        # The superseded row keeps exactly the ledger it was written with.
        assert [(e["ref"], e["carried_from"]) for e in old["evidence"]] == [
            (e["ref"], None) for e in _EVIDENCE
        ]
        # The replacement inherits it, tagged, plus its own new reference.
        carried = {e["ref"]: e["carried_from"] for e in new["evidence"]}
        assert carried["https://example.test/thread/1"] == str(first.fact_id)
        assert carried["https://example.test/thread/9"] is None
        assert len(new["evidence"]) == 3

    async def test_source_content_is_rejected_before_any_write(self, pool, entity):
        with pytest.raises(ValueError, match="references, not source content"):
            await relationship_assert_fact(
                pool,
                entity,
                _PRED_HAS_EMAIL,
                "alice@example.test",
                src="test",
                evidence=[{"type": "text", "ref": "x" * (MAX_TEXT_CHARS + 1), "note": ""}],
            )
        assert await pool.fetchval("SELECT count(*) FROM relationship.entity_facts") == 0

    async def test_unknown_fact_id_is_a_miss_not_an_error(self, pool):
        packet = await read_fact_evidence(pool, uuid.uuid4())
        assert packet == {"fact": None, "provenance": None, "evidence": []}


@pytest.mark.integration
@_docker
@_session_loop
class TestApprovedWriteEvidence:
    """The owner-approved path must land the fact AND its parked evidence."""

    @staticmethod
    async def _park(pool, owner_entity, value="owner@example.test"):
        result = await relationship_assert_fact(
            pool, owner_entity, _PRED_HAS_EMAIL, value, src="relationship"
        )
        assert result.outcome == AssertOutcome.pending_approval
        await pool.execute(
            "UPDATE pending_actions SET status = 'approved' WHERE id = $1", result.action_id
        )
        return result.action_id

    async def test_parked_args_carry_the_action_id_dispatch_needs(self, pool, owner_entity):
        action_id = await self._park(pool, owner_entity)
        args = await pool.fetchval("SELECT tool_args FROM pending_actions WHERE id = $1", action_id)
        import json

        args = json.loads(args) if isinstance(args, str) else args
        assert args["approval_action_id"] == str(action_id)

    async def test_parked_args_never_carry_the_asserting_source(self, pool, owner_entity):
        """src/observed_at stay out of tool_args, which dispatch splats as kwargs.

        Every key in tool_args must also be a parameter of the MCP tool, and a
        ``src`` parameter there is precisely the owner-carve-out bypass bu-vj46x
        closed. The provenance lives in a server-written row instead.
        """
        action_id = await self._park(pool, owner_entity)
        args = await pool.fetchval("SELECT tool_args FROM pending_actions WHERE id = $1", action_id)
        import json

        args = json.loads(args) if isinstance(args, str) else args
        assert "src" not in args
        assert "observed_at" not in args

        ctx = await pool.fetchrow(
            "SELECT src, observed_at FROM relationship.fact_approval_context WHERE action_id = $1",
            action_id,
        )
        assert ctx is not None
        assert ctx["src"] == "relationship"
        assert ctx["observed_at"] is not None

    async def test_approved_replay_keeps_the_observation_time_not_the_approval_time(
        self, pool, owner_entity
    ):
        """The fact is observed when proposed; approving it later must not refresh it."""
        observed = datetime.now(UTC) - timedelta(days=30)
        result = await relationship_assert_fact(
            pool,
            owner_entity,
            _PRED_HAS_EMAIL,
            "owner@example.test",
            src="relationship",
            observed_at=observed,
        )
        assert result.outcome == AssertOutcome.pending_approval
        await pool.execute(
            "UPDATE pending_actions SET status = 'approved' WHERE id = $1", result.action_id
        )
        replayed = await relationship_assert_fact(
            pool,
            owner_entity,
            _PRED_HAS_EMAIL,
            "owner@example.test",
            src="relationship",
            approval_action_id=result.action_id,
        )
        assert replayed.outcome == AssertOutcome.inserted
        stored = await pool.fetchval(
            "SELECT observed_at FROM relationship.entity_facts WHERE id = $1", replayed.fact_id
        )
        assert abs((stored - observed).total_seconds()) < 1

    async def test_approved_replay_writes_the_fact_instead_of_reparking(self, pool, owner_entity):
        action_id = await self._park(pool, owner_entity)
        result = await relationship_assert_fact(
            pool,
            owner_entity,
            _PRED_HAS_EMAIL,
            "owner@example.test",
            src="relationship",
            approval_action_id=action_id,
        )
        assert result.outcome == AssertOutcome.inserted
        assert result.fact_id is not None
        # Exactly one pending_actions row — the replay did not ask again.
        assert await pool.fetchval("SELECT count(*) FROM pending_actions") == 1

    async def test_approved_write_records_approved_provenance_and_evidence(
        self, pool, owner_entity
    ):
        action_id = await self._park(pool, owner_entity)
        result = await relationship_assert_fact(
            pool,
            owner_entity,
            _PRED_HAS_EMAIL,
            "owner@example.test",
            src="relationship",
            approval_action_id=action_id,
        )
        packet = await read_fact_evidence(pool, result.fact_id)
        assert packet["provenance"]["origin"] == "approved"
        assert packet["provenance"]["action_id"] == str(action_id)
        # The dossier the owner actually saw is what got persisted.
        parked = await pool.fetchval(
            "SELECT evidence FROM pending_actions WHERE id = $1", action_id
        )
        import json

        parked = json.loads(parked) if isinstance(parked, str) else parked
        assert {(e["type"], e["ref"]) for e in packet["evidence"]} == {
            (e["type"], e["ref"]) for e in parked
        }
        assert {e["origin"] for e in packet["evidence"]} == {"approved"}

    async def test_unknown_action_id_is_rejected(self, pool, owner_entity):
        with pytest.raises(ValueError, match="does not identify a pending action"):
            await relationship_assert_fact(
                pool,
                owner_entity,
                _PRED_HAS_EMAIL,
                "owner@example.test",
                src="relationship",
                approval_action_id=uuid.uuid4(),
            )

    async def test_action_approved_for_another_triple_is_rejected(self, pool, owner_entity):
        """The escalation attempt: borrow a real approval, write something else."""
        action_id = await self._park(pool, owner_entity, value="owner@example.test")
        with pytest.raises(ValueError, match="approved for a different triple"):
            await relationship_assert_fact(
                pool,
                owner_entity,
                _PRED_HAS_PHONE,
                "+10000000000",
                src="relationship",
                approval_action_id=action_id,
            )
        assert await pool.fetchval("SELECT count(*) FROM relationship.entity_facts") == 0

    async def test_still_pending_action_cannot_execute(self, pool, owner_entity):
        result = await relationship_assert_fact(
            pool, owner_entity, _PRED_HAS_EMAIL, "owner@example.test", src="relationship"
        )
        with pytest.raises(ValueError, match="only an approved action"):
            await relationship_assert_fact(
                pool,
                owner_entity,
                _PRED_HAS_EMAIL,
                "owner@example.test",
                src="relationship",
                approval_action_id=result.action_id,
            )

    async def test_action_for_another_tool_cannot_authorise_a_fact(self, pool, owner_entity):
        foreign = await pool.fetchval(
            """
            INSERT INTO pending_actions (tool_name, tool_args, status)
            VALUES ('notify', '{}'::jsonb, 'approved')
            RETURNING id
            """
        )
        with pytest.raises(ValueError, match="different tool"):
            await relationship_assert_fact(
                pool,
                owner_entity,
                _PRED_HAS_EMAIL,
                "owner@example.test",
                src="relationship",
                approval_action_id=foreign,
            )


@pytest.mark.integration
@_docker
@_session_loop
class TestPredicateCoverage:
    async def test_uncovered_predicate_reads_unknown(self, pool, entity):
        report = await predicate_coverage(pool, entity, [_PRED_HAS_PHONE])
        assert report["target"] == "available"
        assert report["coverage"][_PRED_HAS_PHONE]["state"] == "unknown"
        assert report["coverage"][_PRED_HAS_PHONE]["receipts"] == []

    async def test_a_write_records_its_own_present_receipt(self, pool, entity):
        await relationship_assert_fact(
            pool, entity, _PRED_HAS_EMAIL, "alice@example.test", src="test"
        )
        report = await predicate_coverage(pool, entity, [_PRED_HAS_EMAIL])
        entry = report["coverage"][_PRED_HAS_EMAIL]
        assert entry["state"] == "present"
        assert entry["value_count"] == 1
        assert [(r["src"], r["outcome"]) for r in entry["receipts"]] == [("test", "present")]

    async def test_explicit_absent_receipt_proves_absence(self, pool, entity):
        async with pool.acquire() as conn:
            await record_coverage(
                conn, subject=entity, predicate=_PRED_HAS_PHONE, src="test", outcome="absent"
            )
        report = await predicate_coverage(pool, entity, [_PRED_HAS_PHONE])
        assert report["coverage"][_PRED_HAS_PHONE]["state"] == "absent_proven"

    async def test_unconsultable_source_is_not_evidence_of_absence(self, pool, entity):
        async with pool.acquire() as conn:
            await record_coverage(
                conn, subject=entity, predicate=_PRED_HAS_PHONE, src="test", outcome="unavailable"
            )
        report = await predicate_coverage(pool, entity, [_PRED_HAS_PHONE])
        assert report["coverage"][_PRED_HAS_PHONE]["state"] == "unavailable"

    async def test_merged_away_subject_reads_unavailable(self, pool, entity):
        await relationship_assert_fact(
            pool, entity, _PRED_HAS_EMAIL, "alice@example.test", src="test"
        )
        await pool.execute(
            "UPDATE public.entities SET metadata = jsonb_build_object('merged_into', $2::text) "
            "WHERE id = $1",
            entity,
            str(uuid.uuid4()),
        )
        report = await predicate_coverage(pool, entity, [_PRED_HAS_EMAIL])
        assert report["target"] == "unavailable"
        assert report["coverage"][_PRED_HAS_EMAIL]["state"] == "unavailable"

    async def test_unknown_subject_reads_unavailable(self, pool):
        report = await predicate_coverage(pool, uuid.uuid4(), [_PRED_HAS_EMAIL])
        assert report["target"] == "unavailable"

    async def test_stale_replay_cannot_rewind_a_receipt(self, pool, entity):
        now = datetime.now(UTC)
        async with pool.acquire() as conn:
            await record_coverage(
                conn,
                subject=entity,
                predicate=_PRED_HAS_PHONE,
                src="test",
                outcome="absent",
                observed_at=now,
            )
            await record_coverage(
                conn,
                subject=entity,
                predicate=_PRED_HAS_PHONE,
                src="test",
                outcome="unavailable",
                observed_at=now - timedelta(days=1),
            )
        report = await predicate_coverage(pool, entity, [_PRED_HAS_PHONE])
        assert report["coverage"][_PRED_HAS_PHONE]["state"] == "absent_proven"

    async def test_predicates_reported_in_request_order_deduplicated(self, pool, entity):
        report = await predicate_coverage(
            pool, entity, [_PRED_HAS_PHONE, _PRED_HAS_EMAIL, _PRED_HAS_PHONE]
        )
        assert list(report["coverage"]) == [_PRED_HAS_PHONE, _PRED_HAS_EMAIL]

    async def test_rejects_an_unknown_outcome(self, pool, entity):
        async with pool.acquire() as conn:
            with pytest.raises(ValueError, match="coverage outcome must be one of"):
                await record_coverage(
                    conn, subject=entity, predicate=_PRED_HAS_PHONE, src="test", outcome="maybe"
                )


# ---------------------------------------------------------------------------
# MCP surface
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMcpSurface:
    @staticmethod
    async def _register():
        from fastmcp import FastMCP

        from butlers.modules._roster_relationship import (
            RelationshipModule,
            RelationshipModuleConfig,
        )

        mod = RelationshipModule()
        mcp = FastMCP("test-relationship")
        await mod.register_tools(
            mcp,
            RelationshipModuleConfig(groups=["entity"]),
            db=None,
            butler_name="relationship",
        )
        return mcp

    async def test_evidence_and_coverage_reads_are_registered(self):
        mcp = await self._register()
        names = {t.name for t in await mcp.list_tools()}
        assert {
            "relationship_fact_evidence",
            "relationship_predicate_coverage",
            "relationship_record_coverage",
        } <= names

    async def test_assert_tool_has_no_source_parameter_at_all(self):
        """An LLM session cannot name its own source, approved or not (bu-vj46x).

        Not "supplies one and is rejected" -- the parameter does not exist, so
        neither the MCP schema nor the Python signature offers a way in.
        """
        import inspect

        mcp = await self._register()
        tool = await mcp.get_tool("relationship_assert_fact")
        params = inspect.signature(tool.fn).parameters
        assert "src" not in params
        assert "observed_at" not in params
        with pytest.raises(TypeError):
            await tool.fn(
                subject=uuid.uuid4(),
                predicate=_PRED_HAS_EMAIL,
                object="a@b.test",
                src="owner-self",
            )

    async def test_assert_tool_accepts_the_dispatch_replay_shape(self, monkeypatch):
        """Approval dispatch replays stored tool_args verbatim; the signature must fit."""
        import importlib
        from unittest.mock import AsyncMock, MagicMock

        outcome = MagicMock()
        outcome.as_dict.return_value = {"outcome": "inserted"}
        writer = AsyncMock(return_value=outcome)
        writer_mod = importlib.import_module("butlers.tools.relationship.relationship_assert_fact")
        monkeypatch.setattr(writer_mod, "relationship_assert_fact", writer)

        from fastmcp import FastMCP

        from butlers.modules._roster_relationship import (
            RelationshipModule,
            RelationshipModuleConfig,
        )

        mod = RelationshipModule()
        mod._db = MagicMock()
        mcp = FastMCP("test-relationship")
        await mod.register_tools(
            mcp, RelationshipModuleConfig(groups=["entity"]), db=mod._db, butler_name="relationship"
        )
        tool = await mcp.get_tool("relationship_assert_fact")

        action_id = uuid.uuid4()
        stored_args = {
            "subject": str(uuid.uuid4()),
            "predicate": _PRED_HAS_EMAIL,
            "object": "owner@example.test",
            "object_kind": "literal",
            "conf": 1.0,
            "verified": False,
            "approval_action_id": str(action_id),
        }
        await tool.fn(**stored_args)

        writer.assert_awaited_once()
        kwargs = writer.await_args.kwargs
        # The tool's own hardcoded src reaches the writer; the approval door
        # then replaces it with the source recorded when the action was parked.
        assert kwargs["src"] == "relationship"
        assert kwargs["approval_action_id"] == action_id


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
