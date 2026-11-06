"""RFC 0038 D6: every writer to a stamped store is audited, so the list cannot silently grow.

A stamped read reports a row clean when its pointer is null and its authority is the
owner's. That is only true if nothing puts another writer's text into the row without
setting the pointer (bu-dhloi1.33). This scans the source for every SQL write to those
stores, and every function that copies a stored row forward through ``store_*``. A new
one fails here until someone decides which disposition it has.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from functools import cache
from pathlib import Path

import pytest

from butlers.core.tool_presentation_inventory import TOOL_PRESENTATION_INVENTORY

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
#: A table is named plainly, by schema, quoted, or by an f-string field. A field holding a
#: module-level string constant is resolved first, so only a real unknown needs a disposition.
_WRITE = re.compile(
    r"\b(?:UPDATE|INSERT\s+INTO|MERGE\s+INTO)\s+(?:ONLY\s+)?(?:\"?\w+\"?\.|\{[^}]*\}\.)?\"?"
    r"(facts|rules|episodes|dashboard_messages|dashboard_conversations|\{[^}]*\})\"?(?=[\s(]|$)",
    re.I,
)
#: ``metadata = metadata || '{...}'`` merges a server-held literal, so it is not caller text.
_TEXT_ASSIGNMENT = re.compile(
    r"\b(content|metadata|subject|predicate)\s*=(?!=)(?!\s*metadata\s*\|\|\s*')", re.I
)
_READS_STORE = re.compile(
    r"\bFROM\s+(?:ONLY\s+)?(?:\"?\w+\"?\.|\{[^}]*\}\.)?\"?(facts|episodes|rules)\b", re.I
)
_BASE_STORE_WRITERS = {"store_fact", "store_episode", "store_rule"}
#: Stamps an exposed write: each statement that writes text sets the pointer, unless the
#: function is in ``STAMPS_SEPARATELY``.
STAMPS = {
    "roster/finance/tools/alerts.py::alert_configure",
    "roster/finance/tools/facts.py::bulk_record_transactions",
    "roster/finance/tools/facts.py::bulk_update_transactions",
    "roster/health/tools/conditions.py::symptom_update",
    "roster/health/tools/diet.py::meal_update",
    "roster/health/tools/measurements.py::measurement_update",
    "roster/relationship/tools/dunbar.py::dunbar_tier_set",
    "roster/relationship/tools/facts.py::_fact_set_spo",
    "src/butlers/api/conversations.py::message_create_idempotent",
    "src/butlers/conversation_messages.py::message_create",
    "src/butlers/modules/memory/storage.py::_forget_with_correction_provenance",
    "src/butlers/modules/memory/storage.py::_insert_episode_record",
    "src/butlers/modules/memory/storage.py::_insert_fact_record",
    "src/butlers/modules/memory/storage.py::_stamp_exposed_mutation",
    "src/butlers/modules/memory/storage.py::_update_episode_record",
    "src/butlers/modules/memory/storage.py::mark_harmful",
    "src/butlers/modules/memory/storage.py::store_rule",
}

#: Drops the caller's text under exposure, then stamps in its own statement.
STAMPS_SEPARATELY = {
    "src/butlers/modules/memory/storage.py::_forget_with_correction_provenance",
    "src/butlers/modules/memory/storage.py::mark_harmful",
}

#: Changes no stored text: validity, counters, foreign keys, status. Every statement in
#: the function is an UPDATE that assigns none of content, metadata, subject, predicate.
NO_TEXT = {
    "roster/relationship/tools/gifts.py::gift_update_status",
    "roster/relationship/tools/loans.py::loan_settle",
    "src/butlers/api/conversations.py::message_set_session_id_if_null",
    "src/butlers/entity_fact_repoint.py::repoint_facts_on_conn",
    "src/butlers/modules/memory/consolidation_executor.py::_lock_and_renew_claim_for_persistence",
    "src/butlers/modules/memory/consolidation_executor.py::execute_consolidation",
    "src/butlers/modules/memory/reembedding.py::_process_tier",
    "src/butlers/modules/memory/search.py::recall",
    "src/butlers/modules/memory/storage.py::store_fact",
    "src/butlers/modules/memory/storage.py::_forget_plain",
    "src/butlers/modules/memory/storage.py::confirm_memory",
    "src/butlers/modules/memory/storage.py::endorse_fact",
    "src/butlers/modules/memory/storage.py::endorse_rule",
    "src/butlers/modules/memory/storage.py::get_memory",
    "src/butlers/modules/memory/storage.py::mark_helpful",
    "src/butlers/modules/memory/storage.py::retire_rule",
    "src/butlers/modules/memory/storage.py::retry_dead_letter_episode",
    "src/butlers/modules/memory/tools/entities.py::_retract_facts_on_conn",
    "src/butlers/modules/memory/tools/management.py::memory_reclassify",
}

#: No model-reachable tool runs it: jobs, connectors, dashboard routes, operator scripts.
#: This rests on reading the callers, not on a check.
NOT_MODEL_REACHABLE = {
    "roster/relationship/api/router.py::patch_entity_dunbar_tier",
    "roster/relationship/jobs/relationship_jobs.py::_backfill_object_entity_ids",
    "roster/relationship/jobs/relationship_jobs.py::run_fact_retraction_curation._auto_retract_owner_fact",
    "src/butlers/api/routers/conversations.py::_persist_dashboard_user_message",
    "src/butlers/api/routers/conversations.py::_submit_to_switchboard",
    "src/butlers/chronicler/adapters/home_assistant_sensor_activity.py::HomeAssistantSensorActivityAdapter._recheck_evidence_promotions",
    "src/butlers/chronicler/adapters/owntracks_ssid.py::OwnTracksSsidPresenceAdapter._tombstone_stale_mapping_episodes",
    "src/butlers/chronicler/adapters/situation.py::SituationInferredAdapter._tombstone_stale",
    "src/butlers/chronicler/storage.py::refresh_episode_evidence_refs",
    "src/butlers/chronicler/storage.py::upsert_episode",
    "src/butlers/modules/memory/authority_demotion.py::apply",
    "src/butlers/modules/memory/storage.py::invert_to_anti_pattern",
    "src/butlers/modules/memory/storage.py::run_decay_sweep",
}

#: ``public.dashboard_conversations`` holds no text an audited read returns except
#: ``butler_name``, the server-held routing identity (bu-dhloi1.33).
CONVERSATION_ROUTING = {
    "src/butlers/api/conversations.py::conversation_create",
    "src/butlers/api/conversations.py::conversation_get_or_create_by_thread",
    "src/butlers/api/conversations.py::conversation_message_count_increment",
    "src/butlers/api/conversations.py::conversation_set_routed_butler",
    "src/butlers/api/conversations.py::conversation_unarchive_if_needed",
    "src/butlers/api/conversations.py::conversation_update",
    "src/butlers/conversation_messages.py::conversation_reply_create",
    "src/butlers/core/conversation_sessions.py::conversation_clear_provider_session",
    "src/butlers/core/conversation_sessions.py::conversation_set_provider_session",
}

#: Writes a table named at run time, without text in a stamped store. Each was read to confirm.
OTHER_TABLES = {
    # Its table list includes facts, but it only repoints the contact foreign key.
    "roster/relationship/tools/contacts.py::contact_merge",
    # The trip's own entity tables.
    "roster/travel/tools/bookings.py::update_itinerary",
    # A dashboard route that only sets validity on facts.
    "src/butlers/api/routers/memory.py::delete_entity._retire_facts",
    # Credential tables.
    "src/butlers/api/routers/secrets_v2.py::probe_system_credential",
    "src/butlers/api/routers/secrets_v2.py::set_system_credential",
    # Condition ledger tables.
    "src/butlers/core/condition_ledger.py::_confirm_episode",
    "src/butlers/core/condition_ledger.py::_link_identity_predecessor",
    "src/butlers/core/condition_ledger.py::_open_episode",
    "src/butlers/core/condition_ledger.py::_resolve_episode",
    # corrections and state; its memory path goes through memory_forget.
    "src/butlers/core/corrections.py::create_correction",
    "src/butlers/core/corrections.py::handle_data_correction",
    # Claim columns on the queue's table (attempts, lease), set by the queue policy.
    "src/butlers/core/queue_claim.py::claim_batch",
    "src/butlers/core/queue_claim.py::fail_claimed",
    # A benchmark's own table, run by hand.
    "src/butlers/testing/recall_bench.py::seed_embeddings",
}

#: Reads a stored row and writes a successor that keeps its text or metadata.
COPIES_FORWARD = {
    "roster/health/tools/conditions.py::condition_update",
    "roster/health/tools/medications.py::medication_log_dose",
    "roster/health/tools/medications.py::medication_update",
    "roster/health/tools/research.py::research_update",
    "roster/relationship/tools/gifts.py::gift_update_status",
    "roster/relationship/tools/loans.py::loan_settle",
    "roster/relationship/tools/tasks.py::task_complete",
}

#: Reads ids, counts or expiry from the store (dedupe, supersession lookups), no text.
READS_NO_TEXT = {
    "roster/finance/tools/facts.py::record_transaction_fact",
    "roster/relationship/jobs/relationship_jobs.py::run_interaction_sync",
    "roster/relationship/tools/gifts.py::gift_add_for_entity",
    "roster/relationship/tools/interactions.py::interaction_log",
    "roster/relationship/tools/life_events.py::life_event_log",
    "roster/relationship/tools/notes.py::note_create",
    "roster/relationship/tools/notes.py::note_create_for_entity",
    "src/butlers/modules/memory/tools/preferences.py::set_preference",
    "src/butlers/modules/memory/tools/writing.py::memory_store_episode",
    "src/butlers/modules/memory/tools/writing.py::memory_store_fact",
}

#: Presentable writers to these stores whose owner_authority does not refuse them under
#: exposure. All reach the store through the statements above or a stamped ``store_*``.
AUDITED_WRITER_TOOLS = {
    "bulk_record_transactions",
    "bulk_update_transactions",
    "commitment_resolve_from_utterance",
    "condition_add",
    "condition_update",
    "dunbar_tier_set",
    "gift_add",
    "gift_update_status",
    "ha_maintenance_complete",
    "interaction_log",
    "interaction_log_group",
    "life_event_log",
    "loan_create",
    "loan_settle",
    "meal_delete",
    "meal_log",
    "meal_update",
    "measurement_delete",
    "measurement_log",
    "measurement_update",
    "medication_add",
    "medication_log_dose",
    "memory_confirm",
    "memory_context",
    "memory_forget",
    "memory_get",
    "memory_mark_harmful",
    "memory_mark_helpful",
    "memory_recall",
    "memory_reclassify",
    "memory_reembed",
    "memory_run_consolidation",
    "memory_run_episode_cleanup",
    "memory_store_episode",
    "memory_store_fact",
    "memory_store_rule",
    "note_create",
    "reconcile_bills",
    "record_transaction",
    "research_delete",
    "research_save",
    "research_update",
    "symptom_delete",
    "symptom_log",
    "symptom_update",
    "task_complete",
    "task_create",
    "task_delete",
    "track_account_fact",
    "track_bill",
    "track_subscription_fact",
    "update_itinerary",
}


@cache
def _sources() -> tuple[tuple[str, str], ...]:
    return tuple(
        (path.relative_to(_ROOT).as_posix(), path.read_text(encoding="utf-8"))
        for root in ("src/butlers", "roster")
        for path in sorted((_ROOT / root).rglob("*.py"))
        if not {"tests", "migrations", "alembic"} & set(path.parts)
    )


def _sql_text(node: ast.AST, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        sides = [_sql_text(side, constants) for side in (node.left, node.right)]
        if sides == [None, None]:
            return None  # Not string concatenation that holds any SQL text.
        return "".join(
            text
            if text is not None
            else constants.get(ast.unparse(side), f"{{{ast.unparse(side)}}}")
            for text, side in zip(sides, (node.left, node.right), strict=True)
        )
    if isinstance(node, ast.JoinedStr):
        parts = []
        for part in node.values:
            if isinstance(part, ast.Constant):
                parts.append(str(part.value))
            elif isinstance(part, ast.FormattedValue):
                field = ast.unparse(part.value)
                parts.append(constants.get(field, "{" + field + "}"))
        return "".join(parts)
    return None


def _constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "text"`` (or an f-string of earlier ones), for resolving fields."""
    constants: dict[str, str] = {}
    for node in tree.body:
        value = node.value if isinstance(node, ast.Assign | ast.AnnAssign) else None
        targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
        text = None if value is None else _sql_text(value, constants)
        if text is not None and "{" not in text:
            constants.update({t.id: text for t in targets if isinstance(t, ast.Name)})
    return constants


