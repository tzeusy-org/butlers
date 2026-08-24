"""Fleet-wide guard: an API caller can never choose the actor we record.

An actor value a caller supplies is not attribution — the caller can write
anything — and an audit row that cannot be trusted is worse than an absent one,
because it is read as evidence.  bu-4y9ck fixed one route
(``PATCH /api/ingestion/channel-defaults/{channel}``); bu-6zlqt swept the rest
and left these tests behind so the defect cannot reappear silently.

Every route under ``src/butlers/api/routers/`` and ``roster/*/api/`` that
persists or audits an actor is classified below.  The tests in this module
enforce the classification mechanically against the *live* application, so a
new route that accepts caller-supplied attribution fails here rather than
shipping.

Recorded classification
=======================

Caller-asserted → fixed (attribution now from ``authenticated_principal()``,
the wire field dropped from the request model, older clients still accepted):

===================================================  ========================  ==========
Route                                                Field it used to trust    Fixed by
===================================================  ========================  ==========
``PATCH /api/ingestion/channel-defaults/{channel}``  body ``updated_by``       bu-4y9ck
``PUT  /api/butlers/{name}/prompt``                  body ``actor``            bu-6zlqt
``PUT  /api/butlers/{name}/tools/{tool}``            body ``actor``            bu-6zlqt
``POST /api/butlers/{name}/kill``                    body ``actor``            bu-6zlqt
``POST /api/chronicler/episodes/{id}/corrections``   body ``submitted_by``     bu-6zlqt
===================================================  ========================  ==========

Server-derived — no caller input reaches the recorded actor:

- ``authenticated_principal()`` / ``build_user_context()``: every
  ``emit_dashboard_audit`` emit (the ``DashboardAuditMiddleware`` and the
  explicit emits in ``roster/{chronicler,switchboard,home,finance,education,
  relationship}/api/router.py``, ``runtime_config.py``), plus
  ``channel_defaults.py`` and the four routes fixed by bu-6zlqt.
- Hardcoded ``"owner"``: ``webhooks.py`` (create/update/delete/test),
  ``data_ops.py`` (export/wipe), ``permissions.py`` (permission.set),
  ``memory.py`` (retention policies — SQL literal and audit actor),
  ``oauth.py`` (``_emit_oauth_audit`` default, no call site overrides it),
  ``model_settings.py`` (``audit_actor`` default; the only non-default value is
  the server-side sweep's own ``"model_verify_sweep"``, per
  ``openspec/specs/dashboard-model-settings/spec.md``), ``spend.py``
  (rule/ceiling mutations), ``secrets_v2.py`` (``_OWNER_ACTOR``).
- Hardcoded channel label ``"dashboard"`` / ``"dashboard:rest-api"``:
  ``priority_contacts.py`` (``added_by`` column and audit actor),
  ``ingestion_connectors.py``, ``ingestion_events.py``, ``approvals.py``
  suggestion confirm/dismiss, ``roster/switchboard/api/router.py``
  (``routing_instructions.created_by``, ``ingestion_rules.created_by`` — both
  SQL literals) and its ``decided_by='owner'`` promotion writes.  These name
  the channel rather than a principal; they are not forgeable, but see the
  follow-up note at the end of this docstring.
- Server-verified caller provenance: ``POST /api/approvals/{id}/approve`` and
  ``/deny`` read ``X-Butlers-Decision-Actor``, but
  ``approvals._decision_actor_id`` accepts ``owner@telegram`` **only** when the
  request also passed callback authentication (401 otherwise) and rejects any
  other value on an authenticated callback (403).  The header cannot be used to
  assert an identity the caller did not prove.
- Read-only surfaces, not attribution writes: ``GET /api/audit-log?actor=`` and
  ``GET /api/audit-log/{id}`` (filter / echo of stored rows),
  ``GET /api/butlers/{name}/prompt``, ``.../prompt/history``,
  ``GET /api/ingestion/channel-defaults/{channel}``,
  ``GET /api/ingestion/priority-contacts``, ``GET /api/memory/retention-policies``,
  ``GET .../routing-rules``, ``secrets_v2`` audit-history responses,
  ``GET /api/system/egress`` (``actor_id`` is an *external recipient* resolved
  from the server-side ``_ACTOR_REGISTRY``, not the requester).
- Not attribution despite the name: ``PUT /api/qa/settings/git-author`` stores
  the git author name/email QA commits are made with.  It is a configuration
  value, honestly named, and carries no claim about who called the endpoint.

Deferred (classified, deliberately not edited by bu-6zlqt because open PRs
owned these files at dispatch time): ``src/butlers/api/app.py``,
``routers/provider_settings.py``, ``routers/secrets_v2.py``,
``routers/system.py``.  All four are **server-derived**; no fix is owed.

Follow-up worth filing (not in this bead's scope): the ``"dashboard"`` literals
above put a *channel* in a column named for a *principal*.  They are not
forgeable, so they are not this defect, but ``audit_emit.build_user_context``
already separates ``principal`` from ``source`` and these sites predate it.
"""

