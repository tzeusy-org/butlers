"""The Gmail exposure rule, broker half (RFC 0038 D13; owner rule on bu-kqme7f.5, bu-kqme7f.10).

An exposed or third-party goal may change only the Gmail thread that admitted it; another
target, or removing INBOX or UNREAD, parks for per-event approval; the sixth call in one goal is
refused. Pure rows and the per-call disposition over a fake ledger live here; the same rows on
the migrated ledger, and the domain seam, run in ``test_gmail_exposure_postgres.py``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from butlers.capability_broker import policy
from butlers.capability_broker.contracts import (
    BrokerRefused,
    Catalog,
    Decision,
    Disposition,
    Reason,
    Reservation,
)
from butlers.capability_broker.ledger import goal_trigger_thread
from butlers.capability_broker.policy import (
    GMAIL_MODIFY_CAP,
    PER_EVENT,
    gmail_parks,
    gmail_rule_applies,
)
from butlers.capability_broker.service import Broker
from butlers.core.goal_admission import (
    Evidence,
    EvidenceRefused,
    Grant,
    SourceRef,
    valid_trigger_thread,
)
from butlers.core.goal_ledger import GoalLedger
from butlers.core.runtime_config import GoalBudgetLimits
from butlers.modules import gmail as gmail_module
from butlers.tools.switchboard.ingestion.accepted_source import gmail_trigger_thread
from tests.capability_broker.helpers import capability, principal

pytestmark = pytest.mark.unit

ACCOUNT, THREAD = "owner@example.test", "18c0ffee"
MODIFY = capability(
    "gmail_modify_threads",
    "record",
    "messenger",
    resource_params=("account",),
    safety_args=("account", "thread_ids", "add_labels", "remove_labels"),
)
SEARCH = capability("gmail_search", "read", "messenger", output_trust="untrusted")
LABEL = {"account": ACCOUNT, "thread_ids": [THREAD], "add_labels": ["Receipts"]}


def _bound(*, exposed=True, origin="third_party", trigger=(ACCOUNT, THREAD)):
    return replace(
        principal(Grant(MODIFY.identity), exposed=exposed, origin=origin), trigger_thread=trigger
    )


# -- who the rule binds ------------------------------------------------------------


@pytest.mark.parametrize(
    ("exposed", "origin", "bound"),
    [
        (True, "third_party", True),
        (False, "third_party", True),  # third-party origin is bound even if a row says otherwise
        (True, "owner", True),
        (True, "obligation", True),
        (True, "staffer", True),
        (False, "owner", False),
        (False, "obligation", False),  # exposure decides: an unexposed obligation is owner lineage
        (False, "staffer", False),
    ],
)
def test_the_rule_binds_exposed_or_third_party_calls_only(exposed, origin, bound):
    assert gmail_rule_applies(_bound(exposed=exposed, origin=origin), MODIFY) is bound


def test_the_rule_binds_only_gmail_modify():
    assert not gmail_rule_applies(_bound(), SEARCH)
    assert not gmail_rule_applies(_bound(), capability("modify_threads", "record", "messenger"))


# -- what parks --------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        LABEL,
        {**LABEL, "account": " Owner@Example.test "},  # the account selector is case-blind
        {**LABEL, "thread_ids": [THREAD, THREAD]},
        {**LABEL, "remove_labels": ["STARRED", "Receipts"]},
        {**LABEL, "remove_labels": None},
        {"account": ACCOUNT, "thread_ids": [THREAD], "add_labels": ["INBOX", "UNREAD"]},
    ],
)
def test_a_change_to_the_triggering_thread_dispatches(args):
    assert not gmail_parks(_bound(), args)


@pytest.mark.parametrize(
    "args",
    [
        {**LABEL, "remove_labels": ["INBOX"]},  # archive
        {**LABEL, "remove_labels": ["UNREAD"]},  # mark read
        {**LABEL, "remove_labels": ["STARRED", "UNREAD"]},
        {**LABEL, "thread_ids": ["other"]},  # injected mail targets another thread
        {**LABEL, "thread_ids": [THREAD, "other"]},
        {**LABEL, "account": "else@example.test"},
        {**LABEL, "account": str(uuid4())},  # an account id never matches the bound address
        {**LABEL, "thread_ids": []},
        {**LABEL, "thread_ids": THREAD},
        {**LABEL, "thread_ids": [7]},
        {**LABEL, "remove_labels": "INBOX"},
        {**LABEL, "remove_labels": [None]},
        {"thread_ids": [THREAD], "add_labels": ["Receipts"]},
        {**LABEL, "account": None},
    ],
)
def test_anything_else_parks(args):
    assert gmail_parks(_bound(), args)


def test_a_goal_no_gmail_message_admitted_parks_every_change():
    assert gmail_parks(_bound(trigger=None), LABEL)
    assert gmail_parks(_bound(origin="obligation", trigger=None), LABEL)


def test_the_broker_and_the_domain_agree_on_the_rule():
    assert policy.GMAIL_MODIFY == gmail_module._MODIFY_TOOL
    assert policy.GMAIL_PARKED_REMOVALS == gmail_module._PARKED_REMOVALS
    assert GMAIL_MODIFY_CAP == 5


# -- the binding -------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        (ACCOUNT, THREAD),
        ("a@b", "x"),
        ("a@b", "A" * 64),
    ],
)
def test_an_exact_binding_is_valid(value):
    assert valid_trigger_thread(value)


@pytest.mark.parametrize(
    "value",
    [
        None,
        (ACCOUNT,),
        [ACCOUNT, THREAD],
        ("Owner@example.test", THREAD),
        (" owner@example.test", THREAD),
        ("owner", THREAD),
        ("", THREAD),
        (ACCOUNT, ""),
        (ACCOUNT, "../labels"),
        (ACCOUNT, "A" * 65),
        (ACCOUNT, 7),
        ("x" * 319 + "@b", THREAD),
    ],
)
def test_anything_else_is_no_binding(value):
    assert not valid_trigger_thread(value)


def test_only_a_messages_evidence_carries_a_valid_binding():
    assert Evidence("message", "mail", trigger_thread=(ACCOUNT, THREAD)).trigger_thread
    for kind in ("feed", "domain"):
        with pytest.raises(EvidenceRefused):
            Evidence(kind, "mail", trigger_thread=(ACCOUNT, THREAD))
    with pytest.raises(EvidenceRefused):
        Evidence("message", "mail", trigger_thread=(ACCOUNT, "../x"))


def test_the_reader_binds_a_gmail_envelopes_account_and_conversation():
    context = {"external_conversation_id": THREAD}
    assert gmail_trigger_thread("gmail", "gmail:user:Owner@Example.test", context) == (
        ACCOUNT,
        THREAD,
    )


@pytest.mark.parametrize(
    ("connector", "endpoint", "context"),
    [
        ("telegram_bot", "gmail:user:owner@example.test", {"external_conversation_id": THREAD}),
        ("gmail", "imap:user:owner@example.test", {"external_conversation_id": THREAD}),
        ("gmail", "gmail:user:", {"external_conversation_id": THREAD}),
        ("gmail", "gmail:user:owner@example.test", {}),
        ("gmail", "gmail:user:owner@example.test", {"external_conversation_id": "a/b"}),
        ("gmail", None, {"external_conversation_id": THREAD}),
    ],
)
def test_the_reader_binds_nothing_else(connector, endpoint, context):
    assert gmail_trigger_thread(connector, endpoint, context) is None


@pytest.mark.parametrize(
    "row",
    [
        {},
        {"trigger_gmail_account": None, "trigger_gmail_thread": None},
        {"trigger_gmail_account": ACCOUNT, "trigger_gmail_thread": None},
        {"trigger_gmail_account": "OWNER@example.test", "trigger_gmail_thread": THREAD},
    ],
)
def test_a_goal_row_without_an_exact_binding_binds_nothing(row):
    assert goal_trigger_thread(row) is None


def test_a_goal_row_binding_reads_back():
    row = {"trigger_gmail_account": ACCOUNT, "trigger_gmail_thread": THREAD}
    assert goal_trigger_thread(row) == (ACCOUNT, THREAD)


class _Recorder:
    def __init__(self):
        self.statements: list[tuple[str, tuple]] = []

    async def execute(self, sql, *args):
        self.statements.append((sql, args))


@pytest.mark.parametrize(
    ("evidence", "stored"),
    [
        (Evidence("message", "mail", trigger_thread=(ACCOUNT, THREAD)), (ACCOUNT, THREAD)),
        (Evidence("message", "mail"), (None, None)),
        (Evidence("feed", "feed"), (None, None)),
    ],
)
async def test_admission_records_the_evidence_binding_on_the_goal(evidence, stored):
    recorder = _Recorder()
    ledger = GoalLedger(recorder)
    ledger.catalog_digest, ledger.config_digest = "catalog", "config"
    budget = GoalBudgetLimits(
        max_invocations=1,
        max_children=1,
        max_calls=1,
        max_tokens=1,
        max_spend=Decimal(1),
        deadline_seconds=60,
        invocation_max_turns=1,
        invocation_max_tokens=1,
    )
    source = SourceRef("message", "m1")
    now = datetime.now(UTC)
    await ledger.create_goal(
        uuid4(), source, "third_party", True, (), None, evidence, 0, budget, now
    )
    sql, args = recorder.statements[0]
    assert "trigger_gmail_account,trigger_gmail_thread" in sql
    assert args[-2:] == stored


# -- the per-call disposition --------------------------------------------------------


class _Catalog:
    async def load(self, connection):
        return Catalog("catalog-digest", (MODIFY, SEARCH))


class _Ledger:
    """The per-call transaction's ledger, with ``counted`` prior effects of the capability."""

    def __init__(self, counted: int):
        self.connection = object()
        self.counted = counted
        self.reserved: list[Decision] = []
        self.counted_for: list[tuple[Any, str]] = []

    async def counted_effects(self, goal_id, capability):
        self.counted_for.append((goal_id, capability))
        return self.counted

    async def owner_law(self, *args, **kwargs):
        return None

    async def reserve(self, principal, capability, decision, digest, recipient, channel=None):
        self.reserved.append(decision)
        return Reservation(principal, capability, decision, uuid4(), uuid4(), digest, recipient)


