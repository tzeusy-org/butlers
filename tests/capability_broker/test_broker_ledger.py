"""The broker on a migrated database: sw_047 ledger plus a domain's approvals.

One behavior test per row of the bu-cadcr1 behavior matrix that needs the
ledger; pure disposition rows live in ``test_policy.py``. The Switchboard side
runs as ``butler_switchboard_rw`` under sw_047 RLS; the owning domain
(``messenger``) is a second schema with the core and approvals chains.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from uuid import UUID, uuid4

import asyncpg
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from butlers.capability_broker.assertions import AssertionSigner, verify_assertion
from butlers.capability_broker.contracts import BrokerRefused, Catalog, Reason, mint_token
from butlers.capability_broker.domains import ApprovalsParking, DomainReceipt
from butlers.capability_broker.service import Broker
from butlers.core.capability_json import arguments_digest
from butlers.core.goal_admission import Grant, digest, encode_grants
from butlers.core.tool_catalog import RecipientParameters
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.capability_broker.helpers import capability, notify
from tests.switchboard.goal_ledger_fixtures import TABLES, codec

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

DOMAIN = "messenger"
OWNER = uuid4()
SEARCH = capability("search", "read", DOMAIN, resource_params=("account",))
UNTRUSTED = capability("read_mail", "read", DOMAIN, output_trust="untrusted")
SEND = capability(
    "send",
    "communicate",
    DOMAIN,
    idempotency="key_required",
    recipient_params=RecipientParameters(argument_names=("to",)),
)
TO_OWNER = {"to": str(OWNER), "text": "hi"}
NOTIFY = notify()
PAY = capability("pay", "transact", DOMAIN)
RULES = capability("set_rule", "administer", DOMAIN)
CATALOG = Catalog("catalog-v1", (SEARCH, UNTRUSTED, SEND, NOTIFY, PAY, RULES))


@pytest.fixture(scope="module")
def broker_db_url(postgres_container):
    from butlers.migrations import run_migrations

    url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core", "switchboard", "relationship"],
        schemas={
            "core": "switchboard",
            "switchboard": "switchboard",
            "relationship": "relationship",
        },
    )
    for chain in ("core", "approvals"):
        asyncio.run(run_migrations(url, chain=chain, schema=DOMAIN))
    return url


class StaticCatalog:
    def __init__(self, catalog=CATALOG):
        self.catalog = catalog

    async def load(self, connection):
        return self.catalog


class Forwarder:
    """The owning domain's verified tool wrapper, as seen by the broker."""

    def __init__(self):
        self.calls, self.receipt = [], DomainReceipt("committed", "receipt-1", {"ok": True})

    async def dispatch(self, reservation, args, assertion, *, idempotency_key):
        self.calls.append((reservation, args, assertion, idempotency_key))
        if isinstance(self.receipt, Exception):
            raise self.receipt
        return self.receipt


