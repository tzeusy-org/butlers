"""Owner mode entries: the owner enters and leaves a mode; leaving early needs step-up.

Revision ID: sw_077
Revises: sw_076

RFC 0038 S3.1 and owner decision bu-u33sb7 (bu-dhloi1.21 PR5). Only the owner
enters a mode (no entry is inferred; M7 owns any automatic rule). Exiting a
mode, or ending an active entry sooner, is a loosening and needs the dashboard
step-up; entering or extending does not.

``switchboard.owner_mode_entries`` holds one row per mode the owner has
entered, keyed by the mode ID and versioned: ``since``, the scheduled
``until`` (NULL is open-ended), the owner ``cause`` and ``exited_at``. A row is
active while it is not exited and its end is after the transaction's
``now()`` (``owner_mode_entry_active``); one ending exactly now has ended.

* A write moves exactly one version (an insert is version 1), keeps the mode
  and carries a fresh owner action.
* An inactive (never, exited or expired) mode takes only a fresh entry: started
  at ``now()``, not exited, ending after ``now()``. That needs nothing more.
* An active entry keeps its start. Extending it, or changing its owner cause,
  needs nothing more. Exiting it (stamping ``exited_at = now()`` and changing
  nothing else) or ending it sooner (a finite end earlier than before,
  open-ended being the longest) needs a ``dashboard_stepup.bindings`` row of
  kind ``mode_loosen``, not revoked, consumed by this transaction and not yet
  finished, whose target is ``mode:version:digest16``: the new version and the
  first 16 hex digits of the SHA-256 of ``owner_mode_entry_text``
  (``entry|since|until|cause|open-or-exited``, instants as whole microseconds
  since the Unix epoch, an open end as ``none``). The ``entry|`` prefix keeps
  every entry target apart from every sw_076 overlay target, which share the
  kind and the ``mode:version`` shape. The step-up's finish rule then binds
  its result to ``mode:version``. Without the step-up store, or without the
  privilege to read it, every loosening refuses.
* No row is deleted or truncated (refused per statement, so it is never a
  silent no-op). The table's default runtime privileges are revoked first and
  nothing is granted: the entries are dark until activation grants a writer.

The downgrade refuses while any row exists.
"""

from alembic import op

revision = "sw_077"
down_revision = "sw_076"
branch_labels = None
depends_on = None

_RUNTIME = "butler_switchboard_rw"
_MODES = "'asleep','travel','house_unattended','sick','focus','hosting','holiday','emergency'"

# Pure helpers: the trigger's single statement of each rule, and what the
# Python side (modes.entry_text, modes.active_entry) is pinned against.
_HELPERS = """
    CREATE FUNCTION switchboard.owner_mode_entry_active(
        exited_at timestamptz, until timestamptz, as_of timestamptz)
    RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
        SELECT exited_at IS NULL AND (until IS NULL OR until > as_of)
    $$;
    CREATE FUNCTION switchboard.owner_mode_entry_text(
        since timestamptz, until timestamptz, cause text, exited_at timestamptz)
    RETURNS text LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
        SELECT 'entry|' || (extract(epoch FROM since) * 1000000)::bigint::text || '|'
            || COALESCE((extract(epoch FROM until) * 1000000)::bigint::text, 'none') || '|'
            || cause || '|' || CASE WHEN exited_at IS NULL THEN 'open' ELSE 'exited' END
    $$;
"""

