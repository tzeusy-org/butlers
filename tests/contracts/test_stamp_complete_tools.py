"""RFC 0038 D6: a tool may declare ``stamped`` output only when its account is complete.

The stamp report says which records a tool's readers accounted, not that the tool
returned nothing else (bu-dhloi1.33). Each audited tool is therefore run against a
real database and its whole output walked: every returned value must come from a
record that reached ``account_records``, or from a field named here with its reason.
``relationship_lookup`` is the oracle's negative control: its entity header comes
from a store with no stamp, so it stays ``untrusted`` and always accounts unclean.
"""

from __future__ import annotations

import ast
import json
import shutil
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from butlers.core.goal_lineage import VerifiedLineage, lineage_scope
from butlers.core.stamp_account import (
    StampAccount,
    account_records,
    account_scope,
    account_unstamped,
)
from butlers.core.tool_call_capture import (
    clear_runtime_session_routing_context,
    reset_current_runtime_session_id,
    set_current_runtime_session_id,
    set_runtime_session_routing_context,
)
from butlers.core.tool_catalog import STAMP_COMPLETE_READS
from butlers.core.tool_presentation_inventory import TOOL_PRESENTATION_INVENTORY
from butlers.core_tools._conversation_recall import register_conversation_recall_tool
from butlers.modules.memory import storage
from butlers.modules.memory.content_authority import HELD_FACT_SQL
from butlers.modules.memory.search import resolve_catalog_read_policy
from butlers.modules.memory.tools.reading import memory_get, memory_recall, memory_search
from butlers.tools.finance.facts import bulk_update_transactions
from butlers.tools.health import conditions, medications, research
from butlers.tools.health.conditions import condition_update, symptom_update
from butlers.tools.health.diet import meal_update
from butlers.tools.health.measurements import measurement_update
from butlers.tools.health.medications import medication_log_dose, medication_update
from butlers.tools.health.research import research_update
from butlers.tools.relationship import gifts, loans, tasks
from butlers.tools.relationship.gifts import gift_update_status
from butlers.tools.relationship.loans import loan_settle
from butlers.tools.relationship.notes import note_list, note_search
from butlers.tools.relationship.relationship_lookup import relationship_lookup
from butlers.tools.relationship.tasks import task_complete


def _db(test):
    """A real-database contract: the audit's claims are about the actual SQL."""
    for mark in (
        pytest.mark.integration,
        pytest.mark.asyncio(loop_scope="session"),
        pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
    ):
        test = mark(test)
    return test


_POLICY = resolve_catalog_read_policy("normal")
_COVERED: dict[tuple[str, str], str] = {}
#: Bumped by the read itself, or stored in a form no reader returns as text.
_VOLATILE_COLUMNS = {"reference_count", "last_referenced_at", "embedding", "search_vector"}
#: Arguments echoed back, not stored content.
_ECHOED_NOTE = {"contact_id"}
_ECHOED_THREAD = {"conversation_id"}
#: butler_name is the server-held routing identity of the conversation (no tool or
#: model text sets it); deep_link derives from it and session_id; snippet is
#: ts_headline over the accounted message's own content, asserted separately.
_DERIVED_RECALL = {"butler_name", "deep_link", "snippet"}
#: A joined column selected into an accounted row would pass the value walk, so each
#: tool's output keys are pinned too: exactly these, or for the ``SELECT *`` memory
#: readers, the table's own columns plus the scores the search adds.
_RECALL_KEYS = {
    "conversation_id",
    "message_id",
    "role",
    "created_at",
    "butler_name",
    "snippet",
    "session_id",
    "deep_link",
}
_THREAD_MESSAGE_KEYS = {"message_id", "role", "content", "created_at", "session_id"}
_NOTE_KEYS = {
    "id",
    "contact_id",
    "body",
    "content",
    "emotion",
    "created_at",
    "taint_goal_id",
    "taint_invocation_id",
}
_MEMORY_SCORES = {
    "memory_type",
    "similarity",
    "rank",
    "rrf_score",
    "semantic_rank",
    "keyword_rank",
    "composite_score",
}


def _covers(*keys: tuple[str, str]):
    """Bind ``STAMP_COMPLETE_READS`` entries to the database contract that proves them."""

    def bind(test):
        for key in keys:
            _COVERED[key] = test.__name__
        return test

    return bind


def _engine() -> MagicMock:
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    engine.model_name = "contract-test-model"
    return engine


def _exposed() -> VerifiedLineage:
    return VerifiedLineage(
        goal_id=uuid.uuid4(),
        invocation_id=uuid.uuid4(),
        depth=0,
        origin="owner",
        exposed=True,
        capability="seed",
        args_digest="seed",
        call_id=uuid.uuid4(),
    )