from __future__ import annotations

import re
import typing

import pytest
from fastapi.routing import APIRoute
from pydantic import BaseModel

from butlers.api.app import create_app
from butlers.api.audit_emit import IgnoresCallerAssertedActor, authenticated_principal

pytestmark = pytest.mark.unit

#: Field names that name *who acted*.  A request body must never carry one.
_ACTOR_FIELD = re.compile(
    r"^(updated_by|created_by|changed_by|requested_by|performed_by|acted_by"
    r"|triggered_by|initiated_by|approved_by|resolved_by|assigned_by|deleted_by"
    r"|reviewed_by|owned_by|added_by|granted_by|revoked_by|submitted_by"
    r"|acknowledged_by|closed_by|set_by|decided_by|written_by"
    r"|actor|actor_id|principal|on_behalf_of|as_user)$"
)

#: Header/query names that could carry an asserted identity.
_ACTOR_PARAM = re.compile(r"actor|principal|behalf", re.IGNORECASE)

#: Query parameters that name an actor but only *filter* stored rows.
_ALLOWED_ACTOR_QUERY_PARAMS: frozenset[tuple[str, str]] = frozenset(
    {("GET", "/api/audit-log", "actor")}
)

#: Headers that carry caller provenance the server independently verifies.
#: ``approvals._decision_actor_id`` 401s an unauthenticated caller that claims
#: Telegram provenance and 403s an authenticated callback that claims anything
#: else, so the header cannot assert an unproven identity.
_ALLOWED_ACTOR_HEADERS: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("POST", "/api/approvals/{action_id}/approve", "X-Butlers-Decision-Actor"),
        ("POST", "/api/approvals/{action_id}/deny", "X-Butlers-Decision-Actor"),
    }
)

# Enough of the surface must be reachable for these sweeps to mean anything.
# The numbers are deliberate floors, not exact counts: they exist so a wiring
# change that silently empties the enumeration turns the sweeps red instead of
# letting them pass over nothing.
_MIN_ROUTES = 400
_MIN_BODY_ROUTES = 90
_MIN_QUERY_PARAMS = 400


@pytest.fixture(scope="module")
def api_routes() -> list[APIRoute]:
    """Every ``APIRoute`` the dashboard app mounts, including roster routers."""

    def walk(routes) -> typing.Iterator[APIRoute]:
        for route in routes:
            if isinstance(route, APIRoute):
                yield route
            # FastAPI mounts included routers lazily; descend into the original.
            original = getattr(route, "original_router", None)
            if original is not None:
                yield from walk(original.routes)

    return list(walk(create_app(api_key="").routes))


def _request_models(annotation: typing.Any, seen: set | None = None) -> typing.Iterator[type]:
    """Yield every Pydantic model reachable from a request-body annotation."""
    seen = set() if seen is None else seen
    try:
        if annotation in seen:
            return
        seen.add(annotation)
    except TypeError:  # unhashable annotation (rare); nothing to recurse into
        return
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
        for field in annotation.model_fields.values():
            yield from _request_models(field.annotation, seen)
        return
    for arg in typing.get_args(annotation):
        yield from _request_models(arg, seen)


def test_route_enumeration_is_not_vacuous(api_routes: list[APIRoute]) -> None:
    """Guard the guards: an empty enumeration would make every sweep pass."""
    assert len(api_routes) >= _MIN_ROUTES, (
        f"Only {len(api_routes)} routes enumerated (expected >= {_MIN_ROUTES}). "
        "Every sweep in this module iterates this list, so a truncated "
        "enumeration would make them all pass vacuously."
    )


