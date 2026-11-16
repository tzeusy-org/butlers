"""The connector parses a Chronicler retention grant fail-closed (bu-s11n0s.7 S4b-3b).

Whatever carries the grant to the connector, the payload is only grant ids, digests and the
cutoff. Anything else, or anything malformed, is no batch: the connector deletes nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from butlers.connectors.owntracks_retention import (
    MAX_GRANT_ITEMS,
    MalformedGrant,
    parse_grant,
)

_DIGEST = "v1:" + "b" * 64


def _item(key: str = "owntracks:alice:1:location", **overrides) -> dict:
    return {
        "raw_source_id": str(uuid4()),
        "logical_source_key": key,
        "content_digest": _DIGEST,
        **overrides,
    }


def _payload(**overrides) -> dict:
    return {
        "grant_id": str(uuid4()),
        "cutoff": "2026-03-01T00:00:00+00:00",
        "items": [_item()],
        **overrides,
    }


def test_a_well_formed_grant_parses() -> None:
    payload = _payload(items=[_item("k1"), _item("k2")])
    grant = parse_grant(payload)
    assert str(grant.grant_id) == payload["grant_id"]
    assert grant.cutoff == datetime(2026, 3, 1, tzinfo=UTC)
    assert [item.logical_source_key for item in grant.items] == ["k1", "k2"]


def test_an_empty_grant_parses() -> None:
    assert parse_grant(_payload(items=[])).items == ()


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(_payload(extra="x"), id="extra-field"),
        pytest.param({k: v for k, v in _payload().items() if k != "cutoff"}, id="no-cutoff"),
        pytest.param(_payload(cutoff="2026-03-01T00:00:00"), id="naive-cutoff"),
        pytest.param(_payload(grant_id="not-a-uuid"), id="bad-grant-id"),
        pytest.param(_payload(items=[_item(content_digest="legacy")]), id="legacy-digest"),
        pytest.param(_payload(items=[_item(content_digest="v1:" + "B" * 64)]), id="upper-hex"),
        pytest.param(_payload(items=[_item(logical_source_key=" ")]), id="blank-key"),
        pytest.param(_payload(items=[_item(lat=1.3)]), id="item-carries-coordinates"),
        pytest.param(_payload(items=[_item("k"), _item("k")]), id="duplicate-key"),
        pytest.param(
            _payload(
                items=[
                    _item("k1", raw_source_id=(rid := str(uuid4()))),
                    _item("k2", raw_source_id=rid),
                ]
            ),
            id="duplicate-raw-id",
        ),
        pytest.param(
            _payload(items=[_item(f"k{n}") for n in range(MAX_GRANT_ITEMS + 1)]), id="too-many"
        ),
        pytest.param(_payload(items="k1"), id="items-not-a-list"),
        pytest.param(None, id="not-a-mapping"),
    ],
)
def test_a_malformed_grant_is_refused(payload) -> None:
    with pytest.raises(MalformedGrant):
        parse_grant(payload)


_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _ROOT / "src/butlers/connectors/owntracks_retention.py"


def test_nothing_executes_a_grant_yet() -> None:
    """Deleting raw fixes is irreversible: it stays uncalled until the owner enables it."""
    callers = [
        path.relative_to(_ROOT).as_posix()
        for top in ("src", "roster", "scripts")
        for path in (_ROOT / top).rglob("*.py")
        if path != _MODULE
        and any(
            name in path.read_text(encoding="utf-8")
            for name in (
                "execute_grant",
                "connectors.owntracks_retention",
                "owntracks_retention import",
            )
        )
    ]
    assert callers == []
