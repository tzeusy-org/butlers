"""Single-source definitions for tables that tests provision by hand.

Some integration tests need one table from *another* butler's migration chain
without paying for that whole chain -- an endpoint under test reads
``connector_registry`` (switchboard), but the test itself is about core-chain
behaviour.  The historical answer was a per-file ``CREATE TABLE`` naming only
the columns that file's endpoint happened to query.

That answer drifts.  Each copy is correct on the day it is written, passes in
isolation forever after, and breaks silently the next time the real chain gains
a column: the endpoint's widened ``SELECT`` raises, the route returns its
DEGRADED envelope, and the test dies much later on a missing response key.  The
proximate symptom points at the assertion, never at the DDL.  ``sw_031`` cost
five of the nine failures on PR #3853 exactly this way (bu-r8opr).

So a stand-in is declared once, here, and imported.  Two guards in
``tests/config/test_schema_standin_parity.py`` keep that honest: one diffs each
declaration against the table the real chain builds (naming the drifted
columns, constraints and indexes), the other refuses a new hand-written copy
anywhere under ``tests/``.

A stand-in mirrors columns, primary keys, CHECK constraints and indexes -- the
surface a query binds to, and the surface that decides whether a row is
accepted.  Indexes belong in that set because a unique one is not decoration:
``approvals_013``'s ``ux_pending_actions_active_deduplication_key`` is what
enforces dedup among active rows, so a stand-in missing it accepts writes the
real schema rejects (bu-cwv9l).

Foreign keys stay out, on purpose, so each table remains independently
creatable.  ``pending_actions`` and ``approval_rules`` reference each other in
the real chain via a DEFERRABLE constraint, and mirroring that would leave
neither table creatable alone -- which is the entire point of a stand-in.

Triggers are excluded by default for the same independence boundary, but the
one self-contained trigger is an explicit exception:
``approvals_001``'s append-only guard on ``approval_events`` is declared here,
including its function body and execution metadata.  ``TableStandin.ddl``
renders that declaration with schema-qualified table and function identities,
so a fixture cannot accidentally bind it through a caller-controlled
``search_path``.  The other approvals triggers are named exclusions below:
``approvals_008``/``009``/``011`` replaced dropped foreign keys with plpgsql
guards that read a sibling table.  Installing those guards in an independent
stand-in would either fail to create or silently validate against whichever
schema a hostile ``search_path`` reached first.  Tests that need referential
integrity take the real chain via
:func:`butlers.testing.migration.create_migrated_test_db`.

Chains may be shared (``core``), roster (``switchboard``, ``relationship``) or
module (``approvals``, under ``src/butlers/modules/<chain>/migrations/``);
``create_migrated_test_db`` resolves all three by name.  A roster chain that
owns a schema names it in :attr:`TableStandin.chain_schemas`, because its
tables only reach :attr:`TableStandin.real_schema` when it is migrated under
that ``search_path``.

Usage::

    from butlers.testing.schema_standins import CONNECTOR_REGISTRY

    await pool.execute(CONNECTOR_REGISTRY.ddl())                     # public
    await pool.execute(CONNECTOR_REGISTRY.ddl(schema="switchboard"))  # qualified
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TriggerDefinition:
    """One self-contained trigger and the function it must bind locally.

    The migration parity guard compares the materialised ``pg_trigger`` and
    ``pg_proc`` records, not this declaration's spelling.  Keeping the
    execution metadata beside the body makes the mirror DDL explicit and
    prevents a fixture from quietly growing an unqualified trigger function.
    ``function_body`` is intentionally kept verbatim apart from PostgreSQL's
    own outer dollar-quote delimiters; parity only trims harmless outer
    whitespace and never rewrites SQL inside the body.
    """

    name: str
    function_name: str
    function_body: str
    timing: str
    events: tuple[str, ...]
    level: str = "ROW"
    when: str | None = None
    arguments: tuple[str, ...] = ()
    is_constraint: bool = False
    deferrable: bool = False
    initially_deferred: bool = False
    function_language: str = "plpgsql"
    security_definer: bool = False
    function_config: tuple[str, ...] = ()

    @property
    def function(self) -> str:
        """Backward-readable alias for the unqualified function name."""
        return self.function_name

    def ddl(self, *, schema: str, table: str) -> str:
        """Render schema-qualified function and trigger DDL for *table*."""
        function = f"{schema}.{self.function_name}"
        qualified_table = f"{schema}.{table}"
        function_lines = [
            f"CREATE OR REPLACE FUNCTION {function}()",
            "RETURNS trigger",
            f"LANGUAGE {self.function_language}",
        ]
        if self.security_definer:
            function_lines.append("SECURITY DEFINER")
        function_lines.extend(f"SET {setting}" for setting in self.function_config)
        function_lines.append(f"AS $function${self.function_body}$function$")

        trigger_kind = "CREATE CONSTRAINT TRIGGER" if self.is_constraint else "CREATE TRIGGER"
        events = " OR ".join(self.events)
        drop_trigger = f"DROP TRIGGER IF EXISTS {self.name} ON {qualified_table};"
        trigger_lines = [
            f"{trigger_kind} {self.name}",
            f"{self.timing} {events} ON {qualified_table}",
        ]
        if self.deferrable:
            trigger_lines.append(
                "DEFERRABLE INITIALLY DEFERRED" if self.initially_deferred else "DEFERRABLE"
            )
        elif self.initially_deferred:
            raise ValueError(f"{self.name} cannot be initially deferred while not deferrable")
        trigger_lines.append(f"FOR EACH {self.level}")
        if self.when is not None:
            trigger_lines.append(f"WHEN ({self.when})")
        arguments = ", ".join(self.arguments)
        trigger_lines.append(f"EXECUTE FUNCTION {function}({arguments})")
        create_trigger = "\n".join(trigger_lines) + ";"
        return "\n".join(function_lines) + ";\n" + drop_trigger + "\n" + create_trigger


@dataclass(frozen=True)
class TriggerExclusion:
    """A named real-chain trigger deliberately absent from a stand-in."""

    name: str
    migration: str
    reason: str


@dataclass(frozen=True)
class TableStandin:
    """One table's test-side shape, plus where the real definition lives."""

    table: str
    """Unqualified table name, identical to the migration chain's."""

    chains: tuple[str, ...]
    """Migration chains that must run for the real table to exist."""

    real_schema: str
    """Schema the real table lands in when :attr:`chains` run in a test DB."""

    constant_path: str
    """Where a reconciling engineer edits this declaration."""

    columns: tuple[tuple[str, str], ...]
    """``(name, type-and-column-constraints)`` in migration-chain order."""

    table_constraints: tuple[str, ...] = ()
    """Table-level constraint clauses (primary key, checks)."""

    indexes: tuple[str, ...] = ()
    """``CREATE INDEX`` statements, with ``{table}`` for the qualified name."""

    chain_schemas: tuple[tuple[str, str], ...] = ()
    """``(chain, schema)`` pairs a chain must be migrated under, if any.

    A roster chain that owns its own schema (``relationship``) only puts its
    tables in :attr:`real_schema` when it is migrated with that ``search_path``.
    Stated per stand-in rather than inferred from :attr:`real_schema`, because
    a chain and the schema it lands in are two different facts and only the
    declaration knows both.
    """

    triggers: tuple[TriggerDefinition, ...] = ()
    """Self-contained triggers to render after the table and indexes."""

    excluded_triggers: tuple[TriggerExclusion, ...] = ()
    """Named real-chain triggers intentionally omitted for independence."""

    def ddl(self, *, schema: str | None = None) -> str:
        """Return table, index and declared-trigger DDL, optionally qualified.

        The result is a single script, which both asyncpg's argument-free
        ``execute`` and psycopg2 run as one call.  A stand-in with triggers is
        qualified to its real schema even when the caller omits ``schema``;
        this keeps its function/table binding deterministic under an altered
        ``search_path``.  FK-substitute triggers are never rendered: they are
        recorded in :attr:`excluded_triggers` and owned by real-chain tests.
        """
        trigger_schema = schema or self.real_schema
        qualified = f"{schema}.{self.table}" if schema else self.table
        if self.triggers and schema is None:
            qualified = f"{trigger_schema}.{self.table}"
        clauses = [f"{name} {definition}" for name, definition in self.columns]
        clauses.extend(self.table_constraints)
        body = ",\n    ".join(clauses)
        statements = [f"CREATE TABLE IF NOT EXISTS {qualified} (\n    {body}\n)"]
        statements += [index.replace("{table}", qualified) for index in self.indexes]
        statements.extend(
            trigger.ddl(schema=trigger_schema, table=self.table) for trigger in self.triggers
        )
        return ";\n".join(statements)


