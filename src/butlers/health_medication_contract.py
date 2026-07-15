"""Typed privacy boundary for Health medication data consumed by Travel."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

MEDICATION_TRAVEL_SCHEMA_VERSION = "health.medication-travel.v1"

MedicationTravelErrorCode = Literal[
    "permission_denied",
    "switchboard_unavailable",
    "health_unavailable",
    "invalid_health_response",
]


class MedicationTravelEntry(BaseModel):
    """Minimum medication fields needed to prepare for travel."""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str
    dosage: str
    frequency: str
    schedule: list[str]


class MedicationTravelError(BaseModel):
    """Structured failure returned by the Travel-side consumer."""

    model_config = ConfigDict(extra="forbid", strict=True)

    code: MedicationTravelErrorCode
    message: str
    retryable: bool


class MedicationTravelSnapshot(BaseModel):
    """Versioned Health-to-Travel medication snapshot response."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["health.medication-travel.v1"] = MEDICATION_TRAVEL_SCHEMA_VERSION
    status: Literal["ok", "error"]
    medications: list[MedicationTravelEntry]
    error: MedicationTravelError | None = None

    @model_validator(mode="after")
    def _validate_state(self) -> Self:
        if self.status == "ok" and self.error is not None:
            raise ValueError("successful medication snapshots cannot contain an error")
        if self.status == "error":
            if self.error is None:
                raise ValueError("failed medication snapshots require an error")
            if self.medications:
                raise ValueError("failed medication snapshots cannot contain medications")
        return self

    @classmethod
    def success(cls, medications: list[MedicationTravelEntry]) -> Self:
        """Build a successful snapshot, including a valid empty result."""
        return cls(status="ok", medications=medications)

    @classmethod
    def failure(
        cls,
        *,
        code: MedicationTravelErrorCode,
        message: str,
        retryable: bool,
    ) -> Self:
        """Build a failed snapshot with no medication payload."""
        return cls(
            status="error",
            medications=[],
            error=MedicationTravelError(code=code, message=message, retryable=retryable),
        )
