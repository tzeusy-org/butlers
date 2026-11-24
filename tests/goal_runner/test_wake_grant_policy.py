"""Current policy for a wake lead: still published, and never transact (bu-dhloi1.305 PR3)."""

from __future__ import annotations

from dataclasses import replace

from butlers.core.goal_admission import Grant
from butlers.goal_runner.ledger import published
from tests.capability_broker.test_broker_ledger import CATALOG, PAY, SEARCH, SEND


def test_a_grant_survives_only_while_its_capability_is_published_and_not_transact():
    search = Grant(SEARCH.identity, (("q", ("a",)),), park_only=True)
    grants = (search, Grant(SEND.identity), Grant(PAY.identity), Grant("mail.unpublished"))

    assert published(grants, CATALOG) == (search, Grant(SEND.identity))
    without_send = replace(
        CATALOG, capabilities=tuple(c for c in CATALOG.capabilities if c != SEND)
    )
    assert published(grants, without_send) == (search,)
    assert published((), CATALOG) == ()