async def _authorize(who, args, counted=0):
    broker = Broker(object(), _Catalog(), object(), object())
    ledger = _Ledger(counted)
    reserved, _ = await broker._authorize(ledger, who, MODIFY.identity, args)
    return reserved, ledger


async def test_an_on_thread_label_change_dispatches_and_reserves_its_effect():
    reserved, ledger = await _authorize(_bound(), LABEL)
    assert reserved.decision.disposition is Disposition.DISPATCH
    assert ledger.reserved == [reserved.decision]
    assert ledger.counted_for == [(reserved.principal.goal_id, MODIFY.identity)]


@pytest.mark.parametrize(
    "args", [{**LABEL, "thread_ids": ["other"]}, {**LABEL, "remove_labels": ["INBOX"]}]
)
async def test_an_off_thread_target_or_archive_parks_per_event(args):
    reserved, _ = await _authorize(_bound(), args)
    assert reserved.decision == PER_EVENT
    assert reserved.decision.allow_standing_rule is False


async def test_the_fifth_call_runs_and_the_sixth_is_refused_before_any_reservation():
    reserved, _ = await _authorize(_bound(), LABEL, counted=GMAIL_MODIFY_CAP - 1)
    assert reserved.decision.disposition is Disposition.DISPATCH
    for counted in (GMAIL_MODIFY_CAP, GMAIL_MODIFY_CAP + 3):
        ledger = _Ledger(counted)
        broker = Broker(object(), _Catalog(), object(), object())
        with pytest.raises(BrokerRefused) as refused:
            await broker._authorize(ledger, _bound(), MODIFY.identity, LABEL)
        assert refused.value.reason is Reason.POLICY
        assert ledger.reserved == []


async def test_an_unexposed_owner_goal_is_neither_capped_nor_parked():
    owner = _bound(exposed=False, origin="owner", trigger=None)
    reserved, ledger = await _authorize(
        owner, {**LABEL, "thread_ids": ["other"], "remove_labels": ["INBOX"]}, counted=99
    )
    assert reserved.decision.disposition is Disposition.DISPATCH
    assert ledger.counted_for == []