_GUARD = """
    CREATE FUNCTION switchboard.owner_mode_entries_guard()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    DECLARE
        as_of timestamptz := now();
        old_version bigint := 0;
        was_active boolean := false;
        loosens boolean;
        target text;
        bound boolean;
    BEGIN
        IF TG_OP = 'UPDATE' THEN
            IF NEW.mode IS DISTINCT FROM OLD.mode
               OR NEW.owner_action_id = OLD.owner_action_id THEN
                RAISE EXCEPTION 'An owner mode entry keeps its mode under a fresh owner action'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            old_version := OLD.version;
            was_active := switchboard.owner_mode_entry_active(OLD.exited_at, OLD.until, as_of);
        END IF;
        IF NEW.version IS DISTINCT FROM old_version + 1 THEN
            RAISE EXCEPTION 'Owner mode entries change one version at a time'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NOT was_active THEN
            IF NEW.since IS DISTINCT FROM as_of OR NEW.exited_at IS NOT NULL
               OR NOT (NEW.until IS NULL OR NEW.until > as_of) THEN
                RAISE EXCEPTION 'A mode is entered now, not exited, ending after now'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END IF;
        IF NEW.since IS DISTINCT FROM OLD.since THEN
            RAISE EXCEPTION 'An active mode entry keeps its start'
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.exited_at IS NOT NULL THEN
            IF NEW.exited_at IS DISTINCT FROM as_of OR NEW.until IS DISTINCT FROM OLD.until
               OR NEW.cause IS DISTINCT FROM OLD.cause THEN
                RAISE EXCEPTION 'An exit stamps now and changes nothing else'
                    USING ERRCODE = 'check_violation';
            END IF;
            loosens := true;
        ELSE
            IF NOT (NEW.until IS NULL OR NEW.until > as_of) THEN
                RAISE EXCEPTION 'An active mode entry ends after now'
                    USING ERRCODE = 'check_violation';
            END IF;
            loosens := NEW.until IS NOT NULL AND (OLD.until IS NULL OR NEW.until < OLD.until);
        END IF;
        IF NOT loosens THEN
            RETURN NEW;
        END IF;
        IF to_regclass('dashboard_stepup.bindings') IS NULL THEN
            RAISE EXCEPTION 'Leaving an owner mode early needs the dashboard step-up'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NOT has_schema_privilege('dashboard_stepup', 'USAGE')
           OR NOT has_table_privilege('dashboard_stepup.bindings', 'SELECT') THEN
            RAISE EXCEPTION 'Leaving an owner mode early needs the dashboard step-up'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        target := NEW.mode || ':' || NEW.version::text || ':'
            || left(encode(sha256(convert_to(switchboard.owner_mode_entry_text(
                NEW.since, NEW.until, NEW.cause, NEW.exited_at), 'UTF8')), 'hex'), 16);
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM dashboard_stepup.bindings WHERE kind = '
            || quote_literal('mode_loosen') || ' AND target = $1 AND NOT revoked '
            || 'AND consumed_tx = txid_current() AND result IS NULL)'
            INTO bound USING target;
        IF NOT bound THEN
            RAISE EXCEPTION 'Leaving an owner mode early needs the dashboard step-up'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END $$;
    CREATE FUNCTION switchboard.owner_mode_entries_durable()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    BEGIN
        RAISE EXCEPTION 'The owner mode entries are durable'
            USING ERRCODE = 'insufficient_privilege';
    END $$;
"""


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE switchboard.owner_mode_entries (
            mode TEXT PRIMARY KEY CHECK (mode IN ({_MODES})),
            version BIGINT NOT NULL CHECK (version >= 1),
            since TIMESTAMPTZ NOT NULL CHECK (isfinite(since)),
            until TIMESTAMPTZ CHECK (until IS NULL OR (isfinite(until) AND until > since)),
            cause TEXT NOT NULL CHECK (cause IN ('owner_declared', 'owner_confirmed')),
            exited_at TIMESTAMPTZ CHECK (exited_at IS NULL OR exited_at >= since),
            owner_action_id UUID NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute(_HELPERS)
    op.execute(_GUARD)
    op.execute(f"""
        CREATE TRIGGER owner_mode_entries_guard
            BEFORE INSERT OR UPDATE ON switchboard.owner_mode_entries
            FOR EACH ROW EXECUTE FUNCTION switchboard.owner_mode_entries_guard();
        CREATE TRIGGER owner_mode_entries_durable
            BEFORE DELETE OR TRUNCATE ON switchboard.owner_mode_entries
            FOR EACH STATEMENT EXECUTE FUNCTION switchboard.owner_mode_entries_durable();
        ALTER TABLE switchboard.owner_mode_entries ENABLE ROW LEVEL SECURITY;
        REVOKE ALL ON switchboard.owner_mode_entries FROM PUBLIC;
        -- The schema's default privileges grant the runtime role every table
        -- privilege; take them back. Nothing is granted until activation.
        REVOKE ALL ON switchboard.owner_mode_entries FROM {_RUNTIME};
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF to_regclass('switchboard.owner_mode_entries') IS NOT NULL
               AND EXISTS (SELECT 1 FROM switchboard.owner_mode_entries) THEN
                RAISE EXCEPTION 'Cannot discard the owner mode entries';
            END IF;
        END $$;
        DROP TABLE IF EXISTS switchboard.owner_mode_entries;
        DROP FUNCTION IF EXISTS switchboard.owner_mode_entries_guard();
        DROP FUNCTION IF EXISTS switchboard.owner_mode_entries_durable();
        DROP FUNCTION IF EXISTS switchboard.owner_mode_entry_text(
            timestamptz, timestamptz, text, timestamptz);
        DROP FUNCTION IF EXISTS switchboard.owner_mode_entry_active(
            timestamptz, timestamptz, timestamptz);
    """)
