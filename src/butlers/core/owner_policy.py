"""Owner tighten-only policy terms (RFC 0038 S1, REQ-capability-authorization-020).

The owner may hold capability calls with ``never`` (refuse) or ``always_ask``
(park the exact call for per-event approval). A term selects by exact catalog
capability identity, region (the catalog ``owner_domain``) or effect class,
optionally narrowed by resource arguments, and is matched against the current
catalog on every call. A term never grants: it only narrows a call the earlier
stages already permit.

Storage is sw_059's ``switchboard.owner_policy_*``. Writes are owner-only and
reach the database only through ``OwnerPolicyWriter`` from the owner dashboard;
no model capability, domain or connector writes them. Every change raises the
singleton ``generation`` and the term's ``version`` by one under expected-version
CAS, with one receipt per owner action, so a retried action is not a second
change. ``tighten`` admits only a tightening. ``loosen`` (a weaker constraint,
or an earlier or no-longer-absent expiry, which also lapses a term) needs the
owner's dashboard step-up of kind ``policy_loosen`` (bu-dhloi1.29): a fresh
passkey assertion bound by ``loosen_binding`` to that exact change at that term
version and policy generation, spent once with the write. sw_060's trigger
refuses any loosening whose exact result no step-up consumed in its transaction.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

import asyncpg

from butlers.core.dashboard_stepup import StepUpBinding, consume_stepup, dashboard_stepup_proof

SelectorKind = Literal["capability", "region", "effect_class"]
Constraint = Literal["never", "always_ask"]

SELECTOR_KINDS = frozenset({"capability", "region", "effect_class"})
CONSTRAINTS = frozenset({"never", "always_ask"})
EFFECT_CLASSES = frozenset({"read", "record", "communicate", "transact", "actuate", "administer"})
_IDENTITY = re.compile(r"[a-z][a-z0-9_]{0,62}__[a-z][a-z0-9_]{0,126}")
_REGION = re.compile(r"[a-z][a-z0-9_]{0,62}")
_RESOURCE_ARG = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")


class PolicyUnavailable(RuntimeError):
    """The owner policy state is missing or unreadable: authorization refuses."""


class PolicyConflict(ValueError):
    """A stale expected version, or an owner action id reused for another change."""


class StepUpRequired(PermissionError):
    """A loosening change; it needs the owner's dashboard step-up."""


class NotLoosening(ValueError):
    """A change that does not loosen; it is a tightening and needs no step-up."""


class NarrowingUnsupported(ValueError):
    """A narrowing on a resource param whose identifiers can be aliased."""


def _selector_valid(kind: str, selector: str) -> bool:
    if kind == "capability":
        return bool(_IDENTITY.fullmatch(selector))
    if kind == "region":
        return bool(_REGION.fullmatch(selector))
    return kind == "effect_class" and selector in EFFECT_CLASSES


def canonical_resource(value: Any) -> str | None:
    """One resource identifier's canonical form, or None when it has none.

    Terms are deny-lists, so the comparison must never be the permissive
    direction: a UUID in any spelling is its lowercase hyphenated form, other
    identifiers are NFKC-normalised, trimmed and case-folded. Anything that is
    not a non-empty string has no canonical form; a call naming it is held.
    """
    if type(value) is not str:
        return None
    text = unicodedata.normalize("NFKC", value).strip()
    if not text:
        return None
    try:
        return str(UUID(text))
    except ValueError:
        return text.casefold()


def _resources(value: Any) -> tuple[tuple[str, frozenset[str]], ...]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        raise ValueError("resources")
    narrowed = []
    for name, ids in value.items():
        if not isinstance(name, str) or not _RESOURCE_ARG.fullmatch(name):
            raise ValueError("resources")
        if not isinstance(ids, list) or not ids:
            raise ValueError("resources")
        canonical = [canonical_resource(i) for i in ids]
        if any(c is None for c in canonical):
            raise ValueError("resources")
        narrowed.append((name, frozenset(c for c in canonical if c is not None)))
    return tuple(sorted(narrowed, key=lambda item: item[0]))


