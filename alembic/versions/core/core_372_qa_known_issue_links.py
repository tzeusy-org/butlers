"""qa_known_issue_links: owner-asserted links from a finding to the work item tracking it.

Revision ID: core_372
Revises: core_371
Create Date: 2026-11-24 00:00:00.000000

bu-ejow5p.9. A finding already tracked by a bead, pull request or issue is held back by the
'tracked' triage gate instead of being investigated again: a fixed publisher would otherwise
open duplicate PRs racing a human-owned defect, and every breaker reset would be spent on known
work. One row links a fingerprint to one tracker ref; ``(fingerprint, tracker_ref)`` is unique so
a repeat link is an idempotent upsert.

``state`` is the link's lifecycle: ``tracked`` and ``fix_merged`` hold the finding back;
``awaiting_recovery`` re-admits a recurrence as a regression; ``regressed`` and ``closed`` no
longer gate. ``expires_at`` is required and at most 90 days after ``linked_at`` (a CHECK), so a
link cannot suppress forever.

The table is the QA suppression control, so its ACL is exact: the owner plus ``butler_qa_rw``
with SELECT, INSERT, UPDATE and DELETE, no grant option, and nothing for PUBLIC or any other role.
The core chain runs once per butler schema over the one shared ``public`` table. A run skips only
when the table exists with exactly that ACL; any drift reruns the idempotent DDL and converges the
ACL, and no privilege error is swallowed (core_370). Only the last schema to leave this revision
drops the table (core_246).
"""

from __future__ import annotations

from alembic import op

revision = "core_372"
down_revision = "core_371"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_372:qa_known_issue_links', 0))"
_TABLE = "public.qa_known_issue_links"
_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"
_QA_ROLE = "butler_qa_rw"

#: Catalog-only read: true when the table exists with exactly the intended ACL and no column ACL.
#: Any drift (an extra grantee, PUBLIC, a grant option, a missing or surplus privilege) is false,
#: so the run converges instead of skipping.
_ALREADY_INSTALLED = f"""
WITH rel AS (
    SELECT c.oid, c.relowner, c.relacl FROM pg_class c WHERE c.oid = to_regclass('{_TABLE}')
),
actual AS (
    SELECT a.grantee, a.privilege_type, a.is_grantable
    FROM rel CROSS JOIN LATERAL aclexplode(COALESCE(rel.relacl, acldefault('r', rel.relowner))) a
    WHERE a.grantee <> rel.relowner
),
expected AS (
    SELECT r.oid AS grantee, p.privilege_type, false AS is_grantable
    FROM pg_roles r
    CROSS JOIN (VALUES ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE')) AS p(privilege_type)
    WHERE r.rolname = '{_QA_ROLE}'
)
SELECT EXISTS (SELECT 1 FROM rel)
   AND NOT EXISTS (SELECT 1 FROM (TABLE actual EXCEPT TABLE expected) drift)
   AND NOT EXISTS (SELECT 1 FROM (TABLE expected EXCEPT TABLE actual) drift)
   AND NOT EXISTS (
       SELECT 1 FROM pg_attribute at, rel
       WHERE at.attrelid = rel.oid AND at.attnum > 0 AND at.attacl IS NOT NULL
   )
"""

#: Converge the ACL without swallowing anything: revoke every grantee but the owner and the QA
#: role (PUBLIC included), then give the QA role exactly the four privileges, grant option off.
_CONVERGE_ACL = f"""
DO $$
DECLARE
    _rel regclass := to_regclass('{_TABLE}');
    _grantee name;
BEGIN
    REVOKE ALL PRIVILEGES ON TABLE {_TABLE} FROM PUBLIC;
    FOR _grantee IN
        SELECT r.rolname
        FROM pg_class c
        CROSS JOIN LATERAL aclexplode(c.relacl) a
        JOIN pg_roles r ON r.oid = a.grantee
        WHERE c.oid = _rel AND a.grantee <> c.relowner AND r.rolname <> '{_QA_ROLE}'
    LOOP
        EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE %s FROM %I', _rel, _grantee);
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_QA_ROLE}') THEN
        REVOKE ALL PRIVILEGES ON TABLE {_TABLE} FROM {_QA_ROLE};
        GRANT {_PRIVILEGES} ON TABLE {_TABLE} TO {_QA_ROLE};
    END IF;
END $$
"""

_DDL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fingerprint   TEXT NOT NULL CHECK (btrim(fingerprint) <> ''),
    tracker_kind  TEXT NOT NULL CHECK (tracker_kind IN ('bead', 'github_pr', 'github_issue')),
    tracker_ref   TEXT NOT NULL CHECK (btrim(tracker_ref) <> ''),
    linked_by     TEXT NOT NULL CHECK (linked_by IN ('owner', 'qa_auto')),
    linked_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    fix_ref       TEXT,
    state         TEXT NOT NULL DEFAULT 'tracked' CHECK (
        state IN ('tracked', 'fix_merged', 'awaiting_recovery', 'regressed', 'closed')
    ),
    expires_at    TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_qa_known_issue_links_ref UNIQUE (fingerprint, tracker_ref),
    CONSTRAINT ck_qa_known_issue_links_expiry
        CHECK (expires_at <= linked_at + interval '90 days')
);
CREATE INDEX IF NOT EXISTS idx_qa_known_issue_links_gate
    ON {_TABLE} (fingerprint)
    WHERE state IN ('tracked', 'fix_merged', 'awaiting_recovery');
"""


def _installation_count() -> int:
    """Count schema-local core chains that still depend on this revision (core_246)."""
    bind = op.get_bind()
    schemas = bind.exec_driver_sql(
        """
        SELECT namespace.nspname
        FROM pg_class version_table
        JOIN pg_namespace namespace ON namespace.oid = version_table.relnamespace
        WHERE version_table.relname = 'alembic_version'
          AND version_table.relkind IN ('r', 'p')
          AND left(namespace.nspname, 3) <> 'pg_'
          AND namespace.nspname <> 'information_schema'
        """
    ).scalars()
    quote = bind.dialect.identifier_preparer.quote
    count = 0
    for schema in schemas:
        installed = bind.exec_driver_sql(
            f"""
            SELECT EXISTS (
                SELECT 1 FROM {quote(schema)}.alembic_version
                WHERE CASE WHEN version_num ~ '^core_[0-9]+$'
                    THEN substring(version_num FROM 6)::integer >= 372
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    if op.get_bind().exec_driver_sql(_ALREADY_INSTALLED).scalar():
        return
    op.execute(_DDL)
    # The table is the QA suppression control: a role that can write it can silence QA. Only
    # the QA butler's runtime role touches it (the dashboard API reaches it as the database
    # owner); every other butler and the connector writer get nothing.
    op.execute(_CONVERGE_ACL)


def downgrade() -> None:
    op.execute(_LOCK)
    # One shared table serves every schema still at core_372; only the last one drops it.
    if _installation_count() > 1:
        return
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
