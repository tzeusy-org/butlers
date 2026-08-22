"""runtime_probe_control_receipts: Switchboard-owned replay receipts.

Revision ID: core_201
Revises: core_200
Create Date: 2026-08-22 00:00:00.000000

REQ-database-security-008.  A runtime-probe control capability is a one-minute
signed JWS carrying a 256-bit nonce.  Signature checking alone cannot stop a
replay: two copies of one valid capability both verify.  What stops it is a
durable unique receipt, inserted and committed *before* catalog lookup, runtime
launch, or verification persistence, so exactly one of two concurrent uses gets
past receipt creation and a replay still fails after a Switchboard restart.

The row stores only what the replay decision needs::

    audience        TEXT        the one fixed control-plane audience
    nonce_digest    BYTEA       SHA-256 of the nonce -- never the raw nonce
    kid             TEXT        which deployment key signed it
    capability_exp  TIMESTAMPTZ the capability's own expiry
    received_at     TIMESTAMPTZ when Switchboard took the receipt

The raw nonce and the signature are deliberately absent: a leaked receipt table
must not be reconstructible into a usable capability.

``pk_runtime_probe_control_receipts`` on ``(audience, nonce_digest)`` is the
uniqueness the replay decision rides on.

Two boundaries beyond the grants:

* ``trg_runtime_probe_control_receipts_retention`` refuses a DELETE before
  ``capability_exp + 5s``, the accepted expiry plus the clock-skew allowance.
  A cleanup worker with a wrong predicate therefore fails loudly instead of
  quietly reopening a live replay window.
* Row security is ENABLEd with a ``current_user = 'butler_switchboard_rw'``
  policy.  The revokes alone would not hold: ``scripts/init-db.sql``
  deliberately re-grants broad DML on all public tables to every runtime role
  on each rerun (see its ALTER DEFAULT PRIVILEGES block), and a policy is not a
  grant, so a rerun cannot undo it.

  Row security is deliberately **not** FORCEd.  FORCE would additionally fence
  the table owner -- but the owner is the migration user, which is the same
  identity ``deploy/backup/pg_dump.sh`` dumps as, and ``init-db.sql`` pins that
  role ``NOSUPERUSER`` with no ``BYPASSRLS``.  ``pg_dump`` sets
  ``row_security = off``, which raises rather than silently filtering, and the
  backup script runs under ``set -o pipefail``; a forced table would abort the
  whole nightly dump.  So the owner is NOT fenced by this revision.  Fencing it
  needs a mechanism outside the backup path.

``public.record_runtime_probe_verification`` is the other half of the boundary:
a probe may write the four ``model_catalog`` verification columns and nothing
else, so a probe outcome can never reach ``enabled``, ``priority``, or breaker
state.  ``EXECUTE`` is revoked from ``PUBLIC`` before it is granted, and it is
granted only to Switchboard.

This is representation only.  No mount, endpoint, client, or caller is
activated by this revision.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_201"
down_revision = "core_200"
branch_labels = None
depends_on = None

_RECEIPTS = "public.runtime_probe_control_receipts"
_VERIFICATION_FUNCTION = "public.record_runtime_probe_verification(uuid, boolean, integer, text)"
_SWITCHBOARD_ROLE = "butler_switchboard_rw"
_CONTROL_AUDIENCE = "switchboard.runtime_probe_control.v1"

# Every runtime principal that init-db grants public-schema DML to.  Switchboard
# is deliberately absent: it is the one role that gets an explicit grant back.
_NON_SWITCHBOARD_RUNTIME_ROLES = (
    "butler_chronicler_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_qa_rw",
    "butler_relationship_rw",
    "butler_travel_rw",
    "butler_calendar_rw",
    "connector_writer",
    "restore_drill_executor",
)


def _for_existing_role(role: str, statement: str) -> None:
    """Run ``statement`` only when ``role`` exists in this cluster.

    Core migrations also run against fresh core-only databases where the full
    runtime-role set was never bootstrapped, so a missing role is expected
    rather than an error.
    """
    escaped = statement.replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                EXECUTE '{escaped}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {_RECEIPTS} (
            audience       TEXT        NOT NULL,
            nonce_digest   BYTEA       NOT NULL,
            kid            TEXT        NOT NULL,
            capability_exp TIMESTAMPTZ NOT NULL,
            received_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_runtime_probe_control_receipts
                PRIMARY KEY (audience, nonce_digest),
            CONSTRAINT ck_runtime_probe_control_receipts_audience
                CHECK (audience = '{_CONTROL_AUDIENCE}'),
            CONSTRAINT ck_runtime_probe_control_receipts_digest
                CHECK (octet_length(nonce_digest) = 32),
            CONSTRAINT ck_runtime_probe_control_receipts_kid
                CHECK (kid ~ '^[A-Za-z0-9._-]{{1,64}}$')
        )
    """)

    # Cleanup scans by retention bound, never by primary key.
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_runtime_probe_control_receipts_expiry
            ON {_RECEIPTS} (capability_exp)
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION public.runtime_probe_control_receipt_retention()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF OLD.capability_exp >= now() - INTERVAL '5 seconds' THEN
                RAISE EXCEPTION
                    'runtime-probe control receipt is still within its replay window';
            END IF;
            RETURN OLD;
        END
        $$;
    """)
    op.execute(f"""
        DROP TRIGGER IF EXISTS trg_runtime_probe_control_receipts_retention ON {_RECEIPTS}
    """)
    op.execute(f"""
        CREATE TRIGGER trg_runtime_probe_control_receipts_retention
            BEFORE DELETE ON {_RECEIPTS}
            FOR EACH ROW
            EXECUTE FUNCTION public.runtime_probe_control_receipt_retention()
    """)

    # Grants first: strip the broad public-schema DML that init-db's default
    # privileges hand to every runtime role at CREATE TABLE time.
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {_RECEIPTS} FROM PUBLIC")
    for role in _NON_SWITCHBOARD_RUNTIME_ROLES:
        _for_existing_role(role, f"REVOKE ALL PRIVILEGES ON TABLE {_RECEIPTS} FROM {role}")
    # No UPDATE: a receipt is append-only until its retention bound elapses.
    _for_existing_role(
        _SWITCHBOARD_ROLE,
        f"GRANT SELECT, INSERT, DELETE ON TABLE {_RECEIPTS} TO {_SWITCHBOARD_ROLE}",
    )

    # Then row security, which survives an init-db rerun that re-grants table
    # DML to every runtime role.  A policy is not a grant, so it is not
    # re-granted away.
    #
    # NOT forced, on purpose.  FORCE ROW LEVEL SECURITY would apply policies to
    # the table OWNER as well -- and the owner is the migration user, which is
    # exactly the identity deploy/backup/pg_dump.sh dumps as (init-db.sql
    # pins it NOSUPERUSER, so it has no BYPASSRLS).  pg_dump runs with
    # row_security = off, which RAISES rather than filtering when a policy
    # would apply, and pg_dump.sh uses `set -o pipefail`, so a forced table
    # would abort the entire nightly backup.  The owner is therefore not
    # fenced here; every non-owner runtime role is.
    op.execute(f"ALTER TABLE {_RECEIPTS} ENABLE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS runtime_probe_control_receipts_switchboard ON {_RECEIPTS}")
    op.execute(f"""
        CREATE POLICY runtime_probe_control_receipts_switchboard ON {_RECEIPTS}
            FOR ALL TO PUBLIC
            USING (current_user = '{_SWITCHBOARD_ROLE}')
            WITH CHECK (current_user = '{_SWITCHBOARD_ROLE}')
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION public.record_runtime_probe_verification(
            p_catalog_entry_id uuid,
            p_ok boolean,
            p_latency_ms integer,
            p_error text
        )
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            v_error text;
            v_latency integer;
        BEGIN
            IF p_catalog_entry_id IS NULL OR p_ok IS NULL THEN
                RAISE EXCEPTION 'runtime-probe verification requires an entry and an outcome';
            END IF;

            -- A success has no error to keep, and a caller-supplied dump is
            -- truncated the same way the dashboard verify path truncates it.
            IF p_ok THEN
                v_error := NULL;
            ELSE
                v_error := left(p_error, 4096);
            END IF;

            IF p_latency_ms IS NULL OR p_latency_ms < 0 THEN
                v_latency := NULL;
            ELSE
                v_latency := p_latency_ms;
            END IF;

            -- Verification evidence only.  enabled, priority, and every
            -- breaker-derived column are deliberately out of reach, so a probe
            -- success can never close a breaker.
            UPDATE public.model_catalog
               SET last_verified_at         = now(),
                   last_verified_latency_ms = v_latency,
                   last_verified_ok         = p_ok,
                   last_verified_error      = v_error,
                   updated_at               = now()
             WHERE id = p_catalog_entry_id;

            RETURN FOUND;
        END
        $$;
    """)
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {_VERIFICATION_FUNCTION} FROM PUBLIC")
    for role in _NON_SWITCHBOARD_RUNTIME_ROLES:
        _for_existing_role(
            role, f"REVOKE ALL PRIVILEGES ON FUNCTION {_VERIFICATION_FUNCTION} FROM {role}"
        )
    _for_existing_role(
        _SWITCHBOARD_ROLE,
        f"GRANT EXECUTE ON FUNCTION {_VERIFICATION_FUNCTION} TO {_SWITCHBOARD_ROLE}",
    )


def downgrade() -> None:
    # Nothing consumes receipts yet, so this rollback is genuinely available:
    # removing an unused representation loses no evidence.
    op.execute(f"DROP FUNCTION IF EXISTS {_VERIFICATION_FUNCTION}")
    op.execute(f"DROP POLICY IF EXISTS runtime_probe_control_receipts_switchboard ON {_RECEIPTS}")
    op.execute(
        f"DROP TRIGGER IF EXISTS trg_runtime_probe_control_receipts_retention ON {_RECEIPTS}"
    )
    op.execute("DROP FUNCTION IF EXISTS public.runtime_probe_control_receipt_retention()")
    op.execute("DROP INDEX IF EXISTS public.ix_runtime_probe_control_receipts_expiry")
    op.execute(f"DROP TABLE IF EXISTS {_RECEIPTS}")
