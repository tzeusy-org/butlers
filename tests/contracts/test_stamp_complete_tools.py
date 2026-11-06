"""RFC 0038 D6: a tool may declare ``stamped`` output only when its account is complete.

The stamp report says which records a tool's readers accounted, not that the tool
returned nothing else (bu-dhloi1.33). Each audited tool is therefore run against a
real database and its whole output walked: every returned value must come from a
record that reached ``account_records``, or from a field named here with its reason.
``relationship_lookup`` is the oracle's negative control: its entity header comes
from a store with no stamp, so it stays ``untrusted`` and always accounts unclean.
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Iterator, Mapping
from datetime import datetime
from types import SimpleNamespace
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
from butlers.core.tool_catalog import STAMP_COMPLETE_READS
from butlers.core.tool_presentation_inventory import TOOL_PRESENTATION_INVENTORY
from butlers.core_tools._conversation_recall import register_conversation_recall_tool
from butlers.modules.memory import storage
from butlers.modules.memory.search import resolve_catalog_read_policy
from butlers.modules.memory.tools.reading import memory_get, memory_recall, memory_search
from butlers.tools.relationship.notes import note_list
from butlers.tools.relationship.relationship_lookup import relationship_lookup


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


def _engine() -> MagicMock:
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
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
    """Output fields whose value no accounted record holds."""
    known = [form for record in records for name in record.keys() for form in _forms(record[name])]
    fields = set()
    for name, leaf in _leaves(output):
        text = next(_forms(leaf))
        if name not in exempt and not any(text in form for form in known):
            fields.add(name)
    return fields


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
async def pool(db):
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


async def _memory_columns(pool) -> set[str]:
    rows = await pool.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'relationship' "
        "AND table_name IN ('episodes', 'facts', 'rules')"
    )
    return {row["column_name"] for row in rows}


@pytest.mark.parametrize("read", [memory_search, memory_recall])
@_db
async def test_memory_reads_return_only_accounted_records(pool, accounted, read):
    _, exposed = await _seed_memory(pool, "lighthouse")
    with account_scope() as account:
        output = await read(pool, _engine(), "lighthouse", read_policy=_POLICY)
    assert any("exposed" in item["content"] for item in output)
    assert _uncovered(output, accounted) == set()
    columns = await _memory_columns(pool) | _MEMORY_SCORES
    assert {key for item in output for key in item} <= columns
    assert account.writers == {(exposed.goal_id, exposed.invocation_id)}
    assert account.owner_untainted is True


@_db
async def test_memory_get_returns_only_its_accounted_record(pool, accounted):
    (owner_fact, _, _, tainted), exposed = await _seed_memory(pool, "harbour")
    with account_scope() as clean:
        output = await memory_get(pool, "fact", str(owner_fact), read_policy=_POLICY)
    assert _uncovered(output, accounted) == set()
    assert (clean.owner_untainted, clean.writers) == (True, set())
    assert set(output) <= await _memory_columns(pool)
    with account_scope() as stamped:
        await memory_get(pool, "fact", str(tainted), read_policy=_POLICY)
    assert stamped.writers == {(exposed.goal_id, exposed.invocation_id)}


@_db
async def test_note_list_returns_only_its_accounted_records(pool, accounted):
    contact, engine, exposed = uuid.uuid4(), _engine(), _exposed()
    subject = f"contact:{contact}"
    await storage.store_fact(
        pool,
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
            pool,
            subject,
            "contact_note",
            "exposed note",
            engine,
            permanence="stable",
            scope="relationship",
            valid_at=datetime.fromisoformat("2026-01-02T00:00:00+00:00"),
        )
    with account_scope() as account:
        output = await note_list(pool, contact)
    assert {item["content"] for item in output} == {"owner note", "exposed note"}
    assert _uncovered(output, accounted, exempt=_ECHOED_NOTE) == set()
    assert all(set(item) == _NOTE_KEYS for item in output)
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


@_db
async def test_conversation_recall_returns_only_its_accounted_records(pool, accounted):
    _, writer = await _seed_chat(pool)
    tools = _conversation_tools(pool)
    with account_scope() as account:
        output = await tools["conversation_recall"]("beacon", butler="finance")
    assert len(output) == 2 and all("beacon" in item["snippet"] for item in output)
    assert not any("SECRET" in str(item) for item in output)
    assert _uncovered(output, accounted, exempt=_DERIVED_RECALL) == set()
    assert (account.owner_untainted, account.writers) == (True, {writer})
    assert all(set(item) == _RECALL_KEYS for item in output)


@_db
async def test_conversation_thread_read_returns_only_its_accounted_records(pool, accounted):
    conversation, writer = await _seed_chat(pool)
    tools = _conversation_tools(pool)
    with account_scope() as account:
        output = await tools["conversation_thread_read"](str(conversation))
    assert len(output["messages"]) == 2
    assert _uncovered(output, accounted, exempt=_ECHOED_THREAD) == set()
    assert set(output) == {"conversation_id", "messages"}
    assert all(set(item) == _THREAD_MESSAGE_KEYS for item in output["messages"])
    assert (account.owner_untainted, account.writers) == (True, {writer})


@_db
async def test_relationship_lookup_is_the_oracles_negative_control(pool, accounted):
    """The oracle catches a joined field, and the tool's account is honest about it."""
    entity = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, roles) "
        "VALUES ('Unstamped Name', 'person', '{}') RETURNING id"
    )
    await pool.execute(
        "INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src, "
        "conf, observed_at, validity, content_authority, verified) VALUES "
        "($1, 'has-email', 'a@example.test', 'literal', 'relationship', 1.0, now(), 'active', "
        "'owner', true)",
        entity,
    )
    with account_scope() as account:
        output = await relationship_lookup(pool, entity_id=entity)
    assert "canonical_name" in _uncovered(output, accounted)
    assert (account.opened, account.owner_untainted) == (True, False)

    with account_scope() as resolved:
        await relationship_lookup(pool, entity_ref="Unstamped Name")
    assert (resolved.opened, resolved.owner_untainted) == (True, False)


@_db
async def test_exposed_text_never_joins_a_stored_owner_row_unstamped(pool):
    """The two mutators that store caller text: an exposed call stamps instead."""
    engine, first, second = _engine(), _exposed(), _exposed()
    rule = await storage.store_rule(pool, "keep the owner rule", engine, content_authority="owner")
    fact = await storage.store_fact(
        pool, "Mei", "likes", "owner fact", engine, content_authority="owner"
    )
    with lineage_scope(first):
        await storage.mark_harmful(pool, rule, reason="HOSTILE reason")
    with lineage_scope(second):
        await storage.mark_harmful(pool, rule, reason="HOSTILE again")
        await storage.forget_memory(
            pool, "fact", fact["id"], correction_id="HOSTILE id", correction_reason="HOSTILE text"
        )
    stored = await pool.fetchrow(
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
    forgotten = await pool.fetchrow(
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
    await storage.mark_harmful(pool, await _owner_rule(pool, engine), reason="plain reason")
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM rules WHERE metadata::text LIKE '%plain reason%' "
            "AND taint_goal_id IS NULL"
        )
        == 1
    )


async def _owner_rule(pool, engine) -> uuid.UUID:
    return await storage.store_rule(pool, "another owner rule", engine, content_authority="owner")
