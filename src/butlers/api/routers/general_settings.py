"""Shared general settings endpoints for the dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse
from butlers.core.general_settings import (
    GENERAL_MEASUREMENT_SYSTEM,
    load_general_settings,
    normalize_general_currency,
    normalize_general_date_format,
    normalize_general_language,
    normalize_general_time_format,
    normalize_general_timezone,
    normalize_general_week_starts_on,
    save_general_settings,
)

router = APIRouter(prefix="/api/settings/general", tags=["general-settings"])


def _get_db_manager() -> DatabaseManager:
    """Dependency stub -- overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


class GeneralSettingsResponse(BaseModel):
    """Shared general settings returned to the settings page."""

    timezone: str
    timezone_label: str
    language: str
    date_format: str
    time_format: str
    week_starts_on: str
    currency: str
    measurement_system: str = GENERAL_MEASUREMENT_SYSTEM


class GeneralSettingsUpdate(BaseModel):
    """Request body for updating shared general settings."""

    timezone: str
    language: str
    date_format: str
    time_format: str
    week_starts_on: str
    currency: str

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        return normalize_general_timezone(value)

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        return normalize_general_language(value)

    @field_validator("date_format")
    @classmethod
    def validate_date_format(cls, value: str) -> str:
        return normalize_general_date_format(value)

    @field_validator("time_format")
    @classmethod
    def validate_time_format(cls, value: str) -> str:
        return normalize_general_time_format(value)

    @field_validator("week_starts_on")
    @classmethod
    def validate_week_starts_on(cls, value: str) -> str:
        return normalize_general_week_starts_on(value)

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        return normalize_general_currency(value)


def _shared_pool(db: DatabaseManager):
    try:
        return db.credential_shared_pool()
    except KeyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Shared credential database is not available",
        ) from exc


@router.get("", response_model=ApiResponse[GeneralSettingsResponse])
async def get_general_settings(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[GeneralSettingsResponse]:
    settings = await load_general_settings(_shared_pool(db))
    return ApiResponse[GeneralSettingsResponse](data=GeneralSettingsResponse(**settings))


@router.put("", response_model=ApiResponse[GeneralSettingsResponse])
async def update_general_settings(
    body: GeneralSettingsUpdate,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[GeneralSettingsResponse]:
    settings = await save_general_settings(
        _shared_pool(db),
        timezone=body.timezone,
        language=body.language,
        date_format=body.date_format,
        time_format=body.time_format,
        week_starts_on=body.week_starts_on,
        currency=body.currency,
    )
    return ApiResponse[GeneralSettingsResponse](data=GeneralSettingsResponse(**settings))
