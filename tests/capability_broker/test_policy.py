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


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _fact_matches(mode: str, candidates: list[str], predicate: str, obj: str) -> bool:
    """``public.resolve_owner_triple`` (core_296) for one stored fact.

    ``owner-channel`` matches email and handle facts exactly (case-normalized)
    and phone facts by the custody suffix rule: both at least 8 digits, lengths
    within two, one a suffix of the other. Any other mode is the exact legacy
    ``predicate = mode AND object = ANY(candidates)`` lookup.
    """
    if mode != "owner-channel":
        return predicate == mode and obj in candidates
    if predicate in ("has-email", "has-handle"):
        return f"{predicate}:{obj.strip().lower()}" in candidates
    stored = _digits(obj)
    for candidate in candidates:
        if not candidate.startswith("phone-digits:"):
            continue
        digits = candidate.removeprefix("phone-digits:")
        if (
            len(digits) >= 8
            and len(stored) >= 8
            and abs(len(stored) - len(digits)) <= 2
            and (stored.endswith(digits) or digits.endswith(stored))
        ):
            return True
    return False


class _OwnerFacts(_Connection):
    """The owner-only definer over stored ``(entity, predicate, object)`` facts.

    It returns the owner only when exactly one live entity matches and that
    entity is the owner; ``others`` are facts another entity holds, so a value
    both claim resolves to nobody.
    """

    def __init__(self, owners, facts, others=()):
        super().__init__(owners)
        self.facts = [(OWNER, *fact) for fact in facts] + [(OTHER, *fact) for fact in others]
        self.queries = []

    async def fetchrow(self, query, mode, candidates):
        self.queries.append((mode, tuple(candidates)))
        matched = {
            entity
            for entity, predicate, obj in self.facts
            if _fact_matches(mode, list(candidates), predicate, obj)
        }
        return {"entity_id": OWNER, "is_primary": True} if matched == {OWNER} else None


OTHER = uuid4()
_OWNER_FACTS = (
    ("has-handle", "telegram:4242"),
    ("has-handle", "telegram:owner_handle"),
    ("has-phone", "+6591234567"),
    ("has-handle", "15550001@s.whatsapp.net"),
    ("has-email", "owner@example.com"),
)
_TELEGRAM_SEND = capability(
    "telegram_send_message",
    "communicate",
    "telegram",
    recipient_params=RecipientParameters(argument_names=("chat_id",)),
)
_WHATSAPP_SEND = capability(
    "whatsapp_send_message",
    "communicate",
    "whatsapp",
    recipient_params=RecipientParameters(argument_names=("recipient",)),
)
_EMAIL_SEND = capability(
    "email_send_message",
    "communicate",
    "email",
    recipient_params=RecipientParameters(argument_names=("to",)),
)


@pytest.mark.parametrize(
    ("cap", "args"),
    [
        (_TELEGRAM_SEND, {"chat_id": "4242", "text": "x"}),
        (_TELEGRAM_SEND, {"chat_id": "@owner_handle", "text": "x"}),
        (_WHATSAPP_SEND, {"recipient": "15550001@s.whatsapp.net", "text": "x"}),  # exact JID
        (_WHATSAPP_SEND, {"recipient": "6591234567@s.whatsapp.net", "text": "x"}),  # exact E.164
        (_EMAIL_SEND, {"to": ["owner@example.com"], "subject": "x"}),
        (notify(), {"channel": "telegram", "recipient": "4242", "message": "hi"}),
        (notify(), {"recipient": "telegram:owner_handle", "message": "hi"}),  # telegram default
    ],
)
async def test_owner_channel_identifiers_resolve_with_the_core_notify_rule(cap, args):
    connection = _OwnerFacts([OWNER], _OWNER_FACTS)
    assert await classify_recipients(connection, cap, args) == "owner"
    assert connection.queries  # resolved by the owner-only definer, not by shape


@pytest.mark.parametrize(
    ("cap", "args", "others"),
    [
        (_TELEGRAM_SEND, {"chat_id": "9999"}, ()),  # unknown chat
        (_TELEGRAM_SEND, {"chat_id": "4242"}, (("has-handle", "telegram:4242"),)),  # shared chat
        (_WHATSAPP_SEND, {"recipient": "15550002@s.whatsapp.net"}, ()),
        (
            _WHATSAPP_SEND,
            {"recipient": "15550001@s.whatsapp.net"},
            (("has-handle", "15550001@s.whatsapp.net"),),  # shared JID
        ),
        (_WHATSAPP_SEND, {"recipient": "+15550001"}, ()),  # bare phone: no owner rule
        (_EMAIL_SEND, {"to": ["owner@example.com", "other@example.com"]}, ()),
        (_TELEGRAM_SEND, {"chat_id": "owner@example.com"}, ()),  # another channel's identifier
        (notify(), {"channel": "email", "recipient": "owner@example.com"}, ()),  # no default
        (notify(), {"entity_id": str(uuid4()), "recipient": "4242"}, ()),  # channel unknown
    ],
)
async def test_unknown_or_ambiguous_channel_identifiers_still_park(cap, args, others):
    connection = _OwnerFacts([OWNER], _OWNER_FACTS, others)
    assert await classify_recipients(connection, cap, args) == "non_owner"


@pytest.mark.parametrize(
    "recipient",
    [
        "16591234567@s.whatsapp.net",  # lookalike: one digit prepended to the owner's number
        "446591234567@s.whatsapp.net",  # two digits longer, same suffix
        "91234567@s.whatsapp.net",  # two digits shorter: the owner's number ends with it
    ],
)
@pytest.mark.parametrize("cap", [_WHATSAPP_SEND, notify()], ids=["whatsapp_send", "notify"])
async def test_a_phone_lookalike_is_not_the_owner_for_broker_authorization(cap, recipient):
    """The definer's custody phone rule is a suffix match; broker authorization is exact."""
    args = {"recipient": recipient, "text": "x", "message": "x"}
    if cap.name == "notify":
        args["channel"] = "whatsapp"
    connection = _OwnerFacts([OWNER], _OWNER_FACTS)
    # The shared definer (core notify) does call the lookalike the owner ...
    assert await connection.fetchrow(
        "", "owner-channel", [f"phone-digits:{recipient.split('@')[0]}"]
    )
    # ... but the broker never dispatches to it as the owner.
    assert await classify_recipients(connection, cap, args) == "non_owner"


async def test_a_failed_owner_lookup_is_non_owner():
    class _Broken(_OwnerFacts):
        async def fetchrow(self, query, kind, candidates):
            raise RuntimeError("definer unavailable")

    connection = _Broken([OWNER], _OWNER_FACTS)
    assert await classify_recipients(connection, _TELEGRAM_SEND, {"chat_id": "4242"}) == "non_owner"


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
