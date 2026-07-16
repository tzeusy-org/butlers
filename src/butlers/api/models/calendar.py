"""Calendar workspace API models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CalendarWorkspaceUserMutationRequest(BaseModel):
    """Request envelope for user-view workspace mutations."""

    model_config = ConfigDict(extra="forbid")

    butler_name: str = Field(min_length=1)
    action: Literal["create", "update", "delete"]
    request_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("butler_name")
    @classmethod
    def _normalize_butler_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("butler_name must be a non-empty string")
        return normalized

    @field_validator("request_id")
    @classmethod
    def _normalize_request_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def _validate_explicit_entity_clear(self) -> CalendarWorkspaceUserMutationRequest:
        """Require an unambiguous empty replacement before clearing people links."""
        if "clear_entity_ids" not in self.payload:
            return self

        clear_entity_ids = self.payload["clear_entity_ids"]
        if not isinstance(clear_entity_ids, bool):
            raise ValueError("payload.clear_entity_ids must be a boolean")
        if self.action != "update":
            raise ValueError("payload.clear_entity_ids is only supported for update actions")
        if not clear_entity_ids:
            return self
        if self.payload.get("entity_ids") != []:
            raise ValueError("payload.clear_entity_ids requires payload.entity_ids to be []")
        return self


class CalendarWorkspaceButlerMutationRequest(BaseModel):
    """Request envelope for butler-view workspace mutations."""

    model_config = ConfigDict(extra="forbid")

    butler_name: str = Field(min_length=1)
    action: Literal["create", "update", "delete", "toggle", "dismiss", "snooze"]
    request_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("butler_name")
    @classmethod
    def _normalize_butler_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("butler_name must be a non-empty string")
        return normalized

    @field_validator("request_id")
    @classmethod
    def _normalize_request_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None