@dataclass
class World:
    owner: asyncpg.Pool
    domain: asyncpg.Pool
    broker: Broker
    forwarder: Forwarder
    signer: AssertionSigner
    keys: dict = field(default_factory=dict)

    async def goal(self, *grants: Grant, exposed=False, max_calls=10) -> UUID:
        goal_id, ceiling = uuid4(), encode_grants(grants)
        await self.owner.execute(
            "INSERT INTO switchboard.goals (id, source_key, source_kind, source_ref, origin, "
            "admitted_exposed, exposed, ceiling, ceiling_digest, catalog_digest, config_digest, "
            "chain_depth, status) VALUES ($1, $2, 'message', 'ref', 'owner', $3, $3, $4::jsonb, "
            "$5, 'catalog-v1', 'config', 0, 'running')",
            goal_id,
            f"message:{goal_id}",
            exposed,
            ceiling,
            digest(ceiling),
        )
        await self.owner.execute(
            "INSERT INTO switchboard.budgets (goal_id, max_invocations, max_children, max_calls, "
            "max_tokens, max_spend, deadline, config_snapshot) VALUES "
            "($1, 4, 2, $2, 1000, 1, now() + interval '1 hour', '{}'::jsonb)",
            goal_id,
            max_calls,
        )
        return goal_id

    async def invocation(self, goal_id, *grants, depth=0, epoch=0, parent=None):
        invocation_id, snapshot = uuid4(), encode_grants(grants)
        exposed = await self.owner.fetchval(
            "SELECT exposed FROM switchboard.goals WHERE id = $1", goal_id
        )
        await self.owner.execute(
            "INSERT INTO switchboard.invocations (id, goal_id, parent_invocation_id, depth, role, "
            "lead_epoch, status, grant_snapshot, grant_digest, exposed, max_turns, max_tokens) "
            "VALUES ($1, $2, $3, $4, $5, $6, 'launching', $7::jsonb, $8, $9, 5, 500)",
            invocation_id,
            goal_id,
            parent,
            depth,
            "lead" if depth == 0 else "child",
            epoch,
            snapshot,
            digest(snapshot),
            exposed,
        )
        token = await self.broker.issue_invocation_token(goal_id, invocation_id)
        await self.owner.execute(
            "UPDATE switchboard.invocations SET status = 'running' WHERE id = $1", invocation_id
        )
        return invocation_id, token

    async def effect(self, effect_id):
        return await self.owner.fetchrow(
            "SELECT * FROM switchboard.effects WHERE id = $1", effect_id
        )

    async def action(self, action_id):
        return await self.domain.fetchrow("SELECT * FROM pending_actions WHERE id = $1", action_id)

    def verify(self, assertion, capability_name, args):
        return verify_assertion(
            assertion,
            keys={self.signer.kid: self.signer.ed25519.public_key()},
            audience=DOMAIN,
            capability=capability_name,
            arguments=args,
            now=int(time.time()),
            claim_call_id=lambda call_id, exp: True,
        )


@asynccontextmanager
async def world(url, *, catalog=None, signer=True):
    async def role(connection):
        await connection.execute("SET ROLE butler_switchboard_rw")

    kwargs = {"init": codec, "min_size": 1, "max_size": 6}
    async with (
        asyncpg.create_pool(
            url, server_settings={"search_path": "switchboard,public"}, **kwargs
        ) as owner,
        asyncpg.create_pool(url, setup=role, **kwargs) as runtime,
        asyncpg.create_pool(
            url, server_settings={"search_path": f"{DOMAIN},public"}, **kwargs
        ) as domain,
    ):
        await owner.execute("TRUNCATE " + ",".join("switchboard." + t for t in TABLES) + " CASCADE")
        # Domain rows are keyed by fresh effect ids and rules by fresh targets, so
        # tests never see each other's approvals. Exactly one live owner exists.
        await owner.execute(
            "UPDATE public.entities SET roles = array_remove(roles, 'owner') "
            "WHERE 'owner' = ANY(roles) AND id <> $1",
            OWNER,
        )
        await owner.execute(
            "INSERT INTO public.entities (id, canonical_name, roles) VALUES "
            "($1, 'Broker Test Owner', ARRAY['owner']) ON CONFLICT DO NOTHING",
            OWNER,
        )
        forwarder = Forwarder()
        key = AssertionSigner("k1", Ed25519PrivateKey.generate())
        broker = Broker(
            runtime,
            StaticCatalog() if catalog is None else catalog,
            ApprovalsParking({DOMAIN: domain}, forwarder),
            key if signer else None,
        )
        yield World(owner, domain, broker, forwarder, key)


def _grant(cap, **kwargs) -> Grant:
    return Grant(cap.identity, **kwargs)


async def _refused(awaitable) -> BrokerRefused:
    with pytest.raises(BrokerRefused) as refused:
        await awaitable
    return refused.value


