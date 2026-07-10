"""routing_verdict_log.ingestion_event_id FK: RESTRICT -> ON DELETE CASCADE.

Revision ID: sw_023
Revises: sw_022
Create Date: 2026-07-11 00:00:00.000000

bu-w4m9q (review follow-up from PR #2983). ``routing_verdict_log`` (created in
``sw_019``) FKs ``ingestion_event_id`` to ``public.ingestion_events(id)`` with
the default ``ON DELETE`` action (``NO ACTION`` / RESTRICT). Nothing deleted
from ``routing_verdict_log`` today, but ``public.ingestion_events`` IS purged on
a schedule: ``OwnTracksConnector``'s ``OwnTracksRetention`` (see
``src/butlers/connectors/owntracks.py``) runs ``DELETE FROM
public.ingestion_events WHERE source_channel = 'owntracks' AND received_at <
...`` every 6 hours. The landmine: if any retention-purged channel is ever
reconfigured so its events are routed at full tier (owntracks is skip-routed
today, hence inert), each purged event that carries an attached verdict row
would raise a ``ForeignKeyViolationError``. The purge site swallows every
exception at WARNING (``OwnTracksRetention._run_purge``), so retention would
silently halt — expired rows accumulate forever with only a warning log.

Fix (FK policy decided in bu-w4m9q): ``ON DELETE CASCADE``. Verdict rows embed
per-event routing metadata that a data-minimization purge is precisely meant to
remove; the promotion-mining audit need is aggregate-level, so cascading the
verdict row away with its event is correct. ``SET NULL`` was rejected (orphaned
rows retain the metadata the purge exists to erase); a manual sweep in every
retention job was rejected (relies on every future purge site remembering).

Scope note: this migration lives in the SWITCHBOARD chain, not the core chain —
``routing_verdict_log`` is a switchboard-schema table (created here in
``sw_019``), even though its FK target ``public.ingestion_events`` lives in the
shared ``public`` schema. Altering it from the core chain would be a cross-chain
reference to a table this chain owns. The FK carries the Postgres
auto-generated name ``routing_verdict_log_ingestion_event_id_fkey`` (the
standard ``<table>_<column>_fkey`` form, cf. ``sessions_ingestion_event_id_fkey``
in ``src/butlers/core/sessions.py``); no migration references it by name, so the
drop/recreate is safe.

Downgrade restores the original RESTRICT (``NO ACTION``) FK.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_023"
down_revision = "sw_022"
branch_labels = None
depends_on = None

_FK_NAME = "routing_verdict_log_ingestion_event_id_fkey"


def upgrade() -> None:
    op.execute(f"ALTER TABLE routing_verdict_log DROP CONSTRAINT IF EXISTS {_FK_NAME}")
    op.execute(
        f"""
        ALTER TABLE routing_verdict_log
            ADD CONSTRAINT {_FK_NAME}
            FOREIGN KEY (ingestion_event_id)
            REFERENCES public.ingestion_events(id)
            ON DELETE CASCADE
        """
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE routing_verdict_log DROP CONSTRAINT IF EXISTS {_FK_NAME}")
    op.execute(
        f"""
        ALTER TABLE routing_verdict_log
            ADD CONSTRAINT {_FK_NAME}
            FOREIGN KEY (ingestion_event_id)
            REFERENCES public.ingestion_events(id)
        """
    )
