"""Behavioral tests for consolidation output parser.

Tests exercise the parse_consolidation_output() function at the public API level.
Covers: valid JSON parsing, fenced/bare JSON, error handling, validation.
"""

from __future__ import annotations

import importlib.util
import json
import uuid

import pytest

from tests.modules.memory._test_helpers import MEMORY_MODULE_PATH

_PARSER_PATH = MEMORY_MODULE_PATH / "consolidation_parser.py"


def _load_parser():
    spec = importlib.util.spec_from_file_location("consolidation_parser", _PARSER_PATH)
    assert spec is not None, f"Could not create import spec for {_PARSER_PATH}"
    assert spec.loader is not None, f"Import spec for {_PARSER_PATH} has no loader"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_parser()
parse = _mod.parse_consolidation_output

pytestmark = pytest.mark.unit

UUID1 = str(uuid.uuid4())
UUID2 = str(uuid.uuid4())


def _json(payload: dict, fenced: bool = True) -> str:
    body = json.dumps(payload, indent=2)
    return f"```json\n{body}\n```" if fenced else body


# ---------------------------------------------------------------------------
# Valid parsing
# ---------------------------------------------------------------------------


class TestValidParsing:
    def test_bare_json_parsed(self) -> None:
        payload = {"new_facts": [{"subject": "s", "predicate": "p", "content": "c"}]}
        result = parse(_json(payload, fenced=False))
        assert len(result.new_facts) == 1
        assert result.parse_errors == []

    def test_full_payload(self) -> None:
        payload = {
            "new_facts": [{"subject": "s", "predicate": "p", "content": "c", "importance": 7.0}],
            "updated_facts": [
                {"target_id": UUID1, "subject": "s", "predicate": "p", "content": "c"}
            ],
            "new_rules": [{"content": "Always greet", "tags": ["ux"]}],
            "confirmations": [UUID1, UUID2],
        }
        result = parse(_json(payload))
        assert len(result.new_facts) == 1
        assert result.new_facts[0].importance == 7.0
        assert len(result.updated_facts) == 1
        assert len(result.new_rules) == 1
        assert result.confirmations == [UUID1, UUID2]
        assert result.parse_errors == []

    def test_empty_object_returns_empty_result(self) -> None:
        result = parse(_json({}))
        assert result.new_facts == [] and result.updated_facts == []
        assert result.new_rules == [] and result.confirmations == []
        assert result.parse_errors == []


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_no_json_returns_parse_error(self) -> None:
        result = parse("No JSON here at all.")
        assert len(result.parse_errors) == 1
        assert "No JSON block found" in result.parse_errors[0]

    def test_invalid_json_returns_parse_error(self) -> None:
        result = parse("```json\n{not valid!!!}\n```")
        assert len(result.parse_errors) == 1
        assert "Invalid JSON" in result.parse_errors[0]

    def test_facts_missing_required_fields_skipped(self) -> None:
        payload = {
            "new_facts": [
                {"subject": "s", "predicate": "p"},  # missing content
                {"subject": "s", "predicate": "p", "content": "c"},  # valid
            ]
        }
        result = parse(_json(payload))
        assert len(result.new_facts) == 1
        assert len(result.parse_errors) == 1

    def test_invalid_confirmation_uuid_skipped(self) -> None:
        result = parse(_json({"confirmations": [UUID1, "not-a-uuid", UUID2]}))
        assert result.confirmations == [UUID1, UUID2]
        assert len(result.parse_errors) == 1

    def test_invalid_updated_fact_uuid_skipped(self) -> None:
        payload = {
            "updated_facts": [
                {"target_id": "not-a-uuid", "subject": "s", "predicate": "p", "content": "c"}
            ]
        }
        result = parse(_json(payload))
        assert result.updated_facts == [] and len(result.parse_errors) == 1


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_invalid_permanence_defaults_to_standard(self) -> None:
        payload = {
            "new_facts": [
                {"subject": "s", "predicate": "p", "content": "c", "permanence": "forever"}
            ]
        }
        result = parse(_json(payload))
        assert result.new_facts[0].permanence == "standard"

    @pytest.mark.parametrize("perm", ["permanent", "ephemeral"])
    def test_valid_permanence_values_accepted(self, perm: str) -> None:
        payload = {
            "new_facts": [{"subject": "s", "predicate": "p", "content": "c", "permanence": perm}]
        }
        result = parse(_json(payload))
        assert result.new_facts[0].permanence == perm

    def test_importance_clamped_to_1_10(self) -> None:
        payload = {
            "new_facts": [
                {"subject": "lo", "predicate": "p", "content": "c", "importance": -5.0},
                {"subject": "hi", "predicate": "p", "content": "c", "importance": 99.0},
            ]
        }
        result = parse(_json(payload))
        assert result.new_facts[0].importance == 1.0
        assert result.new_facts[1].importance == 10.0

    def test_default_values_applied(self) -> None:
        payload = {"new_facts": [{"subject": "s", "predicate": "p", "content": "c"}]}
        result = parse(_json(payload))
        fact = result.new_facts[0]
        assert fact.permanence == "standard"
        assert fact.importance == 5.0
        assert fact.tags == []