async def test_tokens_are_hashed_single_invocation_and_listing_is_the_grant(broker_db_url):
    async with world(broker_db_url) as w:
        grants = (_grant(SEARCH), _grant(PAY), _grant(RULES))
        goal = await w.goal(*grants)
        lead, token = await w.invocation(goal, *grants)
        stored = await w.owner.fetchval(
            "SELECT token_hash FROM switchboard.invocations WHERE id = $1", lead
        )
        assert stored != token and len(stored) == 64
        listed = await w.broker.list_tools(goal, token)
        assert [c.identity for c in listed] == [SEARCH.identity, RULES.identity]  # never transact
        child, child_token = await w.invocation(goal, *grants, depth=1, parent=lead)
        assert [c.identity for c in await w.broker.list_tools(goal, child_token)] == [
            SEARCH.identity  # administer is never available to a child
        ]
        unknown = (await _refused(w.broker.authenticate(goal, mint_token()[0]))).reason
        assert unknown is Reason.AUTHENTICATION
        await w.owner.execute(
            "UPDATE switchboard.invocations SET status = 'returned' WHERE id = $1", child
        )
        await w.broker.revoke_invocation_token(goal, child)
        revoked = await _refused(w.broker.authenticate(goal, child_token))
        assert revoked.reason is Reason.AUTHENTICATION


async def test_stale_lead_epoch_is_refused_but_its_child_survives(broker_db_url):
    async with world(broker_db_url) as w:
        goal = await w.goal(_grant(SEARCH))
        lead, lead_token = await w.invocation(goal, _grant(SEARCH))
        _, child_token = await w.invocation(goal, _grant(SEARCH), depth=1, parent=lead)
        await w.owner.execute(
            "INSERT INTO switchboard.invocations (id, goal_id, depth, role, lead_epoch, status, "
            "grant_snapshot, grant_digest, max_turns, max_tokens) VALUES ($1, $2, 0, 'lead', 1, "
            "'failed', '[]'::jsonb, $3, 5, 500)",
            uuid4(),
            goal,
            digest([]),
        )
        assert (await _refused(w.broker.authenticate(goal, lead_token))).reason is Reason.EPOCH
        result = await w.broker.call(goal, child_token, SEARCH.identity, {})
        assert result.status == "returned"


async def test_grant_and_resource_scope_are_checked_per_call(broker_db_url):
    async with world(broker_db_url) as w:
        grant = _grant(SEARCH, resources=(("account", ("a1",)),))
        goal = await w.goal(grant)
        _, token = await w.invocation(goal, grant)
        outside = await _refused(w.broker.call(goal, token, SEARCH.identity, {"account": "a2"}))
        assert outside.reason is Reason.RESOURCE
        missing = await _refused(w.broker.call(goal, token, SEND.identity, {"text": "x"}))
        assert missing.reason is Reason.CAPABILITY
        assert (await w.broker.call(goal, token, SEARCH.identity, {"account": "a1"})).value
        used = await w.owner.fetchval(
            "SELECT used_calls FROM switchboard.budgets WHERE goal_id = $1", goal
        )
        assert used == 1 and len(w.forwarder.calls) == 1


async def test_transact_is_refused_even_when_park_only(broker_db_url):
    async with world(broker_db_url) as w:
        grant = _grant(PAY, park_only=True)
        goal = await w.goal(grant)
        _, token = await w.invocation(goal, grant)
        assert (await _refused(w.broker.call(goal, token, PAY.identity, {}))).reason is (
            Reason.CAPABILITY
        )
        assert await w.owner.fetchval("SELECT count(*) FROM switchboard.effects") == 0
        assert w.forwarder.calls == []


async def test_budget_exhaustion_ends_the_goal(broker_db_url):
    async with world(broker_db_url) as w:
        goal = await w.goal(_grant(SEARCH), max_calls=1)
        _, token = await w.invocation(goal, _grant(SEARCH))
        await w.broker.call(goal, token, SEARCH.identity, {})
        refused = await _refused(w.broker.call(goal, token, SEARCH.identity, {}))
        assert refused.reason is Reason.BUDGET
        status = await w.owner.fetchval("SELECT status FROM switchboard.goals WHERE id = $1", goal)
        assert status == "budget_exhausted"


