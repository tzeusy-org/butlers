"""A fact applied from an external MCP proposal never reads as owner-untainted (bu-y66o00.49).

RFC 0039 D6 and REQ-capability-authorization-022/023: an outside proposal admits no goal, so
the applied fact carries no taint pointer. Its only marker is the server-stamped
``content_authority = 'third_party'`` and ``src = 'external_mcp:<id>'``. This pins that the
marker alone fails closed, end to end across the shipped machinery:

1. a session read of the fact goes through the real Relationship readers and the domain's
   stamp account, which reports the read as not owner-untainted;
2. the report crosses the broker's ``parse_report`` as no account;
3. the broker joins the unknown label (``{world}``, all six categories) into the invocation,
   which makes the invocation exposed;
4. an exposed invocation is refused the owner-authority write path and can only park, never
   dispatch, a communicate path.

Nothing here adds a column or touches the classify-and-route paths; the successor that would
stamp a real taint on these facts is deferred to the RFC 0038 hard cutover (bu-y66o00.10).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.capability_broker.contracts import Disposition
from butlers.capability_broker.policy import decide
from butlers.core.goal_admission import Grant
from butlers.core.goal_labels import CATEGORIES, Label, output_label
from butlers.core.stamp_account import STAMP_ACCOUNT_META, account_scope, parse_report
from butlers.testing.records import row
from butlers.tools.relationship.notes import note_list
from butlers.tools.relationship.relationship_lookup import (
    _fetch_identity_facts,
    _fetch_narrative_facts,
)
from tests.capability_broker.helpers import capability, notify, principal

pytestmark = pytest.mark.unit

READERS = [_fetch_identity_facts, _fetch_narrative_facts, note_list]


def _applied_external_fact(**overrides):
    """A row as the approved external apply writes it: verified, owner-endorsed, still third_party."""
    values = dict(
        id=uuid.uuid4(),
        predicate="employer",
        object="Acme",
        object_kind="literal",
        src=f"external_mcp:{uuid.uuid4()}",
        conf=1.0,
        verified=True,
        primary=None,
        observed_at=datetime(2026, 11, 1, tzinfo=UTC),
        last_seen=None,
        staleness_band="fresh",
        content="works at Acme",
        created_at=None,
        metadata={},
        content_authority="third_party",
        taint_goal_id=None,
        taint_invocation_id=None,
    )
    values.update(overrides)
    return row(**values)


def _owner_fact():
    return _applied_external_fact(src="owner", content_authority="owner")


async def _broker_label(read, rows) -> Label:
    """Read through the real reader in a verified-call scope, then join like the broker does."""
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=rows)
    call_id, digest = uuid.uuid4(), "d" * 64
    with account_scope() as account:
        await read(pool, uuid.uuid4())
    meta = {STAMP_ACCOUNT_META: account.report("recall", call_id, digest)}
    owner_untainted, writers = parse_report(
        meta, capability="recall", call_id=call_id, args_digest=digest
    )
    assert writers == () or owner_untainted  # a refused account never names writers
    return output_label("stamped", owner_untainted, CATEGORIES)


@pytest.mark.parametrize("read", READERS)
async def test_an_applied_external_fact_is_never_owner_untainted(read):
    with_external = await _broker_label(read, [_owner_fact(), _applied_external_fact()])
    assert with_external == Label.unknown()
    assert with_external.exposed

    only_external = await _broker_label(read, [_applied_external_fact()])
    assert only_external == Label.unknown() and only_external.exposed


@pytest.mark.parametrize("read", READERS)
async def test_the_owner_only_control_read_stays_unexposed(read):
    """Without this the test above could pass for a reader that taints everything."""
    label = await _broker_label(read, [_owner_fact()])
    assert not label.exposed


@pytest.mark.parametrize("read", READERS)
async def test_an_exposed_session_cannot_reach_a_write_capable_path_untainted(read):
    label = await _broker_label(read, [_applied_external_fact()])
    exposed = principal(depth=0, exposed=label.exposed)
    assert exposed.exposed
    refused = capability("remember_rule", "record", owner_authority=True)
    sent = capability("send", "communicate")
    for cap, recipient, expected in (
        (refused, "none", Disposition.REFUSE),
        (sent, "non_owner", Disposition.PARK),
        (sent, "owner", Disposition.PARK),
    ):
        grant = Grant(cap.identity)
        decision = decide(principal(grant, exposed=True), cap, grant, recipient, "safe")
        assert decision.disposition is expected, (cap.name, recipient)
        assert decision.disposition is not Disposition.DISPATCH
        if decision.disposition is Disposition.PARK:
            assert decision.allow_standing_rule is True  # the approvals module bounds exposed rules
    # The one exposed dispatch is an owner-recipient notify, whose content the domain neutralises.
    grant = Grant(notify().identity)
    assert decide(principal(grant, exposed=True), notify(), grant, "owner", "safe").disposition is (
        Disposition.DISPATCH
    )


@pytest.mark.parametrize("read", READERS)
async def test_a_fact_carrying_a_pointer_but_third_party_is_still_accounted_by_its_writer(read):
    """A stamped writer pointer does not launder the authority either: it is joined, not trusted."""
    writer = (uuid.uuid4(), uuid.uuid4())
    pool = MagicMock()
    pool.fetch = AsyncMock(
        return_value=[
            _applied_external_fact(taint_goal_id=writer[0], taint_invocation_id=writer[1])
        ]
    )
    with account_scope() as account:
        await read(pool, uuid.uuid4())
    assert account.writers == {writer}
