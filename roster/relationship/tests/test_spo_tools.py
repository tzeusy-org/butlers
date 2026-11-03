"""Tests for relationship butler tools writing/reading SPO facts.

These tests verify that when a `facts` table is present, the tools
write to SPO facts and read back from them correctly.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import FastMCP

from butlers.testing.mcp import registered_tools

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


# ---------------------------------------------------------------------------
# Embedding engine mock — prevents loading sentence_transformers in tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="session")
def mock_embedding_engine():
    """Session-scoped autouse fixture that patches the embedding engine.

    Relationship tools lazy-load an EmbeddingEngine via get_embedding_engine().
    In tests we don't have sentence_transformers available, so we return a
    deterministic fake that produces a list of floats.  The facts table stores
    embedding as TEXT so the stringified list is compatible.
    """
    import sys

    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    engine.model_name = "test-model"

    with patch("butlers.modules.memory.tools.get_embedding_engine", return_value=engine):
        # Reset module-level _embedding_engine cache in each relationship tool
        for mod_name in (
            "butlers.tools.relationship.facts",
            "butlers.tools.relationship.gifts",
            "butlers.tools.relationship.tasks",
            "butlers.tools.relationship.loans",
            "butlers.tools.relationship.reminders",
            "butlers.tools.relationship.interactions",
            "butlers.tools.relationship.life_events",
        ):
            mod = sys.modules.get(mod_name)
            if mod is not None and hasattr(mod, "_embedding_engine"):
                mod._embedding_engine = None
        yield engine


# ---------------------------------------------------------------------------
# Pool fixture — relationship tables + facts table (no pgvector)
# ---------------------------------------------------------------------------


@pytest.fixture
async def rel_pool(db):
    """Core + memory + relationship chains, flat in ``public`` (as contact tools expect)."""
    return await db("core", "memory", "relationship")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _make_contact(rel_pool, first_name: str) -> dict:
    # Create a linked entity first (required by fact_set → resolve_contact_entity_id)
    # canonical_name must be set so task_list JOIN can return the display name.
    entity_row = await rel_pool.fetchrow(
        "INSERT INTO public.entities (canonical_name) VALUES ($1::varchar) RETURNING id",
        first_name,
    )
    row = await rel_pool.fetchrow(
        "INSERT INTO contacts (name, first_name, entity_id) VALUES ($1::text, $1::text, $2)"
        " RETURNING id, first_name",
        first_name,
        entity_row["id"],
    )
    # Populate contact_entity_map (rel_029 bridge) so resolve_contact_entity_id
    # can find the entity_id without reading public.contacts (contacts retirement,
    # bu-oluyt). contact_create() does this automatically; direct-insert helpers
    # must do it manually.
    await rel_pool.execute(
        """
        INSERT INTO contact_entity_map (contact_id, entity_id)
        VALUES ($1, $2)
        ON CONFLICT (contact_id) DO NOTHING
        """,
        row["id"],
        entity_row["id"],
    )
    return dict(row)


# ===========================================================================
# Quick facts (property facts)
# ===========================================================================


async def test_fact_set_reads_from_facts_table(rel_pool):
    """fact_set writes to facts table and fact_list reads from it."""
    from butlers.tools.relationship.facts import fact_list, fact_set

    contact = await _make_contact(rel_pool, "Alice")
    cid = contact["id"]

    result = await fact_set(rel_pool, cid, "color", "blue")
    assert result["key"] == "color"
    assert result["value"] == "blue"
    assert result["contact_id"] == cid

    items = await fact_list(rel_pool, cid)
    assert len(items) == 1
    assert items[0]["key"] == "color"
    assert items[0]["value"] == "blue"


async def test_fact_set_metadata_stored_as_dict(rel_pool):
    """_fact_set_spo writes metadata as a JSONB dict, not a double-encoded string.

    Regression for bu-69p63: json.dumps({}) was passed to a JSONB column that
    already had the asyncpg JSONB codec registered, causing double-encoding.
    After the fix, metadata must round-trip as a dict when read back.
    """
    from butlers.tools.relationship.facts import fact_set

    contact = await _make_contact(rel_pool, "MetaReg")
    cid = contact["id"]

    await fact_set(rel_pool, cid, "language", "python")

    row = await rel_pool.fetchrow(
        "SELECT metadata FROM facts WHERE subject = $1 AND predicate = 'language'",
        f"contact:{cid}",
    )
    assert row is not None
    # asyncpg JSONB codec returns a dict; a double-encoded value would be a str
    assert isinstance(row["metadata"], dict), (
        f"metadata must be a dict, got {type(row['metadata'])}: {row['metadata']!r}"
    )


async def test_fact_set_supersedes_previous(rel_pool):
    """fact_set on same key supersedes the old property fact."""
    from butlers.tools.relationship.facts import fact_list, fact_set

    contact = await _make_contact(rel_pool, "Bob")
    cid = contact["id"]

    await fact_set(rel_pool, cid, "city", "London")
    await fact_set(rel_pool, cid, "city", "Paris")

    items = await fact_list(rel_pool, cid)
    city_items = [i for i in items if i["key"] == "city"]
    assert len(city_items) == 1
    assert city_items[0]["value"] == "Paris"

    # Old fact should be superseded in DB
    superseded = await rel_pool.fetchval(
        "SELECT COUNT(*) FROM facts"
        " WHERE subject = $1 AND predicate = 'city' AND validity = 'superseded'",
        f"contact:{cid}",
    )
    assert superseded == 1


async def test_fact_list_multiple_keys(rel_pool):
    """fact_list returns all active property facts for a contact."""
    from butlers.tools.relationship.facts import fact_list, fact_set

    contact = await _make_contact(rel_pool, "Carol")
    cid = contact["id"]

    await fact_set(rel_pool, cid, "food", "sushi")
    await fact_set(rel_pool, cid, "sport", "tennis")

    items = await fact_list(rel_pool, cid)
    keys = {i["key"] for i in items}
    assert "food" in keys
    assert "sport" in keys


# ===========================================================================
# Gifts (temporal facts)
# ===========================================================================


async def test_gift_add_writes_to_facts(rel_pool):
    """gift_add writes a temporal fact and gift_list reads from it."""
    from butlers.tools.relationship.gifts import gift_add, gift_list

    contact = await _make_contact(rel_pool, "Dave")
    cid = contact["id"]

    gift = await gift_add(rel_pool, cid, "book about Python", occasion="birthday")
    assert gift["description"] == "book about Python"
    assert gift["occasion"] == "birthday"
    assert gift["status"] == "idea"
    assert gift["contact_id"] == cid

    gifts = await gift_list(rel_pool, cid)
    assert len(gifts) == 1
    assert gifts[0]["description"] == "book about Python"


async def test_gift_add_multiple_gifts_coexist(rel_pool):
    """Multiple gifts for a contact coexist as separate temporal facts."""
    from butlers.tools.relationship.gifts import gift_add, gift_list

    contact = await _make_contact(rel_pool, "Eve")
    cid = contact["id"]

    await gift_add(rel_pool, cid, "wine")
    await gift_add(rel_pool, cid, "flowers")

    gifts = await gift_list(rel_pool, cid)
    assert len(gifts) == 2
    descs = {g["description"] for g in gifts}
    assert "wine" in descs
    assert "flowers" in descs


async def test_gift_update_status(rel_pool):
    """gift_update_status supersedes old fact and creates new one."""
    from butlers.tools.relationship.gifts import gift_add, gift_update_status

    contact = await _make_contact(rel_pool, "Frank")
    cid = contact["id"]

    gift = await gift_add(rel_pool, cid, "chocolate box")
    gift_id = gift["id"]

    updated = await gift_update_status(rel_pool, gift_id, "purchased")
    assert updated["status"] == "purchased"

    # Old fact superseded
    superseded = await rel_pool.fetchval(
        "SELECT COUNT(*) FROM facts WHERE id = $1 AND validity = 'superseded'",
        gift_id,
    )
    assert superseded == 1


async def test_gift_update_status_invalid_regression(rel_pool):
    """gift_update_status rejects backward status transitions."""
    from butlers.tools.relationship.gifts import gift_add, gift_update_status

    contact = await _make_contact(rel_pool, "Grace")
    cid = contact["id"]

    gift = await gift_add(rel_pool, cid, "scarf")
    gift_id = gift["id"]

    updated_gift = await gift_update_status(rel_pool, gift_id, "purchased")

    with pytest.raises(ValueError, match="Cannot move"):
        await gift_update_status(rel_pool, updated_gift["id"], "idea")


async def test_gift_list_filter_by_status(rel_pool):
    """gift_list filters by status correctly."""
    from butlers.tools.relationship.gifts import gift_add, gift_list, gift_update_status

    contact = await _make_contact(rel_pool, "Helen")
    cid = contact["id"]

    g1 = await gift_add(rel_pool, cid, "hat")
    await gift_update_status(rel_pool, g1["id"], "purchased")
    await gift_add(rel_pool, cid, "gloves")

    idea_gifts = await gift_list(rel_pool, cid, status="idea")
    assert len(idea_gifts) == 1
    assert idea_gifts[0]["description"] == "gloves"

    purchased_gifts = await gift_list(rel_pool, cid, status="purchased")
    assert len(purchased_gifts) == 1
    assert purchased_gifts[0]["description"] == "hat"


# ===========================================================================
# Tasks (temporal facts)
# ===========================================================================


async def test_task_create_writes_to_facts(rel_pool):
    """task_create writes a temporal fact."""
    from butlers.tools.relationship.tasks import task_create

    contact = await _make_contact(rel_pool, "Ivan")
    cid = contact["id"]

    task = await task_create(rel_pool, cid, "Call back", description="About the meeting")
    assert task["title"] == "Call back"
    assert task["description"] == "About the meeting"
    assert task["completed"] is False
    assert task["contact_id"] == cid


async def test_task_list_returns_tasks(rel_pool):
    """task_list reads tasks from facts."""
    from butlers.tools.relationship.tasks import task_create, task_list

    contact = await _make_contact(rel_pool, "Julia")
    cid = contact["id"]

    await task_create(rel_pool, cid, "Send email")
    await task_create(rel_pool, cid, "Book flight")

    tasks = await task_list(rel_pool, cid)
    assert len(tasks) == 2
    titles = {t["title"] for t in tasks}
    assert "Send email" in titles
    assert "Book flight" in titles


async def test_task_list_includes_contact_name(rel_pool):
    """task_list includes contact_name field."""
    from butlers.tools.relationship.tasks import task_create, task_list

    contact = await _make_contact(rel_pool, "Karl")
    cid = contact["id"]

    await task_create(rel_pool, cid, "Follow up")
    tasks = await task_list(rel_pool, cid)
    assert len(tasks) == 1
    assert tasks[0]["contact_name"] == "Karl"


async def test_task_complete_supersedes_old_fact(rel_pool):
    """task_complete supersedes old task fact and creates completed version."""
    from butlers.tools.relationship.tasks import task_complete, task_create

    contact = await _make_contact(rel_pool, "Linda")
    cid = contact["id"]

    task = await task_create(rel_pool, cid, "Fix bug")
    task_id = task["id"]

    completed = await task_complete(rel_pool, task_id)
    assert completed["completed"] is True
    assert completed["completed_at"] is not None

    # Old fact superseded
    superseded = await rel_pool.fetchval(
        "SELECT COUNT(*) FROM facts WHERE id = $1 AND validity = 'superseded'",
        task_id,
    )
    assert superseded == 1


async def test_task_delete_retracts_fact(rel_pool):
    """task_delete retracts the task fact and cascades the memory_catalog
    disownment + entity_graph_edges deletion forget_memory() performs (bu-9ltqm)."""
    from butlers.tools.relationship.tasks import task_create, task_delete, task_list

    contact = await _make_contact(rel_pool, "Mike")
    cid = contact["id"]
    entity_id = await rel_pool.fetchval(
        "SELECT entity_id FROM contact_entity_map WHERE contact_id = $1", cid
    )

    task = await task_create(rel_pool, cid, "Buy groceries")
    task_id = task["id"]

    await rel_pool.execute(
        """
        INSERT INTO public.memory_catalog (source_schema, source_table, source_id, memory_type)
        VALUES ('public', 'facts', $1, 'fact')
        """,
        task_id,
    )
    await rel_pool.execute(
        """
        INSERT INTO public.entity_graph_edges
            (source_schema, source_table, source_id, subject_entity_id, predicate, object_entity_id)
        VALUES ('public', 'facts', $1, $2, 'test-predicate', $2)
        """,
        task_id,
        entity_id,
    )

    await task_delete(rel_pool, task_id)

    tasks = await task_list(rel_pool, cid)
    task_ids = {t["id"] for t in tasks}
    assert task_id not in task_ids

    catalog_row = await rel_pool.fetchrow(
        "SELECT confidence, invalid_at FROM public.memory_catalog"
        " WHERE source_schema = 'public' AND source_table = 'facts' AND source_id = $1",
        task_id,
    )
    assert catalog_row["confidence"] == 0
    assert catalog_row["invalid_at"] is not None
    edge_row = await rel_pool.fetchrow(
        "SELECT 1 FROM public.entity_graph_edges"
        " WHERE source_schema = 'public' AND source_table = 'facts' AND source_id = $1",
        task_id,
    )
    assert edge_row is None


async def test_task_list_exclude_completed_by_default(rel_pool):
    """task_list excludes completed tasks by default."""
    from butlers.tools.relationship.tasks import task_complete, task_create, task_list

    contact = await _make_contact(rel_pool, "Nancy")
    cid = contact["id"]

    t1 = await task_create(rel_pool, cid, "Active task")
    t2 = await task_create(rel_pool, cid, "Done task")

    completed = await task_complete(rel_pool, t2["id"])

    # Default: no completed tasks
    tasks = await task_list(rel_pool, cid)
    task_ids = {t["id"] for t in tasks}
    assert t1["id"] in task_ids
    assert completed["id"] not in task_ids

    # Include completed
    tasks_all = await task_list(rel_pool, cid, include_completed=True)
    all_ids = {t["id"] for t in tasks_all}
    assert t1["id"] in all_ids
    assert completed["id"] in all_ids


# ===========================================================================
# Loans (temporal facts)
# ===========================================================================


async def test_loan_create_writes_to_facts(rel_pool):
    """loan_create writes a temporal fact."""
    from butlers.tools.relationship.loans import loan_create

    contact = await _make_contact(rel_pool, "Tom")
    cid = contact["id"]

    loan = await loan_create(
        rel_pool,
        contact_id=cid,
        amount=Decimal("50.00"),
        direction="lent",
        description="Coffee money",
        currency="USD",
    )
    assert loan["description"] == "Coffee money"
    assert loan["settled"] is False
    assert loan["lender_contact_id"] == cid


async def test_loan_settle_updates_fact(rel_pool):
    """loan_settle supersedes old loan fact and marks as settled."""
    from butlers.tools.relationship.loans import loan_create, loan_settle

    contact = await _make_contact(rel_pool, "Uma")
    cid = contact["id"]

    loan = await loan_create(
        rel_pool,
        contact_id=cid,
        amount=Decimal("100.00"),
        direction="borrowed",
        description="Taxi",
        currency="USD",
    )
    loan_id = loan["id"]

    settled = await loan_settle(rel_pool, loan_id)
    assert settled["settled"] is True
    assert settled["settled_at"] is not None

    # Old fact superseded
    superseded = await rel_pool.fetchval(
        "SELECT COUNT(*) FROM facts WHERE id = $1 AND validity = 'superseded'",
        loan_id,
    )
    assert superseded == 1


async def test_loan_list_returns_loans(rel_pool):
    """loan_list reads loans from facts.

    Multiple loans from the same contact are stored as temporal facts and
    coexist independently — both must be returned by loan_list.
    """
    from butlers.tools.relationship.loans import loan_create, loan_list

    contact = await _make_contact(rel_pool, "Victor")
    cid = contact["id"]

    await loan_create(
        rel_pool,
        contact_id=cid,
        amount=Decimal("20.00"),
        direction="lent",
        description="Lunch",
        currency="USD",
    )
    await loan_create(
        rel_pool,
        contact_id=cid,
        amount=Decimal("30.00"),
        direction="lent",
        description="Dinner",
        currency="USD",
    )

    loans = await loan_list(rel_pool, cid)
    assert len(loans) == 2


async def test_loan_settle_rolls_back_if_replacement_fact_fails(rel_pool, monkeypatch):
    """The original obligation remains active after a mid-flight storage failure."""
    from butlers.modules.memory import storage
    from butlers.tools.relationship.loans import loan_create, loan_list, loan_settle

    contact = await _make_contact(rel_pool, "Atomic Loan")
    loan = await loan_create(
        rel_pool,
        contact_id=contact["id"],
        amount=Decimal("12.00"),
        direction="lent",
        description="Atomicity",
        currency="SGD",
    )

    async def fail_store(*args, **kwargs):
        raise RuntimeError("injected storage failure")

    monkeypatch.setattr(storage, "store_fact", fail_store)
    with pytest.raises(RuntimeError, match="injected storage failure"):
        await loan_settle(rel_pool, loan["id"])

    remaining = await loan_list(rel_pool, contact["id"])
    assert [item["id"] for item in remaining] == [loan["id"]]


async def test_loan_backfill_counts_currency_less_rows_without_inventing_currency(rel_pool):
    from butlers.tools.relationship.loans import backfill_loan_cost_claims

    await rel_pool.execute(
        """
        INSERT INTO facts
            (subject, predicate, content, metadata, scope, validity, valid_at)
        VALUES ('loan:legacy-no-currency', 'loan', 'Legacy',
                '{"amount_cents": 1200, "direction": "lent"}'::jsonb,
                'relationship', 'active', now())
        """
    )
    result = await backfill_loan_cost_claims(rel_pool)
    assert result["skipped_missing_currency"] == 1


# ===========================================================================
# Interactions (temporal facts)
# ===========================================================================


async def test_interaction_log_writes_to_facts(rel_pool):
    """interaction_log writes a temporal fact keyed by entity_id."""
    from butlers.tools.relationship.interactions import interaction_log

    contact = await _make_contact(rel_pool, "Wendy")
    entity_id = await rel_pool.fetchval(
        "SELECT entity_id FROM contacts WHERE id = $1", contact["id"]
    )
    occurred = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)

    result = await interaction_log(
        rel_pool, entity_id, "call", summary="Caught up", occurred_at=occurred
    )
    assert result["type"] == "call"
    assert result["summary"] == "Caught up"
    assert result["entity_id"] == entity_id

    # Fact must be stored with entity: subject prefix.
    row = await rel_pool.fetchrow("SELECT subject FROM facts WHERE id = $1", result["id"])
    assert row is not None
    assert row["subject"] == f"entity:{entity_id}"


async def test_interaction_log_with_metadata(rel_pool):
    """interaction_log stores user-provided metadata."""
    from butlers.tools.relationship.interactions import interaction_log

    contact = await _make_contact(rel_pool, "Xavier")
    entity_id = await rel_pool.fetchval(
        "SELECT entity_id FROM contacts WHERE id = $1", contact["id"]
    )
    meta = {"topic": "project update"}
    occurred = datetime(2026, 3, 2, 14, 0, tzinfo=UTC)

    result = await interaction_log(
        rel_pool, entity_id, "meeting", summary="Project sync", occurred_at=occurred, metadata=meta
    )
    assert result["metadata"] == meta


async def test_interaction_list_returns_interactions(rel_pool):
    """interaction_list reads interactions from facts by entity_id."""
    from butlers.tools.relationship.interactions import interaction_list, interaction_log

    contact = await _make_contact(rel_pool, "Yara")
    entity_id = await rel_pool.fetchval(
        "SELECT entity_id FROM contacts WHERE id = $1", contact["id"]
    )

    await interaction_log(
        rel_pool, entity_id, "email", summary="Hi!", occurred_at=datetime(2026, 3, 1, tzinfo=UTC)
    )
    await interaction_log(
        rel_pool, entity_id, "call", summary="Chat", occurred_at=datetime(2026, 3, 2, tzinfo=UTC)
    )

    items = await interaction_list(rel_pool, entity_id)
    assert len(items) == 2
    types = {i["type"] for i in items}
    assert "email" in types
    assert "call" in types


# ===========================================================================
# Life events (temporal facts)
# ===========================================================================


async def test_life_event_log_basic(rel_pool):
    """life_event_log writes a temporal fact."""
    from butlers.tools.relationship.life_events import life_event_log

    contact = await _make_contact(rel_pool, "Zara")
    cid = contact["id"]

    result = await life_event_log(rel_pool, cid, "promotion", summary="Got promoted!")
    assert result["type_name"] == "promotion"
    assert result["summary"] == "Got promoted!"
    assert result["contact_id"] == cid


async def test_life_event_log_without_date(rel_pool):
    """life_event_log with no date leaves happened_at as None in return."""
    from butlers.tools.relationship.life_events import life_event_log

    contact = await _make_contact(rel_pool, "Andy")
    cid = contact["id"]

    result = await life_event_log(rel_pool, cid, "new job", summary="New role")
    # happened_at should be None since no date was provided
    assert result.get("happened_at") is None


async def test_life_event_log_with_date(rel_pool):
    """life_event_log with happened_at sets the date."""
    from butlers.tools.relationship.life_events import life_event_log

    contact = await _make_contact(rel_pool, "Beth")
    cid = contact["id"]

    result = await life_event_log(
        rel_pool, cid, "promotion", summary="Promoted", happened_at="2026-01-15"
    )
    from datetime import date

    assert result["happened_at"] == date(2026, 1, 15)


async def test_life_event_list_returns_events(rel_pool):
    """life_event_list reads events from facts table."""
    from butlers.tools.relationship.life_events import life_event_list, life_event_log

    contact = await _make_contact(rel_pool, "Chad")
    cid = contact["id"]

    await life_event_log(rel_pool, cid, "promotion", summary="Promoted to VP")
    await life_event_log(rel_pool, cid, "new job", summary="Started at Acme")

    events = await life_event_list(rel_pool, cid)
    assert len(events) == 2
    type_names = {e["type_name"] for e in events}
    assert "promotion" in type_names
    assert "new job" in type_names


async def test_life_event_list_filter_by_type(rel_pool):
    """life_event_list filters by type_name correctly."""
    from butlers.tools.relationship.life_events import life_event_list, life_event_log

    contact = await _make_contact(rel_pool, "Dana")
    cid = contact["id"]

    await life_event_log(rel_pool, cid, "promotion", summary="Promoted")
    await life_event_log(rel_pool, cid, "new job", summary="New job")

    events = await life_event_list(rel_pool, cid, type_name="promotion")
    assert len(events) == 1
    assert events[0]["type_name"] == "promotion"


async def test_life_event_log_invalid_type_raises(rel_pool):
    """life_event_log raises ValueError for unknown event type."""
    from butlers.tools.relationship.life_events import life_event_log

    contact = await _make_contact(rel_pool, "Evan")
    cid = contact["id"]

    with pytest.raises(ValueError, match="Unknown life event type"):
        await life_event_log(rel_pool, cid, "alien_abduction", summary="Weird")


# ===========================================================================
# Gifts — entity_id contract tests (bu-x7fdu.1)
# ===========================================================================


async def test_gift_add_stores_entity_id(rel_pool):
    """gift_add resolves contact entity_id and stores it on the fact row."""
    from butlers.tools.relationship.gifts import gift_add

    contact = await _make_contact(rel_pool, "Iris")
    cid = contact["id"]

    gift = await gift_add(rel_pool, cid, "notebook")
    fact_row = await rel_pool.fetchrow(
        "SELECT entity_id FROM facts WHERE id = $1",
        gift["id"],
    )
    assert fact_row is not None
    assert fact_row["entity_id"] is not None, "entity_id must be set on the fact (not None)"

    # The stored entity_id must match the one on the contact
    contact_entity_id = await rel_pool.fetchval("SELECT entity_id FROM contacts WHERE id = $1", cid)
    assert fact_row["entity_id"] == contact_entity_id


async def test_gift_update_status_preserves_entity_id(rel_pool):
    """gift_update_status passes entity_id so the superseding fact also carries it."""
    from butlers.tools.relationship.gifts import gift_add, gift_update_status

    contact = await _make_contact(rel_pool, "James")
    cid = contact["id"]

    gift = await gift_add(rel_pool, cid, "candle")
    updated = await gift_update_status(rel_pool, gift["id"], "purchased")

    fact_row = await rel_pool.fetchrow(
        "SELECT entity_id FROM facts WHERE id = $1",
        updated["id"],
    )
    assert fact_row is not None
    assert fact_row["entity_id"] is not None, "superseding fact must carry entity_id"

    contact_entity_id = await rel_pool.fetchval("SELECT entity_id FROM contacts WHERE id = $1", cid)
    assert fact_row["entity_id"] == contact_entity_id


async def test_gift_list_returns_only_active_facts(rel_pool):
    """gift_list returns only validity='active' facts, not superseded ones."""
    from butlers.tools.relationship.gifts import gift_add, gift_list, gift_update_status

    contact = await _make_contact(rel_pool, "Kate")
    cid = contact["id"]

    gift = await gift_add(rel_pool, cid, "perfume")
    # Advance status — old fact becomes superseded
    await gift_update_status(rel_pool, gift["id"], "purchased")

    gifts = await gift_list(rel_pool, cid)
    # Only the active (purchased) fact should be visible
    assert len(gifts) == 1
    assert gifts[0]["status"] == "purchased"

    # Confirm superseded row exists but is not returned
    total = await rel_pool.fetchval(
        "SELECT COUNT(*) FROM facts WHERE subject LIKE $1 AND predicate = 'gift'",
        f"contact:{cid}:gift:%",
    )
    assert total == 2  # original + superseding


async def test_gift_add_no_entity_raises_value_error(rel_pool):
    """gift_add raises ValueError when the contact has no linked entity."""
    from butlers.tools.relationship.gifts import gift_add

    # Create a contact without entity_id
    row = await rel_pool.fetchrow(
        "INSERT INTO contacts (name, first_name) VALUES ($1::text, $1::text) RETURNING id",
        "Orphan",
    )
    cid = row["id"]

    with pytest.raises(ValueError, match="no linked entity_id"):
        await gift_add(rel_pool, cid, "mystery gift")


async def test_gift_update_status_no_entity_raises_value_error(rel_pool):
    """gift_update_status raises ValueError when the contact has no linked entity."""
    from butlers.tools.relationship.gifts import gift_update_status

    # Create a contact without entity_id (no entry in contact_entity_map)
    row = await rel_pool.fetchrow(
        "INSERT INTO contacts (name, first_name) VALUES ($1::text, $1::text) RETURNING id",
        "OrphanUpdate",
    )
    cid = row["id"]

    # Insert a gift fact directly (bypassing gift_add which would also raise).
    # This simulates a gift that exists but whose contact lost its entity link.
    gift_row = await rel_pool.fetchrow(
        """
        INSERT INTO facts (subject, predicate, content, scope, validity, metadata)
        VALUES ($1, 'gift', 'mystery gift', 'relationship', 'active', '{"status": "idea"}')
        RETURNING id
        """,
        f"contact:{cid}:gift:mystery_gift",
    )
    gift_id = gift_row["id"]

    with pytest.raises(ValueError, match="no linked entity_id"):
        await gift_update_status(rel_pool, gift_id, "purchased")


# ===========================================================================
# Feed (unified temporal activity feed)
# ===========================================================================


async def test_feed_get_aggregates_temporal_facts_ordered_desc(rel_pool):
    """feed_get returns interaction_*, life_event, contact_note, and activity
    facts for a contact entity ordered by valid_at DESC, via the real fact store.
    """
    from butlers.modules.memory.storage import store_fact
    from butlers.modules.memory.tools import get_embedding_engine
    from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id
    from butlers.tools.relationship.feed import feed_get
    from butlers.tools.relationship.interactions import interaction_log
    from butlers.tools.relationship.life_events import life_event_log
    from butlers.tools.relationship.notes import note_create

    contact = await _make_contact(rel_pool, "FeedAlice")
    cid = contact["id"]
    entity_id = await resolve_contact_entity_id(rel_pool, cid)
    assert entity_id is not None

    # Distinct valid_at across the four temporal families.
    life_at = datetime(2025, 1, 1, tzinfo=UTC)
    activity_at = datetime(2025, 2, 1, tzinfo=UTC)
    interaction_at = datetime(2025, 3, 1, tzinfo=UTC)

    # life_event (valid_at = happened_at)
    await life_event_log(rel_pool, cid, "promotion", summary="Made partner", occurred_at=life_at)
    # activity (no dedicated tool — write directly through the real fact store)
    await store_fact(
        rel_pool,
        subject=f"entity:{entity_id}",
        predicate="activity",
        content="Joined a book club",
        embedding_engine=get_embedding_engine(),
        permanence="stable",
        scope="relationship",
        entity_id=entity_id,
        valid_at=activity_at,
    )
    # interaction (valid_at = occurred_at); pass contact_id (resolved internally)
    await interaction_log(
        rel_pool, cid, type="call", summary="Quarterly catch-up", occurred_at=interaction_at
    )
    # contact_note (valid_at = now, newest)
    await note_create(rel_pool, cid, content="Loves hiking")

    feed = await feed_get(rel_pool, entity_id)

    kinds = [item["kind"] for item in feed]
    assert "interaction" in kinds
    assert "life_event" in kinds
    assert "contact_note" in kinds
    assert "activity" in kinds
    assert len(feed) == 4

    # Ordered by valid_at DESC: note (now) > interaction (Mar) > activity (Feb) > life_event (Jan)
    valid_ats = [item["valid_at"] for item in feed]
    assert valid_ats == sorted(valid_ats, reverse=True)
    assert feed[0]["kind"] == "contact_note"
    assert feed[1]["kind"] == "interaction"
    assert feed[2]["kind"] == "activity"
    assert feed[3]["kind"] == "life_event"


async def test_feed_get_registered_tool_resolves_contact(rel_pool):
    """The registered feed_get MCP tool accepts a contact_id, resolves it to the
    entity, and returns the contact's temporal feed from the real fact store.
    """
    import importlib

    from butlers.tools.relationship.interactions import interaction_log
    from butlers.tools.relationship.notes import note_create

    # Roster modules are loaded under a synthetic package name by the registry
    # (see butlers.modules.registry._register_roster_modules); conftest triggers
    # discovery so the submodule is importable here.
    register_tools = importlib.import_module(
        "butlers.modules._roster_relationship.tools"
    ).register_tools

    contact = await _make_contact(rel_pool, "FeedBob")
    cid = contact["id"]

    await interaction_log(
        rel_pool, cid, type="meeting", summary="Lunch", occurred_at=datetime(2025, 5, 1, tzinfo=UTC)
    )
    await note_create(rel_pool, cid, content="Allergic to peanuts")

    class _StubModule:
        def _get_pool(self):
            return rel_pool

    mcp = FastMCP("relationship-feed-test")
    register_tools(mcp, _StubModule(), config=None)
    tools = await registered_tools(mcp)
    assert "feed_get" in tools

    feed = await tools["feed_get"](contact_id=cid)
    kinds = {item["kind"] for item in feed}
    assert kinds == {"interaction", "contact_note"}
    assert len(feed) == 2
    # Newest first: the note (created now) precedes the May interaction.
    assert feed[0]["kind"] == "contact_note"
