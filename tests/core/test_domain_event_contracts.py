"""Publisher-owned domain-event contract declarations (bu-6jv4m.8).

Covers the Git-declared contract loader, its fail-closed validation, and the
publish/subscribe admission rules built on it. The live roster declarations
are asserted here too: an event type that is published or subscribed in
production without a declaration is exactly the gap this layer closes, so a
missing declaration must fail this suite rather than silently fail closed at
runtime.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from butlers.core.domain_event_contracts import (
    DECLARATION_FILENAME,
    DOMAIN_EVENT_RETENTION_POLICIES,
    DomainEventContract,
    DomainEventContractError,
    DomainEventContractRegistry,
    load_contract_registry,
    materialize_own_contracts,
    set_contract_registry,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ROSTER_DIR = REPO_ROOT / "roster"


def _acquire(conn):
    @asynccontextmanager
    async def _ctx():
        yield conn

    return lambda: _ctx()


def _declaration(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "type": "travel.trip_booked",
        "schema_version": 1,
        "summary": "A brand-new trip container was created.",
        "retention_policy": "standard",
        "reaction_expectation": "expected",
        "reaction_contract": "Consider a pre-budget check for the newly booked trip.",
        "permitted_subscribers": ["finance"],
        "required_fields": ["trip_id"],
        "optional_fields": ["destination"],
    }
    base.update(overrides)
    return base


_TOML_EVENT = """\
[[event]]
type = 'travel.trip_booked'
schema_version = 1
summary = 'A brand-new trip container was created.'
retention_policy = 'standard'
reaction_expectation = 'optional'
reaction_contract = 'Consider a pre-budget check.'
permitted_subscribers = ['finance']
required_fields = []
optional_fields = []
"""


class TestDeclarationParsing:
    def test_a_well_formed_declaration_parses(self) -> None:
        contract = DomainEventContract.from_declaration(_declaration(), publisher="travel")
        assert contract.event_type == "travel.trip_booked"
        assert contract.publisher == "travel"
        assert contract.schema_version == 1
        assert contract.permitted_subscribers == ("finance",)
        assert contract.declared_fields == ("destination", "trip_id")

    def test_namespace_must_match_the_declaring_butler(self) -> None:
        with pytest.raises(DomainEventContractError, match="namespace"):
            DomainEventContract.from_declaration(_declaration(), publisher="finance")

    def test_unknown_retention_policy_is_refused(self) -> None:
        with pytest.raises(DomainEventContractError, match="retention_policy"):
            DomainEventContract.from_declaration(
                _declaration(retention_policy="forever-and-ever"), publisher="travel"
            )

    def test_unknown_reaction_expectation_is_refused(self) -> None:
        with pytest.raises(DomainEventContractError, match="reaction_expectation"):
            DomainEventContract.from_declaration(
                _declaration(reaction_expectation="whatever"), publisher="travel"
            )

    def test_missing_required_key_is_refused(self) -> None:
        broken = _declaration()
        del broken["summary"]
        with pytest.raises(DomainEventContractError, match="summary"):
            DomainEventContract.from_declaration(broken, publisher="travel")

    def test_unknown_declaration_key_is_refused(self) -> None:
        with pytest.raises(DomainEventContractError, match="unsupported_key"):
            DomainEventContract.from_declaration(
                _declaration(unsupported_key="x"), publisher="travel"
            )

    def test_a_field_declared_both_required_and_optional_is_refused(self) -> None:
        with pytest.raises(DomainEventContractError, match="both required and optional"):
            DomainEventContract.from_declaration(
                _declaration(optional_fields=["trip_id"]), publisher="travel"
            )

    def test_an_omitted_permitted_subscriber_list_is_refused(self) -> None:
        broken = _declaration()
        del broken["permitted_subscribers"]
        with pytest.raises(DomainEventContractError, match="permitted_subscribers"):
            DomainEventContract.from_declaration(broken, publisher="travel")

    def test_an_explicitly_empty_subscriber_policy_admits_nobody(self) -> None:
        """Empty is a policy the publisher stated, not a policy the bus invents."""
        registry = DomainEventContractRegistry.from_declarations(
            [("travel", _declaration(permitted_subscribers=[]))]
        )
        assert (
            registry.check_publish(
                event_type="travel.trip_booked", publisher="travel", payload={"trip_id": "t-1"}
            )
            is None
        )
        error = registry.check_subscription(event_type="travel.trip_booked", subscriber="finance")
        assert error is not None
        assert "(none)" in error

    def test_a_reaction_expectation_of_expected_requires_a_reaction_contract(self) -> None:
        with pytest.raises(DomainEventContractError, match="reaction_contract"):
            DomainEventContract.from_declaration(
                _declaration(reaction_contract="  "), publisher="travel"
            )


class TestRegistryAdmission:
    @pytest.fixture
    def registry(self) -> DomainEventContractRegistry:
        return DomainEventContractRegistry.from_declarations(
            [(("travel"), _declaration())],
        )

    def test_publish_admission_accepts_a_minimized_payload(
        self, registry: DomainEventContractRegistry
    ) -> None:
        assert (
            registry.check_publish(
                event_type="travel.trip_booked",
                publisher="travel",
                payload={"trip_id": "t-1", "destination": "Kyoto"},
            )
            is None
        )

    def test_publish_admission_refuses_an_undeclared_event_type(
        self, registry: DomainEventContractRegistry
    ) -> None:
        error = registry.check_publish(
            event_type="travel.trip_cancelled", publisher="travel", payload={}
        )
        assert error is not None
        assert "no publisher-owned contract" in error

    def test_publish_admission_refuses_a_foreign_publisher(
        self, registry: DomainEventContractRegistry
    ) -> None:
        error = registry.check_publish(
            event_type="travel.trip_booked", publisher="finance", payload={"trip_id": "t-1"}
        )
        assert error is not None
        assert "owned by butler 'travel'" in error

    def test_publish_admission_refuses_an_undeclared_payload_field(
        self, registry: DomainEventContractRegistry
    ) -> None:
        error = registry.check_publish(
            event_type="travel.trip_booked",
            publisher="travel",
            payload={"trip_id": "t-1", "passport_number": "synthetic-value"},
        )
        assert error is not None
        assert "passport_number" in error
        assert "not declared" in error

    def test_publish_admission_refuses_a_missing_required_field(
        self, registry: DomainEventContractRegistry
    ) -> None:
        error = registry.check_publish(
            event_type="travel.trip_booked", publisher="travel", payload={"destination": "Kyoto"}
        )
        assert error is not None
        assert "trip_id" in error

    def test_subscribe_admission_accepts_a_permitted_subscriber(
        self, registry: DomainEventContractRegistry
    ) -> None:
        assert (
            registry.check_subscription(event_type="travel.trip_booked", subscriber="finance")
            is None
        )

    def test_subscribe_admission_refuses_an_unpermitted_subscriber(
        self, registry: DomainEventContractRegistry
    ) -> None:
        error = registry.check_subscription(event_type="travel.trip_booked", subscriber="lifestyle")
        assert error is not None
        assert "lifestyle" in error

    def test_subscribe_admission_refuses_an_undeclared_event_type(
        self, registry: DomainEventContractRegistry
    ) -> None:
        error = registry.check_subscription(
            event_type="travel.trip_cancelled", subscriber="finance"
        )
        assert error is not None
        assert "no publisher-owned contract" in error


class TestRosterDeclarations:
    """The live roster must declare every event type the fleet actually uses."""

    @pytest.fixture
    def registry(self) -> DomainEventContractRegistry:
        return load_contract_registry(ROSTER_DIR)

    @pytest.mark.parametrize(
        ("event_type", "publisher", "subscriber"),
        [
            ("travel.trip_booked", "travel", "finance"),
            ("travel.trip_active", "travel", "health"),
            ("finance.budget_pressure", "finance", None),
            ("health.recovery_state", "health", None),
        ],
    )
    def test_live_event_types_are_declared(
        self,
        registry: DomainEventContractRegistry,
        event_type: str,
        publisher: str,
        subscriber: str | None,
    ) -> None:
        contract = registry.get(event_type)
        assert contract is not None, f"{event_type} has no publisher-owned contract declaration"
        assert contract.publisher == publisher
        assert contract.retention_policy in DOMAIN_EVENT_RETENTION_POLICIES
        if subscriber is not None:
            assert registry.check_subscription(event_type=event_type, subscriber=subscriber) is None

    def test_every_declaration_is_owned_by_the_directory_it_lives_in(
        self, registry: DomainEventContractRegistry
    ) -> None:
        for contract in registry.contracts():
            assert contract.event_type.split(".", 1)[0] == contract.publisher

    def test_loading_a_malformed_declaration_fails_closed(self, tmp_path: Path) -> None:
        butler_dir = tmp_path / "travel"
        butler_dir.mkdir()
        (butler_dir / "butler.toml").write_text("[butler]\nname = 'travel'\n")
        (butler_dir / DECLARATION_FILENAME).write_text(
            "[[event]]\ntype = 'travel.trip_booked'\nschema_version = 1\n"
        )
        with pytest.raises(DomainEventContractError):
            load_contract_registry(tmp_path)

    def test_a_duplicate_declaration_fails_closed(self, tmp_path: Path) -> None:
        """One event type, one publisher — a second declaration is never a merge."""
        butler_dir = tmp_path / "travel"
        butler_dir.mkdir()
        (butler_dir / "butler.toml").write_text("[butler]\nname = 'travel'\n")
        (butler_dir / DECLARATION_FILENAME).write_text(_TOML_EVENT + _TOML_EVENT)
        with pytest.raises(DomainEventContractError, match="declared twice"):
            load_contract_registry(tmp_path)

    def test_a_butler_cannot_declare_another_butlers_namespace(self, tmp_path: Path) -> None:
        """Ownership is the directory, so one butler cannot claim another's events."""
        butler_dir = tmp_path / "finance"
        butler_dir.mkdir()
        (butler_dir / "butler.toml").write_text("[butler]\nname = 'finance'\n")
        (butler_dir / DECLARATION_FILENAME).write_text(_TOML_EVENT)
        with pytest.raises(DomainEventContractError, match="namespace"):
            load_contract_registry(tmp_path)


