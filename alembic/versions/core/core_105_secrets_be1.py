"""secrets BE-1: secret_probe_log table, audit_log credential actions, ix_audit_log_target_ts.

Revision ID: core_105
Revises: core_104
Create Date: 2026-05-25 00:00:00.000000

Implements the three DB structures required by
``openspec/changes/redesign-secrets-passport/specs/core-credentials/spec.md``
(§§ public.secret_probe_log, Audit Action Enum Extension, audit_log index):

1.  ``public.secret_probe_log``
    Canonical cross-butler probe history table.  One row per probe call across
    all butlers.  Retention: ≥ 90 days (archive path not specified here).

    Columns
    -------
    id               BIGSERIAL PK
    credential_scope TEXT NOT NULL           one of 'user', 'system', 'cli'
    credential_key   TEXT NOT NULL           canonical key (provider slug / env var / runtime id)
    ok               BOOLEAN NOT NULL        probe outcome
    code             INTEGER NULL            HTTP/provider code
    latency_ms       INTEGER NULL            round-trip latency
    at               TIMESTAMPTZ NOT NULL    when the probe ran (server clock)
    message          TEXT NULL               verbatim error tail (≤ 512 chars)
    recorded_at      TIMESTAMPTZ NOT NULL    when the row was inserted

    Index
    -----
    ix_secret_probe_log_lookup  on (credential_scope, credential_key, recorded_at DESC)
    Supports "last N probes for this key" queries in < 5 ms at > 1 M rows.

2.  Audit action vocabulary extension
    ``public.audit_log.action`` is plain TEXT (no enum type, no check constraint).
    The credential-lifecycle actions below are now part of the valid action
    vocabulary.  They are documented here because the column carries no constraint
    to alter; see spec §Audit Action Enum Extension for authoritative list.

    New actions   Used by
    -----------   -------
    verified      Probe success
    failed        Probe failure
    rotated       Value replaced
    connected     OAuth dance completed
    disconnected  Credential explicitly disconnected
    warned        Scope mismatch / expiring-soon during probe
    overrode      System override created
    revoked       System override removed
    attempted     OAuth dance initiated but not yet completed
    set           New System secret created (first-time POST)

3.  Index  ix_audit_log_target_ts  on public.audit_log (target, ts DESC)
    Supports ``GET /api/audit-log?key=<key>`` filtering in O(log N) time even
    at > 1 M audit rows.  (Column 'ts' declared by core_092; this migration
    adds only the index.)
"""

from __future__ import annotations

from alembic import op

revision = "core_105"
down_revision = "core_104"
branch_labels = None
depends_on = None

_ALL_RUNTIME_ROLES = (
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
    "butler_switchboard_rw",
    "butler_travel_rw",
    "connector_writer",
)

_TABLE_PRIVILEGES = "SELECT, INSERT"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerate older DBs missing roles."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO {_quote_ident(role)}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. public.secret_probe_log
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.secret_probe_log (
            id               BIGSERIAL    PRIMARY KEY,
            credential_scope TEXT         NOT NULL,
            credential_key   TEXT         NOT NULL,
            ok               BOOLEAN      NOT NULL,
            code             INTEGER,
            latency_ms       INTEGER,
            at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
            message          TEXT,
            recorded_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
    """)

    # Fast "last N probes for (scope, key)" — spec requirement < 5 ms at > 1 M rows.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_secret_probe_log_lookup
        ON public.secret_probe_log (credential_scope, credential_key, recorded_at DESC)
    """)

    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.secret_probe_log", _TABLE_PRIVILEGES, role)

    # ------------------------------------------------------------------
    # 2. Audit action vocabulary (TEXT column — no DDL needed)
    #
    # public.audit_log.action is TEXT with no check constraint (core_092).
    # The credential-lifecycle action values (verified, failed, rotated,
    # connected, disconnected, warned, overrode, revoked, attempted, set)
    # are valid from this migration onward; no ALTER TABLE required.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 3. ix_audit_log_target_ts — supports ?key= filter in O(log N)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_audit_log_target_ts
        ON public.audit_log (target, ts DESC)
    """)


def downgrade() -> None:
    # Reverse the index on audit_log (safe — drop index only, no data loss).
    op.execute("DROP INDEX IF EXISTS public.ix_audit_log_target_ts")

    # Drop secret_probe_log and its index.
    op.execute("DROP INDEX IF EXISTS public.ix_secret_probe_log_lookup")
    op.execute("DROP TABLE IF EXISTS public.secret_probe_log")

    # The audit action vocabulary extension requires no DDL to reverse because
    # the column is TEXT without a check constraint.  Rows written with the new
    # action values remain in the table but cause no schema inconsistency after
    # downgrade — the column still accepts them.  This is acceptable: the
    # downgrade removes the structural additions (table + index) but cannot
    # erase already-written audit rows, which is consistent with the append-only
    # policy of audit_log.
