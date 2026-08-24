"""Publisher-owned, versioned contracts for the domain-event bus.

bu-6jv4m.8 (2026-08-09 JARVIS pursuit dossier, ranked move #8). Companion to
``butlers.core.domain_events`` (the append log, subscriptions, and delivery
ledger) and ``butlers.core.domain_event_reactions`` (the reaction receipt
ledger).

Why a contract layer at all
---------------------------
``event_type`` was validated only against the ``"<namespace>.<event>"``
*shape* (``domain_events.is_valid_event_type``) and ``payload`` was an
arbitrary JSONB blob. That open vocabulary was the whole point of the bus --
any butler can mint an event type without a schema migration -- but "open"
was implemented as "unowned": nothing recorded who owns an event type, what
its payload may carry, how long that payload lives, who is allowed to
subscribe, or what a subscriber is expected to do about it. A subscription
could be stood up against an event type nobody publishes, a publisher could
widen its payload with owner-identifying content in one commit, and neither
change left a reviewable artifact.

This module keeps the vocabulary open at the *type* level while making each
active type owned and reviewable at the *declaration* level: a publisher
declares its own event types in Git, under its own roster directory, and the
bus admits publishes and subscriptions only for a declared contract.

Declaration format
------------------
``roster/<butler>/domain_events.toml``, one ``[[event]]`` table per event
type this butler publishes::

    [[event]]
    type = "travel.trip_booked"
    schema_version = 1
    summary = "A brand-new trip container was created by record_booking."
    retention_policy = "standard"
    reaction_expectation = "expected"
    reaction_contract = "Consider a pre-budget check for the newly booked trip."
    permitted_subscribers = ["finance"]
    required_fields = ["trip_id"]
    optional_fields = ["destination", "start_date", "end_date"]

The declaring directory is the owner: ``type``'s namespace must equal the
butler whose roster directory the file lives in, so an event type has exactly
one publisher and that publisher owns the file a reviewer reads.

Fail-closed, in both directions
-------------------------------
Every validation here refuses rather than degrades:

- a malformed or duplicated declaration raises :class:`DomainEventContract
  Error` at load time -- the registry never comes up half-populated;
- a publish of an undeclared event type, from a butler that does not own it,
  carrying a field the contract does not declare, or missing a declared
  required field, is refused before any ``public.domain_events`` row is
  written;
- a subscription request from a butler outside ``permitted_subscribers`` is
  refused before any ``public.butler_subscriptions`` row is written.

``required_fields``/``optional_fields`` together are the *minimization*
contract: the declared set is exhaustive, so widening a payload is a Git
change a reviewer sees, not a silent runtime drift.

Infrastructure-only, deliberately
---------------------------------
``reaction_expectation``/``reaction_contract`` describe what the publisher
believes a subscriber will want to do. They are documentation carried to the
subscriber and to the dashboard -- the bus never enforces them, never decides
that a subscriber *should* have acted, and never treats "did not act" as an
error. Whether acting is correct is owned by the subscribing butler's own
manifesto; see ``butlers.core.domain_event_reactions`` for how an outcome is
recorded without the bus judging it.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DECLARATION_FILENAME = "domain_events.toml"

# Retention is declared as a reference to one of these named policies rather
# than a free-form duration string, so a reviewer reads the same vocabulary
# on every declaration and an unrecognized reference fails the load instead of
# quietly meaning nothing.
DOMAIN_EVENT_RETENTION_POLICIES: dict[str, str] = {
    "standard": (
        "Payload is retained for as long as its append-only public.domain_events row. "
        "Use only for payloads that carry no owner-identifying content beyond domain "
        "identifiers the subscriber already holds."
    ),
    "minimized-derived": (
        "Derived advisory: the payload carries state, counts, and a validity window only "
        "-- never the underlying records, names, or free text the advisory was computed "
        "from. Retained with its event row, like 'standard', but declared separately so a "
        "reviewer can see the minimization was intentional."
    ),
}

REACTION_EXPECTATIONS: dict[str, str] = {
    "expected": (
        "The publisher believes a subscriber normally has a domain action for this event. "
        "Advisory only -- the bus never requires one."
    ),
    "optional": (
        "The publisher expects most deliveries to be informational. A subscriber that "
        "ignores this event is behaving correctly."
    ),
}

_REQUIRED_KEYS = frozenset(
    {
        "type",
        "schema_version",
        "summary",
        "retention_policy",
        "reaction_expectation",
        "reaction_contract",
        "permitted_subscribers",
        "required_fields",
        "optional_fields",
    }
)


class DomainEventContractError(Exception):
    """A publisher-owned declaration is missing, malformed, or contradictory.

    Raised at load/parse time only. Runtime admission decisions return an
    error *string* instead (see :meth:`DomainEventContractRegistry.check_
    publish`), because a refused publish is a tool result the calling session
    must read, not an exception that would unwind the caller's own domain work.
    """


def _require_str(declaration: Mapping[str, Any], key: str, *, where: str) -> str:
    value = declaration.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DomainEventContractError(f"{where}: {key!r} must be a non-empty string.")
    return value.strip()


def _require_str_list(
    declaration: Mapping[str, Any], key: str, *, where: str, allow_empty: bool = True
) -> tuple[str, ...]:
    value = declaration.get(key)
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise DomainEventContractError(f"{where}: {key!r} must be a list of non-empty strings.")
    if not value and not allow_empty:
        raise DomainEventContractError(f"{where}: {key!r} must not be empty.")
    return tuple(sorted({item.strip() for item in value}))


@dataclass(frozen=True)
class DomainEventContract:
    """One publisher-owned, versioned declaration for a single event type."""

    event_type: str
    publisher: str
    schema_version: int
    summary: str
    retention_policy: str
    reaction_expectation: str
    reaction_contract: str
    permitted_subscribers: tuple[str, ...]
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...]

    @property
    def declared_fields(self) -> tuple[str, ...]:
        """Every payload key this contract permits, required and optional."""
        return tuple(sorted(set(self.required_fields) | set(self.optional_fields)))

    @classmethod
    def from_declaration(
        cls, declaration: Mapping[str, Any], *, publisher: str
    ) -> DomainEventContract:
        """Parse one ``[[event]]`` table, failing closed on anything unexpected."""
        where = f"{publisher}/{DECLARATION_FILENAME}"

        unknown = set(declaration) - _REQUIRED_KEYS
        if unknown:
            raise DomainEventContractError(
                f"{where}: unknown declaration key(s) {', '.join(sorted(unknown))}. "
                f"Supported keys: {', '.join(sorted(_REQUIRED_KEYS))}."
            )
        missing = _REQUIRED_KEYS - set(declaration)
        if missing:
            raise DomainEventContractError(
                f"{where}: missing required declaration key(s) {', '.join(sorted(missing))}."
            )

        event_type = _require_str(declaration, "type", where=where)
        namespace = event_type.split(".", 1)[0]
        if namespace != publisher:
            raise DomainEventContractError(
                f"{where}: event type {event_type!r} has namespace {namespace!r} but is "
                f"declared by butler {publisher!r}. An event type is owned by the butler "
                "whose roster directory declares it; the namespace must match."
            )

        schema_version = declaration.get("schema_version")
        if not isinstance(schema_version, int) or isinstance(schema_version, bool):
            raise DomainEventContractError(
                f"{where}: {event_type!r} 'schema_version' must be an integer."
            )
        if schema_version < 1:
            raise DomainEventContractError(
                f"{where}: {event_type!r} 'schema_version' must be >= 1."
            )

        retention_policy = _require_str(declaration, "retention_policy", where=where)
        if retention_policy not in DOMAIN_EVENT_RETENTION_POLICIES:
            known = ", ".join(sorted(DOMAIN_EVENT_RETENTION_POLICIES))
            raise DomainEventContractError(
                f"{where}: {event_type!r} names unknown retention_policy "
                f"{retention_policy!r}. Known policies: {known}."
            )

        reaction_expectation = _require_str(declaration, "reaction_expectation", where=where)
        if reaction_expectation not in REACTION_EXPECTATIONS:
            known = ", ".join(sorted(REACTION_EXPECTATIONS))
            raise DomainEventContractError(
                f"{where}: {event_type!r} names unknown reaction_expectation "
                f"{reaction_expectation!r}. Known expectations: {known}."
            )
        reaction_contract = _require_str(declaration, "reaction_contract", where=where)

        # An empty list is a deliberate, explicit policy -- "recorded for the
        # log, nobody is authorized to react yet" -- and is how a publisher
        # declares a derived advisory before an owner has decided which butler
        # should act on it. The bus must never invent that decision, so an
        # empty list refuses every subscription rather than admitting any.
        permitted_subscribers = _require_str_list(
            declaration, "permitted_subscribers", where=where, allow_empty=True
        )

        required_fields = _require_str_list(declaration, "required_fields", where=where)
        optional_fields = _require_str_list(declaration, "optional_fields", where=where)
        overlap = set(required_fields) & set(optional_fields)
        if overlap:
            raise DomainEventContractError(
                f"{where}: {event_type!r} declares {', '.join(sorted(overlap))} as both "
                "required and optional."
            )

        return cls(
            event_type=event_type,
            publisher=publisher,
            schema_version=schema_version,
            summary=_require_str(declaration, "summary", where=where),
            retention_policy=retention_policy,
            reaction_expectation=reaction_expectation,
            reaction_contract=reaction_contract,
            permitted_subscribers=permitted_subscribers,
            required_fields=required_fields,
            optional_fields=optional_fields,
        )

    def check_payload(self, payload: Mapping[str, Any] | None) -> str | None:
        """Return a refusal message if *payload* violates the minimization contract."""
        keys = set(payload or {})
        undeclared = keys - set(self.declared_fields)
        if undeclared:
            declared = ", ".join(self.declared_fields) or "(none)"
            return (
                f"payload field(s) {', '.join(sorted(undeclared))} are not declared by the "
                f"contract for {self.event_type!r} (schema_version {self.schema_version}). "
                f"Declared fields: {declared}. Widen the declaration in "
                f"roster/{self.publisher}/{DECLARATION_FILENAME} before publishing them."
            )
        absent_required = set(self.required_fields) - keys
        if absent_required:
            return (
                f"payload is missing required field(s) "
                f"{', '.join(sorted(absent_required))} for {self.event_type!r} "
                f"(schema_version {self.schema_version})."
            )
        return None


class DomainEventContractRegistry:
    """Every publisher-owned contract the fleet declares, indexed by event type."""

    def __init__(self, contracts: Iterable[DomainEventContract]) -> None:
        by_type: dict[str, DomainEventContract] = {}
        for contract in contracts:
            existing = by_type.get(contract.event_type)
            if existing is not None:
                raise DomainEventContractError(
                    f"Event type {contract.event_type!r} is declared twice: by "
                    f"{existing.publisher!r} and {contract.publisher!r}. An event type has "
                    "exactly one publisher."
                )
            by_type[contract.event_type] = contract
        self._by_type = by_type

    @classmethod
    def from_declarations(
        cls, declarations: Sequence[tuple[str, Mapping[str, Any]]]
    ) -> DomainEventContractRegistry:
        """Build a registry from ``(publisher, declaration)`` pairs.

        The loader's own constructor path, exposed because a caller that
        already holds parsed declarations (a test, or a future non-Git
        source) should build the registry through the same validation the
        roster files go through, never by bypassing it.
        """
        return cls(
            DomainEventContract.from_declaration(declaration, publisher=publisher)
            for publisher, declaration in declarations
        )

    def get(self, event_type: str) -> DomainEventContract | None:
        return self._by_type.get(event_type)

    def contracts(self) -> Iterator[DomainEventContract]:
        yield from (self._by_type[key] for key in sorted(self._by_type))

    def __len__(self) -> int:
        return len(self._by_type)

    def _missing(self, event_type: str) -> str:
        return (
            f"event_type {event_type!r} has no publisher-owned contract. Declare it in "
            f"roster/<publisher>/{DECLARATION_FILENAME} before publishing or subscribing "
            "to it."
        )

    def check_publish(
        self,
        *,
        event_type: str,
        publisher: str,
        payload: Mapping[str, Any] | None,
    ) -> str | None:
        """Return a refusal message, or ``None`` when the publish is admissible."""
        contract = self._by_type.get(event_type)
        if contract is None:
            return self._missing(event_type)
        if contract.publisher != publisher:
            return (
                f"event_type {event_type!r} is owned by butler {contract.publisher!r}; "
                f"butler {publisher!r} may not publish it."
            )
        return contract.check_payload(payload)

    def check_subscription(self, *, event_type: str, subscriber: str) -> str | None:
        """Return a refusal message, or ``None`` when the subscription is admissible."""
        contract = self._by_type.get(event_type)
        if contract is None:
            return self._missing(event_type)
        if subscriber not in contract.permitted_subscribers:
            permitted = ", ".join(contract.permitted_subscribers) or "(none)"
            return (
                f"butler {subscriber!r} is not a permitted subscriber of {event_type!r}. "
                f"Permitted subscribers: {permitted}. The publishing butler owns that list "
                f"in roster/{contract.publisher}/{DECLARATION_FILENAME}."
            )
        return None


def _default_roster_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "roster"


def load_contract_registry(roster_dir: Path | None = None) -> DomainEventContractRegistry:
    """Load every ``roster/<butler>/domain_events.toml`` into one registry.

    Fails closed: a malformed declaration, a namespace that does not match its
    declaring directory, or the same event type declared by two butlers raises
    :class:`DomainEventContractError` rather than yielding a partial registry
    that would silently refuse legitimate publishes at runtime.

    A butler with no declaration file simply publishes nothing -- that is not
    an error, it is the normal state for a butler that only subscribes.
    """
    root = roster_dir if roster_dir is not None else _default_roster_dir()
    if not root.is_dir():
        return DomainEventContractRegistry([])

    parsed: list[tuple[str, Mapping[str, Any]]] = []
    for entry in sorted(root.iterdir()):
        declaration_path = entry / DECLARATION_FILENAME
        if not entry.is_dir() or not declaration_path.is_file():
            continue
        try:
            document = tomllib.loads(declaration_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise DomainEventContractError(f"{declaration_path}: unreadable declaration: {exc}")
        events = document.get("event", [])
        if not isinstance(events, list):
            raise DomainEventContractError(
                f"{declaration_path}: 'event' must be an array of tables ([[event]])."
            )
        for declaration in events:
            if not isinstance(declaration, dict):
                raise DomainEventContractError(
                    f"{declaration_path}: every [[event]] entry must be a table."
                )
            parsed.append((entry.name, declaration))

    return DomainEventContractRegistry.from_declarations(parsed)


_registry: DomainEventContractRegistry | None = None


def get_contract_registry() -> DomainEventContractRegistry:
    """Return the process-wide registry, loading it from the roster on first use.

    Cached because the declarations are Git-owned and therefore fixed for the
    lifetime of a daemon process; a redeploy is what picks up a change, the
    same way ``butler.toml`` is picked up.
    """
    global _registry
    if _registry is None:
        _registry = load_contract_registry()
    return _registry


def set_contract_registry(registry: DomainEventContractRegistry | None) -> None:
    """Install (or clear, with ``None``) the process-wide registry.

    The seam a test uses to declare synthetic event types without writing to
    ``roster/``. Production never calls this: the daemon loads the real roster
    through :func:`get_contract_registry`.
    """
    global _registry
    _registry = registry


async def materialize_own_contracts(pool: Any, *, publisher: str) -> list[str]:
    """Project *publisher*'s git-declared contracts into ``public.domain_event_contracts``.

    Called at daemon startup so the dashboard -- and any butler deciding
    whether to subscribe -- can read what a namespace promises without
    reading another butler's roster directory. Each butler writes only its
    own rows, and a declaration it has retired is deleted, so the projection
    tracks git rather than accumulating.

    This table is a *read* surface. Admission control deliberately never
    consults it (see ``butlers.core_tools._domain_events``): a stale or
    half-written projection must never be able to widen what may be
    published. A publisher with nothing declared writes nothing at all --
    an empty projection and an unmaterialized one are the same claim, and
    neither should look like a deliberate "no events".
    """
    contracts = [
        contract
        for contract in get_contract_registry().contracts()
        if contract.publisher == publisher
    ]
    if not contracts:
        return []

    event_types = sorted(contract.event_type for contract in contracts)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM public.domain_event_contracts
            WHERE publisher = $1 AND event_type <> ALL($2::text[])
            """,
            publisher,
            event_types,
        )
        for contract in contracts:
            await conn.execute(
                """
                INSERT INTO public.domain_event_contracts (
                    event_type, publisher, schema_version, summary, retention_policy,
                    reaction_expectation, reaction_contract, permitted_subscribers,
                    required_fields, optional_fields, materialized_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10::jsonb, now())
                ON CONFLICT (event_type) DO UPDATE SET
                    publisher = EXCLUDED.publisher,
                    schema_version = EXCLUDED.schema_version,
                    summary = EXCLUDED.summary,
                    retention_policy = EXCLUDED.retention_policy,
                    reaction_expectation = EXCLUDED.reaction_expectation,
                    reaction_contract = EXCLUDED.reaction_contract,
                    permitted_subscribers = EXCLUDED.permitted_subscribers,
                    required_fields = EXCLUDED.required_fields,
                    optional_fields = EXCLUDED.optional_fields,
                    materialized_at = now()
                """,
                contract.event_type,
                contract.publisher,
                contract.schema_version,
                contract.summary,
                contract.retention_policy,
                contract.reaction_expectation,
                contract.reaction_contract,
                json.dumps(list(contract.permitted_subscribers)),
                json.dumps(list(contract.required_fields)),
                json.dumps(list(contract.optional_fields)),
            )
    return event_types