def _forms(value: Any) -> Iterator[str]:
    """Every text form of a stored value, nested values included."""
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _forms(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _forms(item)
    elif isinstance(value, datetime):
        yield value.isoformat()
    elif value is not None:
        yield str(value)


def _leaves(value: Any, key: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for name, item in value.items():
            yield from _leaves(item, name)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _leaves(item, key)
    elif value is not None and not isinstance(value, bool):
        yield key, value


def _uncovered(output: Any, records: list[Any], *, exempt: set[str] = frozenset()) -> set[str]:
    """Output fields whose value no accounted record holds, whole and non-empty."""
    known = {form for record in records for name in record.keys() for form in _forms(record[name])}
    known.discard("")
    return {
        name
        for name, leaf in _leaves(output)
        if name not in exempt and next(_forms(leaf)) not in known
    }


def _comparable(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in ("{", "["):
        return json.loads(value)
    return value


async def _unfaithful(pool, table: str, records: list[Any], id_key: str = "id") -> set[str]:
    """Record columns whose value is not the stored row's: a joined value under a column's name."""
    drift = set()
    for record in records:
        row = await pool.fetchrow(f"SELECT * FROM {table} WHERE id = $1", record[id_key])
        assert row is not None, (table, record[id_key])
        for name in (set(record.keys()) & set(row.keys())) - _VOLATILE_COLUMNS:
            if _comparable(record[name]) != _comparable(row[name]):
                drift.add(name)
    return drift


@pytest.fixture
def accounted(monkeypatch) -> list[Any]:
    """The records that reached the account, as the real ``StampAccount`` receives them."""
    records: list[Any] = []
    add = StampAccount.add

    def spy(self, record, owner_authored):
        records.append(record)
        add(self, record, owner_authored)

    monkeypatch.setattr(StampAccount, "add", spy)
    return records


@pytest.fixture
async def stores(db):
    """Every audited store: public chat history (core), memory and relationship tables."""
    schemas = {"memory": "relationship", "relationship": "relationship"}
    return await db("core", "memory", "relationship", schemas=schemas, schema="relationship")


@pytest.mark.unit
def test_exactly_the_audited_reads_declare_stamped_output():
    stamped = {
        (item.module_name, item.canonical_name): item
        for item in TOOL_PRESENTATION_INVENTORY
        if item.output_trust == "stamped"
    }
    assert set(stamped) == set(STAMP_COMPLETE_READS)
    for key, item in stamped.items():
        assert set(item.read_stores) <= STAMP_COMPLETE_READS[key], key
        assert item.unknown_output_source is False, key
    assert ("relationship", "relationship_lookup") not in stamped


def _named_by_contract(test_name: str) -> set[str]:
    """Names and strings in a test's body and parameters, excluding docstring and binding."""
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    test = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == test_name
    )
    body = test.body[1:] if ast.get_docstring(test) else test.body
    parameters = [
        decorator
        for decorator in test.decorator_list
        if not (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "_covers"
        )
    ]
    named: set[str] = set()
    for root in [*body, *parameters]:
        for node in ast.walk(root):
            if isinstance(node, ast.Name):
                named.add(node.id)
            elif isinstance(node, ast.Attribute):
                named.add(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                named.add(node.value)
    return named


@pytest.mark.unit
def test_every_audited_read_is_bound_to_a_database_contract():
    assert set(_COVERED) == set(STAMP_COMPLETE_READS)
    for (_, tool), name in _COVERED.items():
        assert globals()[name].pytestmark, name
        assert any(mark.name == "integration" for mark in globals()[name].pytestmark), name
        assert tool in _named_by_contract(name), (tool, name)
    # The control: a binding to another tool's contract is caught.
    assert "note_list" not in _named_by_contract(
        "test_memory_get_returns_only_its_accounted_record"
    )


@pytest.mark.unit
def test_the_oracle_takes_only_whole_non_empty_values():
    record = {"content": "the whole value", "note": ""}
    assert _uncovered({"content": "the whole value"}, [record]) == set()
    assert _uncovered({"content": "whole"}, [record]) == {"content"}
    assert _uncovered({"note": ""}, [record]) == {"note"}


@pytest.mark.unit
def test_output_from_an_unstamped_store_is_never_owner_untainted():
    account_unstamped()  # Outside a verified call this is a no-op.
    with account_scope() as account:
        account_records([])
        assert (account.opened, account.owner_untainted) == (True, True)
        account_unstamped()
    assert (account.opened, account.owner_untainted) == (True, False)
    with account_scope() as fresh:
        account_unstamped()
    assert fresh.opened is True and fresh.report("c", uuid.uuid4(), "d")["owner_untainted"] is False


async def _seed_memory(pool, word: str) -> tuple[list[uuid.UUID], VerifiedLineage]:
    """One owner row per type, and one exposed fact and episode from a real writer."""
    engine, exposed = _engine(), _exposed()
    owner_fact = await storage.store_fact(
        pool, "Mei", "likes", f"{word} owner fact", engine, content_authority="owner"
    )
    rule = await storage.store_rule(
        pool, f"{word} owner rule", engine, content_authority="owner", scope="global"
    )
    episode = await storage.store_episode(
        pool, f"{word} owner episode", "memory", engine, content_authority="owner"
    )
    with lineage_scope(exposed):
        tainted = await storage.store_fact(pool, "Mei", "dislikes", f"{word} exposed fact", engine)
        await storage.store_episode(pool, f"{word} exposed episode", "memory", engine)
    return [owner_fact["id"], rule, episode, tainted["id"]], exposed


async def _memory_columns(pool, table: str) -> set[str]:
    rows = await pool.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'relationship' "
        "AND table_name = $1",
        table,
    )
    return {row["column_name"] for row in rows}


@_covers(("memory", "memory_search"), ("memory", "memory_recall"))
@pytest.mark.parametrize("read", [memory_search, memory_recall])
@_db
async def test_memory_reads_return_only_accounted_records(stores, accounted, read):
    _, exposed = await _seed_memory(stores, "lighthouse")
    with account_scope() as account:
        output = await read(stores, _engine(), "lighthouse", read_policy=_POLICY)
    assert any("exposed" in item["content"] for item in output)
    assert _uncovered(output, accounted) == set()
    # Each row's keys are its own table's, or a value joined under another table's column name
    # would pass here and pass _unfaithful, which compares only the columns they share.
    rules, facts = await _memory_columns(stores, "rules"), await _memory_columns(stores, "facts")
    assert rules - facts
    assert account.writers == {(exposed.goal_id, exposed.invocation_id)}
    assert account.owner_untainted is True
    for memory_type, table in (("fact", "facts"), ("rule", "rules"), ("episode", "episodes")):
        rows = [record for record in accounted if record["memory_type"] == memory_type]
        assert await _unfaithful(stores, table, rows) == set()
        columns = await _memory_columns(stores, table) | _MEMORY_SCORES
        returned = [item for item in output if item["memory_type"] == memory_type]
        assert {key for item in returned for key in item} <= columns


@_covers(("memory", "memory_get"))
@_db
async def test_memory_get_returns_only_its_accounted_record(stores, accounted):
    (owner_fact, _, _, tainted), exposed = await _seed_memory(stores, "harbour")
    with account_scope() as clean:
        output = await memory_get(stores, "fact", str(owner_fact), read_policy=_POLICY)
    assert _uncovered(output, accounted) == set()
    assert (clean.owner_untainted, clean.writers) == (True, set())
    assert set(output) <= await _memory_columns(stores, "facts")
    assert await _unfaithful(stores, "facts", accounted) == set()
    with account_scope() as stamped:
        await memory_get(stores, "fact", str(tainted), read_policy=_POLICY)
    assert stamped.writers == {(exposed.goal_id, exposed.invocation_id)}


@_covers(("relationship", "note_list"))
@_db
async def test_note_list_returns_only_its_accounted_records(stores, accounted):
    contact, engine, exposed = uuid.uuid4(), _engine(), _exposed()
    subject = f"contact:{contact}"
    await storage.store_fact(
        stores,
        subject,
        "contact_note",
        "owner note",
        engine,
        permanence="stable",
        scope="relationship",
        valid_at=datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
        content_authority="owner",
    )
    with lineage_scope(exposed):
        await storage.store_fact(
            stores,
            subject,
            "contact_note",
            "exposed note",
            engine,
            permanence="stable",
            scope="relationship",
            valid_at=datetime.fromisoformat("2026-01-02T00:00:00+00:00"),
        )
    with account_scope() as account:
        output = await note_list(stores, contact)
    assert {item["content"] for item in output} == {"owner note", "exposed note"}
    assert _uncovered(output, accounted, exempt=_ECHOED_NOTE) == set()
    assert all(set(item) == _NOTE_KEYS for item in output)
    assert await _unfaithful(stores, "facts", accounted) == set()
    # The control: a joined value under a column's name is caught.
    aliased = {**dict(accounted[0]), "content": "joined from another table"}
    assert await _unfaithful(stores, "facts", [aliased]) == {"content"}
    assert (account.owner_untainted, account.writers) == (
        True,
        {(exposed.goal_id, exposed.invocation_id)},
    )


def _conversation_tools(pool) -> dict[str, Any]:
    tools: dict[str, Any] = {}

    def core_tool(_group):
        def register(function):
            tools[function.__name__] = function
            return function

        return register

    register_conversation_recall_tool(SimpleNamespace(pool=pool), MagicMock(), core_tool)
    return tools


async def _seed_chat(pool, channel: str = "dashboard") -> tuple[uuid.UUID, tuple[uuid.UUID, ...]]:
    conversation, writer = uuid.uuid4(), (uuid.uuid4(), uuid.uuid4())
    await pool.execute(
        "INSERT INTO public.dashboard_conversations (id, butler_name, title, status, created_at, "
        "updated_at, message_count, source_channel) "
        "VALUES ($1, 'finance', 'SECRET TITLE', 'active', now(), now(), 0, $2)",
        conversation,
        channel,
    )
    for content, pointer in (("beacon owner turn", (None, None)), ("beacon goal turn", writer)):
        await pool.execute(
            "INSERT INTO public.dashboard_messages (id, conversation_id, role, content, created_at, "
            "taint_goal_id, taint_invocation_id) VALUES ($1, $2, 'user', $3, now(), $4, $5)",
            uuid.uuid4(),
            conversation,
            content,
            *pointer,
        )
    return conversation, writer


@_covers(("core", "conversation_recall"))
@_db
async def test_conversation_recall_returns_only_its_accounted_records(stores, accounted):
    _, writer = await _seed_chat(stores)
    tools = _conversation_tools(stores)
    with account_scope() as account:
        output = await tools["conversation_recall"]("beacon", butler="finance")
    assert len(output) == 2 and all("beacon" in item["snippet"] for item in output)
    assert not any("SECRET" in str(item) for item in output)
    assert _uncovered(output, accounted, exempt=_DERIVED_RECALL) == set()
    assert (account.owner_untainted, account.writers) == (True, {writer})
    assert all(set(item) == _RECALL_KEYS for item in output)
    messages = "public.dashboard_messages"
    assert await _unfaithful(stores, messages, accounted, "message_id") == set()
    conversations = "public.dashboard_conversations"
    routing = [
        {key: record[key] for key in ("conversation_id", "butler_name", "source_channel")}
        for record in accounted
    ]  # created_at is the message's here, so only the joined columns compare.
    assert await _unfaithful(stores, conversations, routing, "conversation_id") == set()


@_covers(("core", "conversation_thread_read"))
@_db
async def test_conversation_thread_read_returns_only_its_accounted_records(stores, accounted):
    conversation, writer = await _seed_chat(stores)
    tools = _conversation_tools(stores)
    with account_scope() as account:
        output = await tools["conversation_thread_read"](str(conversation))
    assert len(output["messages"]) == 2
    assert _uncovered(output, accounted, exempt=_ECHOED_THREAD) == set()
    assert set(output) == {"conversation_id", "messages"}
    assert all(set(item) == _THREAD_MESSAGE_KEYS for item in output["messages"])
    assert await _unfaithful(stores, "public.dashboard_messages", accounted) == set()
    assert (account.owner_untainted, account.writers) == (True, {writer})


@_db
async def test_note_search_accounts_its_notes_and_is_unclean_for_the_joined_name(stores, accounted):
    """The audit's failure is contact_name alone: the notes and their pointers are accounted."""
    contact, engine, exposed = uuid.uuid4(), _engine(), _exposed()
    entity = await stores.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, roles) "
        "VALUES ('Joined Name', 'person', '{}') RETURNING id"
    )
    await stores.execute(
        "INSERT INTO contact_entity_map (contact_id, entity_id) VALUES ($1, $2)", contact, entity
    )
    subject = f"contact:{contact}"
    note = {"permanence": "stable", "scope": "relationship"}
    await storage.store_fact(
        stores,
        subject,
        "contact_note",
        "owner lighthouse",
        engine,
        valid_at=datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
        content_authority="owner",
        **note,
    )
    with lineage_scope(exposed):
        await storage.store_fact(
            stores,
            subject,
            "contact_note",
            "exposed lighthouse",
            engine,
            valid_at=datetime.fromisoformat("2026-01-02T00:00:00+00:00"),
            **note,
        )
    for scope in ({}, {"contact_id": contact}):
        accounted.clear()
        with account_scope() as account:
            output = await note_search(stores, "lighthouse", **scope)
        assert {item["content"] for item in output} == {"owner lighthouse", "exposed lighthouse"}
        assert all(set(item) == _NOTE_KEYS | {"contact_name"} for item in output)
        pointer = next(item for item in output if item["content"] == "exposed lighthouse")
        assert pointer["taint_goal_id"] == str(exposed.goal_id)
        assert pointer["taint_invocation_id"] == str(exposed.invocation_id)
        # The accounted row holds the joined name, so the value walk cannot flag it. It is not
        # a column of the stamped table, which is why the account must go unclean below.
        assert _uncovered(output, accounted, exempt=_ECHOED_NOTE) == set()
        assert "contact_name" in accounted[0].keys()
        assert "contact_name" not in await _memory_columns(stores, "facts")
        assert await _unfaithful(stores, "facts", accounted) == set()
        assert account.opened is True
        assert account.owner_untainted is False
        assert account.writers == {(exposed.goal_id, exposed.invocation_id)}
    with account_scope() as miss:
        assert await note_search(stores, "no such note") == []
    assert (miss.opened, miss.owner_untainted) == (True, True)


