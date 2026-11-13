"""The owner mode overlay: owner edits to a mode, loosened only under step-up.

Revision ID: sw_076
Revises: sw_075

RFC 0038 S3.1 and owner decision bu-u33sb7 (bu-dhloi1.21 PR4b). Only the owner
edits a mode's duration, the obligation classes it holds and who may break
through; a loosening needs the dashboard step-up, a tightening does not.

``switchboard.owner_mode_overlay`` holds one row per edited mode, keyed by the
mode ID and versioned; a mode without a row has its recorded default
(``butlers.perception.modes.DEFAULT_PROFILES``: sick holds ``proactive``, every
other mode holds nothing; no break-through sender; open-ended) at version 0.

* A write moves exactly one version (an insert is version 1), keeps the mode
  and carries a fresh owner action.
* A tightening needs nothing more. A loosening (a held class dropped, a
  break-through sender added, or a duration shorter than before, open-ended
  being the longest) needs a ``dashboard_stepup.bindings`` row of kind
  ``mode_loosen``, not revoked, consumed by this transaction and not yet
  finished, whose target is ``mode:version:digest16``: the new version and the
  first 16 hex digits of the SHA-256 of ``duration|holds|senders`` (whole
  seconds or ``none``; each set sorted in C collation and comma-joined). The
  step-up's finish rule then binds its result to ``mode:version``. Without the
  step-up store, or without the privilege to read it, every loosening refuses.
* Safety and medication are never held; break-through senders are bounded,
  non-empty and free of the digest separators.
* No row is deleted or truncated (refused per statement, so it is never a
  silent no-op). The table's default runtime privileges are revoked first and
  nothing is granted: the overlay is dark until activation grants a writer.

The downgrade refuses while any row exists.
"""

from alembic import op

revision = "sw_076"
down_revision = "sw_075"
branch_labels = None
depends_on = None

_RUNTIME = "butler_switchboard_rw"
_MODES = "'asleep','travel','house_unattended','sick','focus','hosting','holiday','emergency'"
_HOLDABLE = "ARRAY['bill_due_48h','proactive','reply_to_person']::text[]"