def test_no_request_body_carries_an_actor_field(api_routes: list[APIRoute]) -> None:
    """No mounted route may accept attribution in its request body."""
    body_routes = [r for r in api_routes if getattr(r, "body_field", None) is not None]
    assert len(body_routes) >= _MIN_BODY_ROUTES, (
        f"Only {len(body_routes)} routes with a request body (expected >= "
        f"{_MIN_BODY_ROUTES}). Without them this assertion would be vacuous."
    )

    offenders: set[tuple[str, str, str, str]] = set()
    for route in body_routes:
        annotation = route.body_field.field_info.annotation
        for model in _request_models(annotation):
            for name in model.model_fields:
                if _ACTOR_FIELD.match(name):
                    offenders.add((sorted(route.methods)[0], route.path, model.__name__, name))

    assert not offenders, (
        "Request models must not carry attribution — a caller can write "
        "anything into them. Derive the value from "
        "butlers.api.audit_emit.authenticated_principal() and drop the field "
        "(inherit IgnoresCallerAssertedActor so old clients are ignored, not "
        f"422-rejected). Offending fields: {sorted(offenders)}"
    )


def test_no_route_takes_an_actor_from_query_or_header(api_routes: list[APIRoute]) -> None:
    """Attribution must not sneak in through a query parameter or header."""
    query_param_count = sum(len(r.dependant.query_params) for r in api_routes)
    assert query_param_count >= _MIN_QUERY_PARAMS, (
        f"Only {query_param_count} query parameters enumerated (expected >= "
        f"{_MIN_QUERY_PARAMS}). Without them this assertion would be vacuous."
    )

    offenders: set[tuple[str, str, str]] = set()
    for route in api_routes:
        method = sorted(route.methods)[0]
        for param in route.dependant.query_params:
            if _ACTOR_FIELD.match(param.name):
                entry = (method, route.path, param.name)
                if entry not in _ALLOWED_ACTOR_QUERY_PARAMS:
                    offenders.add(entry)
        for param in route.dependant.header_params:
            name = param.field_info.alias or param.name
            if _ACTOR_PARAM.search(name):
                entry = (method, route.path, name)
                if entry not in _ALLOWED_ACTOR_HEADERS:
                    offenders.add(entry)

    assert not offenders, (
        "A query parameter or header that names an actor is caller-asserted "
        "unless the server independently verifies it (see "
        "approvals._decision_actor_id). Classify it in this module's docstring "
        f"and allow-list it deliberately, or derive it instead: {sorted(offenders)}"
    )


def test_allow_listed_actor_params_still_exist(api_routes: list[APIRoute]) -> None:
    """A stale allow-list would hide a regression on the route it names."""
    assert _ALLOWED_ACTOR_QUERY_PARAMS or _ALLOWED_ACTOR_HEADERS, (
        "Both allow-lists are empty, so the exemption check below would be vacuous."
    )

    present_queries = {
        (sorted(r.methods)[0], r.path, p.name) for r in api_routes for p in r.dependant.query_params
    }
    present_headers = {
        (sorted(r.methods)[0], r.path, p.field_info.alias or p.name)
        for r in api_routes
        for p in r.dependant.header_params
    }

    stale = (_ALLOWED_ACTOR_QUERY_PARAMS - present_queries) | (
        _ALLOWED_ACTOR_HEADERS - present_headers
    )
    assert not stale, (
        "These allow-list entries no longer match a mounted route. Remove them "
        f"so the sweep keeps its teeth: {sorted(stale)}"
    )


# ---------------------------------------------------------------------------
# The mechanism the fixed routes rely on
# ---------------------------------------------------------------------------


class _StrictModel(IgnoresCallerAssertedActor):
    """Stand-in for a fixed request model that also forbids unknown fields."""

    model_config = {"extra": "forbid"}

    value: str


class _RenamedFieldModel(IgnoresCallerAssertedActor):
    """Stand-in for a model whose legacy wire name was not ``actor``."""

    caller_asserted_actor_fields = ("submitted_by",)

    value: str


def test_caller_supplied_actor_is_dropped_not_rejected() -> None:
    """Old clients keep working: the field is ignored, never a 422."""
    model = _StrictModel(**{"value": "v", "actor": "attacker-not-the-owner"})
    assert model.value == "v"
    assert not hasattr(model, "actor")
    assert "actor" not in model.model_dump()


def test_extra_forbid_still_rejects_genuine_typos() -> None:
    """Ignoring attribution must not weaken validation of everything else."""
    with pytest.raises(ValueError):
        _StrictModel(**{"value": "v", "valeu": "typo"})


def test_subclass_can_rename_the_ignored_wire_field() -> None:
    model = _RenamedFieldModel(**{"value": "v", "submitted_by": "attacker-not-the-owner"})
    assert not hasattr(model, "submitted_by")


def test_authenticated_principal_is_not_a_caller_supplied_default() -> None:
    """The derived principal must differ from the literals routes used to use."""
    principal = authenticated_principal()
    assert principal
    assert principal != "dashboard"
