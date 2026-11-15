"""``propose_fact`` arguments, ids and inbox write mapping (REQ-public-mcp-proposals-001, -002, -003).

Unit level, over a recording connection. The real inbox, grants and cap trigger are exercised in
``tests/integration/test_public_mcp_proposals.py``.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import asyncpg
import pytest
import rfc8785

from butlers.knowledge_read.core import ReadContext, ReadError
from butlers.mcp_public import proposals
from butlers.mcp_public.proposals import NS, payload_of, proposal_id, propose_fact

pytestmark = pytest.mark.unit

# A fixed instant: nothing here depends on the clock.
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
A = "00000000-0000-0000-0000-00000000000a"
B = "00000000-0000-0000-0000-00000000000b"
CONTEXT = ReadContext(
    sub="4242", cid="https://claude.ai/meta", as_of=NOW, now=NOW, cursor_key=b"k" * 32
)
GOOD = {
    "subject_entity_id": A,
    "predicate": "works-at",
    "object_entity_id": B,
    "idempotency_key": "k1",
}


class Recorder:
    def __init__(self, fail: BaseException | None = None):
        self.calls: list[tuple] = []
        self.fail = fail

    async def execute(self, sql: str, *args):
        self.calls.append((sql, *args))
        if self.fail is not None:
            raise self.fail
        return "INSERT 0 1"


def _error(message: str | None, cls=asyncpg.RaiseError) -> asyncpg.PostgresError:
    exc = cls(message)
    exc.message = message  # the driver sets it from the server's message
    return exc


async def _code(arguments, *, context=CONTEXT) -> str:
    recorder = Recorder()
    with pytest.raises(ReadError) as refused:
        await propose_fact(recorder, context, arguments)  # type: ignore[arg-type]
    assert recorder.calls == [], "a refusal writes nothing"
    return refused.value.code


def test_the_namespace_is_fixed():
    """The id of every proposal depends on it: a change re-ids them all."""
    assert NS == uuid.uuid5(uuid.NAMESPACE_URL, "urn:butlers:public-mcp:propose_fact:v1")
    assert str(NS) == "91aad1a5-9534-5720-865f-e229e55a3b92"


def test_the_id_is_uuid5_of_the_jcs_array_of_four_strings():
    payload = payload_of({**GOOD, "evidence_url": None, "note": None})
    digest = hashlib.sha256(rfc8785.dumps(payload)).hexdigest()
    name = rfc8785.dumps(["4242", "https://claude.ai/meta", "k1", digest]).decode()
    assert proposal_id("4242", "https://claude.ai/meta", "k1", payload) == str(uuid.uuid5(NS, name))
    # A golden value: the derivation never drifts silently.
    assert proposal_id("4242", "https://claude.ai/meta", "k1", payload) == (
        "7a8d4515-27a5-5ca7-a4c4-c09a1e750ef4"
    )


def test_the_concatenation_is_unambiguous():
    payload = payload_of({**GOOD, "evidence_url": None, "note": None})
    ids = {
        proposal_id("ab", "c", "k", payload),
        proposal_id("a", "bc", "k", payload),
        proposal_id("a", "b", "ck", payload),
        proposal_id("a", "b\u0000c", "k", payload),
    }
    assert len(ids) == 4


def test_every_payload_field_and_each_key_part_changes_the_id():
    base = {**GOOD, "evidence_url": None, "note": None}
    ids = {proposal_id("1", "c", "k", payload_of(base))}
    for change in (
        {"subject_entity_id": B},
        {"predicate": "knows"},
        {"object_entity_id": A},
        {"evidence_url": "https://example.org/x"},
        {"note": "hi"},
    ):
        ids.add(proposal_id("1", "c", "k", payload_of({**base, **change})))
    ids |= {
        proposal_id("2", "c", "k", payload_of(base)),
        proposal_id("1", "d", "k", payload_of(base)),
        proposal_id("1", "c", "j", payload_of(base)),
    }
    assert len(ids) == 9


async def test_a_valid_proposal_inserts_once_and_returns_the_derived_id():
    recorder = Recorder()
    args = {**GOOD, "evidence_url": "https://example.org/a", "note": "Met at a conference."}
    result = await propose_fact(recorder, CONTEXT, args)  # type: ignore[arg-type]
    expected = proposal_id(
        "4242",
        "https://claude.ai/meta",
        "k1",
        payload_of({**args}),
    )
    assert result == {"proposal_id": expected, "status": "received"}
    [(sql, *params)] = recorder.calls
    assert sql.endswith("ON CONFLICT DO NOTHING")
    assert "SELECT" not in sql.upper().replace("INSERT", "")
    assert params == [
        uuid.UUID(expected),
        "4242",
        "https://claude.ai/meta",
        "k1",
        uuid.UUID(A),
        "works-at",
        uuid.UUID(B),
        "https://example.org/a",
        "Met at a conference.",
    ]


async def test_an_identical_retry_gets_the_same_id():
    first = await propose_fact(Recorder(), CONTEXT, dict(GOOD))  # type: ignore[arg-type]
    again = await propose_fact(Recorder(), CONTEXT, dict(GOOD))  # type: ignore[arg-type]
    changed = await propose_fact(
        Recorder(),
        CONTEXT,
        {**GOOD, "object_entity_id": A},  # type: ignore[arg-type]
    )
    assert first == again
    assert changed["proposal_id"] != first["proposal_id"]


async def test_sub_and_cid_come_from_the_assertion_never_from_arguments():
    for forged in ({"sub": "1"}, {"cid": "https://evil.example/c"}, {"client_id": "x"}):
        assert await _code({**GOOD, **forged}) == "invalid_argument"
    other = replace(CONTEXT, sub="9999")
    one = await propose_fact(Recorder(), CONTEXT, dict(GOOD))  # type: ignore[arg-type]
    two = await propose_fact(Recorder(), other, dict(GOOD))  # type: ignore[arg-type]
    assert one["proposal_id"] != two["proposal_id"]


@pytest.mark.parametrize(
    "predicate",
    [
        "parent-of",
        "child-of",
        "family-of",
        "partner-of",
        "has-email",
        "dunbar_tier_override",
        "Knows",
        "",
    ],
)
async def test_only_the_allowlist_is_proposable(predicate):
    assert await _code({**GOOD, "predicate": predicate}) == "invalid_argument"


@pytest.mark.parametrize(
    "bad",
    [
        {"note": "n" * 501},
        {"note": 5},
        {"idempotency_key": ""},
        {"idempotency_key": "k" * 65},
        {"idempotency_key": None},
        {"evidence_url": "u" * 2049},
        {"evidence_url": "ftp://example.org/x"},
        {"evidence_url": "javascript:alert(1)"},
        {"evidence_url": "https://user:pw@example.org/"},
        {"evidence_url": "https:///nohost"},
        {"evidence_url": "https://example.org/a b"},
        {"evidence_url": "https://example.org/\u0007"},
        {"evidence_url": "/relative"},
        {"subject_entity_id": "not-a-uuid"},
        {"object_entity_id": None},
        {"object_entity_id": "a literal value"},
        {"sql": "x"},
    ],
)
async def test_bad_arguments_are_refused_and_write_nothing(bad):
    assert await _code({**GOOD, **bad}) == "invalid_argument"


async def test_missing_required_arguments_are_refused():
    for name in GOOD:
        assert await _code({k: v for k, v in GOOD.items() if k != name}) == "invalid_argument"
    assert await _code("not an object") == "invalid_argument"  # type: ignore[arg-type]


async def test_a_note_of_exactly_500_and_a_url_of_exactly_2048_are_accepted():
    url = "https://example.org/" + "u" * (2048 - len("https://example.org/"))
    result = await propose_fact(
        Recorder(),
        CONTEXT,  # type: ignore[arg-type]
        {**GOOD, "note": "n" * 500, "evidence_url": url},
    )
    assert result["status"] == "received"


async def test_a_disabled_proposals_domain_refuses_before_the_write():
    off = replace(CONTEXT, disabled_domains=frozenset({"proposals"}))
    recorder = Recorder()
    with pytest.raises(ReadError) as refused:
        await propose_fact(recorder, off, dict(GOOD))  # type: ignore[arg-type]
    assert (refused.value.code, refused.value.domain) == ("domain_disabled", "proposals")
    assert recorder.calls == []


async def test_an_assertion_without_a_client_id_never_writes():
    assert await _code(dict(GOOD), context=replace(CONTEXT, cid="")) == "unavailable"


@pytest.mark.parametrize("field", ["sub", "cid"])
async def test_an_identity_longer_than_the_inbox_bound_is_refused_for_good(field):
    """The inbox CHECKs length 1..256; an over-long identity must not become a retry loop."""
    edge = "https://claude.ai/" + "p" * (256 - len("https://claude.ai/"))
    at_bound = replace(CONTEXT, **{field: edge})
    accepted = await propose_fact(Recorder(), at_bound, dict(GOOD))  # type: ignore[arg-type]
    assert accepted["status"] == "received"
    over = replace(CONTEXT, **{field: edge + "p"})
    recorder = Recorder()
    with pytest.raises(ReadError) as refused:
        await propose_fact(recorder, over, dict(GOOD))  # type: ignore[arg-type]
    error = refused.value.to_dict()["error"]
    assert (error["code"], error["retryable"]) == ("blocked", False)
    assert recorder.calls == [], "a refusal writes nothing"


async def test_the_full_inbox_is_unavailable_and_nothing_else_is_mapped():
    full = Recorder(_error(proposals.INBOX_FULL))
    with pytest.raises(ReadError) as refused:
        await propose_fact(full, CONTEXT, dict(GOOD))  # type: ignore[arg-type]
    assert refused.value.code == "unavailable"
    assert refused.value.to_dict()["error"]["retryable"] is True
    # A different trigger error, and a different driver error, are not "inbox full".
    for other in (
        _error("something else"),
        _error(None),
        _error("public MCP inbox full", asyncpg.CheckViolationError),
        _error("permission denied", asyncpg.InsufficientPrivilegeError),
    ):
        with pytest.raises(asyncpg.PostgresError) as raised:
            await propose_fact(Recorder(other), CONTEXT, dict(GOOD))  # type: ignore[arg-type]
        assert raised.value is other or isinstance(raised.value, type(other))
