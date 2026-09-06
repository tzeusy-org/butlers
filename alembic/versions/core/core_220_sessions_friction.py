"""sessions_friction: typed friction ledger derived at session close.

Revision ID: core_220
Revises: core_219
Create Date: 2026-09-05 00:00:00.000000

bu-8cdl1.9 S2 (friction ledger + outcome-carrying self-observation). Adds the
``sessions_friction`` table to the per-butler core chain (unqualified, so it
fans out to every butler schema via the existing search_path replay — see
``core_061_runtime_config.py`` for the same pattern).

One row per typed friction episode derived deterministically from a
completed session's ``success``/``error``/``model`` columns
(``src/butlers/core/sessions.py::_classify_friction_kind`` — no LLM
judgment). A clean session writes zero rows. ``(session_id, kind, ordinal)``
is the idempotence key: ``ordinal`` distinguishes multiple episodes of the
same kind for one session, reserved for future multi-episode derivation.

Grants a best-effort DML grant to every butler runtime role after creation,
mirroring ``core_210_expected_signals.py``. Without it, a disaster-recovery
replay of this revision through the trusted-bootstrap path (see
``core_196``) would leave the table owned by the bootstrap identity with no
grant back to the ordinary runtime roles, making it unreadable/unwritable
by every butler until repaired by hand — caught by
``test_core_migration_smoke_downgrade_upgrade_round_trip``, which replays
the head revision through that exact bootstrap path.
"""

from __future__ import annotations

from alembic import op

revision = "core_220"
down_revision = "core_219"
branch_labels = None
depends_on = None

_ALL_BUTLER_ROLES = (
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

_KINDS = (
    "degenerate_tool_loop",
    "guardrail_termination",
    "classification_timeout",
    "recovered_error",
    "dead_end",
)


def _grant_best_effort(role: str) -> None:
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE sessions_friction '
                        'TO "{role}"';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
    """)


def upgrade() -> None:
    kinds_list = ", ".join(f"'{kind}'" for kind in _KINDS)
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS sessions_friction (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id  UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            kind        TEXT NOT NULL,
            ordinal     INTEGER NOT NULL DEFAULT 0,
            detail      TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT sessions_friction_kind_check CHECK (kind IN ({kinds_list})),
            CONSTRAINT sessions_friction_session_kind_ordinal_key
                UNIQUE (session_id, kind, ordinal)
        )
    """)
    for role in _ALL_BUTLER_ROLES:
        _grant_best_effort(role)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sessions_friction")
