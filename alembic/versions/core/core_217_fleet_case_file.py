"""Fleet Case File: durable object for one correlated multi-butler situation.

Revision ID: core_217
Revises: core_215
Create Date: 2026-09-05 00:00:00.000000

bu-8cdl1.7 Slice 1 — see RFC 0032 (about/legends-and-lore/rfcs/0032-fleet-case-file.md)
for the full design. This slice adds schema only: ``public.fleet_cases``,
``public.fleet_case_evidence``, ``public.fleet_case_links``. No broker wiring,
no MCP tools, no dashboard surface ship in this slice.

``fleet_cases``/``fleet_case_links`` restrict INSERT/UPDATE to
``butler_switchboard_rw`` via row-level security policies keyed on
``current_user`` — mirroring ``core_210_expected_signals.py`` — because a bare
GRANT/REVOKE does not survive ``scripts/init-db.sql`` re-widening default
privileges on every rerun (see the "Fencing a `public` table to one runtime
role" note in AGENTS.md). ``fleet_case_evidence`` has no such restriction:
every runtime role may contribute evidence.

NOTE: at authoring time several sibling branches had already claimed
``core_215``/``core_216`` and RFC 0031 on as-yet-unmerged PRs. ``core_215``
(entity-graph-edges, RFC 0031) merged to ``main`` first, so this revision
was rebased to chain off it; ``core_216`` was still contested between other
sibling branches at that point, so this revision claims ``core_217`` to
avoid a second collision. Re-verify against ``alembic/versions/core/`` right
before merge and bump ``down_revision`` again if another revision lands
first (alembic revision chains must be linear).
"""

from __future__ import annotations

from alembic import op

revision = "core_217"
down_revision = "core_215"
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
)


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerate older DBs missing roles."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO "{role}"';
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
    # =========================================================================
    # 1. public.fleet_cases
    # =========================================================================
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.fleet_cases (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            correlation_key     TEXT NOT NULL,
            state               TEXT NOT NULL DEFAULT 'open',
            posture             TEXT NOT NULL DEFAULT 'silent',
            outcome             TEXT,
            opened_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            closed_at           TIMESTAMPTZ,
            CONSTRAINT chk_fleet_cases_correlation_key_nonempty
                CHECK (correlation_key <> ''),
            CONSTRAINT chk_fleet_cases_state
                CHECK (state IN ('open', 'watching', 'closing', 'closed')),
            CONSTRAINT chk_fleet_cases_posture
                CHECK (posture IN ('silent', 'routine', 'active', 'urgent')),
            CONSTRAINT chk_fleet_cases_closed_needs_outcome
                CHECK (
                    (state = 'closed' AND outcome IS NOT NULL AND closed_at IS NOT NULL)
                    OR (state <> 'closed' AND outcome IS NULL AND closed_at IS NULL)
                )
        )
        """
    )

    # At most one non-closed (active) case per correlation key — the DB-level
    # backstop for "one situation, one case".
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_fleet_cases_active_correlation_key
        ON public.fleet_cases (correlation_key)
        WHERE state <> 'closed'
        """
    )

    # Dashboard/lookup listing: recent-first per state.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fleet_cases_state_updated
        ON public.fleet_cases (state, updated_at DESC)
        """
    )

    op.execute("ALTER TABLE public.fleet_cases ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.fleet_cases FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS fleet_cases_read ON public.fleet_cases")
    op.execute("CREATE POLICY fleet_cases_read ON public.fleet_cases FOR SELECT USING (true)")
    op.execute("DROP POLICY IF EXISTS fleet_cases_insert_switchboard ON public.fleet_cases")
    op.execute(
        "CREATE POLICY fleet_cases_insert_switchboard ON public.fleet_cases "
        "FOR INSERT WITH CHECK (current_user = 'butler_switchboard_rw')"
    )
    op.execute("DROP POLICY IF EXISTS fleet_cases_update_switchboard ON public.fleet_cases")
    op.execute(
        "CREATE POLICY fleet_cases_update_switchboard ON public.fleet_cases "
        "FOR UPDATE USING (current_user = 'butler_switchboard_rw') "
        "WITH CHECK (current_user = 'butler_switchboard_rw')"
    )
    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.fleet_cases", "SELECT, INSERT, UPDATE", role)

    # =========================================================================
    # 2. public.fleet_case_evidence
    # =========================================================================
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.fleet_case_evidence (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            case_id             UUID NOT NULL REFERENCES public.fleet_cases(id)
                                ON DELETE CASCADE,
            contributor         TEXT NOT NULL,
            kind                TEXT NOT NULL,
            ref                 TEXT NOT NULL,
            payload             JSONB,
            contributed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_fleet_case_evidence_contributor_nonempty
                CHECK (contributor <> ''),
            CONSTRAINT chk_fleet_case_evidence_kind_nonempty
                CHECK (kind <> ''),
            CONSTRAINT chk_fleet_case_evidence_ref_nonempty
                CHECK (ref <> ''),
            CONSTRAINT uq_fleet_case_evidence_contributor
                UNIQUE (case_id, contributor, kind, ref)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fleet_case_evidence_case_id
        ON public.fleet_case_evidence (case_id, contributed_at DESC)
        """
    )

    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.fleet_case_evidence", "SELECT, INSERT", role)

    # =========================================================================
    # 3. public.fleet_case_links
    # =========================================================================
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.fleet_case_links (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            case_id             UUID NOT NULL REFERENCES public.fleet_cases(id)
                                ON DELETE CASCADE,
            link_kind           TEXT NOT NULL,
            ref                 TEXT NOT NULL,
            metadata            JSONB,
            linked_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_fleet_case_links_link_kind_nonempty
                CHECK (link_kind <> ''),
            CONSTRAINT chk_fleet_case_links_ref_nonempty
                CHECK (ref <> ''),
            CONSTRAINT uq_fleet_case_links_ref
                UNIQUE (case_id, link_kind, ref)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fleet_case_links_case_id
        ON public.fleet_case_links (case_id)
        """
    )

    op.execute("ALTER TABLE public.fleet_case_links ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.fleet_case_links FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS fleet_case_links_read ON public.fleet_case_links")
    op.execute(
        "CREATE POLICY fleet_case_links_read ON public.fleet_case_links FOR SELECT USING (true)"
    )
    op.execute(
        "DROP POLICY IF EXISTS fleet_case_links_insert_switchboard ON public.fleet_case_links"
    )
    op.execute(
        "CREATE POLICY fleet_case_links_insert_switchboard ON public.fleet_case_links "
        "FOR INSERT WITH CHECK (current_user = 'butler_switchboard_rw')"
    )
    op.execute(
        "DROP POLICY IF EXISTS fleet_case_links_update_switchboard ON public.fleet_case_links"
    )
    op.execute(
        "CREATE POLICY fleet_case_links_update_switchboard ON public.fleet_case_links "
        "FOR UPDATE USING (current_user = 'butler_switchboard_rw') "
        "WITH CHECK (current_user = 'butler_switchboard_rw')"
    )
    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.fleet_case_links", "SELECT, INSERT, UPDATE", role)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.fleet_case_links CASCADE")
    op.execute("DROP TABLE IF EXISTS public.fleet_case_evidence CASCADE")
    op.execute("DROP TABLE IF EXISTS public.fleet_cases CASCADE")
