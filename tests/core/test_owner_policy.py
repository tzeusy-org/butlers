"""Owner-law term matching without Postgres (REQ-capability-authorization-020).

Storage, CAS and the database guards are in
``tests/switchboard/test_owner_policy_postgres.py``.
"""

from uuid import UUID, uuid4

import pytest

from butlers.core.owner_policy import (
    NarrowingUnsupported,
    OwnerPolicyWriter,
    Policy,
    PolicyUnavailable,
    Term,
    canonical_resource,
    declared_resource_params,
    hold_for,
)

pytestmark = pytest.mark.unit

CALL = ("messenger__send", "messenger", "communicate")
FREE = frozenset({"to"})  # the current catalog marks "to" alias-free


def _term(kind, selector, constraint="never", resources=None, term_id=None):
    return Term.from_row(
        {
            "id": term_id or uuid4(),
            "selector_kind": kind,
            "selector": selector,
            "resources": resources or {},
            "term": constraint,
            "version": 1,
            "expires_at": None,
        }
    )


@pytest.mark.parametrize(
    ("kind", "selector", "selected"),
    [
        ("capability", "messenger__send", True),
        ("capability", "messenger__send_all", False),  # exact identity, never a prefix
        ("region", "messenger", True),
        ("region", "email", False),
        ("effect_class", "communicate", True),
        ("effect_class", "read", False),
    ],
)
def test_a_term_selects_by_exact_capability_region_or_effect_class(kind, selector, selected):
    assert _term(kind, selector).selects(*CALL, {}) is selected


@pytest.mark.parametrize(
    ("args", "selected"),
    [
        ({"to": "a"}, True),
        ({"to": ["b", "a"]}, True),
        ({"to": "b"}, False),
        ({}, True),  # an omitted argument never becomes a permissive gap
    ],
)
def test_resource_narrowing_only_releases_calls_naming_other_resources(args, selected):
    term = _term("capability", "messenger__send", resources={"to": ["a"]})
    assert term.selects(*CALL, args, FREE) is selected


ENTITY = "6f1c2a8e-0b7d-4c3e-9a51-2d4f8e7b1c90"


@pytest.mark.parametrize(
    "value",
    [
        ENTITY.upper(),  # another spelling of the same UUID
        "{" + ENTITY + "}",
        ENTITY.replace("-", ""),
        f"  {ENTITY}\n",  # padding
        ["someone-else", f" {ENTITY.upper()} "],
        123,  # a wrong type has no canonical identifier
        {"id": ENTITY},
        [],
        [None],
        None,
        "",
        "   ",
    ],
)
def test_a_deny_list_holds_any_spelling_or_unreadable_form(value):
    """bu-dhloi1.10 review: narrowing must never be the permissive direction."""
    term = _term("capability", "messenger__send", resources={"to": [ENTITY]})
    assert term.selects(*CALL, {"to": value}, FREE) is True


@pytest.mark.parametrize("value", ["  Alice  ", "ALICE", "Ａｌｉｃｅ"])  # noqa: RUF001
def test_plain_identifiers_match_trimmed_case_folded_and_normalised(value):
    term = _term("capability", "messenger__send", resources={"to": ["alice"]})
    assert term.selects(*CALL, {"to": value}, FREE) is True
    assert term.selects(*CALL, {"to": "bob"}, FREE) is False


def test_stored_identifiers_are_canonical():
    term = _term("capability", "messenger__send", resources={"to": [f" {ENTITY.upper()} "]})
    assert term.resources == (("to", frozenset({ENTITY})),)
    assert term.selects(*CALL, {"to": ENTITY}, FREE) is True
    assert canonical_resource(ENTITY.upper()) == ENTITY


async def test_a_narrowing_on_an_undeclared_resource_key_is_refused_at_write():
    writer = OwnerPolicyWriter(connection=None)  # refused before any database access
    with pytest.raises(ValueError, match="resources"):
        await writer.create(
            uuid4(), "capability", "messenger__send", "never", resources={"account": ["a1"]}
        )
    with pytest.raises(ValueError, match="resources"):
        await writer.create(
            uuid4(),
            "capability",
            "messenger__send",
            "never",
            resources={"account": ["a1"]},
            resource_params=("to",),
        )
    with pytest.raises(ValueError, match="resources"):  # an id with no canonical form
        await writer.create(
            uuid4(),
            "capability",
            "messenger__send",
            "never",
            resources={"to": ["  "]},
            resource_params=("to",),
        )


