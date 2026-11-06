"""Domain resource aliases resolve before owner law, and the call is pinned (bu-dhloi1.30).

A capability declares which resource params its domain resolves (for calendar,
``'primary'`` is the owner's calendar). The broker resolves them through the
trusted ``AliasResolver`` seam before the owner-law stage, matches owner terms on
canonical identifiers (the call's and the terms' own), and dispatches, digests
and parks the canonical arguments, so the domain cannot re-resolve to something
else. Anything unresolvable, a timeout or a non-fixed-point answer fails closed:
a term narrowed on that param selects the call, as for an aliasable param.
"""

from __future__ import annotations

import asyncio

import pytest

from butlers.capability_broker.aliases import CalendarAliases
from butlers.capability_broker.contracts import BrokerRefused, Catalog, Reason
from butlers.core.tool_catalog import RecipientParameters
from tests.capability_broker.helpers import capability
from tests.capability_broker.test_broker_ledger import (
    CATALOG,
    DOMAIN,
    StaticCatalog,
    _grant,
    _law,
    _refused,
    world,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

OWNER_CAL = "owner@example.com"
OTHER_CAL = "family123@group.calendar.google.com"

EVENTS = capability(
    "calendar_list_events",
    "read",
    DOMAIN,
    resource_params=("calendar_id",),
    alias_resolvers=(("calendar_id", "calendar"),),
)
CREATE = capability(
    "calendar_create_event",
    "record",
    DOMAIN,
    resource_params=("calendar_id",),
    alias_resolvers=(("calendar_id", "calendar"),),
    recipient_params=RecipientParameters(),
)
ALIASED = Catalog("catalog-v1", (*CATALOG.capabilities, EVENTS, CREATE))


class Aliases:
    """A domain's answers, as the trusted seam would give them."""

    def __init__(self, answers=None, delay=0.0):
        self.answers = {
            "primary": OWNER_CAL,
            OWNER_CAL: OWNER_CAL,
            OTHER_CAL: OTHER_CAL,
            # Not a fixed point: the domain would re-resolve its answer elsewhere.
            "drifting": "elsewhere",
            "elsewhere": "further",
        }
        self.answers.update(answers or {})
        self.delay, self.asked = delay, []

    async def resolve(self, domain, kind, values):
        self.asked.append((domain, kind, frozenset(values)))
        if self.delay:
            await asyncio.sleep(self.delay)
        return {value: self.answers[value] for value in values if value in self.answers}


async def _never_on(w, ids):
    return await _law(
        w,
        "capability",
        EVENTS.identity,
        "never",
        resources={"calendar_id": ids},
        resource_params=EVENTS.resource_params,
        alias_free_params=("calendar_id",),
    )


async def _calendar_world(url, aliases=None):
    return world(url, catalog=StaticCatalog(ALIASED), aliases=aliases)


async def test_a_term_on_the_owner_calendar_holds_its_primary_alias(broker_db_url):
    async with await _calendar_world(broker_db_url, Aliases()) as w:
        goal = await w.goal(_grant(EVENTS))
        _, token = await w.invocation(goal, _grant(EVENTS))
        await _never_on(w, [OWNER_CAL])
        for spelling in ("primary", OWNER_CAL):
            held = await _refused(
                w.broker.call(goal, token, EVENTS.identity, {"calendar_id": spelling})
            )
            assert held.reason is Reason.POLICY
        result = await w.broker.call(goal, token, EVENTS.identity, {"calendar_id": OTHER_CAL})
        assert result.status == "returned"
        assert w.forwarder.calls[-1][1] == {"calendar_id": OTHER_CAL}


async def test_a_term_written_with_the_alias_holds_the_canonical_id(broker_db_url):
    async with await _calendar_world(broker_db_url, Aliases()) as w:
        goal = await w.goal(_grant(EVENTS))
        _, token = await w.invocation(goal, _grant(EVENTS))
        await _never_on(w, ["primary"])
        held = await _refused(
            w.broker.call(goal, token, EVENTS.identity, {"calendar_id": OWNER_CAL})
        )
        assert held.reason is Reason.POLICY


async def test_the_domain_receives_and_the_assertion_binds_the_canonical_id(broker_db_url):
    async with await _calendar_world(broker_db_url, Aliases()) as w:
        goal = await w.goal(_grant(EVENTS))
        _, token = await w.invocation(goal, _grant(EVENTS))
        await w.broker.call(goal, token, EVENTS.identity, {"calendar_id": "primary"})
        _, args, assertion, _ = w.forwarder.calls[-1]
        assert args == {"calendar_id": OWNER_CAL}
        # The domain's verifier accepts only the pinned arguments it receives.
        assert w.verify(assertion, EVENTS.name, {"calendar_id": OWNER_CAL})
        with pytest.raises(BrokerRefused):
            w.verify(assertion, EVENTS.name, {"calendar_id": "primary"})


async def test_a_parked_call_stores_the_canonical_id(broker_db_url):
    async with await _calendar_world(broker_db_url, Aliases()) as w:
        goal = await w.goal(_grant(CREATE))
        _, token = await w.invocation(goal, _grant(CREATE))
        await _law(w, "capability", CREATE.identity, "always_ask")
        result = await w.broker.call(goal, token, CREATE.identity, {"calendar_id": "primary"})
        assert result.status == "parked"
        assert (await w.action(result.effect_id))["tool_args"] == {"calendar_id": OWNER_CAL}


@pytest.mark.parametrize(
    ("aliases", "spelling"),
    [
        (Aliases(), "unknown@example.com"),  # unresolvable
        (Aliases(), "drifting"),  # not a fixed point
        (Aliases(delay=5), "primary"),  # past the resolution deadline
        (None, "primary"),  # no resolver: the narrowing cannot be trusted
    ],
)
async def test_an_unresolved_alias_keeps_the_term_selected(broker_db_url, aliases, spelling):
    async with await _calendar_world(broker_db_url, aliases) as w:
        w.broker.alias_timeout = 0.2
        goal = await w.goal(_grant(EVENTS))
        _, token = await w.invocation(goal, _grant(EVENTS))
        # Without a term the call still runs, with the arguments as given.
        await w.broker.call(goal, token, EVENTS.identity, {"calendar_id": spelling})
        assert w.forwarder.calls[-1][1] == {"calendar_id": spelling}
        await _never_on(w, [OTHER_CAL])
        held = await _refused(
            w.broker.call(goal, token, EVENTS.identity, {"calendar_id": spelling})
        )
        assert held.reason is Reason.POLICY


async def test_an_unresolvable_term_id_drops_only_its_narrowing(broker_db_url):
    async with await _calendar_world(broker_db_url, Aliases()) as w:
        goal = await w.goal(_grant(EVENTS))
        _, token = await w.invocation(goal, _grant(EVENTS))
        await _never_on(w, ["gone@example.com"])
        held = await _refused(
            w.broker.call(goal, token, EVENTS.identity, {"calendar_id": OTHER_CAL})
        )
        assert held.reason is Reason.POLICY


async def test_the_approval_fence_holds_an_owner_edit_naming_the_alias(broker_db_url):
    async with await _calendar_world(broker_db_url, Aliases()) as w:
        goal = await w.goal(_grant(CREATE))
        _, token = await w.invocation(goal, _grant(CREATE))
        await _law(w, "capability", CREATE.identity, "always_ask")
        parked = await w.broker.call(goal, token, CREATE.identity, {"calendar_id": OTHER_CAL})
        await _law(
            w,
            "capability",
            CREATE.identity,
            "never",
            resources={"calendar_id": [OWNER_CAL]},
            resource_params=CREATE.resource_params,
            alias_free_params=("calendar_id",),
        )
        # The owner's edit names the alias. The approved call is never rewritten,
        # so its alias cannot release the term at the fence: the term holds it.
        edited = {"calendar_id": "primary"}
        held = await _refused(
            w.broker.execute_approved(goal, parked.effect_id, parked.approval_ref, edited)
        )
        assert held.reason is Reason.POLICY
        assert all(call[1] != edited for call in w.forwarder.calls)


async def test_calendar_aliases_resolve_from_the_discovered_provider_sources(broker_db_url):
    async with await _calendar_world(broker_db_url) as w:
        await w.domain.execute("DELETE FROM calendar_sources")
        aliases = CalendarAliases({DOMAIN: w.domain})
        assert await aliases.resolve(DOMAIN, "calendar", {"primary"}) == {}  # undiscovered
        for calendar_id, account in ((OWNER_CAL, OWNER_CAL), (OTHER_CAL, OWNER_CAL)):
            await w.domain.execute(
                "INSERT INTO calendar_sources (source_key, source_kind, provider, calendar_id, "
                "metadata) VALUES ($1, 'provider_event', 'google', $2, $3::jsonb)",
                f"provider:google:{calendar_id}",
                calendar_id,
                {"projection": "provider_sync", "account_email": account},
            )
        resolved = await aliases.resolve(
            DOMAIN, "calendar", {"primary", OWNER_CAL, f" {OTHER_CAL} ", "nope@example.com"}
        )
        assert resolved == {"primary": OWNER_CAL, OWNER_CAL: OWNER_CAL, f" {OTHER_CAL} ": OTHER_CAL}
        assert await aliases.resolve(DOMAIN, "entity", {"primary"}) == {}  # unknown kind
        assert await aliases.resolve("unknown_domain", "calendar", {"primary"}) == {}
        # Two discovered accounts make 'primary' ambiguous: it does not resolve.
        await w.domain.execute(
            "INSERT INTO calendar_sources (source_key, source_kind, provider, calendar_id, "
            "metadata) VALUES ('provider:google:b@example.com', 'provider_event', 'google', "
            "'b@example.com', $1::jsonb)",
            {"projection": "provider_sync", "account_email": "b@example.com"},
        )
        assert "primary" not in await aliases.resolve(DOMAIN, "calendar", {"primary"})