@_db
async def test_relationship_lookup_is_the_oracles_negative_control(stores, accounted):
    """The oracle catches a joined field, and the tool's account is honest about it."""
    entity = await stores.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, roles) "
        "VALUES ('Unstamped Name', 'person', '{}') RETURNING id"
    )
    await stores.execute(
        "INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src, "
        "conf, observed_at, validity, content_authority, verified) VALUES "
        "($1, 'has-email', 'a@example.test', 'literal', 'relationship', 1.0, now(), 'active', "
        "'owner', true)",
        entity,
    )
    with account_scope() as account:
        output = await relationship_lookup(stores, entity_id=entity)
    assert "canonical_name" in _uncovered(output, accounted)
    assert (account.opened, account.owner_untainted) == (True, False)

    with account_scope() as resolved:
        await relationship_lookup(stores, entity_ref="Unstamped Name")
    assert (resolved.opened, resolved.owner_untainted) == (True, False)


@_db
async def test_exposed_text_never_joins_a_stored_owner_row_unstamped(stores):
    """The two mutators that store caller text: an exposed call stamps instead."""
    engine, first, second = _engine(), _exposed(), _exposed()
    rule = await storage.store_rule(
        stores, "keep the owner rule", engine, content_authority="owner"
    )
    fact = await storage.store_fact(
        stores, "Mei", "likes", "owner fact", engine, content_authority="owner"
    )
    with lineage_scope(first):
        await storage.mark_harmful(stores, rule, reason="HOSTILE reason")
    with lineage_scope(second):
        await storage.mark_harmful(stores, rule, reason="HOSTILE again")
        await storage.forget_memory(
            stores, "fact", fact["id"], correction_id="HOSTILE id", correction_reason="HOSTILE text"
        )
    stored = await stores.fetchrow(
        "SELECT metadata::text AS metadata, content, taint_goal_id, taint_invocation_id "
        "FROM rules WHERE id = $1",
        rule,
    )
    assert "HOSTILE" not in stored["metadata"] and "HOSTILE" not in stored["content"]
    # The first writer keeps the pointer: both columns, never a mix.
    assert (stored["taint_goal_id"], stored["taint_invocation_id"]) == (
        first.goal_id,
        first.invocation_id,
    )
    forgotten = await stores.fetchrow(
        "SELECT metadata::text AS metadata, taint_goal_id, taint_invocation_id "
        "FROM facts WHERE id = $1",
        fact["id"],
    )
    assert "HOSTILE" not in forgotten["metadata"]
    assert (forgotten["taint_goal_id"], forgotten["taint_invocation_id"]) == (
        second.goal_id,
        second.invocation_id,
    )
    # The dark path is unchanged: no lineage stores the reason and no pointer.
    await storage.mark_harmful(stores, await _owner_rule(stores, engine), reason="plain reason")
    assert (
        await stores.fetchval(
            "SELECT count(*) FROM rules WHERE metadata::text LIKE '%plain reason%' "
            "AND taint_goal_id IS NULL"
        )
        == 1
    )


