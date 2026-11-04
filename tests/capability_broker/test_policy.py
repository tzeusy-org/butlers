"""D4/D6/D13 disposition matrix and owner-rule recipient classification."""

from uuid import uuid4

import pytest

from butlers.capability_broker.contracts import Disposition
from butlers.capability_broker.policy import decide, listable
from butlers.capability_broker.recipients import classify_recipients
from butlers.core.goal_admission import Grant
from butlers.core.tool_catalog import RecipientParameters
from tests.capability_broker.helpers import capability, notify, principal

pytestmark = pytest.mark.unit

PARK, DISPATCH, REFUSE = Disposition.PARK, Disposition.DISPATCH, Disposition.REFUSE


def _decide(cap, *, park_only=False, depth=0, exposed=False, recipient="none", risk="safe"):
    grant = Grant(cap.identity, park_only=park_only)
    return decide(principal(grant, depth=depth, exposed=exposed), cap, grant, recipient, risk)


@pytest.mark.parametrize(
    ("case", "cap", "kwargs", "expected"),
    [
        # Owner rule 1: transact is refused for every origin, depth and park-only grant.
        ("transact", capability("pay", "transact"), {"park_only": True}, (REFUSE, False)),
        ("transact_child", capability("pay", "transact"), {"depth": 1}, (REFUSE, False)),
        # administer: per-event approval for a lead, never available to a child.
        ("administer_lead", capability("rules", "administer"), {}, (PARK, False)),
        ("administer_child", capability("rules", "administer"), {"depth": 1}, (REFUSE, False)),
        # Owner rule 2: a non-owner party parks; an owner-created rule may match.
        ("non_owner", notify(), {"recipient": "non_owner"}, (PARK, True)),
        ("owner_notify", notify(), {"recipient": "owner"}, (DISPATCH, False)),
        # Park-only and always_per_event: parked, never rule-approved.
        ("park_only", capability("log", "record"), {"park_only": True}, (PARK, False)),
        (
            "always_per_event",
            capability("respond", "communicate", always_per_event=True),
            {"recipient": "owner"},
            (PARK, False),
        ),
        # Exposure (D6).
        (
            "exposed_owner_notify",
            notify(),
            {"exposed": True, "recipient": "owner"},
            (DISPATCH, False),
        ),
        (
            "exposed_send",
            capability("send", "communicate"),
            {"exposed": True, "recipient": "owner"},
            (PARK, True),
        ),
        (
            "exposed_owner_authority",
            capability("remember_rule", "record", owner_authority=True),
            {"exposed": True},
            (REFUSE, False),
        ),
        ("exposed_record", capability("log", "record"), {"exposed": True}, (DISPATCH, False)),
        # actuate follows the RFC 0028 risk map; unknown services are protected.
        ("actuate_safe", capability("light", "actuate"), {"risk": "safe"}, (DISPATCH, False)),
        ("actuate_risky", capability("lock", "actuate"), {"risk": "consequential"}, (PARK, False)),
        ("actuate_unknown", capability("svc", "actuate"), {"risk": "unknown"}, (PARK, False)),
        # The tool's own approval policy.
        ("gated", capability("log", "record", approval_gate=True), {}, (PARK, True)),
        ("read", capability(), {}, (DISPATCH, False)),
    ],
    ids=lambda v: v if isinstance(v, str) else None,
)
def test_disposition_matrix(case, cap, kwargs, expected):
    decision = _decide(cap, **kwargs)
    assert (decision.disposition, decision.allow_standing_rule) == expected


def test_transact_and_child_administer_are_never_listed():
    assert not listable(capability("pay", "transact"), 0)
    assert not listable(capability("rules", "administer"), 1)
    assert listable(capability("rules", "administer"), 0)


class _Connection:
    """Answers the two owner-resolution reads with a fixed live owner."""

    def __init__(self, owners):
        self.owners = owners

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def fetch(self, query, *args):
        return [{"id": owner} for owner in self.owners]

    async def fetchrow(self, query, *args):
        return None  # The email definer finds no owner match.


OWNER = uuid4()


@pytest.mark.parametrize(
    ("args", "owners", "expected"),
    [
        ({"channel": "telegram", "message": "hi"}, [OWNER], "owner"),  # declared default
        ({"entity_id": str(OWNER), "message": "hi"}, [OWNER], "owner"),
        ({"entity_id": str(uuid4()), "message": "hi"}, [OWNER], "non_owner"),
        ({"entity_id": str(OWNER)}, [OWNER, uuid4()], "non_owner"),  # ambiguous owner
        ({"entity_id": str(OWNER), "intent": "reply"}, [OWNER], "non_owner"),  # inherited thread
        ({"channel": "email", "message": "hi"}, [OWNER], "non_owner"),  # no declared default
        ({"recipient": "someone@example.com"}, [OWNER], "non_owner"),  # unresolved address
        ({"request_context": {"chat": 1}}, [OWNER], "non_owner"),  # undeterminable party
    ],
)
async def test_notify_recipients_resolve_with_the_core_notify_rule(args, owners, expected):
    assert await classify_recipients(_Connection(owners), notify(), args) == expected


async def test_undeterminable_capability_is_always_non_owner():
    reply = capability(
        "reply", "communicate", recipient_params=RecipientParameters(undeterminable=True)
    )
    assert await classify_recipients(_Connection([OWNER]), reply, {"text": "x"}) == "non_owner"


async def test_calendar_invitees_are_parties_whatever_the_class():
    params = RecipientParameters(argument_names=("attendees",))
    event = capability("create_event", "record", recipient_params=params)
    connection = _Connection([OWNER])
    assert await classify_recipients(connection, event, {"title": "x"}) == "none"
    invited = {"attendees": [str(OWNER), str(uuid4())]}
    assert await classify_recipients(connection, event, invited) == "non_owner"
    assert await classify_recipients(connection, event, {"attendees": [str(OWNER)]}) == "owner"
