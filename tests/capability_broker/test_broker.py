"""Phase-A software controls, including the real FastMCP HTTP stack.

The connection/domain doubles below prove orchestration, not SQL concurrency,
RLS, a published catalog, an owning approvals write or live runtime wiring.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.testclient import TestClient

from butlers.capability_broker.assertions import AssertionSigner
from butlers.capability_broker.contracts import (
    BrokerRefused,
    BrokerResult,
    Capability,
    Disposition,
    PolicyEvidence,
    Principal,
    Reason,
    RecipientBinding,
    mint_token,
)
from butlers.capability_broker.domains import Catalog, DomainReceipt, ParkReceipt
from butlers.capability_broker.policy import decide
from butlers.capability_broker.server import create_app
from butlers.capability_broker.service import Broker
from butlers.core.capability_json import arguments_digest
from butlers.core.goal_admission import Grant, digest, encode_grants
from butlers.core.tool_catalog import RecipientParameters

pytestmark = pytest.mark.unit


class Connection:
    """Controlled rows, not a substitute PostgreSQL implementation."""

    def __init__(self, *, grant=None):
        self.goal, self.invocation = uuid4(), uuid4()
        self.grants = (grant or Grant("health__read"),)
        self.events = []
        self.in_transaction = False
        self.budget = 2
        self.used = 0
        self.status = "running"
        self.exposed = False
        self.effect = None
        self.epoch = 0
        self.fail_commit = False
        self.fail_exposure = False
        self.invocation_changes = {}
        self.invocation_count = 1

    @asynccontextmanager
    async def transaction(self):
        assert not self.in_transaction
        self.in_transaction = True
        try:
            yield
            if self.fail_commit:
                raise RuntimeError("controlled_commit_unknown")
            self.events.append("commit")
        finally:
            self.in_transaction = False

    async def fetch(self, sql, *args):
        return [
            dict(
                id=self.invocation,
                status="running",
                lease_valid=True,
                depth=0,
                lead_epoch=0,
                exposed=False,
                grant_snapshot=encode_grants(self.grants),
                grant_digest=digest(encode_grants(self.grants)),
            )
            | self.invocation_changes
            for _ in range(self.invocation_count)
        ]

    async def fetchval(self, sql, *args):
        return False if "pg_has_role" in sql else self.epoch

    async def fetchrow(self, sql, *args):
        if "pg_roles" in sql:
            return dict(name="butler_switchboard_rw", rolsuper=False, rolbypassrls=False)
        if "FROM switchboard.goals" in sql:
            return dict(
                status=self.status, origin="owner", exposed=self.exposed, catalog_digest="v1"
            )
        if "FROM switchboard.budgets" in sql:
            return dict(
                deadline_valid=True, max_calls=self.budget, used_calls=self.used, reserved_calls=0
            )
        if "SELECT id FROM switchboard.effects" in sql:
            return {"id": self.effect} if self.effect else None
        if "UPDATE switchboard.effects" in sql:
            self.events.append(args[2] if len(args) > 2 else "dispatched")
            return {"id": args[0]}
        raise AssertionError("unexpected_query")

    async def execute(self, sql, *args):
        if "SET used_calls" in sql:
            self.used += 1
        if "status='budget_exhausted'" in sql:
            self.status = "budget_exhausted"
        if "INSERT INTO switchboard.effects" in sql:
            self.effect = args[0]
            self.events.append("effect")
        if "SET exposed=true" in sql:
            if self.fail_exposure:
                raise RuntimeError("controlled_write_failure")
            self.exposed = True
        return "OK"


class Pool:
    def __init__(self, connection):
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class Authorities:
    def __init__(self, capability):
        self.capability = capability
        self.active_selection = True
        self.evidence = PolicyEvidence("none")

    async def catalog(self, connection, principal):
        assert connection.in_transaction
        return Catalog("v1", (self.capability,))

    async def selection(self, connection, principal, capability, grant):
        assert connection.in_transaction
        return self.active_selection

    async def policy(self, connection, principal, capability, args):
        assert connection.in_transaction
        return self.evidence


class Domains:
    def __init__(self, connection):
        self.connection = connection
        self.receipt = DomainReceipt("committed", value={"ok": True})
        self.error = None
        self.calls = []

    async def dispatch(self, reservation, args, assertion, *, idempotency_key):
        assert not self.connection.in_transaction
        assert self.connection.events[-1] == "commit"
        self.calls.append((reservation, args, assertion, idempotency_key))
        if self.error:
            raise self.error
        return self.receipt

    async def park(self, reservation, args, assertion):
        assert not self.connection.in_transaction
        self.calls.append((reservation, args, assertion, None))
        return ParkReceipt("owner-row")


def build(capability=None, grant=None):
    connection = Connection(grant=grant)
    authorities = Authorities(
        capability
        or Capability(
            "health", "read", "read", "trusted", (), recipient_params=RecipientParameters()
        )
    )
    domains = Domains(connection)
    broker = Broker(
        Pool(connection),
        authorities,
        domains,
        AssertionSigner("test", Ed25519PrivateKey.generate()),
        clock=lambda: 100,
    )
    token, _ = mint_token()
    return broker, connection, authorities, domains, token


def test_behavior_matrix_owner_rules_exposure_and_unremovable_modes():
    p = Principal(uuid4(), uuid4(), 0, "owner", True, "v1", ())
    base = Capability(
        "health",
        "write",
        "record",
        "stamped",
        (),
        approvals_available=True,
        recipient_params=RecipientParameters(("recipient",)),
    )
    cases = [
        (base, Grant(base.identity), PolicyEvidence("none"), Disposition.DISPATCH, False),
        (
            replace(base, owner_authority=True),
            Grant(base.identity),
            PolicyEvidence("none"),
            Disposition.REFUSE,
            False,
        ),
        (
            replace(base, effect_class="transact"),
            Grant(base.identity),
            PolicyEvidence("owner", True, True),
            Disposition.REFUSE,
            False,
        ),
        (
            replace(base, effect_class="communicate"),
            Grant(base.identity),
            PolicyEvidence("non_owner", True),
            Disposition.PARK,
            False,
        ),
        (
            replace(base, effect_class="communicate"),
            Grant(base.identity),
            PolicyEvidence("non_owner", True, True),
            Disposition.PARK,
            True,
        ),
        (
            replace(base, name="notify", effect_class="communicate"),
            Grant(base.identity),
            PolicyEvidence("owner", inert_owner_notify=True),
            Disposition.DISPATCH,
            False,
        ),
        (
            replace(base, name="send", effect_class="communicate"),
            Grant(base.identity),
            PolicyEvidence("owner", inert_owner_notify=True),
            Disposition.PARK,
            False,
        ),
        (
            replace(base, effect_class="actuate"),
            Grant(base.identity),
            PolicyEvidence("none"),
            Disposition.PARK,
            False,
        ),
        (
            base,
            Grant(base.identity, park_only=True),
            PolicyEvidence("owner", True, True),
            Disposition.PARK,
            False,
        ),
        (
            replace(base, always_per_event=True),
            Grant(base.identity),
            PolicyEvidence("owner", True, True),
            Disposition.PARK,
            False,
        ),
    ]
    for capability, grant, evidence, disposition, rules in cases:
        decision = decide(p, capability, grant, evidence)
        assert (decision.disposition, decision.allow_standing_rule) == (disposition, rules)
    assert (
        decide(
            replace(p, depth=1),
            replace(base, effect_class="administer"),
            Grant(base.identity),
            PolicyEvidence("none"),
        ).disposition
        is Disposition.REFUSE
    )
    # Exposure cannot make a non-owner send bypass approval; unexposed still parks.
    assert (
        decide(
            replace(p, exposed=False),
            replace(base, effect_class="communicate"),
            Grant(base.identity),
            PolicyEvidence("non_owner"),
        ).disposition
        is Disposition.PARK
    )


@pytest.mark.asyncio
async def test_actual_service_commits_before_forward_and_deduplicates_effect():
    capability = Capability(
        "health",
        "write",
        "record",
        "stamped",
        (),
        idempotency="key_required",
        recipient_params=RecipientParameters(),
    )
    broker, connection, _, domains, token = build(capability, Grant(capability.identity))
    result = await broker.call(connection.goal, token, capability.mcp_name, {"weight": 1})
    assert result.status == "committed" and connection.exposed
    assert domains.calls[0][3] == str(result.effect_id)
    assert connection.events.index("commit") < connection.events.index("committed")
    with pytest.raises(BrokerRefused) as duplicate:
        await broker.call(connection.goal, token, capability.mcp_name, {"weight": 1.0})
    assert duplicate.value.reason is Reason.DUPLICATE
    assert duplicate.value.effect_id == result.effect_id and len(domains.calls) == 1


@pytest.mark.asyncio
async def test_commit_unknown_and_exposure_write_failure_never_release_content():
    broker, connection, _, domains, token = build()
    connection.fail_commit = True
    with pytest.raises(RuntimeError):
        await broker.call(connection.goal, token, "health__read", {})
    assert domains.calls == []
    connection.fail_commit = False
    connection.fail_exposure = True
    domains.receipt = DomainReceipt("committed", value="must_not_escape")
    broker.authorities.capability = replace(broker.authorities.capability, output_trust="untrusted")
    with pytest.raises(RuntimeError):
        await broker.call(connection.goal, token, "health__read", {})
    assert len(domains.calls) == 1


@pytest.mark.asyncio
async def test_budget_terminal_transition_and_selection_are_real_service_refusals():
    capability = Capability(
        "health",
        "read",
        "read",
        "trusted",
        (("entity", True),),
        ("entity",),
        recipient_params=RecipientParameters(),
    )
    grant = Grant(capability.identity, (("entity", ("owner",)),))
    broker, connection, authorities, domains, token = build(capability, grant)
    authorities.active_selection = False
    assert await broker.list_tools(connection.goal, token) == ()
    with pytest.raises(BrokerRefused) as revoked:
        await broker.call(connection.goal, token, capability.mcp_name, {"entity": "owner"})
    assert revoked.value.reason is Reason.SELECTION
    authorities.active_selection = True
    with pytest.raises(BrokerRefused) as outside:
        await broker.call(connection.goal, token, capability.mcp_name, {"entity": "other"})
    assert outside.value.reason is Reason.RESOURCE
    connection.budget = 0
    with pytest.raises(BrokerRefused) as budget:
        await broker.call(connection.goal, token, capability.mcp_name, {"entity": "owner"})
    assert budget.value.reason is Reason.BUDGET
    assert connection.status == "budget_exhausted" and connection.events[-1] == "commit"
    assert domains.calls == []


@pytest.mark.asyncio
async def test_transport_unknown_is_not_retry_and_external_cancellation_propagates():
    capability = Capability(
        "health", "write", "record", "trusted", (), recipient_params=RecipientParameters()
    )
    broker, connection, _, domains, token = build(capability, Grant(capability.identity))
    domains.error = TimeoutError()
    assert (await broker.call(connection.goal, token, capability.mcp_name, {})).status == "unknown"
    assert len(domains.calls) == 1 and connection.events[-2] == "unknown"
    broker, connection, _, domains, token = build()
    domains.error = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await broker.call(connection.goal, token, "health__read", {})
    assert len(domains.calls) == 1


@pytest.mark.asyncio
async def test_park_only_carries_restriction_and_exact_args_without_direct_dispatch():
    capability = Capability(
        "health",
        "write",
        "record",
        "stamped",
        (),
        approvals_available=True,
        recipient_params=RecipientParameters(),
    )
    broker, connection, _, domains, token = build(
        capability, Grant(capability.identity, park_only=True)
    )
    result = await broker.call(connection.goal, token, capability.mcp_name, {"exact": "original"})
    assert result.status == "parked" and result.approval_ref == "owner-row"
    assert not domains.calls[0][0].decision.allow_standing_rule
    assert domains.calls[0][1] == {"exact": "original"}


@pytest.mark.asyncio
async def test_untyped_domain_receipt_never_releases_content():
    broker, connection, _, domains, token = build()
    domains.receipt = SimpleNamespace(
        status="committed", receipt_ref=None, value="unvalidated", stamped_owner_untainted=True
    )
    result = await broker.call(connection.goal, token, "health__read", {})
    assert result.status == "unknown" and result.value is None
    assert len(domains.calls) == 1


@pytest.mark.asyncio
async def test_park_settlement_uncertainty_is_not_a_second_settlement_attempt():
    capability = Capability(
        "health",
        "write",
        "record",
        "trusted",
        (),
        approvals_available=True,
        recipient_params=RecipientParameters(),
    )
    broker, connection, _, domains, token = build(
        capability, Grant(capability.identity, park_only=True)
    )
    original_park = domains.park

    async def park_then_commit_unknown(*args):
        result = await original_park(*args)
        connection.fail_commit = True
        return result

    domains.park = park_then_commit_unknown
    with pytest.raises(RuntimeError, match="controlled_commit_unknown"):
        await broker.call(connection.goal, token, capability.mcp_name, {})
    assert len(domains.calls) == 1
    assert [event for event in connection.events if event in {"parked", "unknown"}] == ["parked"]


@pytest.mark.asyncio
async def test_recipient_binding_cannot_reuse_an_owner_enum_or_another_call_proof():
    cap = Capability(
        "mail",
        "send",
        "communicate",
        "trusted",
        (),
        recipient_params=RecipientParameters(("to",)),
        approvals_available=True,
    )
    args, owner = {"to": "resolved-by-native-adapter"}, uuid4()
    bound = RecipientBinding(cap.identity, arguments_digest(args), owner, ("to",), (owner,))
    # These values are trusted-adapter controls, not production identity lookup proof.
    invalid = [
        None,
        replace(bound, capability="mail__other"),
        replace(bound, args_digest=arguments_digest({"to": "different"})),
        replace(bound, argument_names=()),
        replace(bound, targets=()),
    ]
    for proof in invalid:
        broker, connection, authority, domains, token = build(cap, Grant(cap.identity))
        authority.evidence = PolicyEvidence("owner", recipient_binding=proof)
        with pytest.raises(BrokerRefused) as refused:
            await broker.call(connection.goal, token, cap.mcp_name, args)
        assert refused.value.reason is Reason.POLICY
        assert domains.calls == [] and connection.used == 0
    for proof, expected in [
        (bound, "committed"),
        (replace(bound, targets=(None,)), "parked"),
        (replace(bound, targets=(owner, uuid4())), "parked"),
        (replace(bound, owner_id=None), "parked"),
        (replace(bound, undeterminable=True), "parked"),
    ]:
        broker, connection, authority, domains, token = build(cap, Grant(cap.identity))
        authority.evidence = PolicyEvidence("owner", recipient_binding=proof)
        assert (await broker.call(connection.goal, token, cap.mcp_name, args)).status == expected
        assert len(domains.calls) == 1


@pytest.mark.asyncio
async def test_default_owner_never_overrides_an_explicit_or_undeterminable_party():
    owner = uuid4()
    cap = Capability(
        "core",
        "notify",
        "communicate",
        "trusted",
        (),
        approvals_available=True,
        recipient_params=RecipientParameters(("recipient",), default_owner_condition="omitted"),
    )
    for args, default, inherited, expected in [
        ({}, True, False, "committed"),
        ({}, False, False, "parked"),
        ({}, True, True, "parked"),
    ]:
        broker, connection, authority, _, token = build(cap, Grant(cap.identity))
        authority.evidence = PolicyEvidence(
            "owner",
            recipient_binding=RecipientBinding(
                cap.identity, arguments_digest(args), owner, (), (), default, inherited
            ),
        )
        assert (await broker.call(connection.goal, token, cap.mcp_name, args)).status == expected
    broker, connection, authority, domains, token = build(cap, Grant(cap.identity))
    args = {"recipient": "explicit"}
    authority.evidence = PolicyEvidence(
        "owner",
        recipient_binding=RecipientBinding(
            cap.identity, arguments_digest(args), owner, ("recipient",), (owner,), True
        ),
    )
    with pytest.raises(BrokerRefused):
        await broker.call(connection.goal, token, cap.mcp_name, args)
    assert domains.calls == []


@pytest.mark.asyncio
async def test_authentication_refuses_ambiguous_revoked_stale_and_corrupt_rows():
    for changes, count, epoch, reason in [
        ({}, 0, 0, Reason.AUTHENTICATION),
        ({}, 2, 0, Reason.AUTHENTICATION),
        ({"status": "returned"}, 1, 0, Reason.AUTHENTICATION),
        ({"lease_valid": False}, 1, 0, Reason.AUTHENTICATION),
        ({}, 1, 1, Reason.EPOCH),
        ({"grant_digest": "different"}, 1, 0, Reason.AUTHENTICATION),
        ({"depth": True}, 1, 0, Reason.AUTHENTICATION),
        ({"exposed": "false"}, 1, 0, Reason.AUTHENTICATION),
    ]:
        broker, connection, _, domains, token = build()
        connection.invocation_changes = changes
        connection.invocation_count, connection.epoch = count, epoch
        with pytest.raises(BrokerRefused) as refused:
            await broker.list_tools(connection.goal, token)
        assert refused.value.reason is reason and not domains.calls and connection.used == 0
    # A real child is not fenced by the replacement lead's epoch.
    broker, connection, _, _, token = build()
    connection.invocation_changes = {"depth": 1}
    connection.epoch = 1
    assert [c.identity for c in await broker.list_tools(connection.goal, token)] == ["health__read"]


@pytest.mark.asyncio
async def test_selection_bound_resource_is_never_implicitly_unscoped():
    cap = Capability(
        "health",
        "read",
        "read",
        "trusted",
        (("entity", True),),
        ("entity",),
        recipient_params=RecipientParameters(),
    )
    broker, connection, authority, domains, token = build(cap, Grant(cap.identity))
    assert authority.active_selection is True
    assert await broker.list_tools(connection.goal, token) == ()
    with pytest.raises(BrokerRefused) as refused:
        await broker.call(connection.goal, token, cap.mcp_name, {"entity": "unscoped"})
    assert refused.value.reason is Reason.SELECTION and domains.calls == []


def test_real_http_mcp_auth_discovery_call_and_duplicate_json_boundary():
    class ControlledBroker:
        def __init__(self):
            self.calls = []

        async def authenticate(self, goal, token):
            if token != "accepted":
                raise BrokerRefused(Reason.AUTHENTICATION)

        async def list_tools(self, goal, token):
            return (
                Capability(
                    "health", "read", "read", "trusted", (), recipient_params=RecipientParameters()
                ),
            )

        async def call(self, goal, token, name, arguments):
            self.calls.append((goal, name, arguments))
            return BrokerResult("returned", value={"ok": True})

    broker, goal = ControlledBroker(), uuid4()
    with TestClient(create_app(broker)) as client:
        url = f"/goals/{goal}/mcp/"
        headers = {
            "Authorization": "Bearer accepted",
            "Accept": "application/json,text/event-stream",
        }
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        assert client.post(url, json=request).status_code == 401
        listed = client.post(url, json=request, headers=headers)
        assert listed.status_code == 200
        assert [t["name"] for t in listed.json()["result"]["tools"]] == ["health__read"]
        call = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "health__read", "arguments": {"x": 1}},
        }
        assert client.post(url, json=call, headers=headers).json()["result"]["isError"] is False
        assert broker.calls == [(goal, "health__read", {"x": 1})]
        raw = b'{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"health__read","arguments":{"x":1,"x":2}}}'
        assert client.post(url, content=raw, headers=headers).status_code == 400
        assert len(broker.calls) == 1


# D4/D13: owner/template standing-rule exceptions are specific to communication.
# They never substitute for the per-event owner decision on exposed administer
# or consequential/protected (including unknown-risk) actuation.
def exposure_rule_specimen(effect, risk, proof):
    args = {"recipient": "controlled-recipient"} if proof == "owner" else {"value": "controlled"}
    capability = Capability(
        "health",
        "change",
        effect,
        "trusted",
        (),
        approvals_available=True,
        recipient_params=RecipientParameters(("recipient",) if proof == "owner" else ()),
    )
    owner = uuid4()
    binding = (
        RecipientBinding(
            capability.identity, arguments_digest(args), owner, ("recipient",), (owner,)
        )
        if proof == "owner"
        else None
    )
    evidence = PolicyEvidence(
        "owner" if proof == "owner" else "none",
        standing_rule=True,
        template_pinned=proof == "template",
        actuation_risk=risk,
        recipient_binding=binding,
    )
    return capability, args, evidence


@pytest.mark.parametrize(
    "effect,risk",
    [
        ("administer", "unknown"),
        ("actuate", "consequential"),
        ("actuate", "protected"),
        ("actuate", "unknown"),
    ],
)
@pytest.mark.parametrize("proof", ["owner", "template"])
def test_exposed_effect_requires_per_event_despite_matching_rule(effect, risk, proof):
    capability, _, evidence = exposure_rule_specimen(effect, risk, proof)
    principal = Principal(uuid4(), uuid4(), 0, "owner", True, "v1", ())
    decision = decide(principal, capability, Grant(capability.identity), evidence)
    assert decision.disposition is Disposition.PARK
    assert decision.allow_standing_rule is False


@pytest.mark.parametrize(
    "effect,risk",
    [
        ("administer", "unknown"),
        ("actuate", "consequential"),
        ("actuate", "protected"),
        ("actuate", "unknown"),
    ],
)
@pytest.mark.parametrize("proof", ["owner", "template"])
async def test_broker_park_receives_per_event_requirement(effect, risk, proof):
    capability, args, evidence = exposure_rule_specimen(effect, risk, proof)
    broker, connection, authorities, domains, token = build(capability, Grant(capability.identity))
    connection.exposed = True
    authorities.evidence = evidence
    at_park = []
    original_park = domains.park

    async def observe_park(reservation, arguments, assertion):
        at_park.append((connection.in_transaction, tuple(connection.events)))
        return await original_park(reservation, arguments, assertion)

    domains.park = observe_park
    result = await broker.call(connection.goal, token, capability.identity, args)
    assert result == BrokerResult("parked", connection.effect, "owner-row")
    assert connection.used == 1
    assert connection.events == ["effect", "commit", "parked", "commit"]
    assert at_park == [(False, ("effect", "commit"))]
    assert len(domains.calls) == 1
    reservation, received_args, assertion, idempotency_key = domains.calls[0]
    assert received_args == args
    assert assertion and idempotency_key is None
    assert reservation.effect_id == connection.effect
    assert reservation.principal.exposed is True
    assert reservation.principal.grants == (Grant(capability.identity),)
    assert reservation.args_digest == arguments_digest(args)
    assert reservation.decision.disposition is Disposition.PARK
    assert reservation.decision.allow_standing_rule is False


@pytest.mark.parametrize(
    "scenario,expected,rules",
    [
        ("owner_communication", Disposition.PARK, True),
        ("template_communication", Disposition.PARK, True),
        ("unpinned_nonowner_communication", Disposition.PARK, False),
        ("inert_owner_notify", Disposition.DISPATCH, False),
        ("safe_actuation", Disposition.DISPATCH, False),
        ("independent_safe_approval", Disposition.PARK, False),
        ("unexposed_admin_rule", Disposition.PARK, True),
        ("park_only", Disposition.PARK, False),
        ("always_per_event", Disposition.PARK, False),
    ],
)
async def test_broker_preserves_specific_exceptions_and_independent_modes(
    scenario, expected, rules
):
    capability, args, evidence = exposure_rule_specimen("communicate", "unknown", "owner")
    grant = Grant(capability.identity)
    if scenario in {"template_communication", "unpinned_nonowner_communication"}:
        binding = replace(evidence.recipient_binding, targets=(uuid4(),))
        evidence = replace(
            evidence,
            recipient="non_owner",
            recipient_binding=binding,
            template_pinned=scenario == "template_communication",
        )
    elif scenario == "inert_owner_notify":
        capability = replace(capability, name="notify")
        grant = Grant(capability.identity)
        evidence = replace(
            evidence,
            standing_rule=False,
            inert_owner_notify=True,
            recipient_binding=replace(evidence.recipient_binding, capability=capability.identity),
        )
    elif scenario in {"safe_actuation", "independent_safe_approval", "unexposed_admin_rule"}:
        capability, args, evidence = exposure_rule_specimen(
            "administer" if scenario == "unexposed_admin_rule" else "actuate", "safe", "template"
        )
        capability = replace(capability, approval_required=scenario != "safe_actuation")
        grant = Grant(capability.identity)
    elif scenario == "park_only":
        grant = replace(grant, park_only=True)
    elif scenario == "always_per_event":
        capability = replace(capability, always_per_event=True)
    broker, connection, authorities, domains, token = build(capability, grant)
    connection.exposed = scenario != "unexposed_admin_rule"
    authorities.evidence = evidence
    result = await broker.call(connection.goal, token, capability.identity, args)
    assert len(domains.calls) == 1
    reservation, received_args, _, _ = domains.calls[0]
    assert reservation.decision.disposition is expected
    assert reservation.decision.allow_standing_rule is rules
    assert received_args == args and connection.used == 1
    if expected is Disposition.PARK:
        assert result == BrokerResult("parked", connection.effect, "owner-row")
        assert connection.events == ["effect", "commit", "parked", "commit"]
    else:
        assert result.status == "committed"
        assert connection.events == ["effect", "dispatched", "commit", "committed", "commit"]


@pytest.mark.parametrize(
    "effect,origin,depth,owner_authority",
    [
        ("administer", "third_party", 0, False),
        ("actuate", "third_party", 0, False),
    ],
)
def test_exposure_rule_cannot_remove_independent_refusals(effect, origin, depth, owner_authority):
    capability, _, evidence = exposure_rule_specimen(effect, "safe", "owner")
    capability = replace(capability, owner_authority=owner_authority)
    principal = Principal(uuid4(), uuid4(), depth, origin, True, "v1", ())
    decision = decide(principal, capability, Grant(capability.identity), evidence)
    assert decision.disposition is Disposition.REFUSE
    assert decision.allow_standing_rule is False


class TokenConnection(Connection):
    """Mutable software row boundary, including actual token-hash selection.

    This records SQL state transitions; PostgreSQL locks/RLS/rollback remain
    separate hosted obligations.
    """

    def __init__(self):
        super().__init__()
        self.token_digest = None
        self.invocation_status = "launching"
        self.ceiling = encode_grants(self.grants)

    def invocation_row(self):
        return (
            dict(
                id=self.invocation,
                goal_id=self.goal,
                status=self.invocation_status,
                token_hash=self.token_digest,
                lease_valid=True,
                depth=0,
                lead_epoch=0,
                exposed=False,
                grant_snapshot=encode_grants(self.grants),
                grant_digest=digest(encode_grants(self.grants)),
            )
            | self.invocation_changes
        )

    async def fetch(self, sql, *args):
        assert "token_hash=$2" in sql
        if args != (self.goal, self.token_digest) or self.token_digest is None:
            return []
        return [self.invocation_row()]

    async def fetchrow(self, sql, *args):
        if "FROM switchboard.invocations" in sql:
            assert args == (self.goal, self.invocation)
            return self.invocation_row()
        if "UPDATE switchboard.invocations SET token_hash" in sql:
            assert args[:2] == (self.goal, self.invocation)
            if len(args) == 3:
                if self.token_digest is not None:
                    return None
                self.token_digest = args[2]
            else:
                assert (
                    "AND status IN ('returned','failed','timed_out','stopped','escalated')" in sql
                )
                if self.invocation_status not in {
                    "returned",
                    "failed",
                    "timed_out",
                    "stopped",
                    "escalated",
                }:
                    return None
                self.token_digest = None
            self.events.append("token_write")
            return {"id": self.invocation}
        row = await super().fetchrow(sql, *args)
        if "FROM switchboard.goals" in sql:
            row["ceiling"] = self.ceiling
        return row


def token_broker():
    broker, _, authority, _, _ = build()
    connection = TokenConnection()
    domains = Domains(connection)
    broker.pool, broker.domains = Pool(connection), domains
    return broker, connection, authority, domains


async def test_real_session_listener_uses_issued_hash_grants_and_live_revocation():
    import httpx

    from butlers.capability_broker.contracts import token_hash
    from butlers.capability_broker.session import BrokerSessionListener

    broker, connection, _, domains = token_broker()
    token = await broker.issue_invocation_token(connection.goal, connection.invocation)
    assert connection.token_digest == token_hash(token) and connection.token_digest != token
    assert connection.events[-1] == "commit"
    connection.invocation_status = "running"
    listener = BrokerSessionListener(broker, host="127.0.0.1", port=0)
    await listener.start()
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{listener.port}", timeout=3, follow_redirects=False
        ) as client:
            path = f"/goals/{connection.goal}/mcp/"
            headers = {
                "Authorization": "Bearer " + token,
                "Accept": "application/json,text/event-stream",
            }
            listing = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
            assert (await client.post(path, json=listing)).status_code == 401
            listed = await client.post(path, json=listing, headers=headers)
            assert listed.status_code == 200
            assert [t["name"] for t in listed.json()["result"]["tools"]] == ["health__read"]
            call = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "health__read", "arguments": {"x": 1}},
            }
            returned = await client.post(path, json=call, headers=headers)
            assert returned.status_code == 200 and returned.json()["result"]["isError"] is False
            assert connection.used == 1 and len(domains.calls) == 1
            assert (await client.post("/mcp", json=listing, headers=headers)).status_code == 401
            connection.epoch = 1
            assert (await client.post(path, json=listing, headers=headers)).status_code == 401
            # A child retains its token when the lead epoch changes.
            connection.invocation_changes = {"depth": 1}
            assert (await client.post(path, json=listing, headers=headers)).status_code == 200
            # The runner owns terminal status and process/budget settlement.
            # A live row cannot be cleared and then silently issued a new token.
            with pytest.raises(BrokerRefused):
                await broker.revoke_invocation_token(connection.goal, connection.invocation)
            connection.invocation_status = "returned"
            assert (await client.post(path, json=call, headers=headers)).status_code == 401
            await broker.revoke_invocation_token(connection.goal, connection.invocation)
            assert connection.token_digest is None
            with pytest.raises(BrokerRefused):
                await broker.issue_invocation_token(connection.goal, connection.invocation)
            assert (await client.post(path, json=call, headers=headers)).status_code == 401
            assert connection.used == 1 and len(domains.calls) == 1
    finally:
        await listener.close()
    assert listener.task.done()
    with pytest.raises(httpx.ConnectError):
        async with httpx.AsyncClient(timeout=1) as client:
            await client.get(f"http://127.0.0.1:{listener.port}/health")


async def test_token_issuer_refuses_invalid_lineage_reissue_and_uncertain_commit():
    from butlers.core.goal_admission import encode_grants

    for mutation in (
        lambda c: setattr(c, "status", "stopped"),
        lambda c: setattr(c, "epoch", 1),
        lambda c: setattr(c, "ceiling", encode_grants((Grant("other__tool"),))),
        lambda c: setattr(c, "invocation_status", "returned"),
        lambda c: setattr(c, "invocation_changes", {"grant_digest": "corrupt"}),
        lambda c: setattr(c, "invocation_changes", {"lease_valid": False}),
    ):
        broker, connection, _, domains = token_broker()
        mutation(connection)
        with pytest.raises(BrokerRefused):
            await broker.issue_invocation_token(connection.goal, connection.invocation)
        assert connection.token_digest is None and not domains.calls
    broker, connection, _, _ = token_broker()
    first = await broker.issue_invocation_token(connection.goal, connection.invocation)
    with pytest.raises(BrokerRefused):
        await broker.issue_invocation_token(connection.goal, connection.invocation)
    from butlers.capability_broker.contracts import token_hash

    assert connection.token_digest == token_hash(first)
    broker, connection, _, domains = token_broker()
    connection.fail_commit = True
    with pytest.raises(BrokerRefused, match="broker_unavailable"):
        await broker.issue_invocation_token(connection.goal, connection.invocation)
    assert not domains.calls