async def _owner_rule(pool, engine) -> uuid.UUID:
    return await storage.store_rule(pool, "another owner rule", engine, content_authority="owner")


def _row_account(row) -> StampAccount:
    with account_scope() as account:
        account_records([row])
    return account


async def _stored(pool, fact_id) -> Any:
    return await pool.fetchrow("SELECT * FROM facts WHERE id = $1", fact_id)


_UPDATE_TEXT = "HOSTILE update text"


def _update_meal(pool, fact_id):
    return meal_update(pool, str(fact_id), description=_UPDATE_TEXT)


def _update_measurement(pool, fact_id):
    return measurement_update(pool, str(fact_id), notes=_UPDATE_TEXT)


def _update_symptom(pool, fact_id):
    return symptom_update(pool, str(fact_id), name=_UPDATE_TEXT)


def _update_transactions(pool, _fact_id):
    operation = {"match": {"merchant_pattern": "Acme"}, "set": {"inferred_category": _UPDATE_TEXT}}
    return bulk_update_transactions(pool, [operation])


@pytest.mark.parametrize(
    ("update", "predicate", "scope", "metadata"),
    [
        pytest.param(_update_meal, "meal_lunch", "health", {}, id="meal_update"),
        pytest.param(
            _update_measurement,
            "measurement_weight",
            "health",
            {"value": 70},
            id="measurement_update",
        ),
        pytest.param(_update_symptom, "symptom", "health", {}, id="symptom_update"),
        pytest.param(
            _update_transactions,
            "transaction_debit",
            "finance",
            {"merchant": "Acme"},
            id="bulk_update_transactions",
        ),
    ],
)
@_db
async def test_an_exposed_text_update_of_an_owner_row_takes_the_pointer(
    stores, update, predicate, scope, metadata
):
    """The row's text is now the writer's, and an unexposed edit leaves the pointer alone."""
    first, second = _exposed(), _exposed()
    fact = await storage.store_fact(
        stores,
        f"contract:{predicate}",
        predicate,
        "owner text",
        _engine(),
        scope=scope,
        valid_at=datetime.now(UTC),
        metadata=metadata,
        content_authority="owner",
    )
    clean = _row_account(await _stored(stores, fact["id"]))
    assert (clean.owner_untainted, clean.writers) == (True, set())

    with lineage_scope(first):
        await update(stores, fact["id"])
    row = await _stored(stores, fact["id"])
    assert _UPDATE_TEXT in f"{row['content']} {row['metadata']}"
    assert _row_account(row).writers == {(first.goal_id, first.invocation_id)}

    with lineage_scope(second):
        await update(stores, fact["id"])
    row = await _stored(stores, fact["id"])
    assert _row_account(row).writers == {(second.goal_id, second.invocation_id)}

    await update(stores, fact["id"])
    row = await _stored(stores, fact["id"])
    assert _row_account(row).writers == {(second.goal_id, second.invocation_id)}


