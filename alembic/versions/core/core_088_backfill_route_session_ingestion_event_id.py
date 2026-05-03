"""backfill_route_session_ingestion_event_id

Revision ID: core_088
Revises: core_087
Create Date: 2026-05-03 00:00:00.000000

Backfill ``ingestion_event_id`` on existing route-triggered session rows
across every butler schema.

Background
----------
``Spawner.trigger`` did not forward the ingestion event UUID to
``session_create``, so ``{schema}.sessions.ingestion_event_id`` was never
populated for routed sub-butler sessions even when the request originated
from an ingested message. ``CoreSessionsAdapter._resolve_contacts`` joins
sessions through ``ingestion_event_id`` to resolve the channel and
contact, so every routed conversation in chronicler ended up titled
``Conversation via unknown channel``.

The companion code change in ``src/butlers/core/spawner.py`` and
``src/butlers/core_tools/_routing.py`` plumbs the value end-to-end going
forward. This migration repairs the historical rows.

Repair logic
------------
For every butler schema with a ``sessions`` table:

    UPDATE {schema}.sessions
    SET ingestion_event_id = request_id::uuid
    WHERE trigger_source       = 'route'
      AND ingestion_event_id   IS NULL
      AND request_id           IS NOT NULL
      AND request_id ~ '^[0-9a-f]{8}-...{27}$'  -- UUID shape
      AND EXISTS (
          SELECT 1 FROM public.ingestion_events ie
          WHERE ie.id = request_id::uuid
      )

The ``request_id`` column on butler sessions is the same UUID7 that
``switchboard.ingest`` writes as ``public.ingestion_events.id`` (inserted
in the same transaction by ``roster/switchboard/tools/ingestion/ingest.py``),
so this is a safe identity backfill — no synthetic values are introduced.
The ``EXISTS`` guard discards request_ids that were never persisted as
ingestion events (e.g. failed ingests or pre-rollout legacy rows).

After the backfill we reset the per-schema watermarks for the
``core.sessions`` projection so the next ``CoreSessionsAdapter`` run
re-resolves contact info and re-titles the affected episodes via the
existing ``_compute_episode_title`` rules.

Idempotency
-----------
Re-running the migration touches no additional rows: the UPDATE is
guarded by ``ingestion_event_id IS NULL``, and the watermark reset is
naturally idempotent.

Downgrade
---------
Downgrade is a no-op. Removing the backfill would re-introduce the
``Conversation via unknown channel`` episode titles without recovering
any user-visible value.
"""

from __future__ import annotations

import logging

from alembic import op

revision = "core_088"
down_revision = "core_087"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)

_BUTLER_SCHEMAS: tuple[str, ...] = (
    "education",
    "finance",
    "general",
    "health",
    "home",
    "lifestyle",
    "messenger",
    "qa",
    "relationship",
    "switchboard",
    "travel",
)

_UUID_REGEX = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


def upgrade() -> None:
    bind = op.get_bind()

    for schema in _BUTLER_SCHEMAS:
        # Skip schemas without a sessions table (e.g. fresh deployments
        # where a butler was just added).
        exists = bind.exec_driver_sql(
            "SELECT to_regclass(%s) IS NOT NULL",
            (f"{schema}.sessions",),
        ).scalar()
        if not exists:
            logger.info("core_088: %s.sessions missing; skipping backfill", schema)
            continue

        result = bind.exec_driver_sql(
            f"""
            UPDATE {schema}.sessions s
            SET ingestion_event_id = s.request_id::uuid
            WHERE s.trigger_source     = 'route'
              AND s.ingestion_event_id IS NULL
              AND s.request_id IS NOT NULL
              AND s.request_id ~ '{_UUID_REGEX}'
              AND EXISTS (
                  SELECT 1 FROM public.ingestion_events ie
                  WHERE ie.id = s.request_id::uuid
              )
            """
        )
        logger.info(
            "core_088: backfilled ingestion_event_id on %d %s.sessions row(s)",
            result.rowcount or 0,
            schema,
        )

    # Reset chronicler watermarks for core.sessions so the next adapter run
    # re-projects route episodes with resolved channel/contact titles.
    # The chronicler schema is deployed via a separate migration chain, so
    # guard the table check — tests that only run the core chain (and
    # deployments that have not yet enabled the chronicler) will skip this
    # step instead of erroring.
    chronicler_table_exists = bind.exec_driver_sql(
        "SELECT to_regclass('chronicler.projection_checkpoints') IS NOT NULL"
    ).scalar()
    if chronicler_table_exists:
        bind.exec_driver_sql(
            """
            UPDATE chronicler.projection_checkpoints
            SET watermark    = NULL,
                watermark_id = NULL
            WHERE source_name = 'core.sessions'
            """
        )
    else:
        logger.info(
            "core_088: chronicler.projection_checkpoints not present; skipping watermark reset"
        )


def downgrade() -> None:
    pass
