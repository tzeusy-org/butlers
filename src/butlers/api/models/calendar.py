"""Calendar workspace API models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class CalendarWorkspaceButlerMutationRequest(BaseModel):
    """Request envelope for butler-view workspace mutations."""

    model_config = ConfigDict(extra="forbid")

    butler_name: str = Field(min_length=1)
    action: Literal["create", "update", "delete", "toggle"]
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
