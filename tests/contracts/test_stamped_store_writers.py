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
from pathlib import Path

import pytest

from butlers.core.tool_presentation_inventory import TOOL_PRESENTATION_INVENTORY

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_MEMORY_MODULE = "src/butlers/modules/memory/"
_WRITE = re.compile(
    r"\b(?:UPDATE|INSERT\s+INTO)\s+(?:public\.)?"
    r"(facts|rules|episodes|dashboard_messages|dashboard_conversations|\{[^}]*\})(?=[\s(]|$)",
    re.I,
)
#: ``metadata = metadata || '{...}'`` merges a server-held literal, so it is not caller text.
_TEXT_ASSIGNMENT = re.compile(
    r"\b(content|metadata|subject|predicate)\s*=(?!=)(?!\s*metadata\s*\|\|\s*')", re.I
)
_READS_STORE = re.compile(r"\bFROM\s+(facts|episodes|rules)\b", re.I)
_STORE_WRITERS = {"store_fact", "store_episode", "store_rule"}
_STAMPS = (
    "taint_stamp",
    "taint_insert",
    "taint_update",
    "taint_goal_id",
    "_stamp_exposed_mutation",
)

#: Stamps an exposed write: the function must name a stamp helper or column.
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


def _sources() -> Iterator[tuple[str, str]]:
    for root in ("src/butlers", "roster"):
        for path in sorted((_ROOT / root).rglob("*.py")):
            relative = path.relative_to(_ROOT).as_posix()
            if not {"tests", "migrations", "alembic"} & set(path.parts):
                yield relative, path.read_text(encoding="utf-8")


def _sql_text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) else "{" + ast.unparse(part.value) + "}"
            for part in node.values
            if isinstance(part, ast.Constant | ast.FormattedValue)
        )
    return None


def _scopes(tree: ast.AST) -> Iterator[tuple[str, ast.AST, list[str]]]:
    """Each scope (innermost function or class, or the module) with the strings it holds."""

    def walk(
        node: ast.AST, name: str, owned: list[str]
    ) -> Iterator[tuple[str, ast.AST, list[str]]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                inner = f"{name}.{child.name}" if name else child.name
                strings: list[str] = []
                yield from walk(child, inner, strings)
                yield inner, child, strings
            else:
                if isinstance(child, ast.Expr) and isinstance(child.value, ast.Constant):
                    continue  # A docstring.
                text = _sql_text(child)
                if text is not None:
                    owned.append(text)
                if not isinstance(child, ast.JoinedStr):
                    yield from walk(child, name, owned)

    module: list[str] = []
    yield from walk(tree, "", module)
    yield "<module>", tree, module


def _is_store_write(path: str, match: re.Match[str]) -> bool:
    return not match.group(1).startswith("{") or path.startswith(_MEMORY_MODULE)


def _writers() -> dict[str, list[str]]:
    """``path::scope`` to the SQL of each statement it writes to a stamped store."""
    found: dict[str, list[str]] = {}
    for path, source in _sources():
        for name, _, strings in _scopes(ast.parse(source)):
            for text in strings:
                if any(_is_store_write(path, match) for match in _WRITE.finditer(text)):
                    found.setdefault(f"{path}::{name}", []).append(text)
    return found


def test_every_sql_writer_to_a_stamped_store_has_a_disposition():
    assert set(_writers()) == (STAMPS | NO_TEXT | NOT_MODEL_REACHABLE | CONVERSATION_ROUTING)


def test_a_stamping_writer_names_a_stamp():
    sources = dict(_sources())
    for key in sorted(STAMPS):
        path, name = key.split("::")
        scope = next(
            node for scoped, node, _ in _scopes(ast.parse(sources[path])) if scoped == name
        )
        assert any(stamp in ast.unparse(scope) for stamp in _STAMPS), key


def test_a_no_text_writer_assigns_no_text():
    writers = _writers()
    for key in sorted(NO_TEXT):
        for text in writers[key]:
            match = next(m for m in _WRITE.finditer(text))
            assert match.group(0).upper().startswith("UPDATE"), (key, text)
            assignments = re.split(r"\bWHERE\b", text[match.end() :], maxsplit=1, flags=re.I)[0]
            assert not _TEXT_ASSIGNMENT.search(assignments), (key, text)


def test_every_function_copying_a_stored_row_forward_keeps_its_stamp():
    copiers = set()
    for path, source in _sources():
        for name, node, strings in _scopes(ast.parse(source)):
            if name == "<module>" or not any(_READS_STORE.search(text) for text in strings):
                continue
            calls = {
                call.func.attr
                if isinstance(call.func, ast.Attribute)
                else getattr(call.func, "id", "")
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
            }
            if calls & _STORE_WRITERS:
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
