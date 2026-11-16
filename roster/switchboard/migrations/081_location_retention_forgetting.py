"""Switchboard forgets its copies of expired OwnTracks fixes (bu-s11n0s.7, slice S4c-2).

Revision ID: sw_081
Revises: sw_080

Every OwnTracks envelope Switchboard accepts stays in ``message_inbox``, whose
``normalized_text`` (and ``raw_payload.payload``) carries the fix's exact coordinates even at
the metadata tier. Before Chronicler may let the connector delete a raw fix, Switchboard
forgets these copies under its own role:

* ``location_retention_forgotten_keys`` keeps one row per forgotten logical key (the
  connector's ``idempotency_key``, which ingest stores as ``request_context.idempotency_key``)
  and the grant that forgot it. A ``BEFORE INSERT`` trigger on ``message_inbox`` withholds the
  location of any later OwnTracks envelope for a forgotten key (a parked submission or replay
  arriving after the forgetting): its text and payload, and its ``content_hash_key`` (a short
  hash of text and sender, brute-forceable back to the fix). It keeps the row, so dedupe and the
  lifecycle audit still work.
* ``location_retention_forgetting`` keeps one count-only receipt per forgetting run for a grant:
  ``complete`` (every copy forgotten, at most once per grant) or ``blocked`` (a copy was still
  processing or had been routed onward, so it was left as it is; may repeat).
* ``location_retention_forget_lock()`` (exclusive, the forgetter) and
  ``location_retention_forget_guard()`` (shared, the trigger) are one transaction advisory lock,
  so an insert racing the forgetter waits for its commit and then sees the forgotten key.

Both tables are append-only by trigger (an init-db replay re-grants schema-wide DML, so a grant
alone would not hold). The runtime role may read and insert them; the downgrade refuses while
either holds a row.
"""

from alembic import op

revision = "sw_081"
down_revision = "sw_080"
branch_labels = None
depends_on = None

_RUNTIME = "butler_switchboard_rw"
_KEYS = "location_retention_forgotten_keys"
_RUNS = "location_retention_forgetting"
_LOCK_ID = "hashtextextended('butlers:switchboard-location-forget', 0)"
#: What a forgotten copy's text becomes. Kept in step with ``WITHHELD_TEXT`` in the Switchboard
#: tool module ``tools/location_retention.py`` (a test pins the two equal).
_WITHHELD = "[location withheld: raw retention]"


def _schema() -> str:
    """The schema this chain migrates into (``switchboard`` at runtime), quoted.

    Tables are named bare (the migration's search_path); functions are qualified, and the insert
    trigger pins its search_path to this schema.
    """
    return str(op.get_bind().exec_driver_sql("SELECT quote_ident(current_schema())").scalar_one())


