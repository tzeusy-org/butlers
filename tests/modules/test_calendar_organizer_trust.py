"""organizer_trust: the policy view mirrors the connector's evaluators; failures read as None."""

from __future__ import annotations

from typing import Any

import pytest

from butlers.modules.calendar.organizer_trust import (
    identity_class,
    load_policy_view,
    stamp_for,
)


class _Pool:
    def __init__(self, rules: list[dict[str, Any]] | Exception, identity: Any = "none") -> None:
        self._rules = rules
        self._identity = identity

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if isinstance(self._rules, Exception):
            raise self._rules
        return self._rules

    async def fetchval(self, query: str, *args: Any) -> Any:
        if isinstance(self._identity, Exception):
            raise self._identity
        return self._identity


def _rule(scope: str, address: str, action: str, **extra: Any) -> dict[str, Any]:
    return {
        "scope": scope,
        "id": f"{scope}:{address}",
        "rule_type": "sender_address",
        "condition": {"address": address, **extra},
        "action": action,
        "priority": 1,
        "name": None,
        "created_at": "2026-11-24T00:00:00+00:00",
        "created_by": "owner",
        "promoted_from_suggestion_id": None,
    }


_CONNECTOR = "connector:google_calendar:me@example.com"


async def test_a_connector_block_and_a_global_skip_both_block() -> None:
    pool = _Pool([_rule(_CONNECTOR, "a@x.test", "block"), _rule("global", "b@x.test", "skip")])
    view = await load_policy_view(pool)  # type: ignore[arg-type]
    assert view is not None
    assert view.blocks("a@x.test", known_contact=False)
    assert view.blocks("b@x.test", known_contact=False)
    assert not view.blocks("c@x.test", known_contact=False)


async def test_a_global_metadata_only_rule_does_not_block() -> None:
    view = await load_policy_view(_Pool([_rule("global", "a@x.test", "metadata_only")]))  # type: ignore[arg-type]
    assert view is not None and not view.blocks("a@x.test", known_contact=False)


async def test_a_rule_that_excepts_known_contacts_passes_a_known_entity() -> None:
    rule = _rule(_CONNECTOR, "a@x.test", "block", except_known_contacts=True)
    view = await load_policy_view(_Pool([rule]))  # type: ignore[arg-type]
    assert view is not None
    assert view.blocks("a@x.test", known_contact=False)
    assert not view.blocks("a@x.test", known_contact=True)


async def test_an_unreadable_rule_store_is_none_and_never_an_empty_view() -> None:
    assert await load_policy_view(_Pool(RuntimeError("denied"))) is None  # type: ignore[arg-type]


async def test_a_failed_identity_lookup_is_none() -> None:
    assert await identity_class(_Pool([], RuntimeError("boom")), "a@x.test") is None  # type: ignore[arg-type]
    assert await identity_class(_Pool([], "bogus"), "a@x.test") is None  # type: ignore[arg-type]
    assert await identity_class(None, "a@x.test") is None


@pytest.mark.parametrize(
    ("identity", "rules", "expected"),
    [
        ("known_entity", [], "known_entity"),
        ("none", [], "first_contact"),
        ("known_entity", [_rule(_CONNECTOR, "a@x.test", "block")], "blocked"),
        (RuntimeError("x"), [], "unknown"),
        ("known_entity", RuntimeError("x"), "unknown"),
    ],
)
async def test_the_stamp_never_admits_on_a_failed_read(identity, rules, expected) -> None:
    pool = _Pool(rules, identity)
    view = await load_policy_view(pool)  # type: ignore[arg-type]
    stamp = await stamp_for(
        pool,  # type: ignore[arg-type]
        view,
        organizer="a@x.test",
        own_email="me@example.com",
        attendee_count=2,
        event_type="default",
    )
    assert stamp["organizer_verdict"] == expected
    assert stamp["verdict_version"] >= 1


def _vouch(scope: str, address: str, **override: Any) -> dict[str, Any]:
    return {**_rule(scope, address, "pass_through"), "created_by": "calendar_vouch", **override}


async def test_only_the_verbs_own_pass_through_rule_in_a_calendar_scope_is_a_vouch() -> None:
    rules = [
        _vouch(_CONNECTOR, "Sam@X.test"),
        # an ordinary pass_through rule is not a vouch
        {**_rule(_CONNECTOR, "other@x.test", "pass_through"), "created_by": "dashboard"},
        # a vouch-shaped rule outside a calendar scope is not a vouch
        _vouch("connector:gmail:me@example.com", "mail@x.test"),
        _vouch("global", "glob@x.test"),
        # a block written by the verb is not a vouch
        {**_rule(_CONNECTOR, "blocked@x.test", "block"), "created_by": "calendar_vouch"},
    ]
    view = await load_policy_view(_Pool(rules))  # type: ignore[arg-type]
    assert view is not None
    assert view.vouches(" sam@x.test ")
    for address in ("other@x.test", "mail@x.test", "glob@x.test", "blocked@x.test"):
        assert not view.vouches(address)


async def test_a_vouch_lifts_a_first_contact_but_a_global_block_still_wins() -> None:
    from butlers.modules.calendar.organizer_trust import stamp_for

    rules = [_vouch(_CONNECTOR, "sam@x.test"), _rule("global", "sam@x.test", "skip")]
    view = await load_policy_view(_Pool(rules))  # type: ignore[arg-type]

    async def verdict(policy) -> str:
        stamp = await stamp_for(
            _Pool([]),  # type: ignore[arg-type]
            policy,
            organizer="sam@x.test",
            own_email="me@example.com",
            attendee_count=2,
            event_type="default",
        )
        return stamp["organizer_verdict"]

    assert await verdict(view) == "blocked"
    only_vouch = await load_policy_view(_Pool([_vouch(_CONNECTOR, "sam@x.test")]))  # type: ignore[arg-type]
    assert await verdict(only_vouch) == "vouched"