class TestMaterialization:
    """The DB copy is a publisher-owned projection of git, never a second source."""

    @pytest.fixture(autouse=True)
    def pinned_registry(self):
        set_contract_registry(
            DomainEventContractRegistry.from_declarations(
                [
                    ("travel", _declaration()),
                    ("finance", _declaration(type="finance.budget_pressure")),
                ]
            )
        )
        yield
        set_contract_registry(None)

    async def test_a_publisher_materializes_only_its_own_declarations(self) -> None:
        conn = AsyncMock()
        pool = AsyncMock()
        pool.acquire = _acquire(conn)

        written = await materialize_own_contracts(pool, publisher="travel")

        assert written == ["travel.trip_booked"]
        upsert_sql, *args = conn.execute.await_args_list[-1].args
        assert "INSERT INTO public.domain_event_contracts" in upsert_sql
        assert args[0] == "travel.trip_booked"
        assert args[1] == "travel"

    async def test_a_retired_declaration_is_removed_from_the_projection(self) -> None:
        conn = AsyncMock()
        pool = AsyncMock()
        pool.acquire = _acquire(conn)

        await materialize_own_contracts(pool, publisher="travel")

        delete_sql, *args = conn.execute.await_args_list[0].args
        assert "DELETE FROM public.domain_event_contracts" in delete_sql
        assert "publisher = $1" in delete_sql
        assert args[0] == "travel"
        assert args[1] == ["travel.trip_booked"]

    async def test_a_publisher_with_no_declarations_writes_nothing(self) -> None:
        """Positive control: silence must not be projected as an empty-but-present set."""
        conn = AsyncMock()
        pool = AsyncMock()
        pool.acquire = _acquire(conn)

        written = await materialize_own_contracts(pool, publisher="lifestyle")

        assert written == []
        conn.execute.assert_not_awaited()