async def test_two_children_racing_the_last_unit_of_budget_exactly_one_wins(broker_db_url):
    async with world(broker_db_url) as w:
        goal = await w.goal(_grant(SEARCH), max_calls=1)
        lead, _ = await w.invocation(goal, _grant(SEARCH))
        tokens = [
            (await w.invocation(goal, _grant(SEARCH), depth=1, parent=lead))[1] for _ in range(2)
        ]
        outcomes = await asyncio.gather(
            *(w.broker.call(goal, t, SEARCH.identity, {}) for t in tokens), return_exceptions=True
        )
        assert sorted(type(o).__name__ for o in outcomes) == ["BrokerRefused", "BrokerResult"]
        assert len(w.forwarder.calls) == 1


async def test_non_owner_send_parks_through_the_domain_with_exact_args_and_lineage(
    broker_db_url,
):
    async with world(broker_db_url) as w:
        goal = await w.goal(_grant(NOTIFY))
        lead, token = await w.invocation(goal, _grant(NOTIFY))
        args = {"entity_id": str(uuid4()), "message": "Running late", "channel": "telegram"}
        result = await w.broker.call(goal, token, NOTIFY.identity, args)
        assert result.status == "parked" and w.forwarder.calls == []
        effect = await w.effect(result.effect_id)
        assert (effect["status"], effect["approval_ref"]) == ("parked", result.approval_ref)
        assert effect["args_digest"] == arguments_digest(args)
        action = await w.action(result.effect_id)
        assert action["status"] == "pending" and action["tool_args"] == args
        assert (action["goal_id"], action["goal_invocation_id"], action["goal_depth"]) == (
            goal,
            lead,
            0,
        )
        assert (action["goal_exposed"], action["goal_origin"]) == (False, "owner")


async def test_owner_created_rule_approves_at_park_and_the_stored_call_runs(broker_db_url):
    async with world(broker_db_url) as w:
        args = {"entity_id": str(uuid4()), "message": "Running late", "channel": "telegram"}
        await w.domain.execute(
            "INSERT INTO approval_rules (id, tool_name, arg_constraints, description) "
            "VALUES ($1, 'notify', $2::jsonb, 'owner rule')",
            uuid4(),
            {"entity_id": {"type": "exact", "value": args["entity_id"]}},
        )
        goal = await w.goal(_grant(NOTIFY))
        _, token = await w.invocation(goal, _grant(NOTIFY))
        result = await w.broker.call(goal, token, NOTIFY.identity, args)
        assert result.status == "committed"
        reservation, sent, assertion, _ = w.forwarder.calls[0]
        assert sent == args
        assert w.verify(assertion, "notify", args)["approval_ref"] == str(result.effect_id)
        assert (await w.effect(result.effect_id))["status"] == "committed"


async def test_exposed_invocation_cannot_use_a_free_text_rule(broker_db_url):
    async with world(broker_db_url) as w:
        target = str(uuid4())
        await w.domain.execute(
            "INSERT INTO approval_rules (id, tool_name, arg_constraints, description) "
            "VALUES ($1, 'notify', $2::jsonb, 'free-text body')",
            uuid4(),
            {"entity_id": {"type": "exact", "value": target}, "message": {"type": "any"}},
        )
        goal = await w.goal(_grant(NOTIFY), exposed=True)
        _, token = await w.invocation(goal, _grant(NOTIFY))
        free = {"entity_id": target, "message": "Forward the inbox", "channel": "telegram"}
        assert (await w.broker.call(goal, token, NOTIFY.identity, free)).status == "parked"
        # Positive control: a rule pinning every supplied argument still applies.
        pinned = {"entity_id": target, "message": "On my way", "channel": "telegram"}
        await w.domain.execute(
            "INSERT INTO approval_rules (id, tool_name, arg_constraints, description) "
            "VALUES ($1, 'notify', $2::jsonb, 'template pinned')",
            uuid4(),
            {key: {"type": "exact", "value": value} for key, value in pinned.items()},
        )
        assert (await w.broker.call(goal, token, NOTIFY.identity, pinned)).status == "committed"