@dataclass(frozen=True, slots=True)
class DeclaredResources:
    """What the current catalog declares for a selector's capabilities."""

    params: frozenset[str]
    # Params alias-free on every selected capability that declares them.
    alias_free: frozenset[str]


def declared_resource_params(
    capabilities: Iterable[Any], selector_kind: str, selector: str
) -> DeclaredResources:
    """The resource arguments a selector's capabilities declare, and the alias-free ones."""
    attribute = {"capability": "identity", "region": "domain", "effect_class": "effect_class"}
    selected = [c for c in capabilities if getattr(c, attribute[selector_kind]) == selector]
    params = frozenset(p for c in selected for p in c.resource_params)
    aliasable = frozenset(
        p for c in selected for p in c.resource_params if p not in c.alias_free_params
    )
    return DeclaredResources(params, params - aliasable)


def _releases(value: Any, ids: frozenset[str]) -> bool:
    """Whether a call's argument provably names only resources outside ``ids``.

    Fails closed: an empty list, a non-string or uncanonicalisable item, or
    any item in ``ids`` keeps the term selected.
    """
    values = value if isinstance(value, list) else [value]
    if not values:
        return False
    for item in values:
        canonical = canonical_resource(item)
        if canonical is None or canonical in ids:
            return False
    return True