def _scopes(tree: ast.Module) -> Iterator[tuple[str, ast.AST, list[str]]]:
    """Each scope (innermost function or class, or the module) with the strings it holds."""
    constants = _constants(tree)

    def walk(
        node: ast.AST, name: str, owned: list[str]
    ) -> Iterator[tuple[str, ast.AST, list[str]]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                inner = f"{name}.{child.name}" if name else child.name
                strings: list[str] = []
                yield from walk(child, inner, strings)
                yield inner, child, strings
            elif isinstance(child, ast.Raise):
                continue  # An error message may name a statement; it is not one.
            elif isinstance(child, ast.Expr) and isinstance(child.value, ast.Constant):
                continue  # A docstring.
            else:
                text = _sql_text(child, constants)
                if text is not None:
                    owned.append(text)
                if text is None:
                    yield from walk(child, name, owned)

    module: list[str] = []
    yield from walk(tree, "", module)
    yield "<module>", tree, module


@cache
def _writers() -> dict[str, list[str]]:
    """``path::scope`` to the SQL of each statement it writes to a stamped store."""
    found: dict[str, list[str]] = {}
    for path, source in _sources():
        for name, _, strings in _scopes(ast.parse(source)):
            for text in strings:
                if _WRITE.search(text):
                    found.setdefault(f"{path}::{name}", []).append(text)
    return found


def _scope_node(key: str) -> ast.AST:
    path, name = key.split("::")
    tree = ast.parse((_ROOT / path).read_text(encoding="utf-8"))
    return next(node for scoped, node, _ in _scopes(tree) if scoped == name)


def _called(call: ast.Call) -> str:
    return call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")


def _writes_text(statement: str) -> bool:
    """Whether any statement in this string inserts, merges or assigns text."""
    matches = list(_WRITE.finditer(statement))
    assert matches
    for match, following in zip(matches, [*matches[1:], None], strict=True):
        if not match.group(0).upper().startswith("UPDATE"):
            return True
        stop = following.start() if following else len(statement)
        body = statement[match.end() : stop]
        assignments = re.split(r"\bWHERE\b", body, maxsplit=1, flags=re.I)[0]
        if _TEXT_ASSIGNMENT.search(assignments):
            return True
    return False


@cache
def _store_writers() -> frozenset[str]:
    """``store_*`` and every function that calls one directly: a wrapper is a writer too."""
    wrappers = set()
    for _, source in _sources():
        for name, node, _strings in _scopes(ast.parse(source)):
            if name != "<module>" and isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                calls = {_called(c) for c in ast.walk(node) if isinstance(c, ast.Call)}
                if calls & _BASE_STORE_WRITERS:
                    wrappers.add(node.name)
    return frozenset(_BASE_STORE_WRITERS | wrappers)


def test_a_table_named_by_concatenation_is_seen():
    source = (
        'TABLE = "facts"\n'
        "async def a(pool, name):\n"
        '    await pool.execute("UPDATE " + TABLE + " SET content = $1")\n'
        '    await pool.execute("UPDATE " + name + " SET content = $1")\n'
        '    await pool.execute("UPDATE public.entities SET " + name)\n'
    )
    scopes = {scope: strings for scope, _, strings in _scopes(ast.parse(source))}
    assert [text for text in scopes["a"] if _WRITE.search(text)] == [
        "UPDATE facts SET content = $1",
        "UPDATE {name} SET content = $1",
    ]


def test_every_sql_writer_to_a_stamped_store_has_a_disposition():
    audited = STAMPS | NO_TEXT | NOT_MODEL_REACHABLE | CONVERSATION_ROUTING | OTHER_TABLES
    assert set(_writers()) == audited


def test_each_text_statement_of_a_stamping_writer_sets_the_pointer():
    """The statement itself carries the stamp, and its call passes the values."""
    writers = _writers()
    for key in sorted(STAMPS):
        source = ast.unparse(_scope_node(key))
        if key in STAMPS_SEPARATELY:
            assert "await _stamp_exposed_mutation(" in source, key
            continue
        statements = [text for text in writers[key] if _writes_text(text)]
        by_helper = [text for text in statements if "{taint." in text]
        by_column = [text for text in statements if "taint_goal_id" in text]
        assert len(by_helper) + len(by_column) == len(statements), (key, statements)
        assert source.count("taint.values") >= len(by_helper), key
        if by_column:
            assert "taint_stamp()" in source, key


def test_the_separate_stamp_statement_sets_both_columns():
    [statement] = _writers()["src/butlers/modules/memory/storage.py::_stamp_exposed_mutation"]
    assert "taint_goal_id = " in statement and "taint_invocation_id = " in statement


def test_a_no_text_writer_assigns_no_text():
    writers = _writers()
    for key in sorted(NO_TEXT):
        for text in writers[key]:
            assert not _writes_text(text), (key, text)


def test_a_copier_writes_only_inside_carrying_forward_from_a_stamped_read():
    for key in sorted(COPIES_FORWARD):
        node = _scope_node(key)
        carried = {
            id(inner)
            for block in ast.walk(node)
            if isinstance(block, ast.With | ast.AsyncWith)
            and any(
                isinstance(item.context_expr, ast.Call)
                and _called(item.context_expr) == "carrying_forward"
                for item in block.items
            )
            for inner in ast.walk(block)
        }
        writes = [
            call
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and _called(call) in _store_writers()
        ]
        assert writes, key
        assert all(id(call) in carried for call in writes), key
        assert "taint_goal_id" in ast.unparse(node), key


def test_every_function_copying_a_stored_row_forward_is_audited():
    """Sees a read written in the function. A read through a helper is not seen (see the doc)."""
    copiers = set()
    for path, source in _sources():
        for name, node, strings in _scopes(ast.parse(source)):
            if name == "<module>" or not any(_READS_STORE.search(text) for text in strings):
                continue
            calls = {_called(call) for call in ast.walk(node) if isinstance(call, ast.Call)}
            if calls & _store_writers():
                copiers.add(f"{path}::{name}")
                assert (f"{path}::{name}" in READS_NO_TEXT) != ("carrying_forward" in calls), name
    assert copiers == COPIES_FORWARD | READS_NO_TEXT


def test_every_presentable_writer_is_audited_or_refused_under_exposure():
    stores = {"facts", "rules", "episodes", "{DYNAMIC}"}
    unaudited = {
        item.canonical_name
        for item in TOOL_PRESENTATION_INVENTORY
        if item.llm_presentable and not item.owner_authority and stores & set(item.write_stores)
    }
    assert unaudited == AUDITED_WRITER_TOOLS