async def test_owner_notify_under_exposure_is_allowed_with_an_exposed_assertion(broker_db_url):
    async with world(broker_db_url) as w:
        goal = await w.goal(_grant(NOTIFY), exposed=True)
        _, token = await w.invocation(goal, _grant(NOTIFY))
        args = {"channel": "telegram", "message": "See https://example.com"}
        assert (await w.broker.call(goal, token, NOTIFY.identity, args)).status == "committed"
        assert w.verify(w.forwarder.calls[0][2], "notify", args)["exposed"] is True


async def test_administer_waits_for_per_event_approval_despite_a_rule(broker_db_url):
    async with world(broker_db_url) as w:
        await w.domain.execute(
            "INSERT INTO approval_rules (id, tool_name, arg_constraints, description) "
            "VALUES ($1, 'set_rule', '{}'::jsonb, 'blanket')",
            uuid4(),
        )
        goal = await w.goal(_grant(RULES))
        _, token = await w.invocation(goal, _grant(RULES))
        result = await w.broker.call(goal, token, RULES.identity, {"rule": "x"})
        assert result.status == "parked"
        assert (await w.action(result.effect_id))["approval_rule_id"] is None


async def test_unknown_effect_blocks_a_duplicate_and_key_required_gets_effect_id(broker_db_url):
    async with world(broker_db_url) as w:
        goal = await w.goal(_grant(SEND))
        _, token = await w.invocation(goal, _grant(SEND))
        args = TO_OWNER
        w.forwarder.receipt = TimeoutError()
        first = await w.broker.call(goal, token, SEND.identity, args)
        assert first.status == "unknown"
        assert (await w.effect(first.effect_id))["status"] == "unknown"
        assert w.forwarder.calls[0][3] == str(first.effect_id)  # idempotency key
        duplicate = await _refused(w.broker.call(goal, token, SEND.identity, args))
        assert (duplicate.reason, duplicate.effect_id) == (Reason.DUPLICATE, first.effect_id)
        assert len(w.forwarder.calls) == 1


async def test_not_received_is_retried_once_then_failed(broker_db_url):
    async with world(broker_db_url) as w:
        goal = await w.goal(_grant(SEND))
        _, token = await w.invocation(goal, _grant(SEND))
        w.forwarder.receipt = DomainReceipt("not_received")
        result = await w.broker.call(goal, token, SEND.identity, TO_OWNER)
        assert result.status == "failed" and len(w.forwarder.calls) == 2
        assert (await w.effect(result.effect_id))["status"] == "failed"


async def test_approved_execution_runs_the_stored_or_edited_call(broker_db_url):
    async with world(broker_db_url) as w:
        goal = await w.goal(_grant(NOTIFY))
        _, token = await w.invocation(goal, _grant(NOTIFY))
        stored = {"entity_id": str(uuid4()), "message": "Draft", "channel": "telegram"}
        parked = await w.broker.call(goal, token, NOTIFY.identity, stored)
        done = await w.broker.execute_approved(goal, parked.effect_id, parked.approval_ref, stored)
        assert done.status == "committed" and w.forwarder.calls[-1][1] == stored
        assert (await w.effect(parked.effect_id))["status"] == "committed"

        other = {**stored, "message": "Second draft"}
        parked = await w.broker.call(goal, token, NOTIFY.identity, other)
        edited = {**other, "message": "Owner's edit"}
        done = await w.broker.execute_approved(goal, parked.effect_id, parked.approval_ref, edited)
        assert w.forwarder.calls[-1][1] == edited
        assert (await w.effect(parked.effect_id))["status"] == "rejected"
        executed = await w.effect(done.effect_id)
        assert executed["args_digest"] == arguments_digest(edited)
        assert executed["approval_ref"] == parked.approval_ref