def test_declared_resource_params_follow_the_selector():
    class Cap:
        def __init__(self, identity, domain, effect_class, params, alias_free=(), resolved=()):
            self.identity, self.domain, self.effect_class = identity, domain, effect_class
            self.resource_params, self.alias_free_params = params, frozenset(alias_free)
            self.alias_resolvers = tuple((name, "calendar") for name in resolved)

    catalog = [
        Cap("messenger__send", "messenger", "communicate", ("to",), ("to",)),
        Cap("messenger__search", "messenger", "read", ("account",)),
        Cap("email__send", "email", "communicate", ("thread", "to")),
    ]
    send = declared_resource_params(catalog, "capability", "messenger__send")
    assert (send.params, send.alias_free) == ({"to"}, {"to"})
    region = declared_resource_params(catalog, "region", "messenger")
    assert (region.params, region.alias_free) == ({"to", "account"}, {"to"})
    # One selected capability where "to" is aliasable makes it aliasable for the class.
    cls = declared_resource_params(catalog, "effect_class", "communicate")
    assert (cls.params, cls.alias_free) == ({"to", "thread"}, set())
    # A param the broker resolves to its canonical id (bu-dhloi1.30) can narrow.
    calendar = [Cap("calendar__list", "calendar", "read", ("calendar_id",), (), ("calendar_id",))]
    listed = declared_resource_params(calendar, "capability", "calendar__list")
    assert listed.alias_free == {"calendar_id"}


async def test_a_narrowing_on_an_aliasable_param_is_refused_at_write():
    """Review r4480176241: calendar_id "primary" aliases the owner's calendar."""
    writer = OwnerPolicyWriter(connection=None)  # refused before any database access
    with pytest.raises(NarrowingUnsupported):
        await writer.create(
            uuid4(),
            "capability",
            "calendar__calendar_create_event",
            "never",
            resources={"calendar_id": ["owner@gmail.com"]},
            resource_params=("calendar_id", "entity_ids"),
            alias_free_params=("entity_ids",),
        )


async def test_the_calendar_id_narrows_from_the_real_inventory_once_resolved():
    """bu-dhloi1.30: the broker resolves "primary" before owner law, so it narrows."""
    from butlers.capability_broker.contracts import Capability
    from butlers.core.tool_catalog import RecipientParameters
    from butlers.core.tool_presentation_inventory import TOOL_PRESENTATION_INVENTORY

    create = next(
        p for p in TOOL_PRESENTATION_INVENTORY if p.canonical_name == "calendar_create_event"
    )
    assert "calendar_id" not in create.alias_free_params  # "primary" is an alias
    assert dict(create.alias_resolvers) == {"calendar_id": "calendar"}
    capability = Capability(
        "calendar",
        create.canonical_name,
        "record",
        "untrusted",
        RecipientParameters(),
        resource_params=tuple(create.resource_params or ()),
        alias_free_params=frozenset(create.alias_free_params or ()),
        alias_resolvers=tuple(sorted(dict(create.alias_resolvers or {}).items())),
    )
    declared = declared_resource_params([capability], "capability", capability.identity)
    assert declared.alias_free == {"calendar_id", "entity_ids"}
    receipt_request = dict(
        resources={"calendar_id": ["owner@gmail.com"]},
        resource_params=tuple(declared.params),
        alias_free_params=tuple(declared.alias_free),
    )

    class Refusing:
        def transaction(self):
            raise AssertionError("validated")

    # Validation passes; the write then begins (refused here by the stub).
    with pytest.raises(AssertionError, match="validated"):
        await OwnerPolicyWriter(Refusing()).create(
            uuid4(), "capability", capability.identity, "never", **receipt_request
        )


def test_a_term_on_a_param_no_longer_alias_free_selects_every_call():
    """A catalog that flips a narrowed param to aliasable can't release the term."""
    term = _term("capability", "messenger__send", resources={"to": ["alice"]})
    assert term.selects(*CALL, {"to": "bob"}, FREE) is False
    assert term.selects(*CALL, {"to": "bob"}, frozenset()) is True
    assert term.selects(*CALL, {"to": "bob"}) is True  # no declaration is aliasable


def test_the_hold_is_never_when_any_holding_term_is_never():
    first, second = UUID(int=1), UUID(int=2)
    policy = Policy(
        7,
        (
            _term("region", "messenger", "always_ask", term_id=first),
            _term("effect_class", "communicate", "never", term_id=second),
            _term("region", "email", "never"),
        ),
    )
    hold = hold_for(policy, *CALL, {})
    assert (hold.constraint, hold.generation) == ("never", 7)
    assert [t["id"] for t in hold.receipt()["terms"]] == [str(second), str(first)]
    assert hold_for(policy, "email__read", "email", "read", {}).constraint == "never"
    assert hold_for(policy, "calendar__read", "calendar", "read", {}) is None


@pytest.mark.parametrize(
    "field",
    [
        {"selector_kind": "mode"},
        {"selector": "messenger__"},
        {"term": "allow"},
        {"version": 0},
        {"resources": {"to": []}},
        {"resources": ["to"]},
    ],
)
def test_an_unreadable_term_makes_the_policy_unavailable(field):
    row = {
        "id": uuid4(),
        "selector_kind": "capability",
        "selector": "messenger__send",
        "resources": {},
        "term": "never",
        "version": 1,
        "expires_at": None,
        **field,
    }
    with pytest.raises(PolicyUnavailable):
        Term.from_row(row)
