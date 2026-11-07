"""Which row survives a merge's same-key collapse, and where its evidence goes (bu-dhloi1.68)."""

from __future__ import annotations

import shutil
import uuid

import asyncpg
import pytest

from butlers.roster_imports import roster_module

merge_entity_pair = roster_module("relationship", "tools/entity_merge").merge_entity_pair
read_fact_evidence = roster_module("relationship", "tools/fact_evidence").read_fact_evidence

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

# Each way a row comes to be ``verified``: ck_ef_verified_authority ties that column to
# owner-class authority or a confirmation, and legacy rows have no authority at all.
_TRUSTED = {
    "legacy_verified": {"verified": "true"},
    "owner": {"verified": "true", "content_authority": "'owner'"},
    "owner_device": {"verified": "true", "content_authority": "'owner_device'"},
    "third_party_confirmed": {
        "verified": "true",
        "content_authority": "'third_party'",
        "confirmed_at": "now()",
        "confirmed_by_original_entity_id": "gen_random_uuid()",
        "confirmation_source": "'owner_assertion'",
    },
}


@pytest.fixture
async def rel_pool(db):
    return await db("core", "memory", "relationship", "approvals")


async def _entity(pool: asyncpg.Pool, tag: str) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) "
        "VALUES ($1, 'person') RETURNING id",
        f"{tag} {uuid.uuid4()}",
    )


async def _fact(
    pool: asyncpg.Pool,
    subject: uuid.UUID,
    predicate: str,
    obj: str,
    *,
    kind: str = "literal",
    conf: float = 0.5,
    trusted: str | None = None,
    evidence: str | None = None,
) -> uuid.UUID:
    extra = _TRUSTED[trusted] if trusted is not None else {}
    columns = "".join(f", {column}" for column in extra)
    values = "".join(f", {value}" for value in extra.values())
    fact = await pool.fetchval(
        f"INSERT INTO relationship.entity_facts "
        f"(subject, predicate, object, object_kind, src, conf{columns}) "
        f"VALUES ($1, $2, $3, $4, 'test', $5{values}) RETURNING id",
        subject,
        predicate,
        obj,
        kind,
        conf,
    )
    if evidence is not None:
        await pool.execute(
            "INSERT INTO relationship.fact_evidence (fact_id, seq, kind, ref, note, src, origin) "
            "VALUES ($1, 1, 'text', $2, '', 'test', 'direct')",
            fact,
            evidence,
        )
    return fact


async def _validity(pool: asyncpg.Pool, fact: uuid.UUID) -> str:
    return await pool.fetchval("SELECT validity FROM relationship.entity_facts WHERE id = $1", fact)


async def _evidence(pool: asyncpg.Pool, fact: uuid.UUID) -> set[str]:
    rows = await pool.fetch("SELECT ref FROM relationship.fact_evidence WHERE fact_id = $1", fact)
    return {row["ref"] for row in rows}


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("trusted", list(_TRUSTED))
async def test_an_owner_confirmed_source_row_beats_an_unconfirmed_target_row(
    rel_pool, trusted
) -> None:
    """Keeping the target would show the owner their own fact as unconfirmed."""
    source = await _entity(rel_pool, "Source")
    target = await _entity(rel_pool, "Target")
    src_fact = await _fact(
        rel_pool, source, "has-email", "a@example.test", trusted=trusted, evidence="from source"
    )
    tgt_fact = await _fact(
        rel_pool, target, "has-email", "a@example.test", conf=1.0, evidence="from target"
    )

    await merge_entity_pair(rel_pool, source_entity_id=source, target_entity_id=target)

    row = await rel_pool.fetchrow(
        "SELECT subject, validity FROM relationship.entity_facts WHERE id = $1", src_fact
    )
    assert (row["subject"], row["validity"]) == (target, "active")
    assert await _validity(rel_pool, tgt_fact) == "superseded"
    assert await _evidence(rel_pool, src_fact) == {"from source", "from target"}
    # The superseded row keeps the exact ledger it was written with.
    assert await _evidence(rel_pool, tgt_fact) == {"from target"}


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    ("source_trusted", "target_trusted"),
    [(None, None), ("owner", "owner"), (None, "owner")],
    ids=["neither", "both", "target_only"],
)
async def test_otherwise_the_target_row_survives_and_receives_the_evidence(
    rel_pool, source_trusted, target_trusted
) -> None:
    source = await _entity(rel_pool, "Source")
    target = await _entity(rel_pool, "Target")
    src_fact = await _fact(
        rel_pool,
        source,
        "has-email",
        "a@example.test",
        conf=1.0,
        trusted=source_trusted,
        evidence="from source",
    )
    tgt_fact = await _fact(
        rel_pool,
        target,
        "has-email",
        "a@example.test",
        trusted=target_trusted,
        evidence="from target",
    )

    await merge_entity_pair(rel_pool, source_entity_id=source, target_entity_id=target)

    assert await _validity(rel_pool, tgt_fact) == "active"
    assert await _validity(rel_pool, src_fact) == "superseded"
    assert await _evidence(rel_pool, tgt_fact) == {"from source", "from target"}
    assert await _evidence(rel_pool, src_fact) == {"from source"}


