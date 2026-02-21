"""Contacts module configuration scaffold.

This module establishes the contacts configuration contract used by roster
butlers and startup validation. Sync execution and provider I/O are added in
follow-up issues.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from butlers.modules.base import Module


class ContactsSyncConfig(BaseModel):
    """Scheduler defaults for incremental and forced full contact sync."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    run_on_startup: bool = True
    interval_minutes: int = Field(default=15, ge=1)
    full_sync_interval_days: int = Field(default=6, ge=1)


class ContactsConfig(BaseModel):
    """Configuration for the Contacts module."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    include_other_contacts: bool = False
    sync: ContactsSyncConfig = Field(default_factory=ContactsSyncConfig)

    @field_validator("provider")
    @classmethod
    def _normalize_provider(cls, value: str, info: ValidationInfo) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return normalized


class ContactsModule(Module):
    """Contacts module scaffold with strict config and provider gating."""

    _SUPPORTED_PROVIDERS = {"google"}

    def __init__(self) -> None:
        self._config: ContactsConfig | None = None
        self._db: Any = None

    @property
    def name(self) -> str:
        return "contacts"

    @property
    def config_schema(self) -> type[BaseModel]:
        return ContactsConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    @property
    def credentials_env(self) -> list[str]:
        # Contacts uses shared Google OAuth credentials from DB-backed secrets.
        return []

    def migration_revisions(self) -> str | None:
        return None

    @staticmethod
    def _coerce_config(config: Any) -> ContactsConfig:
        return config if isinstance(config, ContactsConfig) else ContactsConfig(**(config or {}))

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        # Tools are intentionally added in sync-engine follow-up work.
        self._config = self._coerce_config(config)
        self._db = db

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        self._config = self._coerce_config(config)
        self._db = db
        if self._config.provider not in self._SUPPORTED_PROVIDERS:
            supported = ", ".join(sorted(self._SUPPORTED_PROVIDERS))
            raise RuntimeError(
                f"Unsupported contacts provider '{self._config.provider}'. "
                f"Supported providers: {supported}"
            )

    async def on_shutdown(self) -> None:
        self._config = None
        self._db = None
