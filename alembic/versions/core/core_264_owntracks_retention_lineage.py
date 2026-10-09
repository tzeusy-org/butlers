"""OwnTracks immutable birth, permanent floors and committed source receipts.

Revision ID: core_264
Revises: core_265

Raw mutation remains connector_writer-owned. Chronicler receives only the
explicit additional SELECT receipt surfaces. Legacy NULL accepted lineage is
unknown; this migration does not certify projection or delete any evidence.
"""

import sqlalchemy as sa

from alembic import op
from butlers.location_ingress_schema import (
    LOCAL_COLUMNS,
    LOCAL_CONSTRAINTS,
    LOCAL_TABLES,
    local_schema_sql,
)
from butlers.location_retention_schema import tool_input_dependency_sql
from butlers.owntracks_copy_schema import filtered_copy_schema_sql, filtered_copy_security_sql

revision = "core_264"
down_revision = "core_265"
branch_labels = None
depends_on = None

_LOCK = "butlers:core_264:owntracks-retention"
_RECEIPTS = (
    "owntracks_retention_tombstones",
    "owntracks_retention_batches",
    "owntracks_retention_batch_rows",
)


def _select_core_writer_owner(schema: str, rows: list) -> tuple[int, str]:
    """Resolve the actual foundation state relation, never the invoking login.

    Shared-predecessor replay may have public.state without a local state.
    A present local state always wins, including malformed local
    kinds (which refuse). Only an absent local state permits the fixed public
    fallback. No arbitrary peer table, namespace owner or role name is inferred.
    """
    if any(len(row) != 5 or row[0] not in {schema, "public"} for row in rows):
        raise RuntimeError("Location retention core writer anchor differs")
    local = [row for row in rows if row[0] == schema]
    public = [row for row in rows if row[0] == "public"]
    selected = local if local else public
    if len(selected) != 1:
        raise RuntimeError("Location retention core writer anchor is unavailable")
    namespace, kind, owner_oid, owner_name, target_exists = selected[0]
    if (
        kind != "r"
        or type(owner_oid) is not int
        or owner_oid <= 0
        or not isinstance(owner_name, str)
        or not owner_name
        or target_exists is not True
    ):
        raise RuntimeError("Location retention core writer anchor differs")
    return owner_oid, owner_name


def _core_writer_owner(schema: str) -> tuple[int, str]:
    rows = (
        op.get_bind()
        .execute(
            sa.text("""
        SELECT n.nspname,s.relkind,s.relowner,pg_catalog.pg_get_userbyid(s.relowner),
          EXISTS(SELECT 1 FROM pg_catalog.pg_namespace target WHERE target.nspname=:schema)
        FROM pg_catalog.pg_class s JOIN pg_catalog.pg_namespace n ON n.oid=s.relnamespace
        WHERE n.nspname IN (:schema,'public') AND s.relname='state'
    """),
            {"schema": schema},
        )
        .all()
    )
    return _select_core_writer_owner(schema, rows)


def _create_local_tables(schema: str, statement: str) -> None:
    """New own ledgers share the established resolved core writer's owner.

    The actual foundation state can be local or the adopted shared public
    fallback. Current invocation identity is not its permanent owner. Never
    transfer an existing relation or grant membership to make it fit.
    """
    bind = op.get_bind()
    _, owner = _core_writer_owner(schema)
    present = set(
        bind.execute(
            sa.text("""
        SELECT c.relname FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=:schema AND c.relname IN
          ('location_ingress_server_births','location_ingress_server_ends','location_ingress_input_births','location_ingress_accepted_inputs','location_ingress_input_ends','location_ingress_runtime_inputs','location_ingress_structured_inputs','location_ingress_structured_outputs','location_ingress_structured_sdk_births','location_ingress_structured_sdk_ends','location_ingress_structured_local_ends','location_retention_copy_receipts','location_retention_source_floors',
           'location_catalog_copy_loans','location_catalog_copy_dispositions',
           'location_catalog_copy_lifetimes','location_catalog_copy_finished',
           'location_runtime_context_intents','location_runtime_context_bindings','location_runtime_context_ended','location_runtime_context_server_finished','location_runtime_context_episodes','location_runtime_context_artifacts','location_runtime_context_dispositions','location_runtime_tool_intents','location_runtime_tool_inputs','location_runtime_tool_results','location_ordinary_delegation_inputs','location_native_delegation_inputs',
           'location_received_answer_dispositions','location_received_answer_qualifications','location_received_answer_server_finished','location_received_answer_claims','location_received_answer_claim_parents','location_received_answer_claims_ended','location_runtime_context_answer_intents','location_received_answer_contexts','location_received_answer_schedules','location_received_answer_floors','location_native_question_answer_observations','location_native_answer_question_observations','location_native_question_loan_observations','location_native_answer_observations','location_native_answer_loans','location_received_answer_attempts','location_received_answer_inputs','location_native_delegation_answers','location_native_delegation_answer_parents','location_native_delegation_answer_dispositions','location_native_delegation_parents','location_native_delegation_loans','location_received_delegation_floors','location_received_delegation_dispositions','location_received_delegation_attempts','location_received_delegation_inputs','location_received_delegation_server_finished','location_received_question_recovery_dispositions','location_received_question_recoveries','location_received_question_source_floors','location_received_question_refusals','location_received_question_task_dispositions','location_received_delegation_schedules','location_received_delegation_claims','location_received_delegation_claims_ended','location_received_delegation_contexts','location_runtime_context_question_intents','location_native_delegation_dispositions')
    """),
            {"schema": schema},
        ).scalars()
    )
    op.execute(statement)
    quote = bind.dialect.identifier_preparer.quote
    for table in (
        *LOCAL_TABLES,
        "location_retention_copy_receipts",
        "location_retention_source_floors",
        "location_catalog_copy_loans",
        "location_catalog_copy_dispositions",
        "location_catalog_copy_lifetimes",
        "location_catalog_copy_finished",
        "location_runtime_context_intents",
        "location_runtime_context_bindings",
        "location_runtime_context_ended",
        "location_runtime_context_server_finished",
        "location_runtime_context_episodes",
        "location_runtime_context_artifacts",
        "location_runtime_context_dispositions",
        "location_runtime_tool_intents",
        "location_runtime_tool_inputs",
        "location_runtime_tool_results",
        "location_ordinary_delegation_inputs",
        "location_native_delegation_inputs",
        "location_received_answer_dispositions",
        "location_received_answer_qualifications",
        "location_received_answer_server_finished",
        "location_received_answer_claims",
        "location_received_answer_claim_parents",
        "location_received_answer_claims_ended",
        "location_runtime_context_answer_intents",
        "location_received_answer_contexts",
        "location_received_answer_schedules",
        "location_received_answer_floors",
        "location_received_answer_inputs",
        "location_received_answer_attempts",
        "location_native_question_answer_observations",
        "location_native_answer_question_observations",
        "location_native_question_loan_observations",
        "location_native_answer_observations",
        "location_native_answer_loans",
        "location_native_delegation_answer_parents",
        "location_native_delegation_answer_dispositions",
        "location_native_delegation_answers",
        "location_native_delegation_parents",
        "location_native_delegation_dispositions",
        "location_runtime_context_question_intents",
        "location_received_delegation_contexts",
        "location_received_delegation_claims_ended",
        "location_received_delegation_claims",
        "location_received_question_recovery_dispositions",
        "location_received_question_recoveries",
        "location_received_question_source_floors",
        "location_received_question_refusals",
        "location_received_question_task_dispositions",
        "location_received_delegation_schedules",
        "location_received_delegation_server_finished",
        "location_received_delegation_dispositions",
        "location_received_delegation_floors",
        "location_received_delegation_inputs",
        "location_received_delegation_attempts",
        "location_native_delegation_loans",
    ):
        if table not in present:
            op.execute(f"ALTER TABLE {quote(schema)}.{quote(table)} OWNER TO {quote(owner)}")


