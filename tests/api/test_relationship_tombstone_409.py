"""The merge-tombstone refusal maps to HTTP 409 on the fact-writing routes. [bu-gm93xc]

REQ-relationship-facts-002: HTTP surfaces map ``entity_tombstoned`` to 409 with the
same code, naming the survivor. One route per mapping style: the inline mapping in
``set_entity_preferred_channel`` and the shared ``_assert_fact_http`` wrapper used by
``add_entity_contact``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from butlers.tools.relationship.fact_authority import EntityTombstonedError
from tests.api.test_relationship_entities_contacts import (
    _CONTACTS_PATH,
    _EMAIL,
    _ENT_ID,
    _make_app,
    _make_owner_row,
    _post,
)
from tests.api.test_relationship_preferred_channel import _ASSERT_TARGET, _PATH, _put
from tests.api.test_relationship_preferred_channel import _make_app as _make_channel_app

pytestmark = pytest.mark.unit

_WRITER_TARGET = "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact"


def _assert_409(resp, survivor: str) -> None:
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "entity_tombstoned"
    assert detail["survivor_id"] == survivor
    assert detail["side"] == "subject"
    assert detail["entity_id"] == str(_ENT_ID)


async def test_preferred_channel_tombstoned_subject_returns_409_with_survivor() -> None:
    survivor = str(uuid4())
    err = EntityTombstonedError("subject", _ENT_ID, survivor)
    with patch(_ASSERT_TARGET, new=AsyncMock(side_effect=err)):
        resp = await _put(_make_channel_app(), {"channel": "telegram"}, path=_PATH)
    _assert_409(resp, survivor)


async def test_add_contact_tombstoned_subject_returns_409_through_assert_fact_http() -> None:
    survivor = str(uuid4())
    err = EntityTombstonedError("subject", _ENT_ID, survivor)
    app, _ = _make_app(fetchrow_side_effect=[_make_owner_row()])
    with patch(_WRITER_TARGET, new=AsyncMock(side_effect=err)):
        resp = await _post(
            app, path=_CONTACTS_PATH, json_body={"predicate": "has-email", "value": _EMAIL}
        )
    _assert_409(resp, survivor)
