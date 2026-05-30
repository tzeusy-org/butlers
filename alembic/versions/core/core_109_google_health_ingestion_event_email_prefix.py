"""google_health ingestion_events: add email prefix to external_event_id.

Revision ID: core_109
Revises: core_108
Create Date: 2026-05-26 00:00:00.000000

Rewrites existing ``public.ingestion_events`` rows for the Google Health
connector from the old 3-segment shape::

    google_health:<resource>:<date>

to the new 4-segment email-prefixed shape::

    google_health:<email>:<resource>:<date>

and the same prefix change for sleep-session rows::

    google_health:sleep_session:<id>  →  google_health:<email>:sleep_session:<id>

The ``idempotency_key`` column on the same rows is also rewritten to match.

Scope
-----
Only ``public.ingestion_events`` rows with:
  - ``source_provider = 'google_health'``
  - ``external_event_id`` in the OLD 3-segment shape (no email in position 2)

are touched.  The email is sourced from ``public.google_accounts.email``
(active primary account) by joining on ``public.google_accounts.is_primary = true``.

Idempotency
-----------
The upgrade WHERE clause targets ONLY exact old-shape rows:
  - 3 colon-separated segments (split_part(..., ':', 3) != '' AND split_part(..., ':', 4) = '')

Already-migrated rows have 4 segments — they are excluded by the
``split_part(..., ':', 4) = ''`` guard.  Re-running this migration is a no-op.

Reversibility
-------------
Downgrade targets ONLY exact new-shape rows:
  - 4 colon-separated segments (split_part(..., ':', 4) != '' AND split_part(..., ':', 5) = '')
  - Segment 2 matches the email of the primary active google account

Downgrade strips segment 2 (the email), recovering the old 3-segment key.
"""

from __future__ import annotations

from alembic import op

revision = "core_109"
down_revision = "core_108"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Rewrite old-shape ingestion_events rows for google_health to the
    # email-prefixed shape.
    #
    # Old:  google_health:<resource>:<date>          (3 segments)
    # Old:  google_health:sleep_session:<id>         (3 segments)
    # New:  google_health:<email>:<resource>:<date>  (4 segments)
    # New:  google_health:<email>:sleep_session:<id> (4 segments)
    #
    # We match rows that:
    #   1. source_provider = 'google_health'
    #   2. external_event_id starts with 'google_health:'
    #   3. Exactly 3 colon-delimited segments (segment 3 non-empty, segment 4 empty)
    #
    # The UPDATE joins public.google_accounts on is_primary=true to obtain the
    # email.  Rows with no matching active primary account are left unchanged.
    #
    # NOTE: Only external_event_id is rewritten here.  An earlier version of
    # this migration also tried to update an `idempotency_key` column on
    # public.ingestion_events, but that column does not exist — the ingest
    # pipeline stores the envelope's idempotency_key inside the JSONB
    # request_context column, and the table-level dedup key is `dedupe_key`
    # (format: "idem:<channel>:<endpoint_identity>:<idempotency_key>").
    # Updating dedupe_key in this migration would break dedup lookups for
    # already-processed events, so we leave it unchanged.
    # ------------------------------------------------------------------
    op.execute("""
        UPDATE public.ingestion_events ie
        SET
            external_event_id =
                'google_health'
                || ':' || ga.email
                || ':' || split_part(ie.external_event_id, ':', 2)
                || ':' || split_part(ie.external_event_id, ':', 3)
        FROM public.google_accounts ga
        WHERE ie.source_provider = 'google_health'
          AND ie.external_event_id LIKE 'google_health:%'
          -- Only target old-shape rows: exactly 3 segments.
          AND split_part(ie.external_event_id, ':', 3) != ''
          AND split_part(ie.external_event_id, ':', 4) = ''
          -- Join on active primary account.
          AND ga.is_primary = true
          AND ga.status = 'active'
    """)


def downgrade() -> None:
    # ------------------------------------------------------------------
    # Strip the email segment from migrated rows, recovering the old shape.
    #
    # New:  google_health:<email>:<resource>:<date>  (4 segments)
    # Old:  google_health:<resource>:<date>          (3 segments)
    #
    # We match rows that:
    #   1. source_provider = 'google_health'
    #   2. external_event_id starts with 'google_health:'
    #   3. Exactly 4 colon-delimited segments (segment 4 non-empty, segment 5 empty)
    #   4. Segment 2 matches the email of the active primary google account
    #
    # NOTE: Only external_event_id is reverted.  idempotency_key is not updated
    # because the column does not exist on public.ingestion_events (see upgrade()).
    # ------------------------------------------------------------------
    op.execute("""
        UPDATE public.ingestion_events ie
        SET
            external_event_id =
                'google_health'
                || ':' || split_part(ie.external_event_id, ':', 3)
                || ':' || split_part(ie.external_event_id, ':', 4)
        FROM public.google_accounts ga
        WHERE ie.source_provider = 'google_health'
          AND ie.external_event_id LIKE 'google_health:%'
          -- Only target new-shape rows: exactly 4 segments.
          AND split_part(ie.external_event_id, ':', 4) != ''
          AND split_part(ie.external_event_id, ':', 5) = ''
          -- Segment 2 must be the email of the active primary account.
          AND ga.is_primary = true
          AND ga.status = 'active'
          AND split_part(ie.external_event_id, ':', 2) = ga.email
    """)
