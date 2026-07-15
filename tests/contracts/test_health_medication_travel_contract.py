"""Contract tests for the narrow Health-to-Travel medication MCP payload."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from butlers.health_medication_contract import MedicationTravelSnapshot

pytestmark = pytest.mark.contract


def _success_payload() -> dict:
    return {
        "schema_version": "health.medication-travel.v1",
        "status": "ok",
        "medications": [
            {
                "name": "Metformin",
                "dosage": "500 mg",
                "frequency": "twice daily",
                "schedule": ["08:00", "20:00"],
            }
        ],
        "error": None,
    }


def test_success_contract_accepts_only_the_versioned_minimum_payload() -> None:
    snapshot = MedicationTravelSnapshot.model_validate(_success_payload())

    assert snapshot.schema_version == "health.medication-travel.v1"
    assert snapshot.status == "ok"
    assert snapshot.medications[0].model_dump() == {
        "name": "Metformin",
        "dosage": "500 mg",
        "frequency": "twice daily",
        "schedule": ["08:00", "20:00"],
    }


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema_version",), "health.medication-travel.v2"),
        (("private_health_export",), {"conditions": ["private"]}),
        (("medications", 0, "notes"), "do not expose"),
    ],
)
def test_contract_rejects_unknown_version_and_extra_fields(
    path: tuple[str | int, ...], value: object
) -> None:
    payload = _success_payload()
    target: object = payload
    for part in path[:-1]:
        target = target[part]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]

    with pytest.raises(ValidationError):
        MedicationTravelSnapshot.model_validate(payload)


def test_success_contract_allows_an_empty_medication_list() -> None:
    payload = _success_payload()
    payload["medications"] = []

    snapshot = MedicationTravelSnapshot.model_validate(payload)

    assert snapshot.status == "ok"
    assert snapshot.medications == []
    assert snapshot.error is None


def test_success_contract_rejects_coercible_wrong_field_types() -> None:
    payload = _success_payload()
    payload["medications"][0]["schedule"] = ("08:00", "20:00")

    with pytest.raises(ValidationError):
        MedicationTravelSnapshot.model_validate(payload)


def test_success_contract_rejects_an_error_object() -> None:
    payload = _success_payload()
    payload["error"] = {
        "code": "health_unavailable",
        "message": "Health unavailable.",
        "retryable": True,
    }

    with pytest.raises(ValidationError):
        MedicationTravelSnapshot.model_validate(payload)


def test_error_contract_requires_empty_medications_and_an_error() -> None:
    payload = _success_payload()
    payload.update(
        {
            "status": "error",
            "medications": [],
            "error": {
                "code": "permission_denied",
                "message": "Travel is not permitted to request Health data.",
                "retryable": False,
            },
        }
    )

    snapshot = MedicationTravelSnapshot.model_validate(payload)

    assert snapshot.status == "error"
    assert snapshot.medications == []
    assert snapshot.error is not None
    assert snapshot.error.code == "permission_denied"

    payload["medications"] = _success_payload()["medications"]
    with pytest.raises(ValidationError):
        MedicationTravelSnapshot.model_validate(payload)

    payload["medications"] = []
    payload["error"] = None
    with pytest.raises(ValidationError):
        MedicationTravelSnapshot.model_validate(payload)
