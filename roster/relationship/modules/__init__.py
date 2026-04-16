"""Relationship module — wires relationship domain tools into the butler's MCP server.

Registers 60+ MCP tools that delegate to the existing implementations in
``butlers.tools.relationship``. The tool closures strip ``pool`` and internal
params (``memory_pool``) from the MCP-visible signature
and inject them from module state at call time.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from butlers.modules.base import Module, ToolGroupMixin

logger = logging.getLogger(__name__)


class RelationshipModuleConfig(ToolGroupMixin, BaseModel):
    """Configuration for the Relationship module.

    Tool groups
    -----------
    contacts : contact_create, contact_get, contact_update, contact_search,
               contact_archive, contact_merge, contact_resolve,
               address_add, address_list, address_update, address_remove,
               contact_info_add, contact_info_list, contact_info_remove,
               contact_search_by_info, contact_export_vcard, contact_import_vcard
    interactions : interaction_log, interaction_list, feed_get, fact_set, fact_list
    relationships : relationship_add, relationship_list, relationship_remove,
                    relationship_type_get, relationship_types_list,
                    life_event_types_list, life_event_log, life_event_list
    social : date_add, date_list, upcoming_dates, gift_add, gift_list,
             gift_update_status, group_create, group_add_member, group_list,
             group_members
    notes : note_create, note_list, note_search, label_create, label_assign,
            contact_search_by_label
    tracking : task_create, task_list, task_complete, task_delete,
               loan_create, loan_settle, loan_list
    management : dunbar_tier_set, stay_in_touch_set, contacts_overdue
    entity : entity_resolve, entity_get, entity_update, entity_neighbors
    """


class RelationshipModule(Module):
    """Relationship module providing MCP tools for contacts, interactions,
    dates, gifts, groups, labels, life events, loans, notes, relationships,
    tasks, addresses, contact info, facts, feed, stay-in-touch,
    resolve, and vCard import/export.
    """

    def __init__(self) -> None:
        self._db: Any = None

    @property
    def name(self) -> str:
        return "relationship"

    @property
    def config_schema(self) -> type[BaseModel]:
        return RelationshipModuleConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None  # relationship tables already exist via separate migrations

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        """Store the Database reference for later pool access."""
        self._db = db

    async def on_shutdown(self) -> None:
        """Clear state references."""
        self._db = None

    def _get_pool(self):
        """Return the asyncpg pool, raising if not initialised."""
        if self._db is None:
            raise RuntimeError("RelationshipModule not initialised -- no DB available")
        return self._db.pool

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        """Register all relationship MCP tools."""
        self._db = db
        from .tools import register_tools

        register_tools(mcp, self, config)