_ENTITY = uuid.uuid4()
_SENDER = uuid.uuid4()


def _advance_gift(pool, fact_id):
    return gift_update_status(pool, fact_id, "purchased")


def _update_condition(pool, fact_id):
    return condition_update(pool, str(fact_id), notes="edited")


def _update_medication(pool, fact_id):
    return medication_update(pool, str(fact_id), dosage="2mg")


def _log_dose(pool, fact_id):
    return medication_log_dose(pool, str(fact_id))


def _update_research(pool, fact_id):
    return research_update(pool, str(fact_id), tags=["edited"])


_COPIES = [
    pytest.param(
        _advance_gift,
        gifts,
        (f"entity:{_ENTITY}:gift:a", "gift", "relationship", {"status": "idea"}),
        id="gift_update_status",
    ),
    pytest.param(loan_settle, loans, ("loan:a", "loan", "relationship", {}), id="loan_settle"),
    pytest.param(
        task_complete,
        tasks,
        (f"contact:{_ENTITY}:task:a", "contact_task", "relationship", {}),
        id="task_complete",
    ),
    pytest.param(
        _update_condition,
        conditions,
        ("condition:a", "condition", "health", {"name": "a", "status": "active"}),
        id="condition_update",
    ),
    pytest.param(
        _update_medication,
        medications,
        ("medication:a", "medication", "health", {"name": "a"}),
        id="medication_update",
    ),
    pytest.param(
        _log_dose,
        medications,
        ("medication:a", "medication", "health", {"name": "a"}),
        id="medication_log_dose",
    ),
    pytest.param(
        _update_research,
        research,
        ("research:a", "research", "health", {"title": "a"}),
        id="research_update",
    ),
]


