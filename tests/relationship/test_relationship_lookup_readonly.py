"""Integration tests for the ``relationship_lookup`` read-only contract.

Binding spec:
    openspec/changes/entity-v3-lifecycle-and-depth/specs/relationship-entity-lookup/spec.md
    §"Requirement: Lookup is read-only" — "Repeated identical calls MUST leave
    the database byte-identical."

The unit suite (``test_relationship_lookup.py``) proves read-only structurally:
a FakePool greps each statement for write verbs. That is necessary but weaker
than the spec promise — it only proves the *strings* the tool emits are SELECTs,
not that the *database* is unchanged (a side effect through a trigger, a SELECT
... FOR UPDATE lock escalation, or a stray sequence bump would slip past a text
grep). This file closes that gap against a real PostgreSQL: it snapshots the
relevant rows (row counts + a content checksum) before and after a lookup and
asserts they are identical.

It also pins the SQL-vs-Python staleness-band agreement at the exact day
boundary (bu-ks6wd item 6): the band CASE uses ``>=`` so a row observed exactly
``FRESH_MAX_DAYS`` / ``AGING_MAX_DAYS`` ago lands in the same band the Python
helper assigns.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from butlers.testing.migration import migrated_pool
from butlers.tools.relationship.relationship_lookup import relationship_lookup
from butlers.tools.relationship.staleness import (
    AGING_MAX_DAYS,
    FRESH_MAX_DAYS,
    staleness_band,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


# ---------------------------------------------------------------------------
# Schema provisioning — the actual three owning migration chains.
# ---------------------------------------------------------------------------


@pytest.fixture
async def pool(postgres_container):
    """Read-only controls use real identity, evidence, and narrative catalogs."""
    async with migrated_pool(
        postgres_container,
        chains=["core", "memory", "relationship"],
        schemas={"relationship": "relationship"},
    ) as p:
        yield p


@pytest.fixture
async def seeded_entity(pool: asyncpg.Pool) -> uuid.UUID:
    """An entity with one active identity fact and one narrative fact."""
    eid = await pool.fetchval(
        """
        INSERT INTO public.entities (canonical_name, entity_type, roles)
        VALUES ('Northwind Plumbing', 'organization', '{}')
        RETURNING id
        """
    )
    # The identity fact's effective interval closed years ago; it is still the
    # current assertion, so assertion-current readers must keep returning it
    # (relationship-fact-effective-time adds no implicit effective-now filter).
    await pool.execute(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src, conf, observed_at, validity,
             effective_from, effective_from_precision, effective_to, effective_to_precision)
        VALUES ($1, 'has-email', 'ops@northwind.test', 'literal', 'relationship', 1.0, now(),
                'active', '2019-01-01Z', 'year', '2021-01-01Z', 'year')
        """,
        eid,
    )
    await pool.execute(
        """
        INSERT INTO facts (subject, entity_id, predicate, content, source_butler, confidence, scope, observed_at)
        VALUES ('entity:' || $1::text, $1, 'prefers', 'morning calls', 'memory', 0.8, 'relationship', now())
        """,
        eid,
    )
    return eid


# ---------------------------------------------------------------------------
# Byte-identical-DB snapshot helper.
# ---------------------------------------------------------------------------