def _validate_local_tables(schema: str) -> None:
    expected = {
        "location_retention_copy_receipts": [
            ("decision_id", "uuid", True),
            ("manifest_digest", "bytea", True),
            ("receipt_id", "uuid", True),
            ("source_kind", "text", True),
            ("forgotten_count", "integer", True),
            ("committed_at", "timestamp with time zone", True),
        ],
        "location_retention_source_floors": [
            ("dedupe_digest", "bytea", True),
            ("request_id", "uuid", True),
            ("decision_id", "uuid", True),
            ("logical_source_digest", "bytea", True),
        ],
    }
    expected.update(
        {
            "location_catalog_copy_loans": [
                ("loan_id", "uuid", True),
                ("source_generation", "uuid", True),
                ("catalog_id", "uuid", True),
                ("body_digest", "bytea", True),
                ("receiving_incarnation", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_catalog_copy_lifetimes": [
                ("loan_id", "uuid", True),
                ("holder_kind", "text", True),
                ("holder_id", "uuid", True),
                ("body_digest", "bytea", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_catalog_copy_finished": [
                ("loan_id", "uuid", True),
                ("body_digest", "bytea", True),
                ("receipt_id", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_catalog_copy_dispositions": [
                ("loan_id", "uuid", True),
                ("decision_id", "uuid", True),
                ("manifest_digest", "bytea", True),
                ("receipt_id", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
        }
    )
    expected.update(
        {
            "location_runtime_context_intents": [
                ("input_generation", "uuid", True),
                ("receiving_session", "uuid", True),
                ("server_request", "uuid", False),
                ("captured_at", "timestamp with time zone", True),
            ],
            "location_runtime_context_bindings": [
                ("input_generation", "uuid", True),
                ("receiving_session", "uuid", True),
                ("bundle_digest", "bytea", True),
                ("context_digest", "bytea", True),
                ("system_digest", "bytea", True),
                ("prompt_digest", "bytea", True),
                ("exclusive_input", "boolean", True),
                ("context_bytes", "integer", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_runtime_context_ended": [
                ("input_generation", "uuid", True),
                ("receipt_id", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
        }
    )
    expected.update(
        {
            "location_runtime_context_server_finished": [
                ("input_generation", "uuid", True),
                ("server_request", "uuid", True),
                ("receipt_id", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_runtime_context_episodes": [
                ("input_generation", "uuid", True),
                ("episode_id", "uuid", True),
                ("body_digest", "bytea", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_runtime_context_artifacts": [
                ("artifact_generation", "uuid", True),
                ("input_generation", "uuid", True),
                ("memory_table", "text", True),
                ("artifact_id", "uuid", True),
                ("body_digest", "bytea", True),
                ("content_digest", "bytea", False),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_runtime_tool_intents": [
                ("tool_generation", "uuid", True),
                ("receiving_session", "uuid", True),
                ("tool_name", "text", True),
                ("module_name", "text", True),
                ("input_digest", "bytea", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_runtime_tool_inputs": [
                ("tool_generation", "uuid", True),
                ("loan_id", "uuid", True),
                ("body_digest", "bytea", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_runtime_tool_results": [
                ("tool_generation", "uuid", True),
                ("outcome", "text", True),
                ("result_digest", "bytea", False),
                ("exclusive_inputs", "boolean", True),
                ("receipt_id", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_runtime_context_dispositions": [
                ("input_generation", "uuid", True),
                ("decision_id", "uuid", True),
                ("manifest_digest", "bytea", True),
                ("receipt_id", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
                ("reduced_system_digest", "bytea", False),
                ("reduced_provenance_digest", "bytea", False),
            ],
        }
    )
    expected.update(
        {
            "location_ordinary_delegation_inputs": [
                ("source_generation", "uuid", True),
                ("ledger_id", "uuid", True),
                ("producer_kind", "text", True),
                ("render_date", "date", True),
                ("body_digest", "bytea", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_native_delegation_inputs": [
                ("question_generation", "uuid", True),
                ("ledger_id", "uuid", True),
                ("receiving_session", "uuid", True),
                ("tool_generation", "uuid", True),
                ("context_generation", "uuid", True),
                ("body_digest", "bytea", True),
                ("parent_count", "integer", True),
                ("exclusive_input", "boolean", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_native_delegation_loans": [
                ("loan_id", "uuid", True),
                ("question_generation", "uuid", True),
                ("receiver_name", "text", True),
                ("receiving_incarnation", "uuid", True),
                ("receiving_generation", "uuid", True),
                ("body_digest", "bytea", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_received_delegation_floors": [
                ("receiving_generation", "uuid", True),
                ("decision_id", "uuid", True),
                ("manifest_digest", "bytea", True),
                ("source_name", "text", True),
                ("question_generation", "uuid", True),
                ("ledger_id", "uuid", True),
                ("loan_id", "uuid", True),
                ("body_digest", "bytea", True),
                ("receiving_incarnation", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_received_delegation_dispositions": [
                ("receiving_generation", "uuid", True),
                ("receipt_id", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_received_delegation_attempts": [
                ("receiving_generation", "uuid", True),
                ("ledger_id", "uuid", True),
                ("body_digest", "bytea", True),
                ("receiving_incarnation", "uuid", True),
                ("receiving_session", "uuid", False),
                ("tool_generation", "uuid", False),
                ("server_request", "uuid", False),
                ("committed_at", "timestamp with time zone", True),
                ("source_name", "text", False),
            ],
            "location_received_delegation_inputs": [
                ("receiving_generation", "uuid", True),
                ("ledger_id", "uuid", True),
                ("source_name", "text", True),
                ("source_incarnation", "uuid", True),
                ("question_generation", "uuid", True),
                ("loan_id", "uuid", True),
                ("body_digest", "bytea", True),
                ("receiving_incarnation", "uuid", True),
                ("parent_count", "integer", True),
                ("exclusive_input", "boolean", True),
                ("receiving_session", "uuid", False),
                ("tool_generation", "uuid", False),
                ("server_request", "uuid", False),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_received_delegation_server_finished": [
                ("receiving_generation", "uuid", True),
                ("server_request", "uuid", True),
                ("body_digest", "bytea", True),
                ("receipt_id", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_received_delegation_claims": [
                ("claim_generation", "uuid", True),
                ("receiving_generation", "uuid", True),
                ("task_id", "uuid", True),
                ("prompt_digest", "bytea", True),
                ("receiving_incarnation", "uuid", True),
                ("exclusive_input", "boolean", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_received_delegation_claims_ended": [
                ("claim_generation", "uuid", True),
                ("receipt_id", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_runtime_context_question_intents": [
                ("input_generation", "uuid", True),
                ("claim_generation", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_received_delegation_contexts": [
                ("input_generation", "uuid", True),
                ("claim_generation", "uuid", True),
                ("receiving_session", "uuid", True),
                ("bundle_digest", "bytea", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_received_question_recovery_dispositions": [
                ("receiving_generation", "uuid", True),
                ("receipt_id", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_received_question_recoveries": [
                ("receiving_generation", "uuid", True),
                ("source_name", "text", True),
                ("ledger_id", "uuid", True),
                ("question_generation", "uuid", True),
                ("body_digest", "bytea", True),
                ("decision_id", "uuid", True),
                ("manifest_digest", "bytea", True),
                ("receiving_incarnation", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_received_question_source_floors": [
                ("source_name", "text", True),
                ("ledger_id", "uuid", True),
                ("question_generation", "uuid", True),
                ("body_digest", "bytea", True),
                ("decision_id", "uuid", True),
                ("manifest_digest", "bytea", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_received_question_refusals": [
                ("receiving_generation", "uuid", True),
                ("tool_generation", "uuid", True),
                ("receiving_incarnation", "uuid", True),
                ("body_digest", "bytea", True),
                ("result_digest", "bytea", True),
                ("receipt_id", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_received_question_task_dispositions": [
                ("receiving_generation", "uuid", True),
                ("task_id", "uuid", True),
                ("decision_id", "uuid", True),
                ("manifest_digest", "bytea", True),
                ("original_prompt_digest", "bytea", True),
                ("reduced_prompt_digest", "bytea", True),
                ("receipt_id", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_received_delegation_schedules": [
                ("receiving_generation", "uuid", True),
                ("task_id", "uuid", True),
                ("prompt_digest", "bytea", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_native_delegation_dispositions": [
                ("question_generation", "uuid", True),
                ("decision_id", "uuid", True),
                ("manifest_digest", "bytea", True),
                ("body_digest", "bytea", True),
                ("receipt_id", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
                ("reduced_question_digest", "bytea", False),
            ],
            "location_native_delegation_answers": [
                ("answer_generation", "uuid", True),
                ("ledger_id", "uuid", True),
                ("receiving_session", "uuid", True),
                ("tool_generation", "uuid", True),
                ("context_generation", "uuid", True),
                ("body_digest", "bytea", True),
                ("parent_count", "integer", True),
                ("exclusive_input", "boolean", True),
                ("committed_at", "timestamp with time zone", True),
                ("bundle_digest", "bytea", False),
            ],
            "location_native_question_answer_observations": [
                ("question_generation", "uuid", True),
                ("decision_id", "uuid", True),
                ("manifest_digest", "bytea", True),
                ("answer_owner", "text", True),
                ("answer_generation", "uuid", True),
                ("answer_receipt", "uuid", True),
                ("answer_body_digest", "bytea", True),
                ("answer_bundle_digest", "bytea", True),
                ("wake_key", "text", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_native_answer_question_observations": [
                ("answer_generation", "uuid", True),
                ("decision_id", "uuid", True),
                ("manifest_digest", "bytea", True),
                ("question_owner", "text", True),
                ("question_generation", "uuid", True),
                ("question_receipt", "uuid", True),
                ("original_question_digest", "bytea", True),
                ("reduced_question_digest", "bytea", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_native_question_loan_observations": [
                ("loan_id", "uuid", True),
                ("decision_id", "uuid", True),
                ("manifest_digest", "bytea", True),
                ("receiver_receipt", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_native_answer_observations": [
                ("loan_id", "uuid", True),
                ("decision_id", "uuid", True),
                ("manifest_digest", "bytea", True),
                ("receiver_receipt", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_native_answer_loans": [
                ("loan_id", "uuid", True),
                ("answer_generation", "uuid", True),
                ("receiving_generation", "uuid", True),
                ("receiver_name", "text", True),
                ("receiving_incarnation", "uuid", True),
                ("bundle_digest", "bytea", True),
                ("committed_at", "timestamp with time zone", True),
                ("source_incarnation", "uuid", False),
            ],
            "location_received_answer_attempts": [
                ("receiving_generation", "uuid", True),
                ("ledger_id", "uuid", True),
                ("source_name", "text", True),
                ("wake_key", "text", True),
                ("receiving_incarnation", "uuid", True),
                ("receiving_session", "uuid", False),
                ("tool_generation", "uuid", False),
                ("server_request", "uuid", False),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_received_answer_qualifications": [
                ("receiving_generation", "uuid", True),
                ("receipt_id", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_received_answer_dispositions": [
                ("receiving_generation", "uuid", True),
                ("receipt_id", "uuid", True),
                ("task_id", "uuid", False),
                ("reduced_prompt_digest", "bytea", False),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_received_answer_server_finished": [
                ("receiving_generation", "uuid", True),
                ("server_request", "uuid", True),
                ("receipt_id", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_received_answer_claims": [
                ("claim_generation", "uuid", True),
                ("task_id", "uuid", True),
                ("prompt_digest", "bytea", True),
                ("bundle_digest", "bytea", True),
                ("parent_count", "integer", True),
                ("receiving_incarnation", "uuid", True),
                ("exclusive_input", "boolean", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_received_answer_claim_parents": [
                ("claim_generation", "uuid", True),
                ("receiving_generation", "uuid", True),
                ("bundle_digest", "bytea", True),
            ],
            "location_received_answer_claims_ended": [
                ("claim_generation", "uuid", True),
                ("receipt_id", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_runtime_context_answer_intents": [
                ("input_generation", "uuid", True),
                ("claim_generation", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_received_answer_contexts": [
                ("input_generation", "uuid", True),
                ("claim_generation", "uuid", True),
                ("receiving_session", "uuid", True),
                ("bundle_digest", "bytea", True),
                ("claim_bundle_digest", "bytea", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_received_answer_schedules": [
                ("receiving_generation", "uuid", True),
                ("task_id", "uuid", True),
                ("prompt_digest", "bytea", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_received_answer_floors": [
                ("receiving_generation", "uuid", True),
                ("decision_id", "uuid", True),
                ("manifest_digest", "bytea", True),
                ("bundle_digest", "bytea", True),
                ("source_name", "text", True),
                ("answer_generation", "uuid", True),
                ("ledger_id", "uuid", True),
                ("loan_id", "uuid", True),
                ("source_incarnation", "uuid", True),
                ("receiving_incarnation", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_received_answer_inputs": [
                ("receiving_generation", "uuid", True),
                ("source_name", "text", True),
                ("answer_generation", "uuid", True),
                ("loan_id", "uuid", True),
                ("bundle_digest", "bytea", True),
                ("source_incarnation", "uuid", True),
                ("parent_count", "integer", True),
                ("exclusive_input", "boolean", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_native_delegation_answer_parents": [
                ("answer_generation", "uuid", True),
                ("parent_kind", "text", True),
                ("parent_generation", "uuid", True),
                ("parent_digest", "bytea", True),
            ],
            "location_native_delegation_answer_dispositions": [
                ("answer_generation", "uuid", True),
                ("decision_id", "uuid", True),
                ("manifest_digest", "bytea", True),
                ("body_digest", "bytea", True),
                ("receipt_id", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
                ("bundle_digest", "bytea", False),
                ("question_digest", "bytea", False),
                ("wake_key", "text", False),
                ("reduced_digest", "bytea", False),
                ("question_owner", "text", False),
            ],
            "location_native_delegation_parents": [
                ("question_generation", "uuid", True),
                ("parent_kind", "text", True),
                ("parent_generation", "uuid", True),
                ("parent_digest", "bytea", True),
            ],
        }
    )
    expected_constraints = {
        "location_retention_copy_receipts": {
            "PRIMARY KEY (decision_id)",
            "UNIQUE (receipt_id)",
            "CHECK ((octet_length(manifest_digest) = 32))",
            "CHECK ((source_kind = 'switchboard_skipped'::text))",
            "CHECK ((forgotten_count > 0))",
        },
        "location_retention_source_floors": {
            "PRIMARY KEY (dedupe_digest)",
            "CHECK ((octet_length(dedupe_digest) = 32))",
            "CHECK ((octet_length(logical_source_digest) = 32))",
            "FOREIGN KEY (decision_id) REFERENCES location_retention_copy_receipts(decision_id)",
        },
    }
    expected_constraints.update(
        {
            "location_catalog_copy_loans": {
                "PRIMARY KEY (loan_id)",
                "CHECK ((octet_length(body_digest) = 32))",
            },
            "location_catalog_copy_lifetimes": {
                "PRIMARY KEY (loan_id)",
                "CHECK ((octet_length(body_digest) = 32))",
                "FOREIGN KEY (loan_id) REFERENCES location_catalog_copy_loans(loan_id)",
                "CHECK ((holder_kind = ANY (ARRAY['server_response'::text, "
                "'runtime_session'::text, 'unbound_processing'::text])))",
            },
            "location_catalog_copy_finished": {
                "PRIMARY KEY (loan_id)",
                "CHECK ((octet_length(body_digest) = 32))",
                "UNIQUE (receipt_id)",
                "FOREIGN KEY (loan_id) REFERENCES location_catalog_copy_lifetimes(loan_id)",
            },
            "location_catalog_copy_dispositions": {
                "PRIMARY KEY (loan_id)",
                "CHECK ((octet_length(manifest_digest) = 32))",
                "UNIQUE (receipt_id)",
                "FOREIGN KEY (loan_id) REFERENCES location_catalog_copy_loans(loan_id)",
            },
        }
    )
    expected_constraints.update(
        {
            "location_runtime_context_intents": {
                "PRIMARY KEY (input_generation)",
                "UNIQUE (receiving_session)",
            },
            "location_runtime_context_bindings": {
                "PRIMARY KEY (input_generation)",
                "CHECK ((octet_length(system_digest) = 32))",
                "CHECK ((octet_length(prompt_digest) = 32))",
                "FOREIGN KEY (receiving_session) REFERENCES sessions(id)",
                "FOREIGN KEY (input_generation) REFERENCES "
                "location_runtime_context_intents(input_generation)",
                "UNIQUE (receiving_session)",
                "CHECK ((octet_length(context_digest) = 32))",
                "CHECK ((octet_length(bundle_digest) = 32))",
                "CHECK ((context_bytes >= 0))",
            },
            "location_runtime_context_ended": {
                "PRIMARY KEY (input_generation)",
                "UNIQUE (receipt_id)",
                "FOREIGN KEY (input_generation) REFERENCES "
                "location_runtime_context_intents(input_generation)",
            },
        }
    )
    expected_constraints.update(
        {
            "location_runtime_context_server_finished": {
                "FOREIGN KEY (input_generation) REFERENCES "
                "location_runtime_context_intents(input_generation)",
                "PRIMARY KEY (input_generation)",
                "UNIQUE (receipt_id)",
            },
            "location_runtime_context_episodes": {
                "FOREIGN KEY (input_generation) REFERENCES "
                "location_runtime_context_intents(input_generation)",
                "UNIQUE (episode_id)",
                "CHECK ((octet_length(body_digest) = 32))",
                "PRIMARY KEY (input_generation, episode_id)",
            },
            "location_runtime_context_artifacts": {
                "PRIMARY KEY (artifact_generation)",
                "FOREIGN KEY (input_generation) REFERENCES "
                "location_runtime_context_intents(input_generation)",
                "CHECK ((memory_table = ANY (ARRAY['facts'::text, 'rules'::text])))",
                "CHECK ((octet_length(body_digest) = 32))",
                "CHECK (((content_digest IS NULL) OR (octet_length(content_digest) = 32)))",
            },
            "location_runtime_tool_intents": {
                "PRIMARY KEY (tool_generation)",
                "FOREIGN KEY (receiving_session) REFERENCES sessions(id)",
                "CHECK ((octet_length(input_digest) = 32))",
            },
            "location_runtime_tool_inputs": {
                "PRIMARY KEY (tool_generation, loan_id)",
                "UNIQUE (loan_id)",
                "FOREIGN KEY (tool_generation) REFERENCES "
                "location_runtime_tool_intents(tool_generation)",
                "FOREIGN KEY (loan_id) REFERENCES location_catalog_copy_loans(loan_id)",
                "CHECK ((octet_length(body_digest) = 32))",
            },
            "location_runtime_tool_results": {
                "PRIMARY KEY (tool_generation)",
                "FOREIGN KEY (tool_generation) REFERENCES "
                "location_runtime_tool_intents(tool_generation)",
                "UNIQUE (receipt_id)",
                "CHECK ((outcome = ANY (ARRAY['success'::text, 'error'::text])))",
                "CHECK (((result_digest IS NULL) OR (octet_length(result_digest) = 32)))",
                "CHECK (((outcome = 'success'::text) = (result_digest IS NOT NULL)))",
            },
            "location_runtime_context_dispositions": {
                "FOREIGN KEY (input_generation) REFERENCES "
                "location_runtime_context_intents(input_generation)",
                "PRIMARY KEY (input_generation)",
                "CHECK ((octet_length(manifest_digest) = 32))",
                "UNIQUE (receipt_id)",
                "CHECK (((reduced_system_digest IS NULL) OR "
                "(octet_length(reduced_system_digest) = 32)))",
                "CHECK (((reduced_provenance_digest IS NULL) OR "
                "(octet_length(reduced_provenance_digest) = 32)))",
            },
        }
    )
    expected_constraints.update(
        {
            "location_ordinary_delegation_inputs": {
                "PRIMARY KEY (source_generation)",
                "UNIQUE (ledger_id)",
                "CHECK ((producer_kind = 'birthday_gift_budget_ask'::text))",
                "CHECK ((octet_length(body_digest) = 32))",
            },
            "location_native_delegation_inputs": {
                "PRIMARY KEY (question_generation)",
                "UNIQUE (ledger_id)",
                "CHECK ((octet_length(body_digest) = 32))",
                "CHECK ((parent_count >= 0))",
                "FOREIGN KEY (tool_generation) REFERENCES "
                "location_runtime_tool_intents(tool_generation)",
                "FOREIGN KEY (context_generation) REFERENCES "
                "location_runtime_context_bindings(input_generation)",
            },
            "location_native_delegation_loans": {
                "PRIMARY KEY (loan_id)",
                "UNIQUE (receiver_name, receiving_generation)",
                "CHECK ((octet_length(body_digest) = 32))",
                "FOREIGN KEY (question_generation) REFERENCES "
                "location_native_delegation_inputs(question_generation)",
            },
            "location_received_delegation_floors": {
                "PRIMARY KEY (receiving_generation)",
                "UNIQUE (loan_id)",
                "CHECK ((octet_length(manifest_digest) = 32))",
                "CHECK ((octet_length(body_digest) = 32))",
                "CHECK ((source_name <> ''::text))",
            },
            "location_received_delegation_dispositions": {
                "PRIMARY KEY (receiving_generation)",
                "UNIQUE (receipt_id)",
                "FOREIGN KEY (receiving_generation) REFERENCES "
                "location_received_delegation_floors(receiving_generation)",
            },
            "location_received_delegation_attempts": {
                "PRIMARY KEY (receiving_generation)",
                "CHECK ((octet_length(body_digest) = 32))",
                "CHECK (((receiving_session IS NULL) = (tool_generation IS NULL)))",
                "CHECK (((tool_generation IS NOT NULL) OR (server_request IS NOT NULL)))",
                "FOREIGN KEY (receiving_session) REFERENCES sessions(id)",
                "FOREIGN KEY (tool_generation) REFERENCES "
                "location_runtime_tool_intents(tool_generation)",
            },
            "location_received_delegation_inputs": {
                "CHECK (((receiving_session IS NULL) = (tool_generation IS NULL)))",
                "CHECK (((tool_generation IS NOT NULL) OR (server_request IS NOT NULL)))",
                "FOREIGN KEY (receiving_session) REFERENCES sessions(id)",
                "FOREIGN KEY (tool_generation) REFERENCES "
                "location_runtime_tool_intents(tool_generation)",
                "PRIMARY KEY (receiving_generation)",
                "UNIQUE (loan_id)",
                "CHECK ((octet_length(body_digest) = 32))",
                "CHECK ((parent_count >= 0))",
            },
            "location_received_delegation_server_finished": {
                "PRIMARY KEY (receiving_generation)",
                "UNIQUE (receipt_id)",
                "CHECK ((octet_length(body_digest) = 32))",
                "FOREIGN KEY (receiving_generation) REFERENCES "
                "location_received_delegation_attempts(receiving_generation)",
            },
            "location_received_delegation_claims": {
                "PRIMARY KEY (claim_generation)",
                "CHECK ((octet_length(prompt_digest) = 32))",
                "FOREIGN KEY (receiving_generation) REFERENCES "
                "location_received_delegation_inputs(receiving_generation)",
                "FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id)",
            },
            "location_received_delegation_claims_ended": {
                "PRIMARY KEY (claim_generation)",
                "UNIQUE (receipt_id)",
                "FOREIGN KEY (claim_generation) REFERENCES "
                "location_received_delegation_claims(claim_generation)",
            },
            "location_runtime_context_question_intents": {
                "PRIMARY KEY (input_generation)",
                "FOREIGN KEY (input_generation) REFERENCES "
                "location_runtime_context_intents(input_generation)",
                "FOREIGN KEY (claim_generation) REFERENCES "
                "location_received_delegation_claims(claim_generation)",
            },
            "location_received_delegation_contexts": {
                "PRIMARY KEY (input_generation)",
                "CHECK ((octet_length(bundle_digest) = 32))",
                "FOREIGN KEY (input_generation) REFERENCES "
                "location_runtime_context_intents(input_generation)",
                "FOREIGN KEY (claim_generation) REFERENCES "
                "location_received_delegation_claims(claim_generation)",
                "FOREIGN KEY (receiving_session) REFERENCES sessions(id)",
            },
            "location_received_question_recovery_dispositions": {
                "PRIMARY KEY (receiving_generation)",
                "UNIQUE (receipt_id)",
                "FOREIGN KEY (receiving_generation) REFERENCES "
                "location_received_question_recoveries(receiving_generation)",
            },
            "location_received_question_recoveries": {
                "PRIMARY KEY (receiving_generation)",
                "FOREIGN KEY (receiving_generation) REFERENCES "
                "location_received_delegation_attempts(receiving_generation)",
                "FOREIGN KEY (source_name, ledger_id) REFERENCES "
                "location_received_question_source_floors(source_name, ledger_id)",
                "CHECK ((octet_length(body_digest) = 32))",
                "CHECK ((octet_length(manifest_digest) = 32))",
            },
            "location_received_question_source_floors": {
                "PRIMARY KEY (source_name, ledger_id)",
                "CHECK ((source_name <> ''::text))",
                "CHECK ((octet_length(body_digest) = 32))",
                "CHECK ((octet_length(manifest_digest) = 32))",
            },
            "location_received_question_refusals": {
                "PRIMARY KEY (receiving_generation)",
                "UNIQUE (receipt_id)",
                "FOREIGN KEY (receiving_generation) REFERENCES "
                "location_received_delegation_attempts(receiving_generation)",
                "FOREIGN KEY (tool_generation) REFERENCES "
                "location_runtime_tool_intents(tool_generation)",
                "CHECK ((octet_length(body_digest) = 32))",
                "CHECK ((octet_length(result_digest) = 32))",
            },
            "location_received_question_task_dispositions": {
                "PRIMARY KEY (receiving_generation)",
                "UNIQUE (receipt_id)",
                "FOREIGN KEY (receiving_generation) REFERENCES "
                "location_received_delegation_floors(receiving_generation)",
                "FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id)",
                "CHECK ((octet_length(manifest_digest) = 32))",
                "CHECK ((octet_length(original_prompt_digest) = 32))",
                "CHECK ((octet_length(reduced_prompt_digest) = 32))",
            },
            "location_received_delegation_schedules": {
                "PRIMARY KEY (receiving_generation)",
                "UNIQUE (task_id)",
                "CHECK ((octet_length(prompt_digest) = 32))",
                "FOREIGN KEY (receiving_generation) REFERENCES "
                "location_received_delegation_inputs(receiving_generation)",
                "FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id)",
            },
            "location_native_delegation_dispositions": {
                "PRIMARY KEY (question_generation)",
                "UNIQUE (receipt_id)",
                "CHECK ((octet_length(manifest_digest) = 32))",
                "CHECK ((octet_length(body_digest) = 32))",
                "FOREIGN KEY (question_generation) REFERENCES "
                "location_native_delegation_inputs(question_generation)",
                "CHECK ((octet_length(reduced_question_digest) = 32))",
            },
            "location_native_delegation_answers": {
                "CHECK (((bundle_digest IS NULL) OR (octet_length(bundle_digest) = 32)))",
                "PRIMARY KEY (answer_generation)",
                "UNIQUE (ledger_id)",
                "CHECK ((octet_length(body_digest) = 32))",
                "CHECK ((parent_count >= 0))",
                "FOREIGN KEY (tool_generation) REFERENCES "
                "location_runtime_tool_intents(tool_generation)",
                "FOREIGN KEY (context_generation) REFERENCES "
                "location_runtime_context_bindings(input_generation)",
            },
            "location_native_question_answer_observations": {
                "PRIMARY KEY (question_generation)",
                "FOREIGN KEY (question_generation) REFERENCES "
                "location_native_delegation_inputs(question_generation)",
                "CHECK ((octet_length(manifest_digest) = 32))",
                "CHECK ((octet_length(answer_body_digest) = 32))",
                "CHECK ((octet_length(answer_bundle_digest) = 32))",
            },
            "location_native_answer_question_observations": {
                "PRIMARY KEY (answer_generation)",
                "FOREIGN KEY (answer_generation) REFERENCES "
                "location_native_delegation_answers(answer_generation)",
                "CHECK ((octet_length(manifest_digest) = 32))",
                "CHECK ((octet_length(original_question_digest) = 32))",
                "CHECK ((octet_length(reduced_question_digest) = 32))",
            },
            "location_native_question_loan_observations": {
                "PRIMARY KEY (loan_id)",
                "FOREIGN KEY (loan_id) REFERENCES location_native_delegation_loans(loan_id)",
                "CHECK ((octet_length(manifest_digest) = 32))",
            },
            "location_native_answer_observations": {
                "PRIMARY KEY (loan_id)",
                "FOREIGN KEY (loan_id) REFERENCES location_native_answer_loans(loan_id)",
                "CHECK ((octet_length(manifest_digest) = 32))",
            },
            "location_native_answer_loans": {
                "PRIMARY KEY (loan_id)",
                "UNIQUE (receiving_generation)",
                "FOREIGN KEY (answer_generation) REFERENCES "
                "location_native_delegation_answers(answer_generation)",
                "CHECK ((octet_length(bundle_digest) = 32))",
            },
            "location_received_answer_attempts": {
                "PRIMARY KEY (receiving_generation)",
                "FOREIGN KEY (tool_generation) REFERENCES "
                "location_runtime_tool_intents(tool_generation)",
                "CHECK (((server_request IS NOT NULL) OR ((receiving_session IS NOT NULL) "
                "AND (tool_generation IS NOT NULL))))",
            },
            "location_received_answer_qualifications": {
                "PRIMARY KEY (receiving_generation)",
                "UNIQUE (receipt_id)",
                "FOREIGN KEY (receiving_generation) REFERENCES "
                "location_received_answer_floors(receiving_generation)",
            },
            "location_received_answer_dispositions": {
                "PRIMARY KEY (receiving_generation)",
                "UNIQUE (receipt_id)",
                "FOREIGN KEY (receiving_generation) REFERENCES "
                "location_received_answer_qualifications(receiving_generation)",
                "FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id)",
                "CHECK (((task_id IS NULL) = (reduced_prompt_digest IS NULL)))",
                "CHECK ((octet_length(reduced_prompt_digest) = 32))",
            },
            "location_received_answer_server_finished": {
                "PRIMARY KEY (receiving_generation)",
                "UNIQUE (receipt_id)",
                "FOREIGN KEY (receiving_generation) REFERENCES "
                "location_received_answer_attempts(receiving_generation)",
            },
            "location_received_answer_claims": {
                "PRIMARY KEY (claim_generation)",
                "FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id)",
                "CHECK ((octet_length(prompt_digest) = 32))",
                "CHECK ((octet_length(bundle_digest) = 32))",
                "CHECK ((parent_count >= 1))",
            },
            "location_received_answer_claim_parents": {
                "PRIMARY KEY (claim_generation, receiving_generation)",
                "FOREIGN KEY (claim_generation) REFERENCES "
                "location_received_answer_claims(claim_generation)",
                "FOREIGN KEY (receiving_generation) REFERENCES "
                "location_received_answer_inputs(receiving_generation)",
                "CHECK ((octet_length(bundle_digest) = 32))",
            },
            "location_received_answer_claims_ended": {
                "PRIMARY KEY (claim_generation)",
                "UNIQUE (receipt_id)",
                "FOREIGN KEY (claim_generation) REFERENCES "
                "location_received_answer_claims(claim_generation)",
            },
            "location_runtime_context_answer_intents": {
                "PRIMARY KEY (input_generation)",
                "FOREIGN KEY (input_generation) REFERENCES "
                "location_runtime_context_intents(input_generation)",
                "FOREIGN KEY (claim_generation) REFERENCES "
                "location_received_answer_claims(claim_generation)",
            },
            "location_received_answer_contexts": {
                "PRIMARY KEY (input_generation)",
                "FOREIGN KEY (input_generation) REFERENCES "
                "location_runtime_context_intents(input_generation)",
                "FOREIGN KEY (claim_generation) REFERENCES "
                "location_received_answer_claims(claim_generation)",
                "FOREIGN KEY (receiving_session) REFERENCES sessions(id)",
                "CHECK ((octet_length(bundle_digest) = 32))",
                "CHECK ((octet_length(claim_bundle_digest) = 32))",
            },
            "location_received_answer_schedules": {
                "PRIMARY KEY (receiving_generation)",
                "CHECK ((octet_length(prompt_digest) = 32))",
                "FOREIGN KEY (receiving_generation) REFERENCES "
                "location_received_answer_inputs(receiving_generation)",
                "FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id)",
            },
            "location_received_answer_floors": {
                "PRIMARY KEY (receiving_generation)",
                "CHECK ((octet_length(manifest_digest) = 32))",
                "CHECK ((octet_length(bundle_digest) = 32))",
                "FOREIGN KEY (receiving_generation) REFERENCES "
                "location_received_answer_attempts(receiving_generation)",
            },
            "location_received_answer_inputs": {
                "CHECK ((parent_count >= 0))",
                "PRIMARY KEY (receiving_generation)",
                "FOREIGN KEY (receiving_generation) REFERENCES "
                "location_received_answer_attempts(receiving_generation)",
                "UNIQUE (loan_id)",
                "CHECK ((octet_length(bundle_digest) = 32))",
            },
            "location_native_delegation_answer_parents": {
                "PRIMARY KEY (answer_generation, parent_kind, parent_generation)",
                "CHECK ((octet_length(parent_digest) = 32))",
                "CHECK ((parent_kind = ANY (ARRAY['native_copy'::text, 'catalog_loan'::text, "
                "'received_question'::text, 'received_answer'::text])))",
                "FOREIGN KEY (answer_generation) REFERENCES "
                "location_native_delegation_answers(answer_generation)",
            },
            "location_native_delegation_answer_dispositions": {
                "PRIMARY KEY (answer_generation)",
                "UNIQUE (receipt_id)",
                "CHECK ((octet_length(manifest_digest) = 32))",
                "CHECK ((octet_length(body_digest) = 32))",
                "FOREIGN KEY (answer_generation) REFERENCES "
                "location_native_delegation_answers(answer_generation)",
                "CHECK ((octet_length(bundle_digest) = 32))",
                "CHECK ((octet_length(question_digest) = 32))",
                "CHECK ((octet_length(reduced_digest) = 32))",
            },
            "location_native_delegation_parents": {
                "PRIMARY KEY (question_generation, parent_kind, parent_generation)",
                "CHECK ((octet_length(parent_digest) = 32))",
                "CHECK ((parent_kind = ANY (ARRAY['native_copy'::text, 'catalog_loan'::text, "
                "'received_question'::text, 'received_answer'::text])))",
                "FOREIGN KEY (question_generation) REFERENCES "
                "location_native_delegation_inputs(question_generation)",
            },
        }
    )
    bind = op.get_bind()
    owner_oid, _ = _core_writer_owner(schema)
    expected.update(LOCAL_COLUMNS)
    expected_constraints.update(LOCAL_CONSTRAINTS)
    for table, shape in expected.items():
        relation = bind.execute(
            sa.text("""
            SELECT c.oid,c.relkind,
              c.relowner=CAST(:owner_oid AS oid),
              pg_catalog.pg_get_userbyid(c.relowner)=current_user,
              current_user=session_user,
              pg_catalog.pg_has_role(current_user,CAST(:owner_oid AS oid),'SET')
            FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=:schema AND c.relname=:table
        """),
            {"schema": schema, "table": table, "owner_oid": owner_oid},
        ).one()
        if relation[1] != "r" or not relation[2]:
            # Fixed booleans only: no role names, OIDs, schema/raw source values.
            predicates = (
                relation[1] == "r",
                relation[2],
                relation[3],
                relation[4],
                relation[5],
            )
            raise RuntimeError(
                "Location retention local table identity differs; "
                + ",".join(str(value is True).lower() for value in predicates)
            )
        actual = [
            tuple(row)
            for row in bind.execute(
                sa.text("""
            SELECT attname,pg_catalog.format_type(atttypid,atttypmod),attnotnull
            FROM pg_catalog.pg_attribute WHERE attrelid=:oid AND attnum>0 AND NOT attisdropped
            ORDER BY attnum
        """),
                {"oid": relation[0]},
            )
        ]
        constraints = set(
            bind.execute(
                sa.text("""
            SELECT pg_catalog.pg_get_constraintdef(oid) FROM pg_catalog.pg_constraint
            WHERE conrelid=:oid AND convalidated
        """),
                {"oid": relation[0]},
            ).scalars()
        )
        if actual != shape or constraints != expected_constraints[table]:
            raise RuntimeError("Location retention local table shape differs")


def upgrade() -> None:
    schema = op.get_bind().execute(sa.text("SELECT current_schema()")).scalar_one()
    quoted_schema = op.get_bind().dialect.identifier_preparer.quote(schema)
    op.execute(f"SELECT pg_advisory_xact_lock(hashtextextended('{_LOCK}', 0))")
    op.execute("""
        ALTER TABLE connectors.owntracks_points
          ADD COLUMN IF NOT EXISTS retention_at TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS source_revision BIGINT NOT NULL DEFAULT 1
            CHECK (source_revision > 0),
          ADD COLUMN IF NOT EXISTS logical_source_digest BYTEA
            CHECK (octet_length(logical_source_digest)=32),
          ADD COLUMN IF NOT EXISTS content_digest BYTEA CHECK (octet_length(content_digest)=32),
          ADD COLUMN IF NOT EXISTS accepted_payload_digest BYTEA
            CHECK (octet_length(accepted_payload_digest)=32),
          ADD COLUMN IF NOT EXISTS accepted_normalized_digest BYTEA
            CHECK (octet_length(accepted_normalized_digest)=32),
          ADD COLUMN IF NOT EXISTS accepted_request_id UUID,
          ADD COLUMN IF NOT EXISTS source_input_generation UUID;
        UPDATE connectors.owntracks_points SET retention_at =
          CASE WHEN abs(extract(epoch FROM (ts-recorded_at))) <= 14400
               THEN ts ELSE recorded_at END
          WHERE retention_at IS NULL;
        ALTER TABLE connectors.owntracks_points ALTER COLUMN retention_at SET NOT NULL;
        CREATE OR REPLACE FUNCTION connectors.freeze_owntracks_birth()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path=pg_catalog,pg_temp AS $$
        BEGIN
          IF TG_OP='INSERT' THEN
            IF NEW.source_input_generation IS NOT NULL AND NOT EXISTS(
                SELECT 1 FROM connectors.owntracks_input_copy_births b
                WHERE b.copy_generation=NEW.source_input_generation
                  AND b.logical_source_digest=NEW.logical_source_digest
                  AND b.raw_digest=NEW.content_digest
                  AND b.copy_kind IN (2,3) AND b.producer_contract=1) THEN
              RAISE EXCEPTION 'Native point input birth differs';
            END IF;
            IF NEW.retention_at IS NULL THEN
              NEW.retention_at := CASE
                WHEN abs(extract(epoch FROM (NEW.ts-NEW.recorded_at))) <= 14400
                THEN NEW.ts ELSE NEW.recorded_at END;
            END IF;
          ELSIF NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'OwnTracks source revisions are immutable';
          END IF;
          RETURN NEW;
        END $$;
        DROP TRIGGER IF EXISTS freeze_owntracks_birth ON connectors.owntracks_points;
        CREATE TRIGGER freeze_owntracks_birth BEFORE INSERT OR UPDATE
          ON connectors.owntracks_points FOR EACH ROW
          EXECUTE FUNCTION connectors.freeze_owntracks_birth();
        CREATE INDEX IF NOT EXISTS ix_owntracks_points_arrival
          ON connectors.owntracks_points(recorded_at, id);
        CREATE TABLE IF NOT EXISTS connectors.owntracks_retention_tombstones (
          logical_source_digest BYTEA PRIMARY KEY CHECK(octet_length(logical_source_digest)=32),
          raw_id UUID NOT NULL,
          source_revision BIGINT NOT NULL CHECK(source_revision>0),
          retention_at TIMESTAMPTZ NOT NULL,
          decision_id UUID NOT NULL,
          batch_id UUID NOT NULL,
          purged_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS connectors.owntracks_retention_batches (
          batch_id UUID PRIMARY KEY,
          decision_id UUID NOT NULL UNIQUE,
          grant_id UUID NOT NULL,
          manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
          policy_version BIGINT NOT NULL CHECK(policy_version>0),
          cutoff TIMESTAMPTZ NOT NULL,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          deleted_count INTEGER NOT NULL CHECK(deleted_count>=0),
          already_forgotten_count INTEGER NOT NULL CHECK(already_forgotten_count>=0)
        );
        CREATE TABLE IF NOT EXISTS connectors.owntracks_retention_batch_rows (
          batch_id UUID NOT NULL REFERENCES connectors.owntracks_retention_batches(batch_id),
          raw_id UUID NOT NULL,
          source_revision BIGINT NOT NULL CHECK(source_revision>0),
          logical_source_digest BYTEA NOT NULL CHECK(octet_length(logical_source_digest)=32),
          disposition TEXT NOT NULL CHECK(disposition IN ('deleted','already_forgotten')),
          PRIMARY KEY(batch_id,raw_id,source_revision),
          FOREIGN KEY(logical_source_digest)
            REFERENCES connectors.owntracks_retention_tombstones(logical_source_digest)
        );
        CREATE OR REPLACE FUNCTION connectors.preserve_owntracks_retention_history()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path=pg_catalog,pg_temp AS $$
        BEGIN
          RAISE EXCEPTION 'OwnTracks retention history is permanent';
        END $$;
    """)
    # Fixed connector-owned copy metadata; trusted installer only. These
    # source floors/receipts do not certify projection or remote recipients.
    op.execute(filtered_copy_schema_sql())
    # Native point source bindings are validated by the actual INSERT trigger
    # and remain immutable thereafter; input history itself cannot be deleted.
    # A raw-point FK would be replayed by ordinary pg_dump before scoped input
    # histories arrive. Restore admission separately validates every complete
    # source reference after the exact staged history is installed.
    op.execute(filtered_copy_security_sql())
    # Per-owning-schema ledger: source-holder actions never write through a
    # peer role. Core replay also covers Switchboard-only and legacy public DBs.
    _create_local_tables(
        schema,
        local_schema_sql()
        + """
        CREATE TABLE IF NOT EXISTS location_retention_copy_receipts (
          decision_id UUID PRIMARY KEY,
          manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
          receipt_id UUID NOT NULL UNIQUE,
          source_kind TEXT NOT NULL CHECK(source_kind='switchboard_skipped'),
          forgotten_count INTEGER NOT NULL CHECK(forgotten_count>0),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_runtime_context_intents (
          input_generation UUID PRIMARY KEY,
          receiving_session UUID NOT NULL UNIQUE,
          server_request UUID,
          captured_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_runtime_context_bindings (
          input_generation UUID PRIMARY KEY REFERENCES location_runtime_context_intents,
          receiving_session UUID NOT NULL UNIQUE REFERENCES sessions(id),
          bundle_digest BYTEA NOT NULL CHECK(octet_length(bundle_digest)=32),
          context_digest BYTEA NOT NULL CHECK(octet_length(context_digest)=32),
          system_digest BYTEA NOT NULL CHECK(octet_length(system_digest)=32),
          prompt_digest BYTEA NOT NULL CHECK(octet_length(prompt_digest)=32),
          exclusive_input BOOLEAN NOT NULL,
          context_bytes INTEGER NOT NULL CHECK(context_bytes>=0),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_runtime_context_ended (
          input_generation UUID PRIMARY KEY REFERENCES location_runtime_context_intents,
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_runtime_context_server_finished (
          input_generation UUID PRIMARY KEY REFERENCES location_runtime_context_intents,
          server_request UUID NOT NULL,
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_runtime_context_episodes (
          input_generation UUID NOT NULL REFERENCES location_runtime_context_intents,
          episode_id UUID NOT NULL UNIQUE,
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY(input_generation,episode_id)
        );
        CREATE TABLE IF NOT EXISTS location_runtime_context_artifacts (
          artifact_generation UUID PRIMARY KEY,
          input_generation UUID NOT NULL REFERENCES location_runtime_context_intents,
          memory_table TEXT NOT NULL CHECK(memory_table IN ('facts','rules')),
          artifact_id UUID NOT NULL,
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          content_digest BYTEA CHECK(content_digest IS NULL OR octet_length(content_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_runtime_context_dispositions (
          input_generation UUID PRIMARY KEY REFERENCES location_runtime_context_intents,
          decision_id UUID NOT NULL,
          manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        ALTER TABLE location_runtime_context_dispositions
          ADD COLUMN IF NOT EXISTS reduced_system_digest BYTEA
            CHECK(reduced_system_digest IS NULL OR octet_length(reduced_system_digest)=32),
          ADD COLUMN IF NOT EXISTS reduced_provenance_digest BYTEA
            CHECK(reduced_provenance_digest IS NULL OR octet_length(reduced_provenance_digest)=32);
        CREATE TABLE IF NOT EXISTS location_catalog_copy_loans (
          loan_id UUID PRIMARY KEY,
          source_generation UUID NOT NULL,
          catalog_id UUID NOT NULL,
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          receiving_incarnation UUID NOT NULL,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_catalog_copy_lifetimes (
          loan_id UUID PRIMARY KEY REFERENCES location_catalog_copy_loans(loan_id),
          holder_kind TEXT NOT NULL CHECK(holder_kind IN (
            'server_response','runtime_session','unbound_processing')),
          holder_id UUID NOT NULL,
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_runtime_tool_intents (
          tool_generation UUID PRIMARY KEY,
          receiving_session UUID NOT NULL REFERENCES sessions(id),
          tool_name TEXT NOT NULL,
          module_name TEXT NOT NULL,
          input_digest BYTEA NOT NULL CHECK(octet_length(input_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_runtime_tool_inputs (
          tool_generation UUID NOT NULL REFERENCES location_runtime_tool_intents,
          loan_id UUID NOT NULL UNIQUE REFERENCES location_catalog_copy_loans,
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY(tool_generation,loan_id)
        );
        CREATE TABLE IF NOT EXISTS location_runtime_tool_results (
          tool_generation UUID PRIMARY KEY REFERENCES location_runtime_tool_intents,
          outcome TEXT NOT NULL CHECK(outcome IN ('success','error')),
          result_digest BYTEA CHECK(result_digest IS NULL OR octet_length(result_digest)=32),
          exclusive_inputs BOOLEAN NOT NULL,
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          CHECK((outcome='success')=(result_digest IS NOT NULL))
        );
        CREATE TABLE IF NOT EXISTS location_native_delegation_answers (
          answer_generation UUID PRIMARY KEY,
          ledger_id UUID NOT NULL UNIQUE,
          receiving_session UUID NOT NULL,
          tool_generation UUID NOT NULL REFERENCES location_runtime_tool_intents(tool_generation),
          context_generation UUID NOT NULL
            REFERENCES location_runtime_context_bindings(input_generation),
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          parent_count INTEGER NOT NULL CHECK(parent_count>=0),
          exclusive_input BOOLEAN NOT NULL,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        ALTER TABLE location_native_delegation_answers
          ADD COLUMN IF NOT EXISTS bundle_digest BYTEA
            CHECK(bundle_digest IS NULL OR octet_length(bundle_digest)=32);
        CREATE TABLE IF NOT EXISTS location_native_delegation_answer_parents (
          answer_generation UUID NOT NULL REFERENCES location_native_delegation_answers,
          parent_kind TEXT NOT NULL
            CHECK(parent_kind IN ('native_copy','catalog_loan',
              'received_question','received_answer')),
          parent_generation UUID NOT NULL,
          parent_digest BYTEA NOT NULL CHECK(octet_length(parent_digest)=32),
          PRIMARY KEY(answer_generation,parent_kind,parent_generation)
        );
        CREATE TABLE IF NOT EXISTS location_native_delegation_answer_dispositions (
          answer_generation UUID PRIMARY KEY REFERENCES location_native_delegation_answers,
          decision_id UUID NOT NULL,
          manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        ALTER TABLE location_native_delegation_answer_dispositions
          ADD COLUMN IF NOT EXISTS bundle_digest BYTEA CHECK(octet_length(bundle_digest)=32),
          ADD COLUMN IF NOT EXISTS question_digest BYTEA CHECK(octet_length(question_digest)=32),
          ADD COLUMN IF NOT EXISTS wake_key TEXT,
          ADD COLUMN IF NOT EXISTS reduced_digest BYTEA CHECK(octet_length(reduced_digest)=32),
          ADD COLUMN IF NOT EXISTS question_owner TEXT;
        CREATE TABLE IF NOT EXISTS location_native_answer_loans (
          loan_id UUID PRIMARY KEY,
          answer_generation UUID NOT NULL REFERENCES location_native_delegation_answers,
          receiving_generation UUID NOT NULL UNIQUE,
          receiver_name TEXT NOT NULL,
          receiving_incarnation UUID NOT NULL,
          bundle_digest BYTEA NOT NULL CHECK(octet_length(bundle_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        ALTER TABLE location_native_answer_loans
          ADD COLUMN IF NOT EXISTS source_incarnation UUID;
        CREATE TABLE IF NOT EXISTS location_native_answer_observations (
          loan_id UUID PRIMARY KEY REFERENCES location_native_answer_loans,
          decision_id UUID NOT NULL,
          manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
          receiver_receipt UUID NOT NULL,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_received_answer_attempts (
          receiving_generation UUID PRIMARY KEY,
          ledger_id UUID NOT NULL,
          source_name TEXT NOT NULL,
          wake_key TEXT NOT NULL,
          receiving_incarnation UUID NOT NULL,
          receiving_session UUID,
          tool_generation UUID REFERENCES location_runtime_tool_intents,
          server_request UUID,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          CHECK(server_request IS NOT NULL OR
            (receiving_session IS NOT NULL AND tool_generation IS NOT NULL))
        );
        CREATE TABLE IF NOT EXISTS location_received_answer_server_finished (
          receiving_generation UUID PRIMARY KEY REFERENCES location_received_answer_attempts,
          server_request UUID NOT NULL,
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_received_answer_inputs (
          receiving_generation UUID PRIMARY KEY REFERENCES location_received_answer_attempts,
          source_name TEXT NOT NULL,
          answer_generation UUID NOT NULL,
          loan_id UUID NOT NULL UNIQUE,
          bundle_digest BYTEA NOT NULL CHECK(octet_length(bundle_digest)=32),
          source_incarnation UUID NOT NULL,
          parent_count INTEGER NOT NULL CHECK(parent_count>=0),
          exclusive_input BOOLEAN NOT NULL,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_received_answer_schedules (
          receiving_generation UUID PRIMARY KEY REFERENCES location_received_answer_inputs,
          task_id UUID NOT NULL REFERENCES scheduled_tasks(id),
          prompt_digest BYTEA NOT NULL CHECK(octet_length(prompt_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_received_answer_floors (
          receiving_generation UUID PRIMARY KEY REFERENCES location_received_answer_attempts,
          decision_id UUID NOT NULL,
          manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
          bundle_digest BYTEA NOT NULL CHECK(octet_length(bundle_digest)=32),
          source_name TEXT NOT NULL,
          answer_generation UUID NOT NULL,
          ledger_id UUID NOT NULL,
          loan_id UUID NOT NULL,
          source_incarnation UUID NOT NULL,
          receiving_incarnation UUID NOT NULL,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_received_answer_qualifications (
          receiving_generation UUID PRIMARY KEY REFERENCES location_received_answer_floors,
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_received_answer_dispositions (
          receiving_generation UUID PRIMARY KEY REFERENCES location_received_answer_qualifications,
          receipt_id UUID NOT NULL UNIQUE,
          task_id UUID REFERENCES scheduled_tasks(id),
          reduced_prompt_digest BYTEA CHECK(octet_length(reduced_prompt_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          CHECK((task_id IS NULL)=(reduced_prompt_digest IS NULL))
        );
        CREATE TABLE IF NOT EXISTS location_received_answer_claims (
          claim_generation UUID PRIMARY KEY,
          task_id UUID NOT NULL REFERENCES scheduled_tasks(id),
          prompt_digest BYTEA NOT NULL CHECK(octet_length(prompt_digest)=32),
          bundle_digest BYTEA NOT NULL CHECK(octet_length(bundle_digest)=32),
          parent_count INTEGER NOT NULL CHECK(parent_count>=1),
          receiving_incarnation UUID NOT NULL,
          exclusive_input BOOLEAN NOT NULL,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_received_answer_claim_parents (
          claim_generation UUID NOT NULL REFERENCES location_received_answer_claims,
          receiving_generation UUID NOT NULL REFERENCES location_received_answer_inputs,
          bundle_digest BYTEA NOT NULL CHECK(octet_length(bundle_digest)=32),
          PRIMARY KEY(claim_generation,receiving_generation)
        );
        CREATE TABLE IF NOT EXISTS location_received_answer_claims_ended (
          claim_generation UUID PRIMARY KEY REFERENCES location_received_answer_claims,
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_runtime_context_answer_intents (
          input_generation UUID PRIMARY KEY REFERENCES location_runtime_context_intents,
          claim_generation UUID NOT NULL REFERENCES location_received_answer_claims,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_received_answer_contexts (
          input_generation UUID PRIMARY KEY REFERENCES location_runtime_context_intents,
          claim_generation UUID NOT NULL REFERENCES location_received_answer_claims,
          receiving_session UUID NOT NULL REFERENCES sessions(id),
          bundle_digest BYTEA NOT NULL CHECK(octet_length(bundle_digest)=32),
          claim_bundle_digest BYTEA NOT NULL CHECK(octet_length(claim_bundle_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_ordinary_delegation_inputs (
          source_generation UUID PRIMARY KEY,
          ledger_id UUID NOT NULL UNIQUE,
          producer_kind TEXT NOT NULL CHECK(producer_kind='birthday_gift_budget_ask'),
          render_date DATE NOT NULL,
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_native_delegation_inputs (
          question_generation UUID PRIMARY KEY,
          ledger_id UUID NOT NULL UNIQUE,
          receiving_session UUID NOT NULL,
          tool_generation UUID NOT NULL REFERENCES location_runtime_tool_intents(tool_generation),
          context_generation UUID NOT NULL
            REFERENCES location_runtime_context_bindings(input_generation),
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          parent_count INTEGER NOT NULL CHECK(parent_count>=0),
          exclusive_input BOOLEAN NOT NULL,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_native_delegation_parents (
          question_generation UUID NOT NULL REFERENCES location_native_delegation_inputs,
          parent_kind TEXT NOT NULL
            CHECK(parent_kind IN ('native_copy','catalog_loan',
              'received_question','received_answer')),
          parent_generation UUID NOT NULL,
          parent_digest BYTEA NOT NULL CHECK(octet_length(parent_digest)=32),
          PRIMARY KEY(question_generation,parent_kind,parent_generation)
        );
        CREATE TABLE IF NOT EXISTS location_native_delegation_loans (
          loan_id UUID PRIMARY KEY,
          question_generation UUID NOT NULL REFERENCES location_native_delegation_inputs,
          receiver_name TEXT NOT NULL,
          receiving_incarnation UUID NOT NULL,
          receiving_generation UUID NOT NULL,
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          UNIQUE(receiver_name,receiving_generation)
        );
        CREATE TABLE IF NOT EXISTS location_received_delegation_floors (
          receiving_generation UUID PRIMARY KEY,
          decision_id UUID NOT NULL,
          manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
          source_name TEXT NOT NULL CHECK(source_name<>''),
          question_generation UUID NOT NULL,
          ledger_id UUID NOT NULL,
          loan_id UUID NOT NULL UNIQUE,
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          receiving_incarnation UUID NOT NULL,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        DO $floor_source$
        BEGIN
          IF EXISTS(SELECT 1 FROM pg_catalog.pg_constraint
            WHERE conrelid='location_received_delegation_floors'::regclass
            AND conname='location_received_delegation_floors_source_name_check'
            AND pg_catalog.pg_get_constraintdef(oid)=
              'CHECK ((source_name = ''chronicler''::text))') THEN
            ALTER TABLE location_received_delegation_floors
              DROP CONSTRAINT location_received_delegation_floors_source_name_check;
            ALTER TABLE location_received_delegation_floors ADD CONSTRAINT
              location_received_delegation_floors_source_name_check CHECK(source_name<>'');
          END IF;
        END $floor_source$;
        CREATE TABLE IF NOT EXISTS location_received_delegation_dispositions (
          receiving_generation UUID PRIMARY KEY REFERENCES location_received_delegation_floors,
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_received_delegation_attempts (
          receiving_generation UUID PRIMARY KEY,
          ledger_id UUID NOT NULL,
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          receiving_incarnation UUID NOT NULL,
          receiving_session UUID REFERENCES sessions(id),
          tool_generation UUID REFERENCES location_runtime_tool_intents(tool_generation),
          server_request UUID,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          CHECK((receiving_session IS NULL)=(tool_generation IS NULL)),
          CHECK(tool_generation IS NOT NULL OR server_request IS NOT NULL)
        );
        ALTER TABLE location_received_delegation_attempts
          ADD COLUMN IF NOT EXISTS source_name TEXT;
        CREATE TABLE IF NOT EXISTS location_received_delegation_inputs (
          receiving_generation UUID PRIMARY KEY,
          ledger_id UUID NOT NULL,
          source_name TEXT NOT NULL,
          source_incarnation UUID NOT NULL,
          question_generation UUID NOT NULL,
          loan_id UUID NOT NULL UNIQUE,
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          receiving_incarnation UUID NOT NULL,
          parent_count INTEGER NOT NULL CHECK(parent_count>=0),
          exclusive_input BOOLEAN NOT NULL,
          receiving_session UUID REFERENCES sessions(id),
          tool_generation UUID REFERENCES location_runtime_tool_intents(tool_generation),
          server_request UUID,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          CHECK((receiving_session IS NULL)=(tool_generation IS NULL)),
          CHECK(tool_generation IS NOT NULL OR server_request IS NOT NULL)
        );
        CREATE TABLE IF NOT EXISTS location_received_delegation_server_finished (
          receiving_generation UUID PRIMARY KEY REFERENCES location_received_delegation_attempts,
          server_request UUID NOT NULL,
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_received_delegation_schedules (
          receiving_generation UUID PRIMARY KEY REFERENCES location_received_delegation_inputs,
          task_id UUID NOT NULL UNIQUE REFERENCES scheduled_tasks(id),
          prompt_digest BYTEA NOT NULL CHECK(octet_length(prompt_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_received_question_source_floors (
          source_name TEXT NOT NULL CHECK(source_name<>''),
          ledger_id UUID NOT NULL,
          question_generation UUID NOT NULL,
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          decision_id UUID NOT NULL,
          manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY(source_name,ledger_id)
        );
        CREATE TABLE IF NOT EXISTS location_received_question_recoveries (
          receiving_generation UUID PRIMARY KEY REFERENCES location_received_delegation_attempts,
          source_name TEXT NOT NULL,
          ledger_id UUID NOT NULL,
          question_generation UUID NOT NULL,
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          decision_id UUID NOT NULL,
          manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
          receiving_incarnation UUID NOT NULL,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          FOREIGN KEY(source_name,ledger_id) REFERENCES location_received_question_source_floors
        );
        CREATE TABLE IF NOT EXISTS location_received_question_recovery_dispositions (
          receiving_generation UUID PRIMARY KEY REFERENCES location_received_question_recoveries,
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_received_question_refusals (
          receiving_generation UUID PRIMARY KEY REFERENCES location_received_delegation_attempts,
          tool_generation UUID NOT NULL REFERENCES location_runtime_tool_intents(tool_generation),
          receiving_incarnation UUID NOT NULL,
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          result_digest BYTEA NOT NULL CHECK(octet_length(result_digest)=32),
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_received_question_task_dispositions (
          receiving_generation UUID PRIMARY KEY REFERENCES location_received_delegation_floors,
          task_id UUID NOT NULL REFERENCES scheduled_tasks(id),
          decision_id UUID NOT NULL,
          manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
          original_prompt_digest BYTEA NOT NULL CHECK(octet_length(original_prompt_digest)=32),
          reduced_prompt_digest BYTEA NOT NULL CHECK(octet_length(reduced_prompt_digest)=32),
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_received_delegation_claims (
          claim_generation UUID PRIMARY KEY,
          receiving_generation UUID NOT NULL REFERENCES location_received_delegation_inputs,
          task_id UUID NOT NULL REFERENCES scheduled_tasks(id),
          prompt_digest BYTEA NOT NULL CHECK(octet_length(prompt_digest)=32),
          receiving_incarnation UUID NOT NULL,
          exclusive_input BOOLEAN NOT NULL,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_received_delegation_claims_ended (
          claim_generation UUID PRIMARY KEY REFERENCES location_received_delegation_claims,
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_runtime_context_question_intents (
          input_generation UUID PRIMARY KEY REFERENCES location_runtime_context_intents,
          claim_generation UUID NOT NULL REFERENCES location_received_delegation_claims,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_received_delegation_contexts (
          input_generation UUID PRIMARY KEY REFERENCES location_runtime_context_intents,
          claim_generation UUID NOT NULL REFERENCES location_received_delegation_claims,
          receiving_session UUID NOT NULL REFERENCES sessions(id),
          bundle_digest BYTEA NOT NULL CHECK(octet_length(bundle_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_native_delegation_dispositions (
          question_generation UUID PRIMARY KEY REFERENCES location_native_delegation_inputs,
          decision_id UUID NOT NULL,
          manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        ALTER TABLE location_native_delegation_dispositions
          ADD COLUMN IF NOT EXISTS reduced_question_digest BYTEA
            CHECK(octet_length(reduced_question_digest)=32);

        CREATE TABLE IF NOT EXISTS location_native_question_answer_observations (
          question_generation UUID PRIMARY KEY
            REFERENCES location_native_delegation_inputs(question_generation),
          decision_id UUID NOT NULL,
          manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
          answer_owner TEXT NOT NULL,
          answer_generation UUID NOT NULL,
          answer_receipt UUID NOT NULL,
          answer_body_digest BYTEA NOT NULL CHECK(octet_length(answer_body_digest)=32),
          answer_bundle_digest BYTEA NOT NULL CHECK(octet_length(answer_bundle_digest)=32),
          wake_key TEXT NOT NULL,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_native_answer_question_observations (
          answer_generation UUID PRIMARY KEY
            REFERENCES location_native_delegation_answers(answer_generation),
          decision_id UUID NOT NULL,
          manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
          question_owner TEXT NOT NULL,
          question_generation UUID NOT NULL,
          question_receipt UUID NOT NULL,
          original_question_digest BYTEA NOT NULL CHECK(octet_length(original_question_digest)=32),
          reduced_question_digest BYTEA NOT NULL CHECK(octet_length(reduced_question_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_native_question_loan_observations (
          loan_id UUID PRIMARY KEY REFERENCES location_native_delegation_loans(loan_id),
          decision_id UUID NOT NULL,
          manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
          receiver_receipt UUID NOT NULL,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_catalog_copy_finished (
          loan_id UUID PRIMARY KEY REFERENCES location_catalog_copy_lifetimes(loan_id),
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_catalog_copy_dispositions (
          loan_id UUID PRIMARY KEY REFERENCES location_catalog_copy_loans(loan_id),
          decision_id UUID NOT NULL,
          manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_retention_source_floors (
          dedupe_digest BYTEA PRIMARY KEY CHECK(octet_length(dedupe_digest)=32),
          request_id UUID NOT NULL,
          decision_id UUID NOT NULL REFERENCES location_retention_copy_receipts(decision_id),
          logical_source_digest BYTEA NOT NULL CHECK(octet_length(logical_source_digest)=32)
        );
    """,
    )
    _validate_local_tables(schema)
    op.execute(f"""
        CREATE OR REPLACE FUNCTION {quoted_schema}.preserve_location_copy_history()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path=pg_catalog,pg_temp AS $$
        BEGIN
          RAISE EXCEPTION 'Location source floors are permanent';
        END $$;
        DROP TRIGGER IF EXISTS preserve_location_copy_history ON location_retention_copy_receipts;
        CREATE TRIGGER preserve_location_copy_history BEFORE UPDATE OR DELETE
          ON location_retention_copy_receipts FOR EACH ROW
          EXECUTE FUNCTION {quoted_schema}.preserve_location_copy_history();
        DROP TRIGGER IF EXISTS preserve_location_source_floor ON location_retention_source_floors;
        CREATE TRIGGER preserve_location_source_floor BEFORE UPDATE OR DELETE
          ON location_retention_source_floors FOR EACH ROW
          EXECUTE FUNCTION {quoted_schema}.preserve_location_copy_history();
    """)
    for table in (
        *LOCAL_TABLES,
        "location_catalog_copy_loans",
        "location_catalog_copy_dispositions",
        "location_catalog_copy_lifetimes",
        "location_catalog_copy_finished",
        "location_runtime_context_intents",
        "location_runtime_context_bindings",
        "location_runtime_context_ended",
        "location_runtime_context_server_finished",
        "location_runtime_context_episodes",
        "location_runtime_context_artifacts",
        "location_runtime_context_dispositions",
        "location_runtime_tool_intents",
        "location_runtime_tool_inputs",
        "location_runtime_tool_results",
        "location_ordinary_delegation_inputs",
        "location_native_delegation_inputs",
        "location_received_answer_dispositions",
        "location_received_answer_qualifications",
        "location_received_answer_server_finished",
        "location_received_answer_claims",
        "location_received_answer_claim_parents",
        "location_received_answer_claims_ended",
        "location_runtime_context_answer_intents",
        "location_received_answer_contexts",
        "location_received_answer_schedules",
        "location_received_answer_floors",
        "location_received_answer_inputs",
        "location_received_answer_attempts",
        "location_native_question_answer_observations",
        "location_native_answer_question_observations",
        "location_native_question_loan_observations",
        "location_native_answer_observations",
        "location_native_answer_loans",
        "location_native_delegation_answer_parents",
        "location_native_delegation_answer_dispositions",
        "location_native_delegation_answers",
        "location_native_delegation_parents",
        "location_native_delegation_dispositions",
        "location_runtime_context_question_intents",
        "location_received_delegation_contexts",
        "location_received_delegation_claims_ended",
        "location_received_delegation_claims",
        "location_received_question_recovery_dispositions",
        "location_received_question_recoveries",
        "location_received_question_source_floors",
        "location_received_question_refusals",
        "location_received_question_task_dispositions",
        "location_received_delegation_schedules",
        "location_received_delegation_server_finished",
        "location_received_delegation_dispositions",
        "location_received_delegation_floors",
        "location_received_delegation_inputs",
        "location_received_delegation_attempts",
        "location_native_delegation_loans",
    ):
        op.execute(f"""
            DROP TRIGGER IF EXISTS preserve_location_copy_history ON {table};
            CREATE TRIGGER preserve_location_copy_history BEFORE UPDATE OR DELETE
            ON {table} FOR EACH ROW
            EXECUTE FUNCTION {quoted_schema}.preserve_location_copy_history();
        """)
    for table in _RECEIPTS:
        op.execute(f"""
            DROP TRIGGER IF EXISTS preserve_retention_history ON connectors.{table};
            CREATE TRIGGER preserve_retention_history BEFORE UPDATE OR DELETE
              ON connectors.{table} FOR EACH ROW
              EXECUTE FUNCTION connectors.preserve_owntracks_retention_history();
        """)
        op.execute(f"""
            DO $$ BEGIN
              IF EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='connector_writer') THEN
                GRANT SELECT,INSERT,UPDATE,DELETE ON connectors.{table} TO connector_writer;
              END IF;
              IF '{table}' <> 'owntracks_retention_tombstones' AND EXISTS(
                SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='butler_chronicler_rw') THEN
                GRANT SELECT ON connectors.{table} TO butler_chronicler_rw;
              ELSIF '{table}' = 'owntracks_retention_tombstones' AND EXISTS(
                SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='butler_chronicler_rw') THEN
                -- Bootstrap default privileges grant connector-table SELECT
                -- to every butler. Skipping an explicit grant cannot undo it.
                -- Only the connector owns this permanent source floor; the
                -- approved Chronicler observations are batch/header members.
                REVOKE ALL PRIVILEGES ON connectors.owntracks_retention_tombstones
                  FROM butler_chronicler_rw;
              END IF;
            END $$;
        """)

    op.execute(tool_input_dependency_sql(schema))


def downgrade() -> None:
    op.execute(f"SELECT pg_advisory_xact_lock(hashtextextended('{_LOCK}', 0))")
    # Core is replayed per schema: removing shared history while another schema
    # is still installed is forbidden even when the current table is empty.
    op.execute("""
        DO $$ BEGIN
          IF EXISTS(SELECT 1 FROM connectors.owntracks_input_server_births)
             OR EXISTS(SELECT 1 FROM connectors.owntracks_input_server_ends)
             OR EXISTS(SELECT 1 FROM connectors.owntracks_input_copy_births)
             OR EXISTS(SELECT 1 FROM connectors.owntracks_input_copy_ends)
             OR EXISTS(SELECT 1 FROM connectors.owntracks_filtered_copy_births)
             OR EXISTS(SELECT 1 FROM connectors.owntracks_filtered_copy_floors)
             OR EXISTS(SELECT 1 FROM connectors.owntracks_filtered_copy_batches)
             OR EXISTS(SELECT 1 FROM connectors.owntracks_filtered_copy_members)
             OR EXISTS(SELECT 1 FROM connectors.owntracks_retention_tombstones)
             OR EXISTS(SELECT 1 FROM connectors.owntracks_retention_batches)
             OR EXISTS(SELECT 1 FROM connectors.owntracks_points
                       WHERE accepted_request_id IS NOT NULL)
             OR EXISTS(SELECT 1 FROM location_ingress_server_births)
             OR EXISTS(SELECT 1 FROM location_retention_copy_receipts)
             OR EXISTS(SELECT 1 FROM location_catalog_copy_loans)
             OR EXISTS(SELECT 1 FROM location_runtime_context_intents)
             OR EXISTS(SELECT 1 FROM location_runtime_tool_intents)
             OR EXISTS(SELECT 1 FROM location_native_delegation_inputs)
             OR EXISTS(SELECT 1 FROM location_received_delegation_attempts)
             OR EXISTS(SELECT 1 FROM location_received_delegation_floors)
             OR EXISTS(SELECT 1 FROM location_received_delegation_dispositions) THEN
            RAISE EXCEPTION 'retention history exists; roll forward instead of erasing floors';
          END IF;
        END $$;
    """)
    # Shared additive columns/tables stay inert on downgrade. They are needed by
    # other independently upgraded schema installations and cannot resurrect raw
    # data. No worker is installed by this migration.
