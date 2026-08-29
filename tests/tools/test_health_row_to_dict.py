"""Direct contract coverage for the canonical Health row decoder."""

from __future__ import annotations

import json
from types import MappingProxyType
from typing import Any
from unittest.mock import MagicMock

import asyncpg
import pytest

from butlers.tools.health._helpers import _row_to_dict

pytestmark = pytest.mark.unit


def _record_like(values: dict[str, Any]) -> MagicMock:
    """An asyncpg.Record-spec row double; asyncpg records cannot be constructed directly."""
    row = MagicMock(spec=asyncpg.Record)
    row.keys.return_value = values.keys()
    row.__getitem__.side_effect = values.__getitem__
    return row


@pytest.mark.parametrize(
    ("case", "row", "expected_metadata"),
    [
        (
            "mapping",
            MappingProxyType({"id": "mapping", "metadata": {"source": "mapping"}}),
            {"source": "mapping"},
        ),
        (
            "record",
            _record_like({"id": "record", "metadata": {"source": "record"}}),
            {"source": "record"},
        ),
        (
            "dict",
            {"id": "dict", "metadata": {"source": "dict"}},
            {"source": "dict"},
        ),
        (
            "json-string",
            {"id": "string", "metadata": '{"source": "string"}'},
            {"source": "string"},
        ),
        ("empty-none", {"id": "none", "metadata": None}, {}),
        ("empty-string", {"id": "empty", "metadata": ""}, {}),
    ],
)
def test_row_to_dict_normalizes_health_metadata(
    case: str, row: Any, expected_metadata: dict[str, str]
) -> None:
    """Health fact conversions accept mappings and records with the same metadata semantics."""
    result = _row_to_dict(row)

    assert result["metadata"] == expected_metadata, case


def test_row_to_dict_propagates_malformed_metadata_json() -> None:
    """A nonempty malformed JSON string remains an explicit data error."""
    with pytest.raises(json.JSONDecodeError):
        _row_to_dict({"metadata": "{not-json"})
