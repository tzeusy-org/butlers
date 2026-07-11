"""v_qa_connector_state, v_qa_butler_heartbeat: infra-state QA discovery views.

Revision ID: sw_024
Revises: sw_023
Create Date: 2026-07-11 00:00:00.000000

bu-9r3hd.4 (epic bu-9r3hd "Deploy spine", slice 4/5) — per
openspec/changes/deploy-drift-sentinel/tasks.md's "Deferred" section: "feed
drift (and other infra-health signals: connector-offline, backup-stale,
heartbeat-stale) into the QA patrol's ``DiscoverySource`` pipeline as a
first-class discovery source." Every infra health signal was pull-only
(dashboard tiles only) — connectors have been silently dead for 7+ weeks with
nothing noticing (eco:reliability, 2026-07-10 JARVIS pursuit dossier).

Sanctioned cross-schema exception per RFC 0010 (sibling of core_055's
``public.v_qa_recent_failures`` and core_125's ``public.v_qa_tool_call_failures``).
Both connector liveness (``connector_registry``) and butler-daemon liveness
(``butler_registry``) live in the ``switchboard`` schema, not ``public`` —
outside the QA staffer's own schema-scoped role under hardened posture. These
two read-only views give ``InfraStateSource``
(``butlers.core.qa.sources.infra_state``) an auditable, migration-tracked
read path instead of a direct cross-schema query in application code.

Per standard Postgres view semantics, a view executes with the privileges of
its OWNER (not the querying role) for access to the underlying tables — so no
separate cross-schema GRANT on ``connector_registry`` / ``butler_registry`` is
required for ``butler_qa_rw`` to read them via these views; only SELECT on
the views themselves.

Scope note: this migration lives in the SWITCHBOARD chain, not the core
chain — ``connector_registry`` (``sw_012``/``sw_022``) and ``butler_registry``
(``sw_001``) are switchboard-schema tables, even though the views this
migration creates live in the shared ``public`` schema (mirrors ``sw_023``'s
same reasoning for ``routing_verdict_log``'s FK to ``public.ingestion_events``).
Placing it in the core chain instead would run once per butler schema via
``_migrate_all`` (core chain is schema-scoped, applied to every butler) and,
on a fresh database, would race ``connector_registry``/``butler_registry``
not existing yet for any butler processed before "switchboard" alphabetically
— the same table-doesn't-exist-yet hazard core_055 worked around for
``sessions`` with an ``IF NOT EXISTS`` stub. Living in the switchboard chain
instead means this migration always runs after ``connector_registry`` /
``butler_registry`` already exist (created earlier in this same chain), with
no ordering hazard at all.

View columns
------------
``public.v_qa_connector_state`` (from ``connector_registry``, excluding
soft-deleted and archived rows — an archived/superseded identity is a
deliberate operator action, not a failure):
  connector_type    TEXT
  endpoint_identity TEXT
  state             TEXT        — 'healthy' | 'degraded' | 'error' | 'paused' | 'unknown'
  error_message     TEXT
  last_heartbeat_at TIMESTAMPTZ
  first_seen_at     TIMESTAMPTZ

``public.v_qa_butler_heartbeat`` (from ``butler_registry``, all rows):
  name                 TEXT
  last_seen_at         TIMESTAMPTZ
  registered_at        TIMESTAMPTZ
  liveness_ttl_seconds INTEGER    — per-butler acceptable staleness window
  quarantined_at       TIMESTAMPTZ

RFC 0010 guardrails (mirrors core_055 / core_125):
  1. Read-only — plain SELECT views; PostgreSQL structurally rejects writes.
  2. Deterministic, zero-LLM — polled by a discovery source's discover(), pure SQL.
  3. Batch — polled once per QA patrol cycle (a fixed interval), not on-demand.
  4. Health-check validated by the consuming source before processing rows.
  5. Migration-based grants — tracked in VCS, reversible on downgrade.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_024"
down_revision = "sw_023"
branch_labels = None
depends_on = None

# The QA staffer runtime role that reads these views.
_QA_ROLE = "butler_qa_rw"

# Other butler roles that may read the views for observability (mirrors
# core_163's _ALL_RUNTIME_ROLES, minus the QA role tracked separately above
# and minus "switchboard" itself, which already owns the base tables).
_OTHER_BUTLER_ROLES = (
    "butler_chronicler_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
)

_CONNECTOR_VIEW_FQN = "public.v_qa_connector_state"
_HEARTBEAT_VIEW_FQN = "public.v_qa_butler_heartbeat"

# Unqualified table names: this chain's search_path is set to switchboard
# (see cli.py's _migrate_all -> schema_search_path), so connector_registry
# and butler_registry resolve to this same schema without qualification.
_CONNECTOR_VIEW_SQL = """
    SELECT
        connector_type,
        endpoint_identity,
        state,
        error_message,
        last_heartbeat_at,
        first_seen_at
    FROM connector_registry
    WHERE deleted_at IS NULL
      AND archived_at IS NULL
"""

_HEARTBEAT_VIEW_SQL = """
    SELECT
        name,
        last_seen_at,
        registered_at,
        liveness_ttl_seconds,
        quarantined_at
    FROM butler_registry
"""


def _grant_best_effort(view_fqn: str, role: str) -> None:
    """GRANT SELECT ON view TO role; tolerates a missing role or view."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{view_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT SELECT ON TABLE {view_fqn} TO "{role}"';
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
    op.execute(f"DROP VIEW IF EXISTS {_CONNECTOR_VIEW_FQN} CASCADE")
    op.execute(f"CREATE VIEW {_CONNECTOR_VIEW_FQN} AS{_CONNECTOR_VIEW_SQL}")

    op.execute(f"DROP VIEW IF EXISTS {_HEARTBEAT_VIEW_FQN} CASCADE")
    op.execute(f"CREATE VIEW {_HEARTBEAT_VIEW_FQN} AS{_HEARTBEAT_VIEW_SQL}")

    for role in (_QA_ROLE, *_OTHER_BUTLER_ROLES):
        _grant_best_effort(_CONNECTOR_VIEW_FQN, role)
        _grant_best_effort(_HEARTBEAT_VIEW_FQN, role)


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {_CONNECTOR_VIEW_FQN}")
    op.execute(f"DROP VIEW IF EXISTS {_HEARTBEAT_VIEW_FQN}")
