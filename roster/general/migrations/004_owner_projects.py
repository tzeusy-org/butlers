"""owner_projects: owner-authored, time-bounded project charters with typed links

Revision ID: gen_004
Revises: gen_003
Create Date: 2026-11-02 00:00:00.000000

bu-jv1moa.12 (Owner Projects slice 1; REQ-general-projects-*).  General holds
owner projects: owner-authored, time-bounded charters that group goals,
obligations and links; a project grants no authority.

- ``projects``: one row per Project, Responsibility or Decision.  It mirrors
  the current charter's title and horizon, carries ``state`` draft, closed or
  expired, and binds the create idempotency key to the immutable digest of the
  create request.  While a project is a draft its normalized title plus
  horizon is unique, so a repeated create converges on the same project.  A
  closed or expired project is frozen: no field changes and it never reopens.
- ``project_charter_versions``: insert-only charter history (title, horizon,
  objective, done condition, typed constraints, authenticated owner actor).
  UPDATE, DELETE and TRUNCATE are refused.
- ``project_links``: typed opaque references (deadline, owner_condition,
  trip, drive_file, calendar_event).  A link holds a kind and a record id
  only: no URL and no copied record content.  Removing a link never touches
  the referenced record.

Nothing here schedules, arms, wakes or grants anything.  Downgrade drops the
tables only while all three are empty; otherwise the charter history is
retained, inert, for a later re-upgrade to adopt.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "gen_004"
down_revision = "gen_003"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:gen_004:owner_projects', 0))"


def _schema() -> str:
    bind = op.get_bind()
    return bind.exec_driver_sql("SELECT current_schema()").scalar_one()


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def upgrade() -> None:
    op.execute(_LOCK)
    schema = _quote(_schema())

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            kind TEXT NOT NULL CHECK (kind IN ('project', 'responsibility', 'decision')),
            title TEXT NOT NULL
                CHECK (btrim(title) = title AND title <> '' AND length(title) <= 200),
            title_key TEXT GENERATED ALWAYS AS
                (lower(regexp_replace(title, '[[:space:]]+', ' ', 'g'))) STORED,
            current_charter_version INTEGER NOT NULL CHECK (current_charter_version >= 1),
            horizon_end TIMESTAMPTZ NOT NULL,
            state TEXT NOT NULL DEFAULT 'draft'
                CHECK (state IN ('draft', 'closed', 'expired')),
            closed_at TIMESTAMPTZ,
            create_idempotency_key TEXT NOT NULL
                CHECK (length(create_idempotency_key) BETWEEN 1 AND 200),
            create_request_digest TEXT NOT NULL CHECK (create_request_digest ~ '^[0-9a-f]{64}$'),
            created_by TEXT NOT NULL CHECK (created_by = 'owner'),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT projects_create_idempotency_key_key UNIQUE (create_idempotency_key),
            CONSTRAINT projects_closed_at_matches_state
                CHECK ((state = 'draft') = (closed_at IS NULL))
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS projects_draft_title_horizon_key
        ON projects (title_key, horizon_end) WHERE state = 'draft'
        """
    )

    # A closed or expired project is frozen; a draft's charter version only
    # advances one step at a time.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {schema}.projects_transition_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, pg_temp
        AS $fn$
        BEGIN
            IF OLD.state <> 'draft' THEN
                RAISE EXCEPTION 'a closed or expired project is frozen' USING ERRCODE = '55000';
            END IF;
            IF NEW.current_charter_version NOT IN (
                OLD.current_charter_version, OLD.current_charter_version + 1
            ) THEN
                RAISE EXCEPTION 'charter versions advance one step at a time'
                    USING ERRCODE = '55000';
            END IF;
            IF NEW.create_idempotency_key IS DISTINCT FROM OLD.create_idempotency_key
               OR NEW.create_request_digest IS DISTINCT FROM OLD.create_request_digest
               OR NEW.kind IS DISTINCT FROM OLD.kind
               OR NEW.created_by IS DISTINCT FROM OLD.created_by
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'project identity is immutable' USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $fn$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS projects_transition_guard ON projects")
    op.execute(
        f"""
        CREATE TRIGGER projects_transition_guard
        BEFORE UPDATE ON projects
        FOR EACH ROW EXECUTE FUNCTION {schema}.projects_transition_guard()
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS project_charter_versions (
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
            version INTEGER NOT NULL CHECK (version >= 1),
            title TEXT NOT NULL
                CHECK (btrim(title) = title AND title <> '' AND length(title) <= 200),
            horizon_end TIMESTAMPTZ NOT NULL,
            objective TEXT NOT NULL
                CHECK (btrim(objective) <> '' AND length(objective) <= 1000),
            done_condition TEXT NOT NULL
                CHECK (btrim(done_condition) <> '' AND length(done_condition) <= 1000),
            constraints JSONB NOT NULL CHECK (jsonb_typeof(constraints) = 'object'),
            created_by TEXT NOT NULL CHECK (created_by = 'owner'),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (project_id, version)
        )
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {schema}.project_charter_versions_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, pg_temp
        AS $fn$
        BEGIN
            RAISE EXCEPTION 'project charter versions are insert-only' USING ERRCODE = '55000';
        END;
        $fn$
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS project_charter_versions_immutable ON project_charter_versions"
    )
    op.execute(
        f"""
        CREATE TRIGGER project_charter_versions_immutable
        BEFORE UPDATE OR DELETE ON project_charter_versions
        FOR EACH ROW EXECUTE FUNCTION {schema}.project_charter_versions_immutable()
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS project_charter_versions_no_truncate ON project_charter_versions"
    )
    op.execute(
        f"""
        CREATE TRIGGER project_charter_versions_no_truncate
        BEFORE TRUNCATE ON project_charter_versions
        FOR EACH STATEMENT EXECUTE FUNCTION {schema}.project_charter_versions_immutable()
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS project_links (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
            ref_kind TEXT NOT NULL CHECK (
                ref_kind IN ('deadline', 'owner_condition', 'trip', 'drive_file', 'calendar_event')
            ),
            ref_id TEXT NOT NULL CHECK (ref_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
            created_by TEXT NOT NULL CHECK (created_by = 'owner'),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT project_links_ref_key UNIQUE (project_id, ref_kind, ref_id)
        )
        """
    )


def downgrade() -> None:
    op.execute(_LOCK)
    schema = _quote(_schema())

    # Durable owner records: dropped only when empty, otherwise retained inert.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM project_links)
               AND NOT EXISTS (SELECT 1 FROM project_charter_versions)
               AND NOT EXISTS (SELECT 1 FROM projects) THEN
                DROP TABLE project_links;
                DROP TABLE project_charter_versions;
                DROP TABLE projects;
                DROP FUNCTION IF EXISTS {schema}.project_charter_versions_immutable();
                DROP FUNCTION IF EXISTS {schema}.projects_transition_guard();
            END IF;
        END
        $$;
        """
    )
