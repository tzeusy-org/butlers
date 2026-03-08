"""Idempotent data backfill: CRUD tables -> facts table.

Migrates existing rows from deprecated CRUD tables into the facts table.
One phase per domain:

  - health:       measurements, symptoms, medication_doses, medications,
                  conditions, research
  - relationship: quick_facts, interactions, life_events, notes, gifts,
                  loans, tasks, reminders, activity_feed
  - finance:      transactions, accounts, subscriptions, bills
  - home:         ha_entity_snapshot

Usage
-----
    python -m butlers.scripts.backfill_facts --phase health
    python -m butlers.scripts.backfill_facts --phase relationship
    python -m butlers.scripts.backfill_facts --phase finance
    python -m butlers.scripts.backfill_facts --phase home
    python -m butlers.scripts.backfill_facts --phase all
    python -m butlers.scripts.backfill_facts --phase all --dry-run

Environment
-----------
DATABASE_URL, POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD
control the DB connection (same env vars as the butler daemon).

The BUTLER_SCHEMA env var selects the schema (e.g. "health", "relationship",
"finance", "home"). For cross-phase runs each phase reads its own schema.

Idempotency
-----------
Each source row is fingerprinted with a stable key stored in
facts.metadata->>'backfill_source'. Before inserting, the script checks
whether an active fact with the same backfill_source key already exists.
Rows that already have a corresponding fact are skipped silently.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import logging
import os
import sys
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("backfill_facts")

# ---------------------------------------------------------------------------
# Embedding engine (lazy singleton)
# ---------------------------------------------------------------------------

_embedding_engine: Any = None


def _load_embedding_engine() -> Any:
    """Load the EmbeddingEngine from the memory module."""
    global _embedding_engine
    if _embedding_engine is not None:
        return _embedding_engine

    # Walk up from this file to locate the memory module.
    this_dir = Path(__file__).resolve().parent
    # src/butlers/scripts -> src/butlers -> src -> repo root
    repo_root = this_dir.parent.parent.parent
    embedding_path = repo_root / "src" / "butlers" / "modules" / "memory" / "embedding.py"

    if not embedding_path.exists():
        raise FileNotFoundError(f"EmbeddingEngine not found at {embedding_path}")

    spec = importlib.util.spec_from_file_location("embedding", embedding_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _embedding_engine = mod.EmbeddingEngine()
    return _embedding_engine


def _embed(text: str) -> str:
    """Return pgvector string representation of embedding for *text*."""
    engine = _load_embedding_engine()
    vec = engine.embed(text)
    return str(vec)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _create_pool(db_name: str, schema: str) -> asyncpg.Pool:
    """Create an asyncpg pool connected to *db_name* with *schema* search_path."""
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        pool = await asyncpg.create_pool(
            dsn=database_url,
            server_settings={"search_path": f"{schema},shared,public"},
        )
    else:
        pool = await asyncpg.create_pool(
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            user=os.environ.get("POSTGRES_USER", "butlers"),
            password=os.environ.get("POSTGRES_PASSWORD", "butlers"),
            database=db_name,
            server_settings={"search_path": f"{schema},shared,public"},
        )
    assert pool is not None
    return pool


async def _owner_entity_id(pool: asyncpg.Pool) -> uuid.UUID | None:
    """Resolve the owner entity from shared.contacts -> shared.entities."""
    row = await pool.fetchrow(
        """
        SELECT e.id
        FROM shared.contacts c
        JOIN shared.entities e ON c.entity_id = e.id
        WHERE c.roles @> '["owner"]'::jsonb
        LIMIT 1
        """
    )
    if row:
        return row["id"]
    # Fallback: look directly in shared.entities for owner role.
    row = await pool.fetchrow(
        "SELECT id FROM shared.entities WHERE roles @> ARRAY['owner'] LIMIT 1"
    )
    return row["id"] if row else None


async def _contact_entity_id(pool: asyncpg.Pool, contact_id: uuid.UUID) -> uuid.UUID | None:
    """Resolve entity_id for a relationship contact."""
    row = await pool.fetchrow(
        """
        SELECT e.id
        FROM shared.contacts c
        JOIN shared.entities e ON c.entity_id = e.id
        WHERE c.id = $1
        LIMIT 1
        """,
        contact_id,
    )
    return row["id"] if row else None


def _backfill_key(source_table: str, source_id: Any) -> str:
    """Stable idempotency key stored in facts.metadata.backfill_source."""
    return f"{source_table}:{source_id}"


async def _fact_exists(pool: asyncpg.Pool, backfill_key: str) -> bool:
    """Return True if an active fact with this backfill_source already exists."""
    row = await pool.fetchrow(
        """
        SELECT 1 FROM facts
        WHERE metadata->>'backfill_source' = $1
          AND validity = 'active'
        LIMIT 1
        """,
        backfill_key,
    )
    return row is not None


async def _insert_fact(
    pool: asyncpg.Pool,
    *,
    subject: str,
    predicate: str,
    content: str,
    entity_id: uuid.UUID | None,
    valid_at: datetime | None,
    permanence: str,
    source_butler: str,
    backfill_key: str,
    tags: list[str] | None = None,
    dry_run: bool = False,
) -> None:
    """Insert a single fact row (skipped when dry_run=True)."""
    if dry_run:
        logger.debug("[DRY-RUN] Would insert fact: %s / %s -> %s", subject, predicate, content[:60])
        return

    fact_id = uuid.uuid4()
    searchable = f"{subject} {predicate} {content}"
    embedding_str = _embed(searchable)

    # tsvector via plainto_tsquery-compatible approach using to_tsvector
    decay_rates = {
        "stable": 0.002,
        "standard": 0.008,
        "volatile": 0.03,
    }
    decay_rate = decay_rates.get(permanence, 0.008)
    now = datetime.now(UTC)
    tags_json = json.dumps(tags or [])
    meta_json = json.dumps({"backfill_source": backfill_key})

    await pool.execute(
        """
        INSERT INTO facts (
            id, subject, predicate, content, embedding, search_vector,
            importance, confidence, decay_rate, permanence, source_butler,
            source_episode_id, supersedes_id, validity, scope,
            created_at, last_confirmed_at, tags, metadata, entity_id,
            object_entity_id, valid_at
        )
        VALUES (
            $1, $2, $3, $4, $5::vector,
            to_tsvector('english', $6),
            $7, $8, $9, $10, $11,
            NULL, NULL, 'active', 'global',
            $12, $12, $13, $14, $15,
            NULL, $16
        )
        """,
        fact_id,
        subject,
        predicate,
        content,
        embedding_str,
        searchable,
        5.0,  # importance
        1.0,  # confidence
        decay_rate,
        permanence,
        source_butler,
        now,
        tags_json,
        meta_json,
        entity_id,
        valid_at,
    )


# ---------------------------------------------------------------------------
# Stats tracker
# ---------------------------------------------------------------------------


class Stats:
    def __init__(self) -> None:
        self.processed = 0
        self.inserted = 0
        self.skipped = 0
        self.errors = 0

    def report(self, label: str) -> None:
        logger.info(
            "[%s] processed=%d inserted=%d skipped=%d errors=%d",
            label,
            self.processed,
            self.inserted,
            self.skipped,
            self.errors,
        )


# ---------------------------------------------------------------------------
# Phase: health
# ---------------------------------------------------------------------------


async def _backfill_health_measurements(
    pool: asyncpg.Pool, owner_id: uuid.UUID | None, stats: Stats, dry_run: bool
) -> None:
    rows = await pool.fetch("SELECT * FROM measurements ORDER BY measured_at ASC")
    for row in rows:
        stats.processed += 1
        key = _backfill_key("measurements", row["id"])
        if await _fact_exists(pool, key):
            stats.skipped += 1
            continue
        mtype = row["type"]
        value = row["value"]
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                pass
        content = f"{mtype}: {json.dumps(value) if not isinstance(value, str) else value}"
        if row.get("notes"):
            content += f". Notes: {row['notes']}"
        try:
            await _insert_fact(
                pool,
                subject="user",
                predicate="measurement_baseline",
                content=content,
                entity_id=owner_id,
                valid_at=row["measured_at"],
                permanence="volatile",
                source_butler="health",
                backfill_key=key,
                tags=["health", "measurement", mtype],
                dry_run=dry_run,
            )
            stats.inserted += 1
        except Exception as exc:
            logger.error("measurements row %s: %s", row["id"], exc)
            stats.errors += 1


async def _backfill_health_symptoms(
    pool: asyncpg.Pool, owner_id: uuid.UUID | None, stats: Stats, dry_run: bool
) -> None:
    rows = await pool.fetch("SELECT * FROM symptoms ORDER BY occurred_at ASC")
    for row in rows:
        stats.processed += 1
        key = _backfill_key("symptoms", row["id"])
        if await _fact_exists(pool, key):
            stats.skipped += 1
            continue
        content = f"Symptom: {row['name']}, severity {row['severity']}/10"
        if row.get("notes"):
            content += f". {row['notes']}"
        try:
            await _insert_fact(
                pool,
                subject="user",
                predicate="symptom_pattern",
                content=content,
                entity_id=owner_id,
                valid_at=row["occurred_at"],
                permanence="volatile",
                source_butler="health",
                backfill_key=key,
                tags=["health", "symptom", row["name"]],
                dry_run=dry_run,
            )
            stats.inserted += 1
        except Exception as exc:
            logger.error("symptoms row %s: %s", row["id"], exc)
            stats.errors += 1


async def _backfill_health_medication_doses(
    pool: asyncpg.Pool, owner_id: uuid.UUID | None, stats: Stats, dry_run: bool
) -> None:
    rows = await pool.fetch(
        """
        SELECT d.*, m.name AS med_name, m.dosage, m.frequency
        FROM medication_doses d
        JOIN medications m ON d.medication_id = m.id
        ORDER BY d.taken_at ASC
        """
    )
    for row in rows:
        stats.processed += 1
        key = _backfill_key("medication_doses", row["id"])
        if await _fact_exists(pool, key):
            stats.skipped += 1
            continue
        if row.get("skipped"):
            action = f"Skipped dose of {row['med_name']} ({row['dosage']})"
        else:
            action = f"Took dose of {row['med_name']} ({row['dosage']})"
        if row.get("notes"):
            action += f". Notes: {row['notes']}"
        try:
            await _insert_fact(
                pool,
                subject="user",
                predicate="medication_frequency",
                content=action,
                entity_id=owner_id,
                valid_at=row["taken_at"],
                permanence="volatile",
                source_butler="health",
                backfill_key=key,
                tags=["health", "medication", "dose", row["med_name"]],
                dry_run=dry_run,
            )
            stats.inserted += 1
        except Exception as exc:
            logger.error("medication_doses row %s: %s", row["id"], exc)
            stats.errors += 1


async def _backfill_health_medications(
    pool: asyncpg.Pool, owner_id: uuid.UUID | None, stats: Stats, dry_run: bool
) -> None:
    rows = await pool.fetch("SELECT * FROM medications ORDER BY created_at ASC")
    for row in rows:
        stats.processed += 1
        key = _backfill_key("medications", row["id"])
        if await _fact_exists(pool, key):
            stats.skipped += 1
            continue
        content = f"{row['name']}, {row['dosage']}, {row['frequency']}"
        if row.get("notes"):
            content += f". {row['notes']}"
        active_str = " (inactive)" if not row.get("active", True) else ""
        content += active_str
        try:
            await _insert_fact(
                pool,
                subject="user",
                predicate="medication",
                content=content,
                entity_id=owner_id,
                valid_at=None,  # property fact — current medication state
                permanence="stable" if row.get("active", True) else "standard",
                source_butler="health",
                backfill_key=key,
                tags=["health", "medication", row["name"]],
                dry_run=dry_run,
            )
            stats.inserted += 1
        except Exception as exc:
            logger.error("medications row %s: %s", row["id"], exc)
            stats.errors += 1


async def _backfill_health_conditions(
    pool: asyncpg.Pool, owner_id: uuid.UUID | None, stats: Stats, dry_run: bool
) -> None:
    rows = await pool.fetch("SELECT * FROM conditions ORDER BY created_at ASC")
    for row in rows:
        stats.processed += 1
        key = _backfill_key("conditions", row["id"])
        if await _fact_exists(pool, key):
            stats.skipped += 1
            continue
        content = f"{row['name']}, status: {row['status']}"
        if row.get("notes"):
            content += f". {row['notes']}"
        # chronic/active conditions are stable; resolved ones are standard
        permanence = "stable" if row["status"] in ("active", "managed") else "standard"
        try:
            await _insert_fact(
                pool,
                subject="user",
                predicate="condition_status",
                content=content,
                entity_id=owner_id,
                valid_at=row.get("diagnosed_at"),
                permanence=permanence,
                source_butler="health",
                backfill_key=key,
                tags=["health", "condition", row["status"]],
                dry_run=dry_run,
            )
            stats.inserted += 1
        except Exception as exc:
            logger.error("conditions row %s: %s", row["id"], exc)
            stats.errors += 1


async def _backfill_health_research(
    pool: asyncpg.Pool, owner_id: uuid.UUID | None, stats: Stats, dry_run: bool
) -> None:
    rows = await pool.fetch("SELECT * FROM research ORDER BY created_at ASC")
    for row in rows:
        stats.processed += 1
        key = _backfill_key("research", row["id"])
        if await _fact_exists(pool, key):
            stats.skipped += 1
            continue
        content = f"{row['title']}: {row['content'][:500]}"
        if row.get("source_url"):
            content += f" (source: {row['source_url']})"
        raw_tags = row.get("tags") or []
        if isinstance(raw_tags, str):
            raw_tags = json.loads(raw_tags)
        tags = ["health", "research"] + list(raw_tags)
        try:
            await _insert_fact(
                pool,
                subject="user",
                predicate="note",
                content=content,
                entity_id=owner_id,
                valid_at=row["created_at"],
                permanence="standard",
                source_butler="health",
                backfill_key=key,
                tags=tags,
                dry_run=dry_run,
            )
            stats.inserted += 1
        except Exception as exc:
            logger.error("research row %s: %s", row["id"], exc)
            stats.errors += 1


async def backfill_health(pool: asyncpg.Pool, dry_run: bool = False) -> Stats:
    stats = Stats()
    logger.info("Resolving owner entity…")
    owner_id = await _owner_entity_id(pool)
    if owner_id is None:
        logger.warning("No owner entity found — facts will be stored without entity_id")

    for fn, label in [
        (_backfill_health_measurements, "measurements"),
        (_backfill_health_symptoms, "symptoms"),
        (_backfill_health_medication_doses, "medication_doses"),
        (_backfill_health_medications, "medications"),
        (_backfill_health_conditions, "conditions"),
        (_backfill_health_research, "research"),
    ]:
        logger.info("Backfilling health.%s…", label)
        try:
            await fn(pool, owner_id, stats, dry_run)
        except asyncpg.UndefinedTableError:
            logger.warning("Table %s does not exist — skipping", label)

    stats.report("health")
    return stats


# ---------------------------------------------------------------------------
# Phase: relationship
# ---------------------------------------------------------------------------


async def _backfill_rel_quick_facts(pool: asyncpg.Pool, stats: Stats, dry_run: bool) -> None:
    rows = await pool.fetch(
        """
        SELECT qf.*, c.id AS contact_uuid,
               COALESCE(
                   NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.last_name)), ''),
                   c.nickname,
                   'Unknown'
               ) AS contact_name,
               c.entity_id
        FROM quick_facts qf
        JOIN contacts c ON qf.contact_id = c.id
        ORDER BY qf.updated_at ASC
        """
    )
    for row in rows:
        stats.processed += 1
        key = _backfill_key("quick_facts", f"{row['contact_id']}:{row['key']}")
        if await _fact_exists(pool, key):
            stats.skipped += 1
            continue
        entity_id = row.get("entity_id")
        if entity_id and isinstance(entity_id, str):
            entity_id = uuid.UUID(entity_id)
        content = f"{row['key']}: {row['value']}"
        try:
            await _insert_fact(
                pool,
                subject=row["contact_name"],
                predicate="preference",
                content=content,
                entity_id=entity_id,
                valid_at=None,
                permanence="standard",
                source_butler="relationship",
                backfill_key=key,
                tags=["relationship", "quick_fact"],
                dry_run=dry_run,
            )
            stats.inserted += 1
        except Exception as exc:
            logger.error("quick_facts contact=%s key=%s: %s", row["contact_id"], row["key"], exc)
            stats.errors += 1


async def _backfill_rel_interactions(pool: asyncpg.Pool, stats: Stats, dry_run: bool) -> None:
    rows = await pool.fetch(
        """
        SELECT i.*, c.entity_id,
               COALESCE(
                   NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.last_name)), ''),
                   c.nickname,
                   'Unknown'
               ) AS contact_name
        FROM interactions i
        JOIN contacts c ON i.contact_id = c.id
        ORDER BY i.occurred_at ASC
        """
    )
    for row in rows:
        stats.processed += 1
        key = _backfill_key("interactions", row["id"])
        if await _fact_exists(pool, key):
            stats.skipped += 1
            continue
        entity_id = row.get("entity_id")
        if entity_id and isinstance(entity_id, str):
            entity_id = uuid.UUID(entity_id)
        itype = row.get("type", "interaction")
        content = f"{itype} with {row['contact_name']}"
        if row.get("summary"):
            content += f": {row['summary']}"
        if row.get("direction"):
            content += f" ({row['direction']})"
        if row.get("duration_minutes"):
            content += f", {row['duration_minutes']} min"
        try:
            await _insert_fact(
                pool,
                subject=row["contact_name"],
                predicate="note",
                content=content,
                entity_id=entity_id,
                valid_at=row.get("occurred_at"),
                permanence="volatile",
                source_butler="relationship",
                backfill_key=key,
                tags=["relationship", "interaction", itype],
                dry_run=dry_run,
            )
            stats.inserted += 1
        except Exception as exc:
            logger.error("interactions row %s: %s", row["id"], exc)
            stats.errors += 1


async def _backfill_rel_life_events(pool: asyncpg.Pool, stats: Stats, dry_run: bool) -> None:
    # Handle both legacy (type column) and current (life_event_type_id) schema.
    try:
        cols = await pool.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name='life_events'"
        )
        col_names = {r["column_name"] for r in cols}
    except Exception:
        col_names = set()

    if "life_event_type_id" in col_names:
        rows = await pool.fetch(
            """
            SELECT e.*, t.name AS type_name, c.entity_id,
                   COALESCE(
                       NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.last_name)), ''),
                       c.nickname,
                       'Unknown'
                   ) AS contact_name
            FROM life_events e
            JOIN life_event_types t ON e.life_event_type_id = t.id
            JOIN contacts c ON e.contact_id = c.id
            ORDER BY COALESCE(e.happened_at::timestamptz, e.created_at) ASC
            """
        )
        for row in rows:
            stats.processed += 1
            key = _backfill_key("life_events", row["id"])
            if await _fact_exists(pool, key):
                stats.skipped += 1
                continue
            entity_id = row.get("entity_id")
            if entity_id and isinstance(entity_id, str):
                entity_id = uuid.UUID(entity_id)
            event_at: datetime | None = None
            if d := row.get("happened_at"):
                if isinstance(d, datetime):
                    event_at = d
                elif isinstance(d, date):
                    event_at = datetime(d.year, d.month, d.day, tzinfo=UTC)
            content = row.get("summary") or row.get("description") or row["type_name"]
            try:
                await _insert_fact(
                    pool,
                    subject=row["contact_name"],
                    predicate="note",
                    content=f"Life event ({row['type_name']}): {content}",
                    entity_id=entity_id,
                    valid_at=event_at,
                    permanence="stable",
                    source_butler="relationship",
                    backfill_key=key,
                    tags=["relationship", "life_event", row["type_name"]],
                    dry_run=dry_run,
                )
                stats.inserted += 1
            except Exception as exc:
                logger.error("life_events row %s: %s", row["id"], exc)
                stats.errors += 1
    else:
        # Legacy schema: type + occurred_at
        rows = await pool.fetch(
            """
            SELECT e.*, c.entity_id,
                   COALESCE(
                       NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.last_name)), ''),
                       c.nickname,
                       'Unknown'
                   ) AS contact_name
            FROM life_events e
            JOIN contacts c ON e.contact_id = c.id
            ORDER BY e.occurred_at ASC
            """
        )
        for row in rows:
            stats.processed += 1
            key = _backfill_key("life_events", row["id"])
            if await _fact_exists(pool, key):
                stats.skipped += 1
                continue
            entity_id = row.get("entity_id")
            if entity_id and isinstance(entity_id, str):
                entity_id = uuid.UUID(entity_id)
            content = row.get("description") or row.get("type", "life event")
            event_type = row.get("type", "event")
            try:
                await _insert_fact(
                    pool,
                    subject=row["contact_name"],
                    predicate="note",
                    content=f"Life event ({event_type}): {content}",
                    entity_id=entity_id,
                    valid_at=row.get("occurred_at"),
                    permanence="stable",
                    source_butler="relationship",
                    backfill_key=key,
                    tags=["relationship", "life_event", event_type],
                    dry_run=dry_run,
                )
                stats.inserted += 1
            except Exception as exc:
                logger.error("life_events row %s: %s", row["id"], exc)
                stats.errors += 1


async def _backfill_rel_notes(pool: asyncpg.Pool, stats: Stats, dry_run: bool) -> None:
    rows = await pool.fetch(
        """
        SELECT n.*, c.entity_id,
               COALESCE(
                   NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.last_name)), ''),
                   c.nickname,
                   'Unknown'
               ) AS contact_name
        FROM notes n
        JOIN contacts c ON n.contact_id = c.id
        ORDER BY n.created_at ASC
        """
    )
    for row in rows:
        stats.processed += 1
        key = _backfill_key("notes", row["id"])
        if await _fact_exists(pool, key):
            stats.skipped += 1
            continue
        entity_id = row.get("entity_id")
        if entity_id and isinstance(entity_id, str):
            entity_id = uuid.UUID(entity_id)
        note_text = row.get("body") or row.get("content") or ""
        if row.get("title"):
            note_text = f"{row['title']}: {note_text}"
        try:
            await _insert_fact(
                pool,
                subject=row["contact_name"],
                predicate="note",
                content=note_text,
                entity_id=entity_id,
                valid_at=row.get("created_at"),
                permanence="standard",
                source_butler="relationship",
                backfill_key=key,
                tags=["relationship", "note"],
                dry_run=dry_run,
            )
            stats.inserted += 1
        except Exception as exc:
            logger.error("notes row %s: %s", row["id"], exc)
            stats.errors += 1


async def _backfill_rel_gifts(pool: asyncpg.Pool, stats: Stats, dry_run: bool) -> None:
    rows = await pool.fetch(
        """
        SELECT g.*, c.entity_id,
               COALESCE(
                   NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.last_name)), ''),
                   c.nickname,
                   'Unknown'
               ) AS contact_name
        FROM gifts g
        JOIN contacts c ON g.contact_id = c.id
        ORDER BY g.created_at ASC
        """
    )
    for row in rows:
        stats.processed += 1
        key = _backfill_key("gifts", row["id"])
        if await _fact_exists(pool, key):
            stats.skipped += 1
            continue
        entity_id = row.get("entity_id")
        if entity_id and isinstance(entity_id, str):
            entity_id = uuid.UUID(entity_id)
        content = f"Gift idea for {row['contact_name']}: {row['description']}"
        if row.get("occasion"):
            content += f" (occasion: {row['occasion']})"
        content += f" — status: {row['status']}"
        try:
            await _insert_fact(
                pool,
                subject=row["contact_name"],
                predicate="note",
                content=content,
                entity_id=entity_id,
                valid_at=row.get("created_at"),
                permanence="volatile",
                source_butler="relationship",
                backfill_key=key,
                tags=["relationship", "gift", row["status"]],
                dry_run=dry_run,
            )
            stats.inserted += 1
        except Exception as exc:
            logger.error("gifts row %s: %s", row["id"], exc)
            stats.errors += 1


async def _backfill_rel_loans(pool: asyncpg.Pool, stats: Stats, dry_run: bool) -> None:
    # Schema may vary; use information_schema to check columns.
    try:
        cols = await pool.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name='loans'"
        )
        col_names = {r["column_name"] for r in cols}
    except Exception:
        col_names = set()

    if not col_names:
        return

    rows = await pool.fetch("SELECT * FROM loans ORDER BY created_at ASC")
    for row in rows:
        stats.processed += 1
        key = _backfill_key("loans", row["id"])
        if await _fact_exists(pool, key):
            stats.skipped += 1
            continue

        # Resolve contact entity — loans may have lender/borrower contact IDs.
        contact_id = (
            row.get("contact_id") or row.get("lender_contact_id") or row.get("borrower_contact_id")
        )
        entity_id: uuid.UUID | None = None
        contact_name = "Unknown"
        if contact_id:
            entity_id = await _contact_entity_id(pool, contact_id)
            name_row = await pool.fetchrow(
                """
                SELECT COALESCE(
                    NULLIF(TRIM(CONCAT_WS(' ', first_name, last_name)), ''),
                    nickname,
                    'Unknown'
                ) AS name
                FROM contacts WHERE id = $1
                """,
                contact_id,
            )
            if name_row:
                contact_name = name_row["name"]

        # Build amount string
        amount_str = ""
        if "amount_cents" in col_names and row.get("amount_cents"):
            amount_str = f"${row['amount_cents'] / 100:.2f}"
        elif "amount" in col_names and row.get("amount"):
            amount_str = str(row["amount"])

        direction = row.get("direction", "")
        desc = row.get("description") or "loan"
        content = f"Loan ({direction}): {amount_str} — {desc} with {contact_name}"
        status = row.get("status", "pending")
        content += f", status: {status}"

        try:
            await _insert_fact(
                pool,
                subject=contact_name,
                predicate="note",
                content=content,
                entity_id=entity_id,
                valid_at=row.get("created_at"),
                permanence="standard" if status in ("settled", "forgiven") else "stable",
                source_butler="relationship",
                backfill_key=key,
                tags=["relationship", "loan", direction or "loan"],
                dry_run=dry_run,
            )
            stats.inserted += 1
        except Exception as exc:
            logger.error("loans row %s: %s", row["id"], exc)
            stats.errors += 1


async def _backfill_rel_tasks(pool: asyncpg.Pool, stats: Stats, dry_run: bool) -> None:
    rows = await pool.fetch(
        """
        SELECT t.*, c.entity_id,
               COALESCE(
                   NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.last_name)), ''),
                   c.nickname,
                   'Unknown'
               ) AS contact_name
        FROM tasks t
        JOIN contacts c ON t.contact_id = c.id
        ORDER BY t.created_at ASC
        """
    )
    for row in rows:
        stats.processed += 1
        key = _backfill_key("tasks", row["id"])
        if await _fact_exists(pool, key):
            stats.skipped += 1
            continue
        entity_id = row.get("entity_id")
        if entity_id and isinstance(entity_id, str):
            entity_id = uuid.UUID(entity_id)
        content = f"Task for {row['contact_name']}: {row['title']}"
        if row.get("description"):
            content += f" — {row['description']}"
        completed = row.get("completed", False)
        content += f" (completed: {completed})"
        try:
            await _insert_fact(
                pool,
                subject=row["contact_name"],
                predicate="note",
                content=content,
                entity_id=entity_id,
                valid_at=row.get("created_at"),
                permanence="volatile",
                source_butler="relationship",
                backfill_key=key,
                tags=["relationship", "task", "completed" if completed else "pending"],
                dry_run=dry_run,
            )
            stats.inserted += 1
        except Exception as exc:
            logger.error("tasks row %s: %s", row["id"], exc)
            stats.errors += 1


async def _backfill_rel_reminders(pool: asyncpg.Pool, stats: Stats, dry_run: bool) -> None:
    rows = await pool.fetch(
        """
        SELECT r.*, c.entity_id,
               COALESCE(
                   NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.last_name)), ''),
                   c.nickname,
                   'Unknown'
               ) AS contact_name
        FROM reminders r
        JOIN contacts c ON r.contact_id = c.id
        ORDER BY r.created_at ASC
        """
    )
    for row in rows:
        stats.processed += 1
        key = _backfill_key("reminders", row["id"])
        if await _fact_exists(pool, key):
            stats.skipped += 1
            continue
        entity_id = row.get("entity_id")
        if entity_id and isinstance(entity_id, str):
            entity_id = uuid.UUID(entity_id)
        label = row.get("message") or row.get("label") or "reminder"
        rtype = row.get("type") or "one_time"
        content = f"Reminder for {row['contact_name']}: {label} (type: {rtype})"
        due = row.get("next_trigger_at") or row.get("due_at")
        if due:
            content += f", due: {due}"
        try:
            await _insert_fact(
                pool,
                subject=row["contact_name"],
                predicate="note",
                content=content,
                entity_id=entity_id,
                valid_at=row.get("created_at"),
                permanence="volatile",
                source_butler="relationship",
                backfill_key=key,
                tags=["relationship", "reminder", rtype],
                dry_run=dry_run,
            )
            stats.inserted += 1
        except Exception as exc:
            logger.error("reminders row %s: %s", row["id"], exc)
            stats.errors += 1


async def _backfill_rel_activity_feed(pool: asyncpg.Pool, stats: Stats, dry_run: bool) -> None:
    rows = await pool.fetch(
        """
        SELECT a.*, c.entity_id,
               COALESCE(
                   NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.last_name)), ''),
                   c.nickname,
                   'Unknown'
               ) AS contact_name
        FROM activity_feed a
        JOIN contacts c ON a.contact_id = c.id
        ORDER BY a.created_at ASC
        """
    )
    for row in rows:
        stats.processed += 1
        key = _backfill_key("activity_feed", row["id"])
        if await _fact_exists(pool, key):
            stats.skipped += 1
            continue
        entity_id = row.get("entity_id")
        if entity_id and isinstance(entity_id, str):
            entity_id = uuid.UUID(entity_id)
        event_type = row.get("event_type", "event")
        content = f"Activity ({event_type}) for {row['contact_name']}: {row.get('summary', '')}"
        try:
            await _insert_fact(
                pool,
                subject=row["contact_name"],
                predicate="note",
                content=content,
                entity_id=entity_id,
                valid_at=row.get("created_at"),
                permanence="volatile",
                source_butler="relationship",
                backfill_key=key,
                tags=["relationship", "activity", event_type],
                dry_run=dry_run,
            )
            stats.inserted += 1
        except Exception as exc:
            logger.error("activity_feed row %s: %s", row["id"], exc)
            stats.errors += 1


async def backfill_relationship(pool: asyncpg.Pool, dry_run: bool = False) -> Stats:
    stats = Stats()

    for fn, label in [
        (_backfill_rel_quick_facts, "quick_facts"),
        (_backfill_rel_interactions, "interactions"),
        (_backfill_rel_life_events, "life_events"),
        (_backfill_rel_notes, "notes"),
        (_backfill_rel_gifts, "gifts"),
        (_backfill_rel_loans, "loans"),
        (_backfill_rel_tasks, "tasks"),
        (_backfill_rel_reminders, "reminders"),
        (_backfill_rel_activity_feed, "activity_feed"),
    ]:
        logger.info("Backfilling relationship.%s…", label)
        try:
            await fn(pool, stats, dry_run)
        except asyncpg.UndefinedTableError:
            logger.warning("Table %s does not exist — skipping", label)

    stats.report("relationship")
    return stats


# ---------------------------------------------------------------------------
# Phase: finance
# ---------------------------------------------------------------------------


async def _backfill_fin_transactions(
    pool: asyncpg.Pool, owner_id: uuid.UUID | None, stats: Stats, dry_run: bool
) -> None:
    rows = await pool.fetch("SELECT * FROM transactions ORDER BY posted_at ASC")
    for row in rows:
        stats.processed += 1
        key = _backfill_key("transactions", row["id"])
        if await _fact_exists(pool, key):
            stats.skipped += 1
            continue
        merchant = row.get("merchant", "Unknown")
        amount = row.get("amount", Decimal("0"))
        currency = row.get("currency", "USD")
        direction = row.get("direction", "debit")
        category = row.get("category", "")
        content = f"{direction.capitalize()} transaction: {merchant}, {amount} {currency}"
        if category:
            content += f", category: {category}"
        if row.get("description"):
            content += f" — {row['description']}"
        try:
            await _insert_fact(
                pool,
                subject=merchant,
                predicate="note",
                content=content,
                entity_id=owner_id,
                valid_at=row.get("posted_at"),
                permanence="volatile",
                source_butler="finance",
                backfill_key=key,
                tags=(
                    ["finance", "transaction", direction, category]
                    if category
                    else ["finance", "transaction", direction]
                ),
                dry_run=dry_run,
            )
            stats.inserted += 1
        except Exception as exc:
            logger.error("transactions row %s: %s", row["id"], exc)
            stats.errors += 1


async def _backfill_fin_accounts(
    pool: asyncpg.Pool, owner_id: uuid.UUID | None, stats: Stats, dry_run: bool
) -> None:
    rows = await pool.fetch("SELECT * FROM accounts ORDER BY created_at ASC")
    for row in rows:
        stats.processed += 1
        key = _backfill_key("accounts", row["id"])
        if await _fact_exists(pool, key):
            stats.skipped += 1
            continue
        name = row.get("name") or row.get("institution") or "account"
        atype = row.get("type") or row.get("account_type") or ""
        content = f"Financial account: {name}"
        if atype:
            content += f" ({atype})"
        currency = row.get("currency", "USD")
        content += f", currency: {currency}"
        try:
            await _insert_fact(
                pool,
                subject=name,
                predicate="note",
                content=content,
                entity_id=owner_id,
                valid_at=row.get("created_at"),
                permanence="stable",
                source_butler="finance",
                backfill_key=key,
                tags=["finance", "account", atype] if atype else ["finance", "account"],
                dry_run=dry_run,
            )
            stats.inserted += 1
        except Exception as exc:
            logger.error("accounts row %s: %s", row["id"], exc)
            stats.errors += 1


async def _backfill_fin_subscriptions(
    pool: asyncpg.Pool, owner_id: uuid.UUID | None, stats: Stats, dry_run: bool
) -> None:
    rows = await pool.fetch("SELECT * FROM subscriptions ORDER BY created_at ASC")
    for row in rows:
        stats.processed += 1
        key = _backfill_key("subscriptions", row["id"])
        if await _fact_exists(pool, key):
            stats.skipped += 1
            continue
        service = row.get("service", "Unknown")
        amount = row.get("amount", Decimal("0"))
        currency = row.get("currency", "USD")
        frequency = row.get("frequency", "monthly")
        status = row.get("status", "active")
        content = f"Subscription: {service}, {amount} {currency}/{frequency}, status: {status}"
        if row.get("next_renewal"):
            content += f", next renewal: {row['next_renewal']}"
        # active subscriptions are stable obligations; cancelled ones are standard
        permanence = "stable" if status == "active" else "standard"
        try:
            await _insert_fact(
                pool,
                subject=service,
                predicate="note",
                content=content,
                entity_id=owner_id,
                valid_at=row.get("created_at"),
                permanence=permanence,
                source_butler="finance",
                backfill_key=key,
                tags=["finance", "subscription", status],
                dry_run=dry_run,
            )
            stats.inserted += 1
        except Exception as exc:
            logger.error("subscriptions row %s: %s", row["id"], exc)
            stats.errors += 1


async def _backfill_fin_bills(
    pool: asyncpg.Pool, owner_id: uuid.UUID | None, stats: Stats, dry_run: bool
) -> None:
    rows = await pool.fetch("SELECT * FROM bills ORDER BY created_at ASC")
    for row in rows:
        stats.processed += 1
        key = _backfill_key("bills", row["id"])
        if await _fact_exists(pool, key):
            stats.skipped += 1
            continue
        payee = row.get("payee", "Unknown")
        amount = row.get("amount", Decimal("0"))
        currency = row.get("currency", "USD")
        status = row.get("status", "pending")
        due_date = row.get("due_date")
        content = f"Bill: {payee}, {amount} {currency}, due: {due_date}, status: {status}"
        frequency = row.get("frequency", "one_time")
        if frequency and frequency != "one_time":
            content += f", frequency: {frequency}"
        # pending/overdue bills are stable obligations
        permanence = "stable" if status in ("pending", "overdue") else "standard"
        valid_at: datetime | None = None
        if due_date:
            if isinstance(due_date, datetime):
                valid_at = due_date
            elif isinstance(due_date, date):
                valid_at = datetime(due_date.year, due_date.month, due_date.day, tzinfo=UTC)
        try:
            await _insert_fact(
                pool,
                subject=payee,
                predicate="note",
                content=content,
                entity_id=owner_id,
                valid_at=valid_at,
                permanence=permanence,
                source_butler="finance",
                backfill_key=key,
                tags=["finance", "bill", status],
                dry_run=dry_run,
            )
            stats.inserted += 1
        except Exception as exc:
            logger.error("bills row %s: %s", row["id"], exc)
            stats.errors += 1


async def backfill_finance(pool: asyncpg.Pool, dry_run: bool = False) -> Stats:
    stats = Stats()
    logger.info("Resolving owner entity…")
    owner_id = await _owner_entity_id(pool)
    if owner_id is None:
        logger.warning("No owner entity found — facts will be stored without entity_id")

    for fn, label in [
        (_backfill_fin_transactions, "transactions"),
        (_backfill_fin_accounts, "accounts"),
        (_backfill_fin_subscriptions, "subscriptions"),
        (_backfill_fin_bills, "bills"),
    ]:
        logger.info("Backfilling finance.%s…", label)
        try:
            await fn(pool, owner_id, stats, dry_run)
        except asyncpg.UndefinedTableError:
            logger.warning("Table %s does not exist — skipping", label)

    stats.report("finance")
    return stats


# ---------------------------------------------------------------------------
# Phase: home
# ---------------------------------------------------------------------------


async def backfill_home(pool: asyncpg.Pool, dry_run: bool = False) -> Stats:
    stats = Stats()
    logger.info("Resolving owner entity for home phase…")
    owner_id = await _owner_entity_id(pool)
    if owner_id is None:
        logger.warning("No owner entity found — facts will be stored without entity_id")

    logger.info("Backfilling home.ha_entity_snapshot…")
    try:
        rows = await pool.fetch("SELECT * FROM ha_entity_snapshot ORDER BY captured_at ASC")
    except asyncpg.UndefinedTableError:
        logger.warning("Table ha_entity_snapshot does not exist — skipping")
        stats.report("home")
        return stats

    for row in rows:
        stats.processed += 1
        entity_ha_id = row["entity_id"]
        key = _backfill_key("ha_entity_snapshot", entity_ha_id)
        if await _fact_exists(pool, key):
            stats.skipped += 1
            continue
        state = row.get("state", "unknown")
        attrs = row.get("attributes") or {}
        if isinstance(attrs, str):
            try:
                attrs = json.loads(attrs)
            except Exception:
                attrs = {}
        friendly = attrs.get("friendly_name", entity_ha_id)
        unit = attrs.get("unit_of_measurement", "")
        content = f"Home Assistant entity {friendly} ({entity_ha_id}): state={state}"
        if unit:
            content += f" {unit}"
        if row.get("last_updated"):
            content += f", last_updated={row['last_updated']}"
        # Determine device category from entity_id prefix (e.g. sensor.*, light.*)
        domain = entity_ha_id.split(".")[0] if "." in entity_ha_id else "unknown"
        try:
            await _insert_fact(
                pool,
                subject=friendly,
                predicate="status",
                content=content,
                entity_id=owner_id,
                valid_at=row.get("captured_at"),
                permanence="volatile",
                source_butler="home",
                backfill_key=key,
                tags=["home", "ha", domain],
                dry_run=dry_run,
            )
            stats.inserted += 1
        except Exception as exc:
            logger.error("ha_entity_snapshot entity_id=%s: %s", entity_ha_id, exc)
            stats.errors += 1

    stats.report("home")
    return stats


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

_VALID_PHASES = ("health", "relationship", "finance", "home", "all")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill CRUD data into the facts table.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--phase",
        choices=_VALID_PHASES,
        required=True,
        help="Which domain to backfill (or 'all' to run every phase).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Compute embeddings and log what would be inserted without writing.",
    )
    parser.add_argument(
        "--db",
        default=None,
        metavar="DB_NAME",
        help="PostgreSQL database name (overrides DATABASE_URL).",
    )
    parser.add_argument(
        "--schema",
        default=None,
        metavar="SCHEMA",
        help="Schema name (e.g. 'health', 'relationship'). Defaults to the phase name.",
    )
    return parser.parse_args(argv)


async def _run_phase(phase: str, args: argparse.Namespace) -> Stats:
    schema = args.schema or phase
    db_name = args.db or os.environ.get("POSTGRES_DB", "butlers")
    dry_run = args.dry_run

    logger.info("Connecting to db=%s schema=%s dry_run=%s", db_name, schema, dry_run)
    pool = await _create_pool(db_name, schema)

    try:
        if phase == "health":
            return await backfill_health(pool, dry_run=dry_run)
        elif phase == "relationship":
            return await backfill_relationship(pool, dry_run=dry_run)
        elif phase == "finance":
            return await backfill_finance(pool, dry_run=dry_run)
        elif phase == "home":
            return await backfill_home(pool, dry_run=dry_run)
        else:
            raise ValueError(f"Unknown phase: {phase}")
    finally:
        await pool.close()


async def _main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    phases = list(_VALID_PHASES[:-1]) if args.phase == "all" else [args.phase]

    total_errors = 0
    for phase in phases:
        logger.info("=== Starting phase: %s ===", phase)
        try:
            stats = await _run_phase(phase, args)
            total_errors += stats.errors
        except Exception as exc:
            logger.exception("Phase %s failed: %s", phase, exc)
            total_errors += 1

    if total_errors:
        logger.error("Backfill completed with %d error(s).", total_errors)
        return 1
    logger.info("Backfill completed successfully.")
    return 0


def main() -> None:
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