@pytest.mark.asyncio(loop_scope="session")
async def test_carried_evidence_sharing_a_seq_reads_oldest_first(rel_pool) -> None:
    """A collapse can leave two rows on one ``seq``; their read order must still be fixed."""
    source = await _entity(rel_pool, "Source")
    target = await _entity(rel_pool, "Target")
    await _fact(rel_pool, source, "has-email", "a@example.test", evidence="older")
    tgt_fact = await _fact(rel_pool, target, "has-email", "a@example.test", evidence="newer")

    await merge_entity_pair(rel_pool, source_entity_id=source, target_entity_id=target)

    packet = await read_fact_evidence(rel_pool, tgt_fact)
    assert [(item["seq"], item["ref"]) for item in packet["evidence"]] == [
        (1, "older"),
        (1, "newer"),
    ]


@pytest.mark.asyncio(loop_scope="session")
async def test_the_object_side_prefers_the_confirmed_row_and_moves_only_its_edge(rel_pool) -> None:
    source = await _entity(rel_pool, "Source")
    target = await _entity(rel_pool, "Target")
    other = await _entity(rel_pool, "Other")
    src_fact = await _fact(
        rel_pool,
        other,
        "knows",
        str(source),
        kind="entity",
        trusted="legacy_verified",
        evidence="from source",
    )
    tgt_fact = await _fact(
        rel_pool, other, "knows", str(target), kind="entity", evidence="from target"
    )
    for fact, obj in ((src_fact, source), (tgt_fact, target)):
        await rel_pool.execute(
            """
            INSERT INTO public.entity_graph_edges
                (source_schema, source_table, source_id, subject_entity_id, predicate,
                 object_entity_id)
            VALUES ('relationship', 'entity_facts', $1, $2, 'knows', $3)
            """,
            fact,
            other,
            obj,
        )

    await merge_entity_pair(rel_pool, source_entity_id=source, target_entity_id=target)

    row = await rel_pool.fetchrow(
        "SELECT object, validity FROM relationship.entity_facts WHERE id = $1", src_fact
    )
    assert (row["object"], row["validity"]) == (str(target), "active")
    assert await _validity(rel_pool, tgt_fact) == "superseded"
    assert await _evidence(rel_pool, src_fact) == {"from source", "from target"}
    edges = await rel_pool.fetch(
        "SELECT source_id, object_entity_id FROM public.entity_graph_edges "
        "WHERE subject_entity_id = $1",
        other,
    )
    assert [(e["source_id"], e["object_entity_id"]) for e in edges] == [(src_fact, target)]


@pytest.mark.asyncio(loop_scope="session")
async def test_a_confirmed_single_value_beats_a_higher_confidence_unconfirmed_one(rel_pool) -> None:
    """Different objects: a different claim, so no evidence crosses between them."""
    source = await _entity(rel_pool, "Source")
    target = await _entity(rel_pool, "Target")
    confirmed = await _fact(
        rel_pool,
        source,
        "has-birthday",
        "1990-01-01",
        conf=0.1,
        trusted="legacy_verified",
        evidence="a",
    )
    guessed = await _fact(rel_pool, target, "has-birthday", "1991-02-02", conf=1.0, evidence="b")

    await merge_entity_pair(rel_pool, source_entity_id=source, target_entity_id=target)

    assert await _validity(rel_pool, confirmed) == "active"
    assert await _validity(rel_pool, guessed) == "superseded"
    assert await _evidence(rel_pool, confirmed) == {"a"}


@pytest.mark.asyncio(loop_scope="session")
async def test_the_subject_side_prefers_the_confirmed_row_and_moves_only_its_edge(rel_pool) -> None:
    source = await _entity(rel_pool, "Source")
    target = await _entity(rel_pool, "Target")
    other = await _entity(rel_pool, "Other")
    src_fact = await _fact(
        rel_pool,
        source,
        "knows",
        str(other),
        kind="entity",
        trusted="legacy_verified",
        evidence="from source",
    )
    tgt_fact = await _fact(
        rel_pool, target, "knows", str(other), kind="entity", evidence="from target"
    )
    for fact, subject in ((src_fact, source), (tgt_fact, target)):
        await rel_pool.execute(
            """
            INSERT INTO public.entity_graph_edges
                (source_schema, source_table, source_id, subject_entity_id, predicate,
                 object_entity_id)
            VALUES ('relationship', 'entity_facts', $1, $2, 'knows', $3)
            """,
            fact,
            subject,
            other,
        )

    await merge_entity_pair(rel_pool, source_entity_id=source, target_entity_id=target)

    row = await rel_pool.fetchrow(
        "SELECT subject, validity FROM relationship.entity_facts WHERE id = $1", src_fact
    )
    assert (row["subject"], row["validity"]) == (target, "active")
    assert await _validity(rel_pool, tgt_fact) == "superseded"
    assert await _evidence(rel_pool, src_fact) == {"from source", "from target"}
    edges = await rel_pool.fetch(
        "SELECT source_id, subject_entity_id FROM public.entity_graph_edges "
        "WHERE object_entity_id = $1",
        other,
    )
    assert [(e["source_id"], e["subject_entity_id"]) for e in edges] == [(src_fact, target)]