def upgrade() -> None:
    s = _schema()
    op.execute(f"""
        CREATE TABLE {_KEYS} (
            logical_source_key TEXT PRIMARY KEY,
            grant_id UUID NOT NULL,
            forgotten_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE {_RUNS} (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            grant_id UUID NOT NULL,
            outcome TEXT NOT NULL CHECK (outcome IN ('complete', 'blocked')),
            item_count INTEGER NOT NULL CHECK (item_count >= 0),
            forgotten_count INTEGER NOT NULL CHECK (forgotten_count >= 0),
            already_forgotten_count INTEGER NOT NULL CHECK (already_forgotten_count >= 0),
            absent_count INTEGER NOT NULL CHECK (absent_count >= 0),
            blocked_count INTEGER NOT NULL CHECK (blocked_count >= 0),
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            CHECK ((outcome = 'complete') = (blocked_count = 0))
        );
        CREATE UNIQUE INDEX location_retention_forgetting_complete_once
            ON {_RUNS} (grant_id) WHERE outcome = 'complete';

        CREATE FUNCTION {s}.location_retention_append_only()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            RAISE EXCEPTION 'location retention forgetting is append-only'
                USING ERRCODE = 'insufficient_privilege';
        END $$;
        CREATE TRIGGER location_retention_forgotten_keys_append_only
            BEFORE UPDATE OR DELETE OR TRUNCATE ON {_KEYS}
            FOR EACH STATEMENT EXECUTE FUNCTION {s}.location_retention_append_only();
        CREATE TRIGGER location_retention_forgetting_append_only
            BEFORE UPDATE OR DELETE OR TRUNCATE ON {_RUNS}
            FOR EACH STATEMENT EXECUTE FUNCTION {s}.location_retention_append_only();

        CREATE FUNCTION {s}.location_retention_forget_lock()
        RETURNS void LANGUAGE sql VOLATILE SET search_path = pg_catalog, pg_temp AS $$
            SELECT pg_advisory_xact_lock({_LOCK_ID})
        $$;
        CREATE FUNCTION {s}.location_retention_forget_guard()
        RETURNS void LANGUAGE sql VOLATILE SET search_path = pg_catalog, pg_temp AS $$
            SELECT pg_advisory_xact_lock_shared({_LOCK_ID})
        $$;

        CREATE FUNCTION {s}.message_inbox_withhold_forgotten_location()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, {s}, pg_temp AS $$
        BEGIN
            IF NEW.request_context ->> 'source_channel' IS DISTINCT FROM 'owntracks' THEN
                RETURN NEW;
            END IF;
            -- Wait for any forgetter to commit, then read its forgotten keys.
            PERFORM location_retention_forget_guard();
            IF EXISTS (
                SELECT 1 FROM {_KEYS}
                WHERE logical_source_key = NEW.request_context ->> 'idempotency_key'
            ) THEN
                NEW.normalized_text := '{_WITHHELD}';
                NEW.raw_payload := jsonb_set(
                    NEW.raw_payload, '{{payload}}',
                    jsonb_build_object('raw', NULL, 'normalized_text', '{_WITHHELD}')
                );
                -- A short hash of the text and sender: brute-forceable back to the fix.
                NEW.request_context := NEW.request_context - 'content_hash_key';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER message_inbox_withhold_forgotten_location
            BEFORE INSERT ON message_inbox
            FOR EACH ROW EXECUTE FUNCTION {s}.message_inbox_withhold_forgotten_location();

        REVOKE ALL ON {_KEYS}, {_RUNS} FROM PUBLIC;
        REVOKE ALL ON FUNCTION {s}.location_retention_forget_lock(),
            {s}.location_retention_forget_guard() FROM PUBLIC;
    """)
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_RUNTIME}') THEN
                -- Default privileges grant the runtime role every table privilege.
                REVOKE ALL ON {_KEYS}, {_RUNS} FROM {_RUNTIME};
                GRANT SELECT, INSERT ON {_KEYS}, {_RUNS} TO {_RUNTIME};
                GRANT EXECUTE ON FUNCTION {s}.location_retention_forget_lock(),
                    {s}.location_retention_forget_guard() TO {_RUNTIME};
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    s = _schema()
    op.execute(f"""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM {_KEYS}) OR EXISTS (SELECT 1 FROM {_RUNS}) THEN
                RAISE EXCEPTION 'location retention forgetting is recorded; not dropping it'
                    USING ERRCODE = '55000';
            END IF;
        END $$;
        DROP TRIGGER IF EXISTS message_inbox_withhold_forgotten_location
            ON message_inbox;
        DROP FUNCTION IF EXISTS {s}.message_inbox_withhold_forgotten_location();
        DROP FUNCTION IF EXISTS {s}.location_retention_forget_guard();
        DROP FUNCTION IF EXISTS {s}.location_retention_forget_lock();
        DROP TABLE IF EXISTS {_RUNS};
        DROP TABLE IF EXISTS {_KEYS};
        DROP FUNCTION IF EXISTS {s}.location_retention_append_only();
    """)