async def _seed_entities(pool) -> None:
    for entity, roles in ((_ENTITY, ["owner"]), (_SENDER, [])):
        await pool.execute(
            "INSERT INTO public.entities (id, canonical_name, entity_type, roles) "
            "VALUES ($1, $2, 'person', $3::text[]) ON CONFLICT DO NOTHING",
            entity,
            str(entity),
            roles,
        )


async def _seed_copy_source(pool, seed, **authority) -> dict:
    subject, predicate, scope, metadata = seed
    await _seed_entities(pool)
    return await storage.store_fact(
        pool,
        subject,
        predicate,
        "source text",
        _engine(),
        scope=scope,
        metadata=metadata,
        **authority,
    )


async def _seed_authority(pool, seed, authority: str | None) -> dict:
    """A source of this authority, naming a sender where a third party is in it."""
    if authority is None:  # Legacy: no writer stamps NULL, so it starts as a system row.
        fact = await _seed_copy_source(pool, seed, content_authority="system")
        await pool.execute("UPDATE facts SET content_authority = NULL WHERE id = $1", fact["id"])
        return fact
    sender = _SENDER if authority in ("third_party", "mixed") else None
    return await _seed_copy_source(
        pool, seed, content_authority=authority, authority_entity_id=sender
    )


@contextmanager
def _writing_as(writer: str) -> Iterator[None]:
    if writer == "owner":
        with lineage_scope(_owner_lineage()):
            yield
    elif writer == "routed_owner":  # An ordinary owner session: no broker, the live path.
        session_id = str(uuid.uuid4())
        set_runtime_session_routing_context(session_id, {"source_entity_id": str(_ENTITY)})
        token = set_current_runtime_session_id(session_id)
        try:
            yield
        finally:
            reset_current_runtime_session_id(token)
            clear_runtime_session_routing_context(session_id)
    else:  # No routing context: a scheduled session or a dashboard write.
        yield


