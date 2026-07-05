"""Swap ivfflat embedding indexes for HNSW on episodes, facts, and rules.

Revision ID: mem_007
Revises: mem_006
Create Date: 2026-07-05 00:00:00.000000

Pursuit slice 4 deferred from bu-qvnce.3 / PR #2903
(docs/redesigns/2026-07-04-jarvis-pursuit.md ranked move #3). Follow-up: bu-4ftb2.

CREATE location (ivfflat originals): 001_memory_schema.py (mem_001), lines
~84 (idx_episodes_embedding), ~203 (idx_facts_embedding), ~330
(idx_rules_embedding). All three were built with ``lists = 20`` at table
creation time, when every memory-enabled butler schema had zero rows.

Why this was wrong from day one
--------------------------------
ivfflat's ``lists`` parameter is a k-means partition count that should be
tuned to the table's row count at *build* time (rule of thumb: ``rows / 1000``
for tables under ~1M rows, minimum a handful of lists). Building with
``lists = 20`` against an *empty* table means the partitions are meaningless
(k-means over zero training vectors), so the index degrades to an
effectively unpartitioned scan that provides no recall/speed guarantee as
rows accumulate — and nothing in this codebase ever ran ``REINDEX`` to
retrain it. Runtime read paths (``search.py``) also never set
``ivfflat.probes`` (checked: no occurrence of ``ivfflat`` or ``probes``
anywhere in ``src/butlers/modules/memory/``), so every query ran at the
default ``probes = 1`` — the single worst-recall setting ivfflat has.

Why HNSW instead
-----------------
HNSW (available since pgvector 0.5.0; the ``pgvector/pgvector:pg17`` image
used by this repo's Docker Compose and testcontainers ships a current
pgvector) has no equivalent "must retune as the table grows" trap: it is
built incrementally from a graph, needs no row-count-dependent parameter,
and its query-time recall knob (``hnsw.ef_search``, default 40) only ever
trades latency for recall — it never degrades to "no partitioning happened."
That makes it strictly the safer default for a table whose row count is
unknown and growing across 9 butler schemas that currently hold on the
order of thousands of rows each.

Parameter choice: ``m = 16, ef_construction = 64`` (pgvector's own
defaults). At the current few-thousand-rows-per-schema scale there is no
recall/build-time pressure that would justify paying the extra memory/build
cost of raising ``m``; these defaults already give >95% recall on typical
384-dim embedding workloads at this scale per pgvector's own benchmarks. If
any butler schema grows past roughly 100k-1M rows, re-benchmark with the
nightly recall harness (tests/migrations/test_memory_hnsw_recall_nightly.py)
before deciding whether to raise ``m``/``ef_construction`` or tune
``hnsw.ef_search`` at query time.

No runtime GUC changes: since ``ivfflat.probes`` was never set, there is no
existing query-time knob to migrate. Runtime queries (``search.py``,
``tools/reading.py``) continue to run with HNSW's default ``ef_search = 40``
GUC, which needs no explicit ``SET`` to take effect.

Guards:
  - DROP INDEX IF EXISTS / CREATE INDEX IF NOT EXISTS are idempotent.
  - Applied per butler schema (this chain runs once per memory-enabled
    butler's schema, like the rest of the memory migration chain).

Downgrade recreates the original ivfflat (lists = 20) indexes from mem_001.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_007"
down_revision = "mem_006"
branch_labels = None
depends_on = None

# (table, index_name) pairs carrying an embedding column, in the same order
# they were originally created in mem_001.
_EMBEDDING_INDEXES = (
    ("episodes", "idx_episodes_embedding"),
    ("facts", "idx_facts_embedding"),
    ("rules", "idx_rules_embedding"),
)


def upgrade() -> None:
    for table, index_name in _EMBEDDING_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
        op.execute(f"""
            CREATE INDEX IF NOT EXISTS {index_name}
            ON {table} USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """)


def downgrade() -> None:
    for table, index_name in _EMBEDDING_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
        op.execute(f"""
            CREATE INDEX IF NOT EXISTS {index_name}
            ON {table} USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20)
        """)
