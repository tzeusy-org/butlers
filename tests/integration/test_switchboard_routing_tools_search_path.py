"""Integration teeth for bu-0aker: the routing-tools verdict/promotion/demotion
writers must resolve switchboard tables under a caller pool whose search_path
does NOT include the switchboard schema.

This is the ``routing_verdict_log`` / ``rule_promotion_suggestions`` /
``ingestion_rules`` counterpart to
``test_deliver_search_path_integration.py`` (which covers the same production
hazard for ``deliver()``'s ``butler_registry`` / ``notifications`` /
``routing_log`` reads): a pool whose ``search_path`` omits the switchboard
schema reaching a *bare* switchboard table silently fails.

The failure is especially quiet here because
:func:`record_routing_verdict` / :func:`maybe_create_demotion_suggestion`
follow the degraded-honesty contract — they swallow the resulting
``UndefinedTableError`` and return ``None`` / ``ran=False`` rather than
raising. So a bare ``INSERT INTO routing_verdict_log`` under a public-only
pool would drop the ledger row with no visible error at all.

These tests provision the *real* switchboard schema through the (now
schema-faithful, bu-9auxy) :func:`create_migrated_test_db` harness, then drive
the writers through a pool whose ``search_path`` is scoped to ``public`` only.
They pass only because every switchboard-table reference in the exercised call
graph is schema-qualified to ``switchboard.<table>`` (bu-0aker) — reverting any
qualification turns the assertions red (fail-before / pass-after).
"""

from __future__ import annotations

import shutil
import uuid

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.tools.switchboard.routing.rule_demotion import maybe_create_demotion_suggestion
from butlers.tools.switchboard.routing.verdict_log import record_routing_verdict

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    # Faithful topology: switchboard chain provisioned into its own schema
    # (bu-9auxy), so a public-only pool genuinely cannot see its tables
    # unqualified -- which is what makes the qualification teeth meaningful.
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )


@pytest.fixture
async def public_pool(migrated_db_url: str) -> asyncpg.Pool:
    """Pool whose ``search_path`` is scoped to ``public`` only.

    Mirrors the production shared/public-scoped caller shape (e.g. the shared
    credential pool in ``deps.py``, ``shared_db_schema = "public"``) that
    ``test_deliver_search_path_integration.py`` reproduces for ``deliver()``.
    """
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        server_settings={"search_path": "public"},
    )
    yield p
    await p.close()


@pytest.fixture
async def switchboard_pool(migrated_db_url: str) -> asyncpg.Pool:
    """Pool scoped to the switchboard schema, used only to seed/read-back rows."""
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        server_settings={"search_path": "switchboard,public"},
    )
    yield p
    await p.close()


async def _insert_ingestion_event(pool: asyncpg.Pool, *, dedupe_key: str) -> uuid.UUID:
    """Seed a ``public.ingestion_events`` FK parent (core table, lives in public)."""
    event_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO public.ingestion_events
            (id, source_channel, source_provider, source_endpoint_identity,
             external_event_id, dedupe_key, dedupe_strategy, ingestion_tier, policy_tier)
        VALUES ($1, 'email', 'gmail', 'owner@example.com', $2, $3, 'connector_api', 'full', 'default')
        """,
        event_id,
        f"ext-{dedupe_key}",
        dedupe_key,
    )
    return event_id


async def _insert_promoted_rule(
    pool: asyncpg.Pool, *, action: str = "route_to:finance"
) -> uuid.UUID:
    rule_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO switchboard.ingestion_rules
            (id, scope, rule_type, condition, action, priority, created_by)
        VALUES ($1, 'global', 'sender_address', '{"address": "alerts@chase.com"}'::jsonb,
                $2, 100, 'promotion')
        """,
        rule_id,
        action,
    )
    return rule_id


@pytest.mark.asyncio(loop_scope="session")
async def test_record_routing_verdict_writes_under_public_only_search_path(
    public_pool: asyncpg.Pool, switchboard_pool: asyncpg.Pool
) -> None:
    """``record_routing_verdict`` must land its row through a public-only pool.

    Pre-fix (bare ``INSERT INTO routing_verdict_log``) the write raises
    ``UndefinedTableError`` under this pool, which the degraded-honesty
    contract swallows -> the function returns ``None`` and the ledger row is
    silently dropped. The schema-qualified INSERT resolves and returns an id.
    """
    # Sanity: the calling pool genuinely cannot see the switchboard table
    # unqualified -- this is what gives the qualification its teeth.
    with pytest.raises(asyncpg.UndefinedTableError):
        await public_pool.fetch("SELECT id FROM routing_verdict_log")

    event_id = await _insert_ingestion_event(public_pool, dedupe_key="sp-verdict-1")

    row_id = await record_routing_verdict(
        public_pool,
        ingestion_event_id=event_id,
        sender_identity="alerts@chase.com",
        source_channel="email",
        verdict_source="llm",
        verdict_action="route_to",
        verdict_target="finance",
    )
    assert row_id is not None  # teeth: None pre-qualification (swallowed UndefinedTableError)

    row = await switchboard_pool.fetchrow(
        "SELECT verdict_source, verdict_target FROM switchboard.routing_verdict_log WHERE id = $1",
        uuid.UUID(row_id),
    )
    assert row is not None
    assert row["verdict_source"] == "llm"
    assert row["verdict_target"] == "finance"


@pytest.mark.asyncio(loop_scope="session")
async def test_maybe_create_demotion_suggestion_runs_under_public_only_search_path(
    public_pool: asyncpg.Pool, switchboard_pool: asyncpg.Pool
) -> None:
    """``maybe_create_demotion_suggestion`` reads ``ingestion_rules`` +
    ``routing_verdict_log`` and writes ``rule_promotion_suggestions`` — all
    three enumerated switchboard tables — so it exercises every qualification
    in this bead through the public-only pool in one flow.

    Pre-fix, ``_fetch_promoted_rule``'s bare ``FROM ingestion_rules`` raises
    ``UndefinedTableError``, swallowed into ``ran=False, reason='error'``. The
    qualified reads/insert let the whole demotion evaluation complete.
    """
    rule_id = await _insert_promoted_rule(switchboard_pool, action="route_to:finance")

    # 5 spot-checks, all disagreeing -> sustained disagreement -> demotion filed.
    for i in range(5):
        event_id = await _insert_ingestion_event(public_pool, dedupe_key=f"sp-demote-{i}")
        await record_routing_verdict(
            public_pool,
            ingestion_event_id=event_id,
            sender_identity="alerts@chase.com",
            source_channel="email",
            verdict_source="spot_check",
            verdict_action="route_to",
            verdict_target="general",
            matched_rule_id=rule_id,
        )

    result = await maybe_create_demotion_suggestion(
        public_pool, rule_id=str(rule_id), min_samples=5
    )

    assert result.ran is True  # teeth: ran=False, reason='error' pre-qualification
    assert result.demoted_suggestion_created is True
    assert result.agreement_score == 0.0

    row = await switchboard_pool.fetchrow(
        "SELECT suggestion_kind, status, evidence_count "
        "FROM switchboard.rule_promotion_suggestions WHERE target_rule_id = $1",
        rule_id,
    )
    assert row is not None
    assert row["suggestion_kind"] == "demotion"
    assert row["status"] == "pending_review"
    assert row["evidence_count"] == 5
