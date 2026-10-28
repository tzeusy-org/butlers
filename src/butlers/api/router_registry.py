"""One deterministic registry of core and sorted roster router exports.

The explicit core list preserves route precedence and includes modules exporting
multiple routers. Roster loading retains router_discovery's identity/cache policy.
Constructing this registry imports routes; it never initializes database pools.
"""

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from types import ModuleType

from fastapi import APIRouter

from butlers.api.router_discovery import discover_butler_routers

_CORE_EXPORTS = (
    ("butlers.api.routers.approvals", "router"),
    ("butlers.api.routers.confirms", "router"),
    ("butlers.api.routers.attention_ledger", "router"),
    ("butlers.api.routers.events", "router"),
    ("butlers.api.routers.butler_logs", "router"),
    ("butlers.api.routers.butlers", "router"),
    ("butlers.api.routers.butler_management", "router"),
    ("butlers.api.routers.notifications", "router"),
    ("butlers.api.routers.notifications", "butler_notifications_router"),
    ("butlers.api.routers.issues", "router"),
    ("butlers.api.routers.spend", "router"),
    ("butlers.api.routers.sessions", "router"),
    ("butlers.api.routers.sessions", "butler_sessions_router"),
    ("butlers.api.routers.activity_feed", "router"),
    ("butlers.api.routers.schedules", "router"),
    ("butlers.api.routers.modules", "router"),
    ("butlers.api.routers.secrets", "router"),
    ("butlers.api.routers.secrets_v2", "router"),
    ("butlers.api.routers.state", "router"),
    ("butlers.api.routers.ingestion_events", "router"),
    ("butlers.api.routers.ingestion_events", "rollup_router"),
    ("butlers.api.routers.identity", "router"),
    ("butlers.api.routers.ingestion_connectors", "router"),
    ("butlers.api.routers.ingestion_pipeline", "router"),
    ("butlers.api.routers.priority_contacts", "router"),
    ("butlers.api.routers.contacts", "router"),
    ("butlers.api.routers.channel_defaults", "router"),
    ("butlers.api.routers.timeline", "router"),
    ("butlers.api.routers.timeline_saved_views", "router"),
    ("butlers.api.routers.calendar_workspace", "router"),
    ("butlers.api.routers.calendar_workspace", "accounts_router"),
    ("butlers.api.routers.search", "router"),
    ("butlers.api.routers.audit", "router"),
    ("butlers.api.routers.memory", "router"),
    ("butlers.api.routers.memory", "butler_memory_router"),
    ("butlers.api.routers.oauth", "router"),
    ("butlers.api.routers.cli_auth", "router"),
    ("butlers.api.routers.sse", "router"),
    ("butlers.api.routers.model_settings", "catalog_router"),
    ("butlers.api.routers.model_settings", "pricing_router"),
    ("butlers.api.routers.model_settings", "butler_model_router"),
    ("butlers.api.routers.model_settings", "dispatch_router"),
    ("butlers.api.routers.healing", "router"),
    ("butlers.api.routers.qa", "router"),
    ("butlers.api.routers.provider_settings", "router"),
    ("butlers.api.routers.general_settings", "router"),
    ("butlers.api.routers.blob_storage", "router"),
    ("butlers.api.routers.owntracks", "router"),
    ("butlers.api.routers.home_assistant", "router"),
    ("butlers.api.routers.home_person_mappings", "router"),
    ("butlers.api.routers.spotify", "router"),
    ("butlers.api.routers.google_health", "router"),
    ("butlers.api.routers.steam", "router"),
    ("butlers.api.routers.telegram_auth", "router"),
    ("butlers.api.routers.whatsapp", "router"),
    ("butlers.api.routers.conversations", "router"),
    ("butlers.api.routers.conversations", "messages_search_router"),
    ("butlers.api.routers.preferences", "router"),
    ("butlers.api.routers.runtime_config", "router"),
    ("butlers.api.routers.system", "router"),
    ("butlers.api.routers.dashboard_briefing", "router"),
    ("butlers.api.routers.permissions", "router"),
    ("butlers.api.routers.settings_console", "router"),
    ("butlers.api.routers.data_ops", "router"),
    ("butlers.api.routers.delegation", "router"),
    ("butlers.api.routers.entities", "router"),
    ("butlers.api.routers.domain_events", "router"),
    ("butlers.api.routers.webhooks", "router"),
)


@dataclass(frozen=True)
class RouterRegistration:
    module: ModuleType
    export: str
    butler_name: str | None = None

    @property
    def router(self) -> APIRouter:
        return getattr(self.module, self.export)


def build_router_registry(roster_dir: Path | None = None) -> tuple[RouterRegistration, ...]:
    """Return every core export followed by valid roster exports in stable order."""
    core = tuple(
        RouterRegistration(import_module(module), export) for module, export in _CORE_EXPORTS
    )
    roster = tuple(
        RouterRegistration(module, "router", name)
        for name, module in discover_butler_routers(roster_dir)
    )
    return core + roster
