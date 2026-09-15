"""create_concierge_fleet_views

Revision ID: concierge_001
Revises:
Create Date: 2026-09-04 00:00:00.000000

Sanctioned cross-schema exception per RFC 0010, extended to fleet operational
telemetry by RFC 0030 (about/legends-and-lore/rfcs/0030-system-plane-read-
exception.md). Creates two read-only UNION views in the ``concierge`` schema:

  concierge.v_fleet_sessions — one row per session across every butler
      schema (running and completed), for the dashboard_read module's
      session-oriented tools (sessions_recent, session_detail, aggregates).
  concierge.v_fleet_spend    — one row per COMPLETED session across every
      butler schema (token counts only; dollar cost is computed downstream
      in Python from ``pricing.toml`` via ``estimate_session_cost``, never
      in SQL — this repo never stores a canonical dollar/cents column on
      ``sessions``, see ``src/butlers/core/pricing.py``).

Column allowlist (RFC 0030 guardrail 6 — enforced by view definition, not by
application code):

  source_butler   TEXT        — hardcoded string literal per UNION term
  id              UUID        — sessions.id
  started_at      TIMESTAMPTZ — sessions.started_at
  ended_at        TIMESTAMPTZ — sessions.completed_at
  status          TEXT        — derived: 'running' | 'success' | 'failed' | 'unknown'
  trigger_source  TEXT        — sessions.trigger_source
  model           TEXT        — sessions.model
  input_tokens    INTEGER     — sessions.input_tokens
  output_tokens   INTEGER     — sessions.output_tokens
  error_class     TEXT        — derived short classifier, NEVER the raw error
                                 message: only a value matching
                                 ^[A-Za-z_][A-Za-z0-9_.]{0,63}$ (e.g. an
                                 exception class name) passes through; every
                                 other non-null error collapses to 'other'.

Neither ``sessions.prompt`` nor ``sessions.result`` (nor any tool_calls/cost
JSONB payload) is ever selected into these views — see the DB security test
in ``roster/concierge/tests/test_dashboard_read.py`` for the assertion that
enforces this at the column-set level.

Access model (why ``butler_concierge_rw`` never gets a direct grant on any
other butler's ``sessions`` table): PostgreSQL views execute with the
privileges of their OWNER by default (no ``security_invoker`` option is set
here), and the migration/runtime user that creates these views already owns
every table in every schema. So ``butler_concierge_rw`` needs no grant at all
on e.g. ``health.sessions`` for the view to resolve — only ``SELECT`` on the
two views themselves. This is deliberate, not an oversight: it is what makes
"direct cross-schema SELECT is denied, but the sanctioned view succeeds" a
real, database-enforced property instead of an access path this role simply
chooses not to use. See the DB security test in
``roster/concierge/tests/test_dashboard_read.py``.

RFC 0010 guardrails applied (mirrors core_055/core_125, with guardrail 5
tightened per the access-model note above):
  1. Read-only SQL view — UNION view; PostgreSQL structurally rejects writes.
  2. Explicit source attribution — ``source_butler`` is a hardcoded literal
     per UNION term, never derived from row data.
  3. Filtered — ``v_fleet_spend`` additionally filters to
     ``completed_at IS NOT NULL`` (running sessions have incomplete token
     counts); callers apply their own date-range predicate on top.
  4. Health-check validated — the dashboard_read module's ``on_startup``
     probes both views before registering tools; a missing/ungranted view
     fails loudly rather than degrading to an empty result.
  5. Migration-based grants — the ONLY grant issued is ``SELECT`` on the two
     views, to ``butler_concierge_rw``, versioned here and reversible on
     downgrade. No grant on any other butler's underlying table is ever
     issued (see access model above).
  6. (RFC 0030 addition) Column allowlist — enumerated above; no
     prompt/result/tool_calls/cost column is ever selected.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "concierge_001"
down_revision = None
branch_labels = ("concierge",)
depends_on = None

# Every butler/staffer schema known to have its own ``sessions`` table
# (core_001's original role schemas, plus chronicler/qa/concierge which were
# added to the roster afterward — see core_089/core_055 precedent).
_FLEET_SCHEMAS: tuple[str, ...] = (
    "chronicler",
    "concierge",
    "education",
    "finance",
    "general",
    "health",
    "home",
    "lifestyle",
    "messenger",
    "qa",
    "relationship",
    "switchboard",
    "travel",
)

_CONCIERGE_ROLE = "butler_concierge_rw"

_SESSIONS_VIEW_FQN = "concierge.v_fleet_sessions"
_SPEND_VIEW_FQN = "concierge.v_fleet_spend"

# Only a short identifier-shaped prefix (e.g. an exception class name) may
# cross the schema boundary as `error_class`; anything else — including a
# bare message with no colon-prefixed classname — collapses to 'other'. This
# is what keeps free-text error content from ever leaving its owning schema.
_ERROR_CLASS_SQL = """
        CASE
            WHEN s.error IS NULL THEN NULL
            WHEN split_part(s.error, ':', 1) ~ '^[A-Za-z_][A-Za-z0-9_.]{0,63}$'
                THEN split_part(s.error, ':', 1)
            ELSE 'other'
        END"""

_STATUS_SQL = """
        CASE
            WHEN s.completed_at IS NULL THEN 'running'
            WHEN s.success IS TRUE THEN 'success'
            WHEN s.success IS FALSE THEN 'failed'
            ELSE 'unknown'
        END"""


def _sessions_union_term(schema: str) -> str:
    return f"""
        SELECT
            '{schema}'::text                  AS source_butler,
            s.id                               AS id,
            s.started_at                       AS started_at,
            s.completed_at                      AS ended_at,
            {_STATUS_SQL.strip()}              AS status,
            s.trigger_source                   AS trigger_source,
            s.model                            AS model,
            s.input_tokens                     AS input_tokens,
            s.output_tokens                    AS output_tokens,
            {_ERROR_CLASS_SQL.strip()}         AS error_class
        FROM {schema}.sessions s"""


def _spend_union_term(schema: str) -> str:
    return f"""
        SELECT
            '{schema}'::text    AS source_butler,
            s.id                AS id,
            s.started_at        AS started_at,
            s.completed_at       AS ended_at,
            s.model             AS model,
            s.input_tokens      AS input_tokens,
            s.output_tokens     AS output_tokens
        FROM {schema}.sessions s
        WHERE s.completed_at IS NOT NULL"""


def _grant_best_effort(table_or_view_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table/view TO role; tolerates missing role/object."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_or_view_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_or_view_fqn} TO "{role}"';
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
    # ------------------------------------------------------------------- #
    # 1. Create the two UNION views (RFC 0010 guardrails 1-3, RFC 0030
    #    guardrail 6 — see module docstring for the exact column allowlist).
    #    No grant on any other schema is needed first: the view is created
    #    by the migration user, which already owns every butler schema, and
    #    a non-security-invoker view executes with its owner's privileges
    #    (see the access-model note in the module docstring).
    # ------------------------------------------------------------------- #
    sessions_union = "\n        UNION ALL".join(_sessions_union_term(s) for s in _FLEET_SCHEMAS)
    op.execute(f"DROP VIEW IF EXISTS {_SESSIONS_VIEW_FQN} CASCADE")
    op.execute(f"CREATE VIEW {_SESSIONS_VIEW_FQN} AS{sessions_union}")

    spend_union = "\n        UNION ALL".join(_spend_union_term(s) for s in _FLEET_SCHEMAS)
    op.execute(f"DROP VIEW IF EXISTS {_SPEND_VIEW_FQN} CASCADE")
    op.execute(f"CREATE VIEW {_SPEND_VIEW_FQN} AS{spend_union}")

    # ------------------------------------------------------------------- #
    # 2. The ONLY grant: SELECT on the two views themselves. This is what
    #    RFC 0010 guardrail 5 requires here — no grant on any other
    #    butler's underlying table is ever issued (see access model above).
    # ------------------------------------------------------------------- #
    _grant_best_effort(_SESSIONS_VIEW_FQN, "SELECT", _CONCIERGE_ROLE)
    _grant_best_effort(_SPEND_VIEW_FQN, "SELECT", _CONCIERGE_ROLE)


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {_SPEND_VIEW_FQN}")
    op.execute(f"DROP VIEW IF EXISTS {_SESSIONS_VIEW_FQN}")
