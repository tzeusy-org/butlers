"""Tests for the consolidation output parser."""

from __future__ import annotations

import importlib.util
import json
import uuid

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load the parser module from disk (roster/ is not a Python package).
# ---------------------------------------------------------------------------
_PARSER_PATH = MEMORY_MODULE_PATH / "consolidation_parser.py"


def _load_parser_module():
    spec = importlib.util.spec_from_file_location("consolidation_parser", _PARSER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_parser_module()
parse_consolidation_output = _mod.parse_consolidation_output
NewFact = _mod.NewFact
UpdatedFact = _mod.UpdatedFact
NewRule = _mod.NewRule
ConsolidationResult = _mod.ConsolidationResult

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UUID1 = str(uuid.uuid4())
_UUID2 = str(uuid.uuid4())
_UUID3 = str(uuid.uuid4())


def _make_full_payload() -> dict:
    """Return a complete valid consolidation payload."""
    return {
        "new_facts": [
            {
                "subject": "user",
                "predicate": "prefers",
                "content": "dark mode",
                "permanence": "stable",
                "importance": 7.0,
                "tags": ["preference", "ui"],
            },
        ],
        "updated_facts": [
            {
                "target_id": _UUID1,
                "subject": "user",
                "predicate": "lives_in",
                "content": "Berlin",
                "permanence": "standard",
            },
        ],
        "new_rules": [
            {
                "content": "Always greet the user by name",
                "tags": ["greeting"],
            },
        ],
        "confirmations": [_UUID2, _UUID3],
    }


def _wrap_json(payload: dict, *, fenced: bool = True, prefix: str = "", suffix: str = "") -> str:
    """Wrap a payload dict as text with optional fencing and surrounding text."""
    body = json.dumps(payload, indent=2)
    if fenced:
        return f"{prefix}```json\n{body}\n```{suffix}"
    return f"{prefix}{body}{suffix}"


# ---------------------------------------------------------------------------
# Tests: valid parsing
# ---------------------------------------------------------------------------


class TestValidParsing:
    def test_parses_valid_json_with_all_sections(self):
        payload = _make_full_payload()
        text = _wrap_json(payload)
        result = parse_consolidation_output(text)

        assert len(result.new_facts) == 1
        assert result.new_facts[0].subject == "user"
        assert result.new_facts[0].predicate == "prefers"
        assert result.new_facts[0].content == "dark mode"
        assert result.new_facts[0].permanence == "stable"
        assert result.new_facts[0].importance == 7.0
        assert result.new_facts[0].tags == ["preference", "ui"]

        assert len(result.updated_facts) == 1
        assert result.updated_facts[0].target_id == _UUID1
        assert result.updated_facts[0].subject == "user"
        assert result.updated_facts[0].content == "Berlin"

        assert len(result.new_rules) == 1
        assert result.new_rules[0].content == "Always greet the user by name"
        assert result.new_rules[0].tags == ["greeting"]

        assert result.confirmations == [_UUID2, _UUID3]
        assert result.parse_errors == []

    def test_parses_json_in_markdown_code_block(self):
        payload = {"new_facts": [], "confirmations": [_UUID1]}
        text = "Here is the result:\n```json\n" + json.dumps(payload) + "\n```\nDone."
        result = parse_consolidation_output(text)

        assert result.confirmations == [_UUID1]
        assert result.parse_errors == []

    def test_parses_bare_json_no_code_fences(self):
        payload = _make_full_payload()
        text = _wrap_json(payload, fenced=False)
        result = parse_consolidation_output(text)

        assert len(result.new_facts) == 1
        assert len(result.updated_facts) == 1
        assert len(result.new_rules) == 1
        assert len(result.confirmations) == 2
        assert result.parse_errors == []

    def test_handles_text_before_and_after_json_block(self):
        payload = {"new_facts": [{"subject": "s", "predicate": "p", "content": "c"}]}
        text = _wrap_json(
            payload,
            fenced=True,
            prefix="I analyzed the episodes and here are my findings:\n\n",
            suffix="\n\nLet me know if you need anything else.",
        )
        result = parse_consolidation_output(text)

        assert len(result.new_facts) == 1
        assert result.new_facts[0].subject == "s"
        assert result.parse_errors == []

    def test_handles_text_before_and_after_bare_json(self):
        payload = {"confirmations": [_UUID1]}
        text = _wrap_json(
            payload,
            fenced=False,
            prefix="Analysis complete. Output:\n",
            suffix="\nEnd of output.",
        )
        result = parse_consolidation_output(text)

        assert result.confirmations == [_UUID1]
        assert result.parse_errors == []


# ---------------------------------------------------------------------------
# Tests: error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_returns_empty_result_for_no_json(self):
        result = parse_consolidation_output("No JSON here at all, just plain text.")

        assert result.new_facts == []
        assert result.updated_facts == []
        assert result.new_rules == []
        assert result.confirmations == []
        assert len(result.parse_errors) == 1
        assert "No JSON block found" in result.parse_errors[0]

    def test_returns_parse_error_for_invalid_json(self):
        text = "```json\n{invalid json content!!!}\n```"
        result = parse_consolidation_output(text)

        assert result.new_facts == []
        assert len(result.parse_errors) == 1
        assert "Invalid JSON" in result.parse_errors[0]

    def test_skips_facts_with_missing_required_fields(self):
        payload = {
            "new_facts": [
                {"subject": "user", "predicate": "likes"},  # missing content
                {"subject": "user", "content": "coffee"},  # missing predicate
                {"predicate": "likes", "content": "coffee"},  # missing subject
                {"subject": "user", "predicate": "likes", "content": "coffee"},  # valid
            ],
        }
        text = _wrap_json(payload)
        result = parse_consolidation_output(text)

        assert len(result.new_facts) == 1
        assert result.new_facts[0].content == "coffee"
        assert len(result.parse_errors) == 3
        for err in result.parse_errors:
            assert "missing required fields" in err

    def test_skips_updated_facts_with_missing_required_fields(self):
        payload = {
            "updated_facts": [
                {"target_id": _UUID1, "subject": "s", "predicate": "p"},  # missing content
                {
                    "target_id": _UUID1,
                    "subject": "s",
                    "predicate": "p",
                    "content": "c",
                },  # valid
            ],
        }
        text = _wrap_json(payload)
        result = parse_consolidation_output(text)

        assert len(result.updated_facts) == 1
        assert len(result.parse_errors) == 1

    def test_skips_new_rules_with_missing_content(self):
        payload = {
            "new_rules": [
                {"tags": ["oops"]},  # missing content
                {"content": "valid rule"},  # valid
            ],
        }
        text = _wrap_json(payload)
        result = parse_consolidation_output(text)

        assert len(result.new_rules) == 1
        assert result.new_rules[0].content == "valid rule"
        assert len(result.parse_errors) == 1


# ---------------------------------------------------------------------------
# Tests: validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_validates_permanence_rejects_invalid_defaults_to_standard(self):
        payload = {
            "new_facts": [
                {
                    "subject": "s",
                    "predicate": "p",
                    "content": "c",
                    "permanence": "super_permanent",
                },
            ],
        }
        text = _wrap_json(payload)
        result = parse_consolidation_output(text)

        assert len(result.new_facts) == 1
        assert result.new_facts[0].permanence == "standard"
        # No parse_error for this -- just a log warning and default applied
        assert result.parse_errors == []

    def test_validates_all_valid_permanence_values(self):
        for perm in ("permanent", "stable", "standard", "volatile", "ephemeral"):
            payload = {
                "new_facts": [
                    {"subject": "s", "predicate": "p", "content": "c", "permanence": perm}
                ],
            }
            text = _wrap_json(payload)
            result = parse_consolidation_output(text)
            assert result.new_facts[0].permanence == perm

    def test_clamps_importance_to_1_10(self):
        payload = {
            "new_facts": [
                {"subject": "low", "predicate": "p", "content": "c", "importance": -5.0},
                {"subject": "high", "predicate": "p", "content": "c", "importance": 99.0},
                {"subject": "normal", "predicate": "p", "content": "c", "importance": 5.5},
            ],
        }
        text = _wrap_json(payload)
        result = parse_consolidation_output(text)

        assert result.new_facts[0].importance == 1.0
        assert result.new_facts[1].importance == 10.0
        assert result.new_facts[2].importance == 5.5

    def test_validates_target_id_looks_like_uuid(self):
        payload = {
            "updated_facts": [
                {
                    "target_id": "not-a-uuid",
                    "subject": "s",
                    "predicate": "p",
                    "content": "c",
                },
            ],
        }
        text = _wrap_json(payload)
        result = parse_consolidation_output(text)

        assert result.updated_facts == []
        assert len(result.parse_errors) == 1
        assert "invalid UUID" in result.parse_errors[0]

    def test_validates_confirmation_uuids(self):
        payload = {
            "confirmations": [_UUID1, "not-a-uuid", _UUID2],
        }
        text = _wrap_json(payload)
        result = parse_consolidation_output(text)

        assert result.confirmations == [_UUID1, _UUID2]
        assert len(result.parse_errors) == 1
        assert "invalid UUID" in result.parse_errors[0]


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_ignores_extra_fields(self):
        payload = {
            "new_facts": [
                {
                    "subject": "s",
                    "predicate": "p",
                    "content": "c",
                    "extra_field": "ignored",
                    "another_extra": 42,
                },
            ],
            "unknown_section": [1, 2, 3],
        }
        text = _wrap_json(payload)
        result = parse_consolidation_output(text)

        assert len(result.new_facts) == 1
        assert result.new_facts[0].subject == "s"
        assert result.parse_errors == []

    def test_handles_empty_arrays(self):
        payload = {
            "new_facts": [],
            "updated_facts": [],
            "new_rules": [],
            "confirmations": [],
        }
        text = _wrap_json(payload)
        result = parse_consolidation_output(text)

        assert result.new_facts == []
        assert result.updated_facts == []
        assert result.new_rules == []
        assert result.confirmations == []
        assert result.parse_errors == []

    def test_handles_partial_output_some_sections_missing(self):
        # Only new_facts present, rest omitted entirely
        payload = {
            "new_facts": [
                {"subject": "s", "predicate": "p", "content": "c"},
            ],
        }
        text = _wrap_json(payload)
        result = parse_consolidation_output(text)

        assert len(result.new_facts) == 1
        assert result.updated_facts == []
        assert result.new_rules == []
        assert result.confirmations == []
        assert result.parse_errors == []

    def test_handles_completely_empty_object(self):
        text = _wrap_json({})
        result = parse_consolidation_output(text)

        assert result.new_facts == []
        assert result.updated_facts == []
        assert result.new_rules == []
        assert result.confirmations == []
        assert result.parse_errors == []

    def test_default_values_applied_when_optional_fields_missing(self):
        payload = {
            "new_facts": [
                {"subject": "s", "predicate": "p", "content": "c"},
            ],
        }
        text = _wrap_json(payload)
        result = parse_consolidation_output(text)

        fact = result.new_facts[0]
        assert fact.permanence == "standard"
        assert fact.importance == 5.0
        assert fact.tags == []

    def test_updated_fact_default_permanence(self):
        payload = {
            "updated_facts": [
                {
                    "target_id": _UUID1,
                    "subject": "s",
                    "predicate": "p",
                    "content": "c",
                },
            ],
        }
        text = _wrap_json(payload)
        result = parse_consolidation_output(text)

        assert result.updated_facts[0].permanence == "standard"

    def test_new_rule_default_tags(self):
        payload = {
            "new_rules": [{"content": "some rule"}],
        }
        text = _wrap_json(payload)
        result = parse_consolidation_output(text)

        assert result.new_rules[0].tags == []

    def test_tags_not_a_list_defaults_to_empty(self):
        payload = {
            "new_facts": [
                {"subject": "s", "predicate": "p", "content": "c", "tags": "not-a-list"},
            ],
        }
        text = _wrap_json(payload)
        result = parse_consolidation_output(text)

        assert result.new_facts[0].tags == []