@dataclass(frozen=True, slots=True)
class Term:
    id: UUID
    selector_kind: SelectorKind
    selector: str
    resources: tuple[tuple[str, frozenset[str]], ...]
    constraint: Constraint
    version: int
    expires_at: datetime | None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Term:
        """A stored term; anything unreadable makes the whole policy unavailable."""
        try:
            kind, selector, constraint = row["selector_kind"], row["selector"], row["term"]
            version = row["version"]
            if (
                kind not in SELECTOR_KINDS
                or not isinstance(selector, str)
                or not _selector_valid(kind, selector)
                or constraint not in CONSTRAINTS
                or type(version) is not int
                or version < 1
                or not isinstance(row["id"], UUID)
            ):
                raise ValueError("term")
            return cls(
                row["id"],
                kind,
                selector,
                _resources(row["resources"]),
                constraint,
                version,
                row["expires_at"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PolicyUnavailable("owner policy term unreadable") from exc

    def selects(
        self,
        identity: str,
        region: str,
        effect_class: str,
        args: Mapping,
        alias_free: frozenset[str] = frozenset(),
    ) -> bool:
        """Whether this term constrains the call, against the current catalog.

        A resource narrowing is a match unless the call names, for that
        argument, only canonical resources outside it; a call that omits the
        argument, or names it in any form without a canonical identifier, is
        matched, so narrowing never becomes a permissive gap. A narrowing on
        a param the current catalog does not mark alias-free for this
        capability (``alias_free``) cannot be trusted, so it selects the call.
        """
        target = {"capability": identity, "region": region, "effect_class": effect_class}
        if target[self.selector_kind] != self.selector:
            return False
        if any(name not in alias_free for name, _ in self.resources):
            return True
        for name, ids in self.resources:
            if name in args and _releases(args[name], ids):
                return False
        return True

    def ref(self) -> dict[str, Any]:
        """The content-blind reference a receipt records."""
        return {
            "id": str(self.id),
            "version": self.version,
            "term": self.constraint,
            "selector_kind": self.selector_kind,
            "selector": self.selector,
        }


@dataclass(frozen=True, slots=True)
class Policy:
    generation: int
    terms: tuple[Term, ...]

    def holding(
        self,
        identity: str,
        region: str,
        effect_class: str,
        args: Mapping,
        alias_free: frozenset[str] = frozenset(),
    ) -> tuple[Term, ...]:
        """Every active term constraining the call, ``never`` first."""
        matched = (
            t for t in self.terms if t.selects(identity, region, effect_class, args, alias_free)
        )
        return tuple(sorted(matched, key=lambda t: (t.constraint != "never", str(t.id))))


@dataclass(frozen=True, slots=True)
class PolicyHold:
    """The owner-law stage's holding terms, as a typed receipt records them."""

    constraint: Constraint
    generation: int
    terms: tuple[Term, ...]

    @property
    def refs(self) -> list[dict[str, Any]]:
        return [term.ref() for term in self.terms]

    def receipt(self) -> dict[str, Any]:
        return {
            "stage": "owner_law",
            "constraint": self.constraint,
            "generation": self.generation,
            "terms": self.refs,
        }


def hold_for(
    policy: Policy,
    identity: str,
    region: str,
    effect_class: str,
    args,
    alias_free: frozenset[str] = frozenset(),
) -> PolicyHold | None:
    """The owner-law hold on a call, or None when no active term selects it."""
    terms = policy.holding(identity, region, effect_class, args, alias_free)
    if not terms:
        return None
    constraint: Constraint = "never" if terms[0].constraint == "never" else "always_ask"
    return PolicyHold(constraint, policy.generation, terms)


async def read_policy(connection: Any) -> Policy:
    """The current policy, read ``FOR SHARE`` so a change waits for this call.

    Called inside the call's transaction; expiry is judged on the database
    clock. A missing state row or any unreadable term is PolicyUnavailable.
    """
    try:
        generation = await connection.fetchval(
            "SELECT generation FROM switchboard.owner_policy_state WHERE id = 1 FOR SHARE"
        )
        rows = await connection.fetch(
            "SELECT * FROM switchboard.owner_policy_terms "
            "WHERE expires_at IS NULL OR expires_at > clock_timestamp() ORDER BY id"
        )
    except PolicyUnavailable:
        raise
    except Exception as exc:
        raise PolicyUnavailable("owner policy unreadable") from exc
    if type(generation) is not int:
        raise PolicyUnavailable("owner policy state missing")
    return Policy(generation, tuple(Term.from_row(row) for row in rows))


def _digest(request: Any) -> str:
    encoded = json.dumps(request, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _tightens(old: Mapping[str, Any], constraint: str, expires_at: datetime | None) -> bool:
    if old["term"] == "never" and constraint != "never":
        return False
    if expires_at is not None and (old["expires_at"] is None or expires_at < old["expires_at"]):
        return False
    return True


LOOSEN_KIND = "policy_loosen"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_NAMESPACE = "SELECT current_database() || ':' || current_schema()"


def _expiry_us(expires_at: datetime | None) -> str:
    # Exact integer microseconds; sw_060 derives the same text from the row.
    if expires_at is None:
        return "none"
    return str((expires_at - _EPOCH) // timedelta(microseconds=1))


def loosen_target(term_id: UUID, version: int, constraint: str, expires_at) -> str:
    """The resulting term state a policy_loosen step-up authorizes, exactly."""
    return f"{term_id}:{version}:{constraint}:{_expiry_us(expires_at)}"


def _change_request(
    action: str, term_id: UUID, expected_version: int, constraint: str, expires_at
) -> dict[str, Any]:
    if constraint not in CONSTRAINTS:
        raise ValueError("constraint")
    if type(expected_version) is not int or expected_version < 1:
        raise ValueError("expected_version")
    return {
        "action": action,
        "term_id": str(term_id),
        "expected_version": expected_version,
        "term": constraint,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }


def _snapshot(row: Mapping[str, Any]) -> dict[str, Any]:
    resources = row["resources"]
    if isinstance(resources, str):
        resources = json.loads(resources)
    return {
        "id": str(row["id"]),
        "selector_kind": row["selector_kind"],
        "selector": row["selector"],
        "resources": resources,
        "term": row["term"],
        "version": row["version"],
        "expires_at": _expiry_us(row["expires_at"]),
        "owner_action_id": str(row["owner_action_id"]),
    }


async def loosen_binding(
    connection: Any,
    owner_action_id: UUID,
    term_id: UUID,
    expected_version: int,
    constraint: str,
    expires_at: datetime | None,
    *,
    lock: bool = False,
) -> StepUpBinding:
    """The exact loosening a policy_loosen step-up confirms, from this database.

    The request binds the owner action, the term, the expected version and the
    resulting constraint and expiry; the action binds the term row as it is now
    and the policy generation, so a confirmation issued before any later policy
    change cannot be spent after it. The dashboard derives it with an unlocked
    read to issue the confirmation; ``OwnerPolicyWriter.loosen`` re-derives it
    with the generation and term locked to spend it.
    """
    if expires_at is not None and expires_at.utcoffset() is None:
        raise ValueError("expires_at")
    request = _change_request("loosen", term_id, expected_version, constraint, expires_at)
    namespace = await connection.fetchval(_NAMESPACE)
    if not isinstance(namespace, str) or not namespace:
        raise PolicyUnavailable("owner policy namespace unavailable")
    suffix = " FOR UPDATE" if lock else ""
    generation = await connection.fetchval(
        "SELECT generation FROM switchboard.owner_policy_state WHERE id = 1" + suffix
    )
    if type(generation) is not int:
        raise PolicyUnavailable("owner policy state missing")
    row = await connection.fetchrow(
        "SELECT * FROM switchboard.owner_policy_terms WHERE id = $1" + suffix, term_id
    )
    if row is None or row["version"] != expected_version:
        raise PolicyConflict("owner policy version changed")
    if _tightens(row, constraint, expires_at):
        raise NotLoosening("not a loosening")
    stepped = {
        "kind": LOOSEN_KIND,
        "namespace": namespace,
        "version": 1,
        "owner_action_id": str(owner_action_id),
        **request,
    }
    return StepUpBinding(
        LOOSEN_KIND,
        loosen_target(term_id, expected_version + 1, constraint, expires_at),
        _digest(stepped),
        _digest([stepped, _snapshot(row), generation]),
        namespace,
    )


class OwnerPolicyWriter:
    """Owner-only policy changes; the owner dashboard is the one caller.

    Each method runs in its own transaction on ``connection``, takes the
    generation row ``FOR UPDATE`` (so it serialises with every in-flight call
    holding it ``FOR SHARE``) and returns the action's receipt. A retry of the
    same owner action returns the same receipt; a different change under the
    same action id, or a stale expected version, raises PolicyConflict.
    """

    def __init__(self, connection: Any):
        self.connection = connection

    async def create(
        self,
        owner_action_id: UUID,
        selector_kind: str,
        selector: str,
        constraint: str,
        *,
        resources: Mapping[str, list[str]] | None = None,
        resource_params: Iterable[str] = (),
        alias_free_params: Iterable[str] = (),
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Add a term; ``resources`` keys must be declared and alias-free.

        ``resource_params`` and ``alias_free_params`` are what the current
        catalog declares for the capabilities the selector names (see
        ``declared_resource_params``). A narrowing on an undeclared key is
        invalid; one on an aliasable key (``"primary"``, ``"me"``, name or
        email alternatives) is NarrowingUnsupported, because a deny-list on an
        aliasable identifier is released by its alias. Identifiers are stored
        canonically.
        """
        if selector_kind not in SELECTOR_KINDS or not _selector_valid(selector_kind, selector):
            raise ValueError("selector")
        if constraint not in CONSTRAINTS:
            raise ValueError("constraint")
        narrowed = {name: sorted(ids) for name, ids in _resources(dict(resources or {}))}
        if not set(narrowed) <= set(resource_params):
            raise ValueError("resources")
        if not set(narrowed) <= set(alias_free_params):
            raise NarrowingUnsupported("resources")
        request = {
            "action": "create",
            "selector_kind": selector_kind,
            "selector": selector,
            "term": constraint,
            "resources": narrowed,
            "expires_at": expires_at.isoformat() if expires_at else None,
        }
        async with self.connection.transaction():
            generation = await self._begin(owner_action_id, request)
            if isinstance(generation, dict):
                return generation
            term_id = uuid4()
            await self.connection.execute(
                "INSERT INTO switchboard.owner_policy_terms (id, selector_kind, selector, "
                "resources, term, version, expires_at, owner_action_id) "
                "VALUES ($1, $2, $3, $4::jsonb, $5, 1, $6, $7)",
                term_id,
                selector_kind,
                selector,
                narrowed,
                constraint,
                expires_at,
                owner_action_id,
            )
            return await self._receipt(owner_action_id, request, term_id, None, 1, generation)

    async def tighten(
        self,
        owner_action_id: UUID,
        term_id: UUID,
        expected_version: int,
        *,
        constraint: str,
        expires_at: datetime | None,
    ) -> dict[str, Any]:
        """Replace a term's constraint/expiry; only a tightening is admitted."""
        request = _change_request("tighten", term_id, expected_version, constraint, expires_at)
        async with self.connection.transaction():
            generation = await self._begin(owner_action_id, request)
            if isinstance(generation, dict):
                return generation
            row = await self.connection.fetchrow(
                "SELECT * FROM switchboard.owner_policy_terms WHERE id = $1 FOR UPDATE", term_id
            )
            if row is None or row["version"] != expected_version:
                raise PolicyConflict("owner policy version changed")
            if not _tightens(row, constraint, expires_at):
                raise StepUpRequired("loosening owner policy needs dashboard step-up")
            await self.connection.execute(
                "UPDATE switchboard.owner_policy_terms SET term = $2, expires_at = $3, "
                "version = version + 1, owner_action_id = $4, updated_at = clock_timestamp() "
                "WHERE id = $1",
                term_id,
                constraint,
                expires_at,
                owner_action_id,
            )
            return await self._receipt(
                owner_action_id,
                request,
                term_id,
                expected_version,
                expected_version + 1,
                generation,
            )

    async def loosen(
        self,
        owner_action_id: UUID,
        term_id: UUID,
        expected_version: int,
        *,
        constraint: str,
        expires_at: datetime | None,
    ) -> dict[str, Any]:
        """Loosen a term at its expected version under a fresh dashboard step-up.

        Without a step-up proof, or with one the trusted store does not admit
        for exactly this change at this version and generation, nothing is
        written and StepUpRequired is raised. The step-up is spent with the
        write and its receipt, in this one transaction.
        """
        request = _change_request("loosen", term_id, expected_version, constraint, expires_at)
        if dashboard_stepup_proof.get() is None:
            raise StepUpRequired("loosening owner policy needs dashboard step-up")
        try:
            async with self.connection.transaction():
                generation = await self._begin(owner_action_id, request)
                if isinstance(generation, dict):
                    return generation
                binding = await loosen_binding(
                    self.connection,
                    owner_action_id,
                    term_id,
                    expected_version,
                    constraint,
                    expires_at,
                    lock=True,
                )
                admission = await consume_stepup(self.connection, "begin", binding)
                if admission.get("state") != "admitted":
                    raise StepUpRequired("dashboard step-up not admitted")
                await self.connection.execute(
                    "UPDATE switchboard.owner_policy_terms SET term = $2, expires_at = $3, "
                    "version = version + 1, owner_action_id = $4, "
                    "updated_at = clock_timestamp() WHERE id = $1",
                    term_id,
                    constraint,
                    expires_at,
                    owner_action_id,
                )
                receipt = await self._receipt(
                    owner_action_id,
                    request,
                    term_id,
                    expected_version,
                    expected_version + 1,
                    generation,
                )
                retained = await consume_stepup(
                    self.connection,
                    "finish",
                    binding,
                    {
                        "version": 1,
                        "kind": LOOSEN_KIND,
                        "id": str(owner_action_id),
                        "rule_id": str(term_id),
                    },
                )
                if retained.get("state") != "completed":
                    raise RuntimeError("Step-up receipt unavailable")
                return receipt
        except (
            asyncpg.InvalidSchemaNameError,
            asyncpg.UndefinedTableError,
            asyncpg.UndefinedFunctionError,
            asyncpg.InsufficientPrivilegeError,
        ) as exc:
            # An unavailable trusted interface never falls back to an unconfirmed write.
            raise StepUpRequired("dashboard step-up unavailable") from exc

    async def _begin(self, owner_action_id: UUID, request: Mapping[str, Any]) -> int | dict:
        """Lock the generation; a retried action returns its existing receipt."""
        generation = await self.connection.fetchval(
            "SELECT generation FROM switchboard.owner_policy_state WHERE id = 1 FOR UPDATE"
        )
        if type(generation) is not int:
            raise PolicyUnavailable("owner policy state missing")
        existing = await self.connection.fetchrow(
            "SELECT * FROM switchboard.owner_policy_receipts WHERE owner_action_id = $1",
            owner_action_id,
        )
        if existing is not None:
            if existing["request_digest"] != _digest(request):
                raise PolicyConflict("owner action already recorded a different change")
            return _receipt_view(existing)
        return generation

    async def _receipt(
        self,
        owner_action_id: UUID,
        request: Mapping[str, Any],
        term_id: UUID,
        from_version: int | None,
        to_version: int,
        generation: int,
    ) -> dict[str, Any]:
        await self.connection.execute(
            "UPDATE switchboard.owner_policy_state SET generation = $1, "
            "updated_at = clock_timestamp() WHERE id = 1",
            generation + 1,
        )
        row = await self.connection.fetchrow(
            "INSERT INTO switchboard.owner_policy_receipts (owner_action_id, term_id, action, "
            "request_digest, from_version, to_version, generation) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *",
            owner_action_id,
            term_id,
            request["action"],
            _digest(request),
            from_version,
            to_version,
            generation + 1,
        )
        return _receipt_view(row)


def _receipt_view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "owner_action_id": str(row["owner_action_id"]),
        "term_id": str(row["term_id"]),
        "action": row["action"],
        "from_version": row["from_version"],
        "to_version": row["to_version"],
        "generation": row["generation"],
    }


async def list_terms(connection: Any) -> dict[str, Any]:
    """The owner's read: every term with its version, expiry and whether it is active."""
    policy = await read_policy(connection)
    rows = await connection.fetch(
        "SELECT *, expires_at IS NOT NULL AND expires_at <= clock_timestamp() AS expired "
        "FROM switchboard.owner_policy_terms ORDER BY created_at, id"
    )
    terms = []
    for row in rows:
        term = Term.from_row(row)
        terms.append(
            {
                **term.ref(),
                "resources": {name: sorted(ids) for name, ids in term.resources},
                "expires_at": term.expires_at.isoformat() if term.expires_at else None,
                "active": not row["expired"],
            }
        )
    return {"generation": policy.generation, "terms": terms}


_HOLD_SUBJECTS = {"goal_id": "goal_id", "effect_id": "effect_id", "refusal_id": "refusal_id"}


async def holds_for(connection: Any, subject: str, subject_id: UUID) -> list[dict[str, Any]]:
    """The owner's read of the owner-law holds on a goal, an effect or a refusal.

    An effect id is also its parked pending action's id, so this one read
    serves the effect, the refusal and the pending action alike.
    """
    column = _HOLD_SUBJECTS[subject]
    rows = await connection.fetch(
        f"SELECT * FROM switchboard.owner_policy_holds WHERE {column} = $1 ORDER BY created_at, id",
        subject_id,
    )
    return [hold_view(row) for row in rows]


async def holds_for_goal(connection: Any, goal_id: UUID) -> list[dict[str, Any]]:
    """The owner's read of every owner-law hold on one goal's calls."""
    return await holds_for(connection, "goal_id", goal_id)


def hold_view(row: Mapping[str, Any]) -> dict[str, Any]:
    """A stored hold; ``receipt`` serializes exactly as ``PolicyHold.receipt()``."""
    terms = row["terms"]
    if isinstance(terms, str):
        terms = json.loads(terms)
    return {
        "disposition": row["disposition"],
        "capability": row["capability"],
        "invocation_id": str(row["invocation_id"]),
        "effect_id": str(row["effect_id"]) if row["effect_id"] else None,
        "refusal_id": str(row["refusal_id"]) if row["refusal_id"] else None,
        "receipt": {
            "stage": "owner_law",
            "constraint": row["term"],
            "generation": row["generation"],
            "terms": terms,
        },
        "created_at": row["created_at"].isoformat(),
    }


__all__ = [
    "CONSTRAINTS",
    "SELECTOR_KINDS",
    "DeclaredResources",
    "LOOSEN_KIND",
    "NarrowingUnsupported",
    "NotLoosening",
    "OwnerPolicyWriter",
    "Policy",
    "PolicyConflict",
    "PolicyHold",
    "PolicyUnavailable",
    "StepUpRequired",
    "Term",
    "canonical_resource",
    "declared_resource_params",
    "hold_for",
    "hold_view",
    "holds_for",
    "holds_for_goal",
    "list_terms",
    "loosen_binding",
    "loosen_target",
    "read_policy",
]