CONNECTOR_REGISTRY = TableStandin(
    table="connector_registry",
    chains=("core", "switchboard"),
    real_schema="public",
    constant_path="src/butlers/testing/schema_standins.py::CONNECTOR_REGISTRY",
    # Mirrors roster/switchboard/migrations: sw_002 (base table), sw_012
    # (deleted_at, replay_safe), sw_022 (archived_at), sw_031 (operational_role,
    # parent_endpoint_identity, valid_operational_role).
    columns=(
        ("connector_type", "TEXT NOT NULL"),
        ("endpoint_identity", "TEXT NOT NULL"),
        ("instance_id", "UUID"),
        ("version", "TEXT"),
        ("state", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("error_message", "TEXT"),
        ("uptime_s", "INTEGER"),
        ("last_heartbeat_at", "TIMESTAMPTZ"),
        ("first_seen_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
        ("registered_via", "TEXT NOT NULL DEFAULT 'self'"),
        ("counter_messages_ingested", "BIGINT DEFAULT 0"),
        ("counter_messages_failed", "BIGINT DEFAULT 0"),
        ("counter_source_api_calls", "BIGINT DEFAULT 0"),
        ("counter_checkpoint_saves", "BIGINT DEFAULT 0"),
        ("counter_dedupe_accepted", "BIGINT DEFAULT 0"),
        ("checkpoint_cursor", "TEXT"),
        ("checkpoint_updated_at", "TIMESTAMPTZ"),
        ("capabilities", "JSONB DEFAULT NULL"),
        ("settings", "JSONB DEFAULT NULL"),
        ("deleted_at", "TIMESTAMPTZ NULL"),
        ("replay_safe", "BOOLEAN NOT NULL DEFAULT TRUE"),
        ("archived_at", "TIMESTAMPTZ NULL"),
        ("operational_role", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("parent_endpoint_identity", "TEXT NULL"),
    ),
    table_constraints=(
        "PRIMARY KEY (connector_type, endpoint_identity)",
        (
            "CONSTRAINT valid_operational_role CHECK ("
            "operational_role IN ('runtime_instance', 'checkpoint', 'unknown'))"
        ),
    ),
    # sw_002 (three lookup indexes), sw_012 (active), sw_022 (live).
    indexes=(
        "CREATE INDEX IF NOT EXISTS ix_connector_registry_last_heartbeat_at "
        "ON {table} (last_heartbeat_at DESC) WHERE last_heartbeat_at IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS ix_connector_registry_state_last_heartbeat "
        "ON {table} (state, last_heartbeat_at DESC)",
        "CREATE INDEX IF NOT EXISTS ix_connector_registry_connector_type "
        "ON {table} (connector_type)",
        "CREATE INDEX IF NOT EXISTS ix_connector_registry_active "
        "ON {table} (connector_type, endpoint_identity) WHERE deleted_at IS NULL",
        "CREATE INDEX IF NOT EXISTS ix_connector_registry_live "
        "ON {table} (connector_type, endpoint_identity) "
        "WHERE deleted_at IS NULL AND archived_at IS NULL",
    ),
)


# The approvals tables come from a MODULE chain
# (src/butlers/modules/approvals/migrations/), not a roster one: approvals_001
# creates all five, 003 adds fingerprint versions and their CHECKs/indexes, 005
# adds pending-action blast_radius/reversibility and their CHECKs, 007 adds the
# suggestion source-action link, 012 adds the 'abandoned' status and
# 'action_abandoned' event type, 013 adds deduplication_key, and 014 adds origin.
PENDING_ACTIONS = TableStandin(
    table="pending_actions",
    chains=("core", "approvals"),
    real_schema="public",
    constant_path="src/butlers/testing/schema_standins.py::PENDING_ACTIONS",
    # approvals_001 order, then the columns later revisions appended.
    columns=(
        ("id", "UUID PRIMARY KEY DEFAULT gen_random_uuid()"),
        ("tool_name", "TEXT NOT NULL"),
        ("tool_args", "JSONB NOT NULL"),
        ("agent_summary", "TEXT"),
        ("session_id", "UUID"),
        ("status", "VARCHAR NOT NULL DEFAULT 'pending'"),
        ("requested_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
        ("expires_at", "TIMESTAMPTZ"),
        ("decided_by", "TEXT"),
        ("decided_at", "TIMESTAMPTZ"),
        ("execution_result", "JSONB"),
        ("why", "TEXT"),
        ("evidence", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
        ("approval_rule_id", "UUID"),
        ("blast_radius", "TEXT"),
        ("reversibility", "TEXT"),
        ("deduplication_key", "TEXT"),
        ("origin", "TEXT"),
    ),
    table_constraints=(
        (
            "CONSTRAINT pending_actions_status_check CHECK (status IN ("
            "'pending', 'approved', 'rejected', 'expired', 'executed', 'abandoned'))"
        ),
        (
            "CONSTRAINT pending_actions_blast_radius_check CHECK ("
            "blast_radius IS NULL OR blast_radius IN "
            "('none', 'self', 'contact', 'external'))"
        ),
        (
            "CONSTRAINT pending_actions_reversibility_check CHECK ("
            "reversibility IS NULL OR reversibility IN "
            "('reversible', 'compensable', 'irreversible'))"
        ),
        (
            "CONSTRAINT pending_actions_origin_check CHECK ("
            "origin IS NULL OR origin IN ('prepared'))"
        ),
    ),
    # approvals_001 (two lookup indexes), approvals_013 (dedup uniqueness).
    # The unique one is behaviour: it is what makes a second active action with
    # the same deduplication_key fail, so a stand-in without it lets a test
    # write rows production would reject.
    indexes=(
        "CREATE INDEX IF NOT EXISTS idx_pending_actions_status_requested "
        "ON {table} (status, requested_at)",
        "CREATE INDEX IF NOT EXISTS idx_pending_actions_session_id ON {table} (session_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_pending_actions_active_deduplication_key "
        "ON {table} (deduplication_key) WHERE deduplication_key IS NOT NULL "
        "AND status IN ('pending', 'approved', 'rejected', 'abandoned')",
    ),
)


AUTONOMY_APPROVAL_HISTORY = TableStandin(
    table="autonomy_approval_history",
    chains=("core", "approvals"),
    real_schema="public",
    constant_path="src/butlers/testing/schema_standins.py::AUTONOMY_APPROVAL_HISTORY",
    # approvals_001 base table, then approvals_003 fingerprint version.
    columns=(
        ("id", "UUID PRIMARY KEY DEFAULT gen_random_uuid()"),
        ("pattern_fingerprint", "VARCHAR(64) NOT NULL"),
        ("tool_name", "TEXT NOT NULL"),
        ("tool_args", "JSONB NOT NULL"),
        ("action_id", "UUID"),
        ("approved_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
        ("time_to_decision_seconds", "DOUBLE PRECISION"),
        ("fingerprint_version", "SMALLINT NOT NULL DEFAULT 1"),
    ),
    table_constraints=(
        (
            "CONSTRAINT autonomy_approval_history_fingerprint_version_check "
            "CHECK (fingerprint_version IN (1, 2))"
        ),
    ),
    indexes=(
        "CREATE INDEX IF NOT EXISTS idx_autonomy_history_fingerprint "
        "ON {table} (pattern_fingerprint)",
        "CREATE INDEX IF NOT EXISTS idx_autonomy_history_fingerprint_approved_at "
        "ON {table} (pattern_fingerprint, approved_at)",
        "CREATE INDEX IF NOT EXISTS idx_autonomy_history_fingerprint_version "
        "ON {table} (pattern_fingerprint, fingerprint_version)",
    ),
)


AUTONOMY_SUGGESTIONS = TableStandin(
    table="autonomy_suggestions",
    chains=("core", "approvals"),
    real_schema="public",
    constant_path="src/butlers/testing/schema_standins.py::AUTONOMY_SUGGESTIONS",
    # approvals_001 base table, approvals_003 fingerprint version, then the
    # approvals_007 source-action link in migration order.
    columns=(
        ("id", "UUID PRIMARY KEY DEFAULT gen_random_uuid()"),
        ("suggestion_type", "VARCHAR NOT NULL DEFAULT 'promotion'"),
        ("pattern_fingerprint", "VARCHAR(64) NOT NULL"),
        ("tool_name", "TEXT NOT NULL"),
        ("representative_args", "JSONB NOT NULL"),
        ("status", "VARCHAR NOT NULL DEFAULT 'pending'"),
        ("approval_count_at_creation", "INTEGER NOT NULL DEFAULT 0"),
        ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
        ("decided_at", "TIMESTAMPTZ"),
        ("decided_by", "TEXT"),
        ("resulting_rule_id", "UUID"),
        ("cooldown_until", "TIMESTAMPTZ"),
        ("dismissal_reason", "TEXT"),
        ("fingerprint_version", "SMALLINT NOT NULL DEFAULT 1"),
        ("action_id", "UUID"),
    ),
    table_constraints=(
        (
            "CONSTRAINT autonomy_suggestions_type_check CHECK (suggestion_type IN "
            "('promotion', 'demotion'))"
        ),
        (
            "CONSTRAINT autonomy_suggestions_status_check CHECK (status IN "
            "('pending', 'confirmed', 'dismissed', 'superseded'))"
        ),
        (
            "CONSTRAINT autonomy_suggestions_fingerprint_version_check "
            "CHECK (fingerprint_version IN (1, 2))"
        ),
    ),
    indexes=(
        "CREATE INDEX IF NOT EXISTS idx_autonomy_suggestions_fingerprint "
        "ON {table} (pattern_fingerprint)",
        "CREATE INDEX IF NOT EXISTS idx_autonomy_suggestions_status_created "
        "ON {table} (status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_autonomy_suggestions_fingerprint_version "
        "ON {table} (pattern_fingerprint, fingerprint_version)",
        "CREATE INDEX IF NOT EXISTS idx_autonomy_suggestions_action_id ON {table} (action_id)",
    ),
)


APPROVAL_RULES = TableStandin(
    table="approval_rules",
    chains=("core", "approvals"),
    real_schema="public",
    constant_path="src/butlers/testing/schema_standins.py::APPROVAL_RULES",
    columns=(
        ("id", "UUID PRIMARY KEY DEFAULT gen_random_uuid()"),
        ("tool_name", "TEXT NOT NULL"),
        ("arg_constraints", "JSONB NOT NULL"),
        ("description", "TEXT NOT NULL"),
        ("created_from", "UUID"),
        ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
        ("expires_at", "TIMESTAMPTZ"),
        ("max_uses", "INT"),
        ("use_count", "INT NOT NULL DEFAULT 0"),
        ("active", "BOOL NOT NULL DEFAULT true"),
    ),
    indexes=(
        "CREATE INDEX IF NOT EXISTS idx_approval_rules_tool_active ON {table} (tool_name, active)",
    ),
    excluded_triggers=(
        TriggerExclusion(
            name="trg_approval_rules_created_from_reference",
            migration="approvals_009",
            reason=(
                "FK substitute reads pending_actions, a sibling omitted so this "
                "stand-in remains independently creatable"
            ),
        ),
    ),
)


_APPROVAL_EVENTS_IMMUTABILITY_TRIGGER = TriggerDefinition(
    name="trg_approval_events_immutable",
    function_name="prevent_approval_events_mutation",
    # Keep the migration's dollar-quoted body intact.  The parity normalizer
    # trims only this outer whitespace; it must never rewrite literals or SQL
    # identifiers inside ``prosrc``.
    function_body="""
        BEGIN
            RAISE EXCEPTION 'approval_events is append-only: % is not allowed', TG_OP;
        END;
        """,
    timing="BEFORE",
    events=("UPDATE", "DELETE"),
    level="ROW",
)


APPROVAL_EVENTS = TableStandin(
    table="approval_events",
    chains=("core", "approvals"),
    real_schema="public",
    constant_path="src/butlers/testing/schema_standins.py::APPROVAL_EVENTS",
    columns=(
        ("event_id", "UUID PRIMARY KEY DEFAULT gen_random_uuid()"),
        ("action_id", "UUID"),
        ("rule_id", "UUID"),
        ("event_type", "TEXT NOT NULL"),
        ("actor", "TEXT NOT NULL"),
        ("reason", "TEXT"),
        ("event_metadata", "JSONB NOT NULL DEFAULT '{}'::jsonb"),
        ("occurred_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
    ),
    table_constraints=(
        (
            "CONSTRAINT approval_events_type_check CHECK (event_type IN ("
            "'action_queued', 'action_auto_approved', 'action_approved', "
            "'action_rejected', 'action_expired', 'action_abandoned', "
            "'action_execution_succeeded', 'action_execution_failed', "
            "'rule_created', 'rule_revoked', "
            "'promotion_suggested', 'promotion_confirmed', 'promotion_dismissed', "
            "'promotion_superseded', 'demotion_suggested', 'demotion_confirmed', "
            "'demotion_dismissed'))"
        ),
        (
            "CONSTRAINT approval_events_link_check CHECK ("
            "action_id IS NOT NULL OR rule_id IS NOT NULL OR event_type IN ("
            "'promotion_suggested', 'promotion_confirmed', 'promotion_dismissed', "
            "'promotion_superseded', 'demotion_suggested', 'demotion_confirmed', "
            "'demotion_dismissed'))"
        ),
    ),
    indexes=(
        "CREATE INDEX IF NOT EXISTS idx_approval_events_action_id ON {table} (action_id)",
        "CREATE INDEX IF NOT EXISTS idx_approval_events_rule_id ON {table} (rule_id)",
        "CREATE INDEX IF NOT EXISTS idx_approval_events_occurred_at ON {table} (occurred_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_approval_events_event_type ON {table} (event_type)",
    ),
    triggers=(_APPROVAL_EVENTS_IMMUTABILITY_TRIGGER,),
    excluded_triggers=(
        TriggerExclusion(
            name="trg_approval_events_action_reference",
            migration="approvals_008",
            reason=(
                "FK substitute reads pending_actions, a sibling omitted so this "
                "stand-in remains independently creatable"
            ),
        ),
        TriggerExclusion(
            name="trg_approval_events_rule_reference",
            migration="approvals_011",
            reason=(
                "FK substitute reads approval_rules, a sibling omitted so this "
                "stand-in remains independently creatable"
            ),
        ),
    ),
)


# The relationship chain owns its own schema. rel_014 explicitly qualifies
# ``entity_predicate_registry``; rel_029 leaves ``contact_entity_map``
# unqualified, so it lands in ``relationship`` only when the chain is migrated
# with that ``search_path``. ``chain_schemas`` records that chain-level
# requirement for the parity fixture; each test fixture chooses DDL
# qualification for its own pool. The registry is qualified at every call
# site; ``contact_entity_map`` is qualified in relationship-schema fixtures and
# left unqualified only where the test pool's default schema is public.
ENTITY_PREDICATE_REGISTRY = TableStandin(
    table="entity_predicate_registry",
    chains=("core", "relationship"),
    real_schema="relationship",
    chain_schemas=(("relationship", "relationship"),),
    constant_path="src/butlers/testing/schema_standins.py::ENTITY_PREDICATE_REGISTRY",
    # roster/relationship/migrations: rel_014 (base table), rel_015 (widens the
    # kind CHECK with 'state'), rel_021 (cardinality), rel_022 (seeds a row).
    columns=(
        ("predicate", "TEXT NOT NULL PRIMARY KEY"),
        (
            "kind",
            "TEXT NOT NULL CHECK (kind IN ('contact', 'relational', 'override', 'state'))",
        ),
        ("object_kind", "TEXT NOT NULL CHECK (object_kind IN ('literal', 'entity'))"),
        ("description", "TEXT"),
        ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
        (
            "cardinality",
            "TEXT NOT NULL DEFAULT 'multi' CHECK (cardinality IN ('single', 'multi'))",
        ),
    ),
)
"""The predicate vocabulary ``assert_fact`` validates against.

``cardinality`` is behaviour, not decoration: ``single`` is what makes a second
assertion of the same predicate supersede the first rather than accumulate
(rel_021), so a stand-in that omits it lets a test observe multi-valued
behaviour the real schema does not have.
"""


CONTACT_ENTITY_MAP = TableStandin(
    table="contact_entity_map",
    chains=("core", "relationship"),
    real_schema="relationship",
    chain_schemas=(("relationship", "relationship"),),
    constant_path="src/butlers/testing/schema_standins.py::CONTACT_ENTITY_MAP",
    # roster/relationship/migrations/029_contact_entity_map.py, unchanged since.
    # rel_029 deliberately declares no FK on either column, so this stand-in is
    # a complete mirror rather than one narrowed by the no-FK rule above.
    columns=(
        ("contact_id", "UUID NOT NULL"),
        ("entity_id", "UUID NOT NULL"),
    ),
    table_constraints=("CONSTRAINT contact_entity_map_pkey PRIMARY KEY (contact_id)",),
    indexes=("CREATE INDEX IF NOT EXISTS idx_contact_entity_map_entity_id ON {table} (entity_id)",),
)


ENTITY_GRAPH_EDGES = TableStandin(
    table="entity_graph_edges",
    chains=("core",),
    real_schema="public",
    constant_path="src/butlers/testing/schema_standins.py::ENTITY_GRAPH_EDGES",
    # alembic/versions/core/core_215_entity_graph_edges.py, unchanged since.
    # FKs on subject_entity_id/object_entity_id to public.entities are dropped
    # per the no-FK rule above (each stand-in stays independently creatable).
    columns=(
        ("id", "UUID PRIMARY KEY DEFAULT gen_random_uuid()"),
        ("source_schema", "TEXT NOT NULL"),
        ("source_table", "TEXT NOT NULL"),
        ("source_id", "UUID NOT NULL"),
        ("subject_entity_id", "UUID NOT NULL"),
        ("predicate", "TEXT"),
        ("object_entity_id", "UUID"),
        (
            "sensitivity",
            "TEXT NOT NULL DEFAULT 'normal' "
            "CHECK (sensitivity IN ('normal', 'pii', 'confidential'))",
        ),
        ("withheld_reason", "TEXT CHECK (withheld_reason IN ('sensitivity'))"),
        ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
        ("updated_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
    ),
    table_constraints=(
        "CONSTRAINT uq_entity_graph_edges_source UNIQUE (source_schema, source_table, source_id)",
        "CONSTRAINT chk_entity_graph_edges_payload_xor_withheld CHECK ("
        "(withheld_reason IS NULL AND predicate IS NOT NULL AND object_entity_id IS NOT NULL)"
        " OR "
        "(withheld_reason IS NOT NULL AND predicate IS NULL AND object_entity_id IS NULL)"
        ")",
    ),
    indexes=(
        "CREATE INDEX IF NOT EXISTS idx_entity_graph_edges_subject ON {table} (subject_entity_id)",
        "CREATE INDEX IF NOT EXISTS idx_entity_graph_edges_object "
        "ON {table} (object_entity_id) WHERE object_entity_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_entity_graph_edges_withheld "
        "ON {table} (subject_entity_id) WHERE withheld_reason IS NOT NULL",
    ),
)


STANDINS: dict[str, TableStandin] = {
    standin.table: standin
    for standin in (
        CONNECTOR_REGISTRY,
        PENDING_ACTIONS,
        AUTONOMY_APPROVAL_HISTORY,
        AUTONOMY_SUGGESTIONS,
        APPROVAL_RULES,
        APPROVAL_EVENTS,
        ENTITY_PREDICATE_REGISTRY,
        CONTACT_ENTITY_MAP,
        ENTITY_GRAPH_EDGES,
    )
}
"""Every declared stand-in, keyed by table name. Both guards iterate this."""


__all__ = [
    "APPROVAL_EVENTS",
    "APPROVAL_RULES",
    "AUTONOMY_APPROVAL_HISTORY",
    "AUTONOMY_SUGGESTIONS",
    "CONNECTOR_REGISTRY",
    "CONTACT_ENTITY_MAP",
    "ENTITY_GRAPH_EDGES",
    "ENTITY_PREDICATE_REGISTRY",
    "PENDING_ACTIONS",
    "STANDINS",
    "TableStandin",
    "TriggerDefinition",
    "TriggerExclusion",
]
