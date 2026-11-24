"""The future ledger's snapshot identity must be stable and bounded."""

import math

import pytest

from butlers.chronicler.derivation_config import MAX_SNAPSHOT_BYTES, serialize_snapshot

pytestmark = pytest.mark.unit


def test_snapshot_identity_is_canonical_and_kind_scoped() -> None:
    first = serialize_snapshot("anchors", {"z": [1, "home"], "a": {"y": True, "x": None}})
    same = serialize_snapshot("anchors", {"a": {"x": None, "y": True}, "z": [1, "home"]})
    assert first == same
    assert (
        first[0]
        != serialize_snapshot("place_refs", {"z": [1, "home"], "a": {"y": True, "x": None}})[0]
    )
    assert (
        first[0]
        != serialize_snapshot("anchors", {"z": [2, "home"], "a": {"y": True, "x": None}})[0]
    )


@pytest.mark.parametrize("value", [math.nan, math.inf, object(), {1: "x"}, (1, 2)])
def test_rejects_non_json_values_without_exposing_content(value: object) -> None:
    with pytest.raises(ValueError, match="configuration snapshot"):
        serialize_snapshot("anchors", {"value": value})


def test_snapshot_size_and_depth_are_bounded() -> None:
    with pytest.raises(ValueError, match="maximum size"):
        serialize_snapshot("ssid_map", {"value": "x" * MAX_SNAPSHOT_BYTES})
    nested: object = "leaf"
    for _ in range(22):
        nested = [nested]
    with pytest.raises(ValueError, match="maximum depth"):
        serialize_snapshot("ssid_map", {"value": nested})


def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown configuration snapshot kind"):
        serialize_snapshot("secrets", {})


def test_invalid_unicode_does_not_expose_snapshot_content() -> None:
    with pytest.raises(ValueError, match="invalid Unicode") as exc:
        serialize_snapshot("anchors", {"private": "sensitive\ud800"})
    assert "sensitive" not in str(exc.value)