async def _db_snapshot(p: asyncpg.Pool) -> dict[str, tuple[int, str]]:
    """Row count + ordered content checksum for every table the lookup reads.

    A change to any value, an inserted/deleted row, or a side-effect bump to a
    timestamp would change the count or the checksum. ``md5(string_agg(...))``
    over the whole row, ordered by id, gives a deterministic content fingerprint.
    """
    snap: dict[str, tuple[int, str]] = {}
    for table in ("public.entities", "relationship.entity_facts", "facts"):
        count = await p.fetchval(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed table list
        checksum = await p.fetchval(
            f"""
            SELECT COALESCE(
                md5(string_agg(t.row_text, '|' ORDER BY t.row_text)),
                ''
            )
            FROM (SELECT (x.*)::text AS row_text FROM {table} x) t
            """  # noqa: S608 - table name is from the fixed list above, not user input
        )
        snap[table] = (count, checksum)
    return snap


# ---------------------------------------------------------------------------
# Read-only contract — real DB stays byte-identical across repeated lookups.
# ---------------------------------------------------------------------------


async def test_lookup_leaves_db_byte_identical(pool, seeded_entity):
    before = await _db_snapshot(pool)

    result = await relationship_lookup(pool, entity_id=seeded_entity)
    assert result["entity"]["id"] == str(seeded_entity)
    # Sanity: the lookup actually read the seeded facts (otherwise the snapshot
    # parity would be trivially true against an untouched empty read path).
    stores = {f["store"] for f in result["facts"]}
    assert stores == {"identity", "narrative"}

    after = await _db_snapshot(pool)
    assert after == before, (
        "relationship_lookup mutated the database; the spec requires repeated "
        f"identical calls to leave it byte-identical. before={before} after={after}"
    )

    # Repeat the call — still no drift.
    await relationship_lookup(pool, entity_id=seeded_entity)
    assert await _db_snapshot(pool) == before


async def test_lookup_by_ref_leaves_db_byte_identical(pool, seeded_entity):
    before = await _db_snapshot(pool)
    result = await relationship_lookup(pool, entity_ref="Northwind Plumbing")
    assert result["entity"]["id"] == str(seeded_entity)
    assert await _db_snapshot(pool) == before


# ---------------------------------------------------------------------------
# SQL vs Python staleness band agree at the exact day boundary (item 6).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "age_days",
    [0, FRESH_MAX_DAYS, FRESH_MAX_DAYS + 1, AGING_MAX_DAYS, AGING_MAX_DAYS + 1, 400],
)
@pytest.mark.pg_clock
async def test_sql_band_matches_python_at_boundaries(pool, age_days):
    """The lookup's SQL band must equal the Python helper at exact boundaries.

    Insert a fact whose ``observed_at`` is exactly ``age_days`` ago, read it back
    through ``relationship_lookup`` (SQL CASE), and compare to ``staleness_band``
    (Python). They must agree.

    Note: sub-second wall-clock progression between computing ``observed`` and
    evaluating ``now()`` in SQL/Python means the effective age at evaluation is
    ``age_days + ε``. So the exact-edge cases (``FRESH_MAX_DAYS`` = 30,
    ``AGING_MAX_DAYS`` = 180) actually land just past the boundary in the next
    band — in BOTH SQL and Python — so this test proves SQL/Python *agree* end to
    end but does not, by itself, exercise the inclusive ``>=`` edge (exactly 30d
    classified as ``fresh``). The inclusive-boundary semantics are pinned
    structurally in ``test_staleness.py::test_band_comparison_is_inclusive_to_match_python``;
    a strict ``>`` regression would be caught there.
    """
    eid = await pool.fetchval(
        """
        INSERT INTO public.entities (canonical_name, entity_type, roles)
        VALUES ('Boundary Co', 'organization', '{}')
        RETURNING id
        """
    )
    observed = datetime.now(UTC) - timedelta(days=age_days)
    await pool.execute(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src, conf, observed_at, validity)
        VALUES ($1, 'has-email', 'edge@boundary.test', 'literal', 'relationship', 1.0, $2, 'active')
        """,
        eid,
        observed,
    )

    result = await relationship_lookup(pool, entity_id=eid)
    identity = next(f for f in result["facts"] if f["store"] == "identity")
    sql_band = identity["staleness_band"]

    # live-clock: the SQL band is computed against Postgres now(); the Python
    # band must read the same real clock or the two sides are not comparable.
    # That mixing is what @pytest.mark.pg_clock declares.
    expected = staleness_band(
        store="identity",
        observed_at=observed,
        created_at=observed,
        now=datetime.now(UTC),
    )
    assert sql_band == expected.value, (
        f"age={age_days}d: SQL band {sql_band!r} != Python band {expected.value!r}"
    )


@pytest.mark.parametrize("age_days", [5, 90, 365])
@pytest.mark.pg_clock
async def test_recency_band_matches_python_via_shared_builder(pool, age_days):
    """The whole-entity recency band agrees with the Python helper.

    ``_fetch_recency`` derives the entity band from ``max(last_seen)`` through the
    SAME ``staleness_band_sql_for`` builder used for per-fact bands (item 5/6
    dedupe — no inline 30d/180d intervals in the recency path). Offsets are kept
    safely inside each band so the wall-clock skew between the test's ``now()``
    and the DB's ``now()`` cannot flip the expected band; the inclusive-boundary
    ``>=`` semantics are pinned structurally in ``test_staleness.py``.
    """
    eid = await pool.fetchval(
        """
        INSERT INTO public.entities (canonical_name, entity_type, roles)
        VALUES ('Recency Co', 'organization', '{}')
        RETURNING id
        """
    )
    last_seen = datetime.now(UTC) - timedelta(days=age_days)
    await pool.execute(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src, conf, last_seen, validity)
        VALUES ($1, 'has-email', 'r@recency.test', 'literal', 'relationship', 1.0, $2, 'active')
        """,
        eid,
        last_seen,
    )
    result = await relationship_lookup(pool, entity_id=eid)
    # live-clock: as above — the recency band comes from the same SQL builder
    # evaluated against Postgres now(), so the Python side reads the real clock.
    expected = staleness_band(
        store="identity",
        observed_at=None,
        last_seen=last_seen,
        created_at=last_seen,
        now=datetime.now(UTC),
    )
    assert result["recency"]["staleness_band"] == expected.value
