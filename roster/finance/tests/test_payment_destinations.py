"""Unit tests for payment destination normalization, fingerprinting and stamping (bu-s11n0s.21).

No database: the real-Postgres cases through ``track_bill`` live in ``test_tools.py`` and pin
the verdicts. These cover only what those do not: normalization and classification, keyed
fingerprinting, credential-store key resolution, and the unparseable path. Keys and
destinations here are synthetic.
"""

from __future__ import annotations

import pytest

from butlers.tools.finance import payment_destinations as pd
from butlers.tools.finance.bills import _stamp_destination

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_key():
    pd.configure_destination_key(None)
    yield
    pd.configure_destination_key(None)


class _ScriptedConn:
    """Answers the two queries ``_stamp_destination`` issues."""

    def __init__(self, *, prior_count: int, inserted: bool) -> None:
        self.prior_count = prior_count
        self.inserted = inserted
        self.queries: list[str] = []

    async def fetchval(self, sql: str, *args):
        self.queries.append(sql)
        if "count(*)" in sql:
            return self.prior_count
        return self.inserted


@pytest.mark.parametrize(
    ("raw", "kind", "normalized"),
    [
        ("GB82 WEST 1234 5698 7654 32", "iban", "GB82WEST12345698765432"),
        ("gb82-west-1234-5698-7654-32", "iban", "GB82WEST12345698765432"),
        ("012-345-6789", "account", "0123456789"),
        ("  0123 4567 89 ", "account", "0123456789"),
        ("$SyntheticPayee", "handle", "$syntheticpayee"),
        ("Pay.Me@Example.COM", "handle", "pay.me@example.com"),
    ],
)
def test_normalize_classifies_and_canonicalizes(raw, kind, normalized):
    destination = pd.normalize_destination(raw)
    assert destination is not None
    assert (destination.kind, destination.normalized) == (kind, normalized)
    assert destination.last4 == normalized[-4:]


@pytest.mark.parametrize("raw", [None, "", "   ", "-- . --", "no digits here", "paynow"])
def test_unparseable_destinations_return_none(raw):
    assert pd.normalize_destination(raw) is None


def test_equivalent_spellings_share_a_fingerprint():
    pd.configure_destination_key("synthetic-key-a")
    a = pd.fingerprint_destination(pd.normalize_destination("GB82 WEST 1234 5698 7654 32"))
    b = pd.fingerprint_destination(pd.normalize_destination("gb82-west-1234-5698-7654-32"))
    assert a == b
    assert len(a) == 64


def test_fingerprint_depends_on_key_and_kind():
    destination = pd.normalize_destination("0123456789")
    pd.configure_destination_key("synthetic-key-a")
    first = pd.fingerprint_destination(destination)
    pd.configure_destination_key("synthetic-key-b")
    assert pd.fingerprint_destination(destination) != first
    # The same normalized text under another kind is a different identity.
    pd.configure_destination_key("synthetic-key-a")
    assert pd.fingerprint_destination(destination) == first
    other_kind = pd.Destination(kind="handle", normalized=destination.normalized)
    assert pd.fingerprint_destination(other_kind) != first


def test_fingerprint_is_none_without_a_key():
    assert pd.fingerprint_destination(pd.normalize_destination("0123456789")) is None


class _Store:
    def __init__(self, value=None, *, raises=False) -> None:
        self.value = value
        self.raises = raises
        self.calls: list[tuple] = []

    async def resolve(self, key, *, env_fallback=True):
        self.calls.append((key, env_fallback))
        if self.raises:
            raise RuntimeError("store down")
        return self.value


async def test_key_resolves_from_the_credential_store_without_env_fallback():
    store = _Store("synthetic-key-a")
    assert await pd.resolve_destination_key(store) is True
    assert store.calls == [(pd.PAYMENT_DESTINATION_KEY_NAME, False)]
    assert pd.destination_key_configured()


@pytest.mark.parametrize(
    ("store", "expected_warnings"),
    [(None, 1), (_Store(None), 1), (_Store("  "), 1), (_Store(raises=True), 2)],
)
async def test_missing_key_is_not_configured_and_warns_once(store, expected_warnings, caplog):
    with caplog.at_level("WARNING"):
        assert await pd.resolve_destination_key(store) is False
    assert not pd.destination_key_configured()
    # A raising store logs its failure and then the single missing-key warning.
    assert (
        sum(pd.PAYMENT_DESTINATION_KEY_NAME in r.getMessage() for r in caplog.records)
        == expected_warnings
    )


async def test_unparseable_destination_stores_nothing():
    """Not pinned by the Postgres cases: junk input is unverifiable and touches no table."""
    pd.configure_destination_key("synthetic-key-a")
    conn = _ScriptedConn(prior_count=0, inserted=True)
    stamp, unchanged = await _stamp_destination(
        conn, payee_key="acme", payment_destination="--", existing_fingerprint=None
    )
    assert unchanged is False
    assert (stamp.verdict, stamp.reason, stamp.fingerprint) == (
        "unverifiable",
        pd.REASON_UNPARSEABLE,
        None,
    )
    assert conn.queries == []