def _owner_lineage() -> VerifiedLineage:
    return replace(_exposed(), exposed=False)


@pytest.mark.parametrize(("copy", "module", "seed"), _COPIES)
@_db
async def test_an_unexposed_successor_keeps_its_sources_writer(
    stores, monkeypatch, copy, module: ModuleType, seed
):
    monkeypatch.setattr(module, "_get_embedding_engine", _engine)
    writer = _exposed()
    with lineage_scope(writer):
        source = await _seed_copy_source(stores, seed)
    with lineage_scope(_owner_lineage()):
        result = await copy(stores, source["id"])
    successor = await _stored(stores, result["id"])
    assert result["id"] != source["id"]
    assert _row_account(successor).writers == {(writer.goal_id, writer.invocation_id)}


#: (writer, source authority, stored authority, sender kept). Docs: REQ-module-memory-013.
_SUCCESSOR_RULE = [
    ("owner", "owner", "owner", False),
    ("owner", "owner_device", "owner", False),
    ("owner", "third_party", "mixed", True),
    ("owner", "mixed", "mixed", True),
    ("owner", "system", "system", False),
    ("owner", None, "mixed", False),
    ("system", "third_party", "mixed", True),
    ("system", "mixed", "mixed", True),
    ("system", "system", "system", False),
    ("system", None, "mixed", False),
    ("system", "owner", "system", False),
    ("routed_owner", "owner", "owner", False),
    ("routed_owner", "owner_device", "owner", False),
    ("routed_owner", "third_party", "mixed", True),
    ("routed_owner", "mixed", "mixed", True),
    ("routed_owner", "system", "system", False),
    ("routed_owner", None, "mixed", False),
]


@pytest.mark.parametrize(("writer", "source_authority", "stored", "sender"), _SUCCESSOR_RULE)
@pytest.mark.parametrize(("copy", "module", "seed"), _COPIES)
@_db
async def test_a_successor_follows_the_authority_rule_on_every_copying_tool(
    stores, monkeypatch, copy, module: ModuleType, seed, writer, source_authority, stored, sender
):
    monkeypatch.setattr(module, "_get_embedding_engine", _engine)
    source = await _seed_authority(stores, seed, source_authority)
    with _writing_as(writer):
        result = await copy(stores, source["id"])
    successor = await _stored(stores, result["id"])
    assert successor["content_authority"] == stored
    if stored in ("mixed", "system"):
        assert successor["authority_entity_id"] == (_SENDER if sender else None)
    clean = stored in ("owner", "owner_device")
    assert _row_account(successor).owner_untainted is clean
    held = await stores.fetchval(f"SELECT {HELD_FACT_SQL} FROM facts WHERE id = $1", result["id"])
    assert bool(held) is (stored == "mixed")


@pytest.mark.parametrize(
    ("authority", "stored", "held"), [(None, "mixed", True), ("system", "system", False)]
)
@_db
async def test_a_legacy_or_system_successor_reads_unclean_through_each_stamped_read(
    stores, monkeypatch, authority, stored, held
):
    """Neither is owner data, and only a third party's possible text is held."""
    monkeypatch.setattr(tasks, "_get_embedding_engine", _engine)
    seed = (f"contact:{_ENTITY}:task:a", "contact_task", "relationship", {})
    source = await _seed_authority(stores, seed, authority)
    with _writing_as("routed_owner"):
        result = await task_complete(stores, source["id"])
    assert (await _stored(stores, result["id"]))["content_authority"] == stored

    with account_scope() as got:
        await memory_get(stores, "fact", str(result["id"]), read_policy=_POLICY)
    assert (got.opened, got.owner_untainted) == (True, False)
    for read in (memory_search, memory_recall):
        with account_scope() as found:
            output = await read(stores, _engine(), "source text", read_policy=_POLICY)
        assert any(str(result["id"]) == str(item["id"]) for item in output), read
        assert (found.opened, found.owner_untainted) == (True, False), read

    await stores.execute(
        "UPDATE facts SET subject = $2, predicate = 'contact_note' WHERE id = $1",
        result["id"],
        f"contact:{_ENTITY}",
    )
    with account_scope() as noted:
        assert await note_list(stores, _ENTITY)
    assert (noted.opened, noted.owner_untainted) == (True, False)
    assert (
        bool(
            await stores.fetchval(f"SELECT {HELD_FACT_SQL} FROM facts WHERE id = $1", result["id"])
        )
        is held
    )


_REFUSES_STALE = [p for p in _COPIES if p.id not in ("research_update", "loan_settle")]
_SUBJECT_KEYED = [
    p for p in _COPIES if p.id in ("condition_update", "medication_update", "task_complete")
]


