"""Record protocol owners; DB-free values do not claim SQL correctness."""

from __future__ import annotations

import pytest

from butlers.testing.records import Record, records, row

pytestmark = pytest.mark.unit


def test_record_key_index_and_value_iteration():
    record = row(id=7, label="seven", optional=None)
    assert isinstance(record, Record)
    assert record["id"] == record[0] == 7
    assert record[-1] is None
    assert record[1:] == ("seven", None)
    assert list(record) == [7, "seven", None]
    assert list(record.keys()) == ["id", "label", "optional"]
    assert list(record.values()) == list(record)
    assert list(record.items()) == [("id", 7), ("label", "seven"), ("optional", None)]
    assert dict(record) == {"id": 7, "label": "seven", "optional": None}
    assert len(record) == 3
    assert "id" in record and "missing" not in record
    assert record.get("optional", "fallback") is None
    assert record.get("missing", "fallback") == "fallback"


def test_record_refuses_missing_keys_attributes_and_mutation():
    record = row(id=7)
    with pytest.raises(KeyError, match="missing"):
        record["missing"]
    with pytest.raises(IndexError):
        record[1]
    with pytest.raises(AttributeError):
        _ = record.id
    with pytest.raises(TypeError):
        record["id"] = 8


def test_records_copy_columns_into_independent_instances():
    source = {"id": 7}
    first, second = records([source, source])
    source["id"] = 8
    assert first is not second
    assert first["id"] == second["id"] == 7
    assert row(id=7) is not row(id=7)
    assert records([]) == []
