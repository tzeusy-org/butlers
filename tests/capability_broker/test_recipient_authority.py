"""Real broker recipient producer with controlled SQL rows; no PG/catalog claim."""

from dataclasses import replace
from uuid import uuid4

import pytest

from butlers.capability_broker.contracts import (
    BrokerRefused,
    Capability,
    PolicyEvidence,
    Principal,
)
from butlers.capability_broker.policy import bind_recipient_policy, decide
from butlers.capability_broker.recipients import RecipientAuthorities
from butlers.core.capability_json import arguments_digest
from butlers.core.goal_admission import Grant
from butlers.core.tool_catalog import RecipientParameters
from tests.capability_broker.test_broker import Authorities

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def notify():
    return Capability(
        "health",
        "notify",
        "communicate",
        "untrusted",
        (("recipient", False), ("entity_id", False), ("request_context", False)),
        resource_params=("recipient", "entity_id", "request_context"),
        recipient_resolver="core_notify",
        recipient_params=RecipientParameters(
            ("recipient", "entity_id", "request_context"),
            undeterminable=True,
            default_owner_condition="only telegram send/insight after no explicit entity/recipient/context endpoint",
            undeterminable_condition="reply/react or unresolved inherited party",
        ),
    )


class Rows:
    def __init__(self):
        self.in_transaction = True
        self.owner = uuid4()
        self.owners = [dict(id=self.owner)]
        self.targets = {}
        self.events = []

    def is_in_transaction(self):
        return True

    async def fetch(self, sql, *args):
        self.events.append((sql, args))
        return self.owners

    async def fetchrow(self, sql, *args):
        self.events.append((sql, args))
        if "resolve_owner_triple" in sql:
            return self.targets.get("channel")
        return self.targets.get(args[0])


@pytest.mark.parametrize(
    "branch",
    [
        "implicit",
        "explicit_owner",
        "explicit_unknown",
        "explicit_non_owner",
        "ambiguous_owner",
        "channel_owner",
        "channel_unresolved",
        "conflicting_selectors",
        "reply",
        "context",
        "email_missing",
        "unbound_native",
    ],
)
async def test_actual_notify_recipient_branch_never_uses_text_as_owner_authority(branch):
    cap, connection = notify(), Rows()
    base = Authorities(cap)
    authority = RecipientAuthorities(base)
    principal = Principal(uuid4(), uuid4(), 0, "owner", False, "v1", (Grant(cap.identity),))
    args = {}
    if branch == "unbound_native":
        cap = replace(cap, recipient_resolver="none")
    expected_owner = branch in {"implicit", "explicit_owner", "channel_owner"}
    if branch in {
        "explicit_owner",
        "explicit_unknown",
        "explicit_non_owner",
        "conflicting_selectors",
    }:
        target = connection.owner if branch == "explicit_owner" else uuid4()
        args["entity_id"] = str(target)
        if branch != "explicit_unknown":
            connection.targets[target] = dict(id=target)
    if branch in {"channel_owner", "channel_unresolved", "conflicting_selectors"}:
        args.update(channel="email", recipient="owner@example.invalid")
        if branch != "channel_unresolved":
            connection.targets["channel"] = dict(entity_id=connection.owner, is_primary=True)
    if branch == "ambiguous_owner":
        connection.owners.append(dict(id=uuid4()))
    if branch == "reply":
        args["intent"] = "reply"
    if branch == "context":
        args["request_context"] = {"owner": True}
    if branch == "email_missing":
        args["channel"] = "email"
    evidence = await authority.policy(connection, principal, cap, args)
    bound = bind_recipient_policy(cap, args, arguments_digest(args), evidence)
    assert bound.recipient == ("owner" if expected_owner else "non_owner")
    decision = decide(principal, cap, principal.grants[0], bound)
    assert decision.disposition.value == ("dispatch" if expected_owner else "park")
    assert connection.events and all("public." in query for query, _ in connection.events)


async def test_other_declared_parties_are_conservatively_non_owner_without_native_resolver():
    cap = replace(notify(), name="calendar_respond")
    connection = Rows()
    authority = RecipientAuthorities(Authorities(cap))
    principal = Principal(uuid4(), uuid4(), 0, "owner", False, "v1", ())
    args = {"entity_id": str(connection.owner)}
    evidence = await authority.policy(connection, principal, cap, args)
    assert (
        bind_recipient_policy(cap, args, arguments_digest(args), evidence).recipient == "non_owner"
    )
    assert evidence.recipient_binding.undeterminable


async def test_recipient_free_calls_preserve_policy_and_no_identity_IO():
    cap = Capability(
        "health", "read", "read", "trusted", (), recipient_params=RecipientParameters()
    )
    base, connection = Authorities(cap), Rows()
    base.evidence = PolicyEvidence("none", template_pinned=True)
    authority = RecipientAuthorities(base)
    assert await authority.policy(connection, None, cap, {}) is base.evidence
    assert connection.events == []


async def test_recipient_resolution_refuses_outside_transaction_and_missing_owner_store():
    cap, connection = notify(), Rows()
    authority = RecipientAuthorities(Authorities(cap))
    connection.is_in_transaction = lambda: False
    with pytest.raises(BrokerRefused):
        await authority.policy(connection, None, cap, {})
    connection.is_in_transaction = lambda: True

    async def unavailable(*_):
        raise RuntimeError("controlled")

    connection.fetch = unavailable
    with pytest.raises(BrokerRefused):
        await authority.policy(connection, None, cap, {})


async def test_real_broker_admission_consumes_independent_SQL_owner_evidence():
    from tests.capability_broker.test_broker import build

    for explicit_unknown in (False, True):
        cap = replace(notify(), approvals_available=True)
        broker, connection, authority, domains, token = build(cap, Grant(cap.identity))
        identity_rows = Rows()
        invocation_rows = connection.fetch

        async def fetch(sql, *args):
            if "public.entities" in sql:
                assert connection.in_transaction
                return await identity_rows.fetch(sql, *args)
            return await invocation_rows(sql, *args)

        connection.fetch = fetch
        connection.is_in_transaction = lambda: connection.in_transaction
        broker.authorities = RecipientAuthorities(authority)
        args = (
            {"recipient": "unknown@example.invalid", "channel": "email"} if explicit_unknown else {}
        )
        connection.fetchrow_original = connection.fetchrow

        async def fetchrow(sql, *args):
            if "public.resolve_owner_triple" in sql:
                assert connection.in_transaction
                return None
            return await connection.fetchrow_original(sql, *args)

        connection.fetchrow = fetchrow
        result = await broker.call(connection.goal, token, cap.identity, args)
        assert result.status == ("parked" if explicit_unknown else "committed")
        assert identity_rows.events and connection.used == 1 and len(domains.calls) == 1