class TestProductionPayloadCompatibility:
    """The four event types already in flight must migrate without a break.

    Each payload below mirrors the exact keys the production publisher
    builds today. If a publisher grows a field and its declaration does not,
    admission would start refusing a publish that works right now -- these
    tests are the tripwire for that, and they must be updated in the same
    change as the publisher, not after.
    """

    @pytest.fixture
    def registry(self) -> DomainEventContractRegistry:
        return load_contract_registry(ROSTER_DIR)

    @pytest.mark.parametrize(
        ("event_type", "publisher", "payload"),
        [
            # roster/travel/tools/bookings.py::_create_trip_from_payload
            (
                "travel.trip_booked",
                "travel",
                {
                    "trip_id": "trip-synthetic",
                    "name": "Trip to Kyoto",
                    "destination": "Kyoto",
                    "start_date": "2026-09-01",
                    "end_date": "2026-09-08",
                },
            ),
            # roster/travel/modules/tools.py::record_booking fallback payload
            ("travel.trip_booked", "travel", {"trip_id": "trip-synthetic"}),
            # src/butlers/jobs/context_producers.py::_publish_trip_active_event
            (
                "travel.trip_active",
                "travel",
                {
                    "trip_id": "trip-synthetic",
                    "name": "Trip to Kyoto",
                    "destination": "Kyoto",
                    "start_date": "2026-09-01",
                    "end_date": "2026-09-08",
                    "status": "active",
                },
            ),
            # roster/finance/jobs/finance_jobs.py::_publish_budget_pressure_event
            (
                "finance.budget_pressure",
                "finance",
                {
                    "category": "dining",
                    "period": "2026-08",
                    "status": "over",
                    "spent": "420.00",
                    "budget_amount": "300.00",
                    "currency": "SGD",
                    "utilization_pct": 140.0,
                    "period_start": "2026-08-01",
                    "period_end": "2026-08-31",
                    "valid_until": "2026-08-31T23:59:59+00:00",
                },
            ),
            # roster/health/jobs/health_jobs.py::_publish_recovery_state_event
            (
                "health.recovery_state",
                "health",
                {
                    "state": "depleted",
                    "max_severity": 8,
                    "symptom_count": 3,
                    "window_days": 7,
                    "valid_until": "2026-08-29T00:00:00+00:00",
                },
            ),
        ],
    )
    def test_a_live_publisher_payload_is_admitted_unchanged(
        self,
        registry: DomainEventContractRegistry,
        event_type: str,
        publisher: str,
        payload: dict[str, object],
    ) -> None:
        assert (
            registry.check_publish(event_type=event_type, publisher=publisher, payload=payload)
            is None
        )

    def test_the_compatibility_check_can_actually_fail(self) -> None:
        """Positive control: an added field must be refused, or these prove nothing."""
        registry = load_contract_registry(ROSTER_DIR)
        error = registry.check_publish(
            event_type="travel.trip_booked",
            publisher="travel",
            payload={"trip_id": "trip-synthetic", "seat_number": "12A"},
        )
        assert error is not None
        assert "seat_number" in error

    def test_the_seeded_finance_subscription_is_still_permitted(self) -> None:
        """core_186 seeds finance -> travel.trip_booked; the contract must admit it."""
        registry = load_contract_registry(ROSTER_DIR)
        assert (
            registry.check_subscription(event_type="travel.trip_booked", subscriber="finance")
            is None
        )