async def _store_current(pool, seed) -> dict:
    subject, predicate, scope, metadata = seed
    return await storage.store_fact(
        pool,
        subject,
        predicate,
        "owner current text",
        _engine(),
        scope=scope,
        metadata=metadata,
        content_authority="owner",
    )


async def _live_rows(pool, seed) -> int:
    return await pool.fetchval(
        "SELECT count(*) FROM facts WHERE subject = $1 AND predicate = $2 AND validity = 'active'",
        seed[0],
        seed[1],
    )


@pytest.mark.parametrize("source_authority", ["owner", "system", "third_party", None])
@pytest.mark.parametrize(("copy", "module", "seed"), _REFUSES_STALE)
@_db
async def test_a_superseded_id_is_refused_and_the_current_fact_stays(
    stores, monkeypatch, copy, module, seed, source_authority
):
    monkeypatch.setattr(module, "_get_embedding_engine", _engine)
    stale = await _seed_authority(stores, seed, source_authority)
    current = await _store_current(stores, seed)
    assert (await _stored(stores, stale["id"]))["validity"] == "superseded"
    for writer in ("routed_owner", "system"):
        with _writing_as(writer), pytest.raises(ValueError, match="not found"):
            await copy(stores, stale["id"])
    assert (await _stored(stores, current["id"]))["validity"] == "active"
    assert await _live_rows(stores, seed) == 1


@pytest.mark.parametrize(("copy", "module", "seed"), _REFUSES_STALE)
@_db
async def test_a_retracted_id_is_refused(stores, monkeypatch, copy, module, seed):
    monkeypatch.setattr(module, "_get_embedding_engine", _engine)
    source = await _seed_authority(stores, seed, "system")
    await stores.execute("UPDATE facts SET validity = 'retracted' WHERE id = $1", source["id"])
    with pytest.raises(ValueError, match="not found"):
        await copy(stores, source["id"])
    assert await _live_rows(stores, seed) == 0


@pytest.mark.parametrize(("copy", "module", "seed"), _SUBJECT_KEYED)
@_db
async def test_a_fact_superseded_after_the_read_is_refused(stores, monkeypatch, copy, module, seed):
    """The read saw a current row; a newer fact landed before the store."""
    monkeypatch.setattr(module, "_get_embedding_engine", _engine)
    source = await _seed_authority(stores, seed, "system")
    real, landed = storage.store_fact, {}

    async def racing(pool, *args, **kwargs):
        monkeypatch.setattr(storage, "store_fact", real)
        landed.update(await _store_current(pool, seed))
        return await real(pool, *args, **kwargs)

    monkeypatch.setattr(storage, "store_fact", racing)
    with pytest.raises(storage.StaleSupersessionTargetError):
        await copy(stores, source["id"])
    assert (await _stored(stores, landed["id"]))["validity"] == "active"
    assert await _live_rows(stores, seed) == 1


@pytest.mark.parametrize(("copy", "module", "seed"), _SUBJECT_KEYED)
@_db
async def test_a_live_legacy_row_with_an_entity_id_is_still_editable(
    stores, monkeypatch, copy, module, seed
):
    """A subject-only store cannot find this row, so naming it would refuse a live edit."""
    monkeypatch.setattr(module, "_get_embedding_engine", _engine)
    source = await _seed_copy_source(stores, seed, content_authority="system", entity_id=_ENTITY)
    result = await copy(stores, source["id"])
    assert (await _stored(stores, result["id"]))["validity"] == "active"


@pytest.mark.parametrize(("copy", "module", "seed"), _REFUSES_STALE)
@_db
async def test_a_fading_id_is_still_editable(stores, monkeypatch, copy, module, seed):
    """A fading fact is still the live value, so it is not stale."""
    monkeypatch.setattr(module, "_get_embedding_engine", _engine)
    source = await _seed_authority(stores, seed, "system")
    await stores.execute("UPDATE facts SET validity = 'fading' WHERE id = $1", source["id"])
    result = await copy(stores, source["id"])
    assert (await _stored(stores, result["id"]))["validity"] == "active"


@_db
async def test_a_vouch_does_not_carry_to_the_successor(stores, monkeypatch):
    monkeypatch.setattr(tasks, "_get_embedding_engine", _engine)
    seed = (f"contact:{_ENTITY}:task:vouched", "contact_task", "relationship", {})
    source = await _seed_authority(stores, seed, "third_party")
    await stores.execute("UPDATE facts SET endorsed_at = now() WHERE id = $1", source["id"])
    with _writing_as("routed_owner"):
        result = await task_complete(stores, source["id"])
    assert (
        await stores.fetchval("SELECT endorsed_at FROM facts WHERE id = $1", result["id"]) is None
    )
    assert await stores.fetchval(f"SELECT {HELD_FACT_SQL} FROM facts WHERE id = $1", result["id"])