async def test_stop_cancels_parked_effects_and_a_late_approval_never_dispatches(broker_db_url):
    async with world(broker_db_url) as w:
        goal = await w.goal(_grant(NOTIFY))
        _, token = await w.invocation(goal, _grant(NOTIFY))
        args = {"entity_id": str(uuid4()), "message": "Hello", "channel": "telegram"}
        first = await w.broker.call(goal, token, NOTIFY.identity, args)
        second_args = {**args, "message": "Hello again"}
        second = await w.broker.call(goal, token, NOTIFY.identity, second_args)
        await w.owner.execute("UPDATE switchboard.goals SET status = 'stopped' WHERE id = $1", goal)
        # An approval that arrives after Stop, before cancellation ran, is not dispatched.
        late = await _refused(
            w.broker.execute_approved(goal, first.effect_id, first.approval_ref, args)
        )
        assert late.reason is Reason.GOAL and w.forwarder.calls == []
        assert (await w.effect(first.effect_id))["status"] == "cancelled"
        assert await w.broker.cancel_parked(goal) == 1
        for parked in (first, second):
            assert (await w.effect(parked.effect_id))["status"] == "cancelled"
            assert (await w.action(parked.effect_id))["status"] == "cancelled"
        events = await w.domain.fetchval(
            "SELECT count(*) FROM approval_events WHERE event_type = 'action_cancelled' "
            "AND action_id = ANY($1::uuid[])",
            [first.effect_id, second.effect_id],
        )
        assert events == 2
        refused = await _refused(w.broker.call(goal, token, NOTIFY.identity, args))
        assert refused.reason is Reason.GOAL


async def test_untrusted_output_exposes_the_invocation_and_goal(broker_db_url):
    async with world(broker_db_url) as w:
        goal = await w.goal(_grant(UNTRUSTED))
        invocation, token = await w.invocation(goal, _grant(UNTRUSTED))
        await w.broker.call(goal, token, UNTRUSTED.identity, {})
        row = await w.owner.fetchrow(
            "SELECT i.exposed, g.exposed AS goal_exposed FROM switchboard.invocations i "
            "JOIN switchboard.goals g ON g.id = i.goal_id WHERE i.id = $1",
            invocation,
        )
        assert (row["exposed"], row["goal_exposed"]) == (True, True)


async def test_catalog_change_is_refused_until_republished(broker_db_url):
    changed = StaticCatalog(replace(CATALOG, digest="catalog-v2"))
    async with world(broker_db_url, catalog=changed) as w:
        goal = await w.goal(_grant(SEARCH))
        _, token = await w.invocation(goal, _grant(SEARCH))
        refused = await _refused(w.broker.call(goal, token, SEARCH.identity, {}))
        assert refused.reason is Reason.CATALOG and w.forwarder.calls == []
        changed.catalog = CATALOG
        assert (await w.broker.call(goal, token, SEARCH.identity, {})).status == "returned"


class _BrokenCatalog:
    async def load(self, connection):
        raise BrokerRefused(Reason.UNAVAILABLE)


class _BrokenPool:
    def acquire(self):
        raise OSError("ledger down")


async def test_broker_fails_closed_without_key_ledger_or_catalog(broker_db_url):
    async with world(broker_db_url) as w:
        goal = await w.goal(_grant(SEARCH))
        _, token = await w.invocation(goal, _grant(SEARCH))
        domains = ApprovalsParking({DOMAIN: w.domain}, w.forwarder)
        for broker in (
            Broker(w.broker.pool, StaticCatalog(), domains, None),
            Broker(w.broker.pool, _BrokenCatalog(), domains, w.signer),
            Broker(w.broker.pool, None, domains, w.signer),
            Broker(_BrokenPool(), StaticCatalog(), domains, w.signer),
        ):
            refused = await _refused(broker.call(goal, token, SEARCH.identity, {}))
            assert refused.reason is Reason.UNAVAILABLE
        assert w.forwarder.calls == []
        # Positive control: the same goal and token work with every dependency present.
        assert (await w.broker.call(goal, token, SEARCH.identity, {})).status == "returned"