_GUARD = """
    CREATE FUNCTION switchboard.owner_mode_overlay_guard()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    DECLARE
        old_version bigint;
        old_duration bigint;
        old_holds text[];
        old_senders text[];
        state text;
        target text;
        bound boolean;
    BEGIN
        IF cardinality(ARRAY(SELECT DISTINCT x FROM unnest(NEW.holds) x))
               <> cardinality(NEW.holds)
           OR cardinality(ARRAY(SELECT DISTINCT x FROM unnest(NEW.break_through) x))
               <> cardinality(NEW.break_through) THEN
            RAISE EXCEPTION 'An owner mode overlay set holds each value once'
                USING ERRCODE = 'check_violation';
        END IF;
        IF TG_OP = 'INSERT' THEN
            old_version := 0;
            old_duration := NULL;
            old_holds := CASE WHEN NEW.mode = 'sick' THEN ARRAY['proactive']::text[]
                              ELSE ARRAY[]::text[] END;
            old_senders := ARRAY[]::text[];
        ELSE
            IF NEW.mode IS DISTINCT FROM OLD.mode
               OR NEW.owner_action_id = OLD.owner_action_id THEN
                RAISE EXCEPTION 'An owner mode edit keeps its mode under a fresh owner action'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            old_version := OLD.version;
            old_duration := OLD.duration_s;
            old_holds := OLD.holds;
            old_senders := OLD.break_through;
        END IF;
        IF NEW.version IS DISTINCT FROM old_version + 1 THEN
            RAISE EXCEPTION 'Owner mode edits change one version at a time'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF old_holds <@ NEW.holds AND NEW.break_through <@ old_senders
           AND NOT (NEW.duration_s IS NOT NULL
                    AND (old_duration IS NULL OR NEW.duration_s < old_duration)) THEN
            RETURN NEW;
        END IF;
        IF to_regclass('dashboard_stepup.bindings') IS NULL THEN
            RAISE EXCEPTION 'Loosening an owner mode needs the dashboard step-up'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NOT has_schema_privilege('dashboard_stepup', 'USAGE')
           OR NOT has_table_privilege('dashboard_stepup.bindings', 'SELECT') THEN
            RAISE EXCEPTION 'Loosening an owner mode needs the dashboard step-up'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        state := COALESCE(NEW.duration_s::text, 'none') || '|'
            || array_to_string(ARRAY(SELECT x FROM unnest(NEW.holds) x
                                     ORDER BY x COLLATE "C"), ',') || '|'
            || array_to_string(ARRAY(SELECT x FROM unnest(NEW.break_through) x
                                     ORDER BY x COLLATE "C"), ',');
        target := NEW.mode || ':' || NEW.version::text || ':'
            || left(encode(sha256(convert_to(state, 'UTF8')), 'hex'), 16);
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM dashboard_stepup.bindings WHERE kind = '
            || quote_literal('mode_loosen') || ' AND target = $1 AND NOT revoked '
            || 'AND consumed_tx = txid_current() AND result IS NULL)'
            INTO bound USING target;
        IF NOT bound THEN
            RAISE EXCEPTION 'Loosening an owner mode needs the dashboard step-up'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END $$;
    CREATE FUNCTION switchboard.owner_mode_overlay_durable()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    BEGIN
        RAISE EXCEPTION 'The owner mode overlay is durable'
            USING ERRCODE = 'insufficient_privilege';
    END $$;
"""


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE switchboard.owner_mode_overlay (
            mode TEXT PRIMARY KEY CHECK (mode IN ({_MODES})),
            version BIGINT NOT NULL CHECK (version >= 1),
            duration_s BIGINT CHECK (duration_s IS NULL OR duration_s > 0),
            holds TEXT[] NOT NULL CHECK (
                holds <@ {_HOLDABLE} AND array_position(holds, NULL) IS NULL),
            break_through TEXT[] NOT NULL CHECK (
                cardinality(break_through) <= 64
                AND array_position(break_through, NULL) IS NULL
                AND array_position(break_through, '') IS NULL
                AND array_to_string(break_through, chr(31)) !~ '[,|]'),
            owner_action_id UUID NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute(_GUARD)
    op.execute(f"""
        CREATE TRIGGER owner_mode_overlay_guard
            BEFORE INSERT OR UPDATE ON switchboard.owner_mode_overlay
            FOR EACH ROW EXECUTE FUNCTION switchboard.owner_mode_overlay_guard();
        CREATE TRIGGER owner_mode_overlay_durable
            BEFORE DELETE OR TRUNCATE ON switchboard.owner_mode_overlay
            FOR EACH STATEMENT EXECUTE FUNCTION switchboard.owner_mode_overlay_durable();
        ALTER TABLE switchboard.owner_mode_overlay ENABLE ROW LEVEL SECURITY;
        REVOKE ALL ON switchboard.owner_mode_overlay FROM PUBLIC;
        -- The schema's default privileges grant the runtime role every table
        -- privilege; take them back. Nothing is granted until activation.
        REVOKE ALL ON switchboard.owner_mode_overlay FROM {_RUNTIME};
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF to_regclass('switchboard.owner_mode_overlay') IS NOT NULL
               AND EXISTS (SELECT 1 FROM switchboard.owner_mode_overlay) THEN
                RAISE EXCEPTION 'Cannot discard the owner mode overlay';
            END IF;
        END $$;
        DROP TABLE IF EXISTS switchboard.owner_mode_overlay;
        DROP FUNCTION IF EXISTS switchboard.owner_mode_overlay_guard();
        DROP FUNCTION IF EXISTS switchboard.owner_mode_overlay_durable();
    """)
