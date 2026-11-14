from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, cast

import pytest
from pydantic import ValidationError

from butlers.core import vendor_door_resolver as resolver
from butlers.core import vendor_doors as flag
from butlers.tools.switchboard.contracts import DoorReferenceV1

DOOR = uuid.uuid4()
TEMPLATE = "https://my.shop.example.com/orders/{order}/cancel"
ROW = {
    "id": DOOR,
    "version": 4,
    "kind": "cancel",
    "host": "example.com",
    "template": TEMPLATE,
    "slot_types": {"order": "order_ref"},
}
SLOTS = {"order": "ABC-123"}


class Reader:
    def __init__(self, row=ROW, error: BaseException | None = None, delay: float = 0):
        self.row, self.error, self.delay, self.queries = row, error, delay, []

    async def fetchrow(self, query, *args):
        self.queries.append((query, args))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.row


@pytest.fixture(autouse=True)
def live(monkeypatch):
    monkeypatch.setattr(flag, "VENDOR_DOORS_LIVE", True)


async def _resolve(reader, **kw):
    args = {"door_id": DOOR, "version": 4, "slots": SLOTS}
    return await resolver.resolve_door(cast(Any, reader), **{**args, **kw})


async def test_current_confirmed_door_resolves_to_a_derived_link():
    out = await _resolve(Reader())
    assert out is not None
    assert out.url == "https://my.shop.example.com/orders/ABC-123/cancel"
    assert out.host == "example.com" and out.provenance == "vendor_door"
    assert out.label == "Open example.com (vendor_door)" and out.link_preview is False


async def test_it_reads_only_the_current_confirmed_view_by_id():
    reader = Reader()
    await _resolve(reader)
    sql, args = reader.queries[0]
    assert "public.vendor_doors_current" in sql and "vendor_doors " not in sql
    assert args == (DOOR,)


async def test_dark_and_exposed_never_read_or_resolve(monkeypatch):
    reader = Reader()
    assert await _resolve(reader, exposed=True) is None
    monkeypatch.setattr(flag, "VENDOR_DOORS_LIVE", False)
    assert await _resolve(reader) is None
    assert reader.queries == []


@pytest.mark.parametrize("version", [3, 5])
async def test_a_stale_or_future_version_never_resolves(version):
    assert await _resolve(Reader(), version=version) is None


async def test_missing_row_is_inert():
    assert await _resolve(Reader(row=None)) is None


async def test_a_row_for_another_id_is_refused():
    assert await _resolve(Reader(row=ROW | {"id": uuid.uuid4()})) is None


async def test_read_failure_and_timeout_are_inert_not_partial():
    assert await _resolve(Reader(error=RuntimeError("db"))) is None
    assert await _resolve(Reader(delay=1), deadline_s=0.01) is None


async def test_cancellation_propagates_rather_than_yielding_a_link():
    with pytest.raises(asyncio.CancelledError):
        await _resolve(Reader(error=asyncio.CancelledError()))


@pytest.mark.parametrize(
    "slots",
    [
        {},
        {"order": "ABC", "extra": "x"},
        {"other": "ABC"},
        {"order": ""},
        {"order": "a/b"},
        {"order": "a?b=1"},
        {"order": "a#b"},
        {"order": "a%2fb"},
        {"order": "a@b"},
        {"order": "a:b"},
        {"order": ".."},
        {"order": ".hidden"},
        {"order": "x" * 65},
        {"order": 7},
    ],
)
async def test_hostile_or_mismatched_slots_never_resolve(slots):
    assert await _resolve(Reader(), slots=slots) is None


@pytest.mark.parametrize(
    "override",
    [
        {"host": "evil.org"},  # template host is not under the stored registrable host
        {"host": "my.shop.example.com"},  # not a registrable domain
        {"template": "http://my.shop.example.com/orders/{order}/cancel"},
        {"template": "https://my.shop.example.com/orders/{order}/cancel?x=1"},
        {"template": "https://my.shop.example.com/orders/{order}/../cancel"},
        {"template": "https://my.shop.example.com/o/{ORDER}/cancel"},
        {"template": "https://{order}.example.com/cancel"},
        {"template": "https://user@my.shop.example.com/orders/{order}/cancel"},
        {"template": "https://my.shop.example.com:8443/orders/{order}/cancel"},
        {"slot_types": {"order": "free_text"}},
        {"slot_types": {"order": "order_ref", "ghost": "order_ref"}},
        {"slot_types": "not json"},
        {"slot_types": None},
    ],
)
async def test_a_stored_row_that_breaks_a_door_rule_never_resolves(override):
    assert await _resolve(Reader(row=ROW | override)) is None


async def test_slot_types_may_arrive_as_json_text():
    out = await _resolve(Reader(row=ROW | {"slot_types": json.dumps({"order": "order_ref"})}))
    assert out is not None


async def test_the_rebuilt_url_is_length_bounded():
    long_template = "https://my.shop.example.com/" + "a" * 280 + "/{order}"
    row = ROW | {"template": long_template}
    assert await _resolve(Reader(row=row), slots={"order": "z" * 40}) is None


async def test_a_door_without_slots_resolves_with_none_supplied():
    row = ROW | {"template": "https://example.com/manage", "slot_types": {}}
    out = await _resolve(Reader(row=row), slots={})
    assert out is not None and out.url == "https://example.com/manage"


def test_reference_is_typed_and_has_no_url_field():
    ref = DoorReferenceV1(kind="door", door_id=DOOR, version=4, slots={"order": "A1"})
    assert ref.model_dump(mode="json")["door_id"] == str(DOOR)
    for extra in (
        {"url": "https://x.example.com"},
        {"host": "x"},
        {"label": "x"},
        {"template": "x"},
    ):
        with pytest.raises(ValidationError):
            DoorReferenceV1.model_validate(
                {"kind": "door", "door_id": str(DOOR), "version": 4, **extra}
            )
    with pytest.raises(ValidationError):
        DoorReferenceV1(kind="door", door_id=DOOR, version=0)
    with pytest.raises(ValidationError):
        DoorReferenceV1(kind="self", door_id=DOOR, version=1)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        DoorReferenceV1(kind="door", door_id=DOOR, version=1, slots={str(i): "x" for i in range(9)})
