"""Relationship MCP tool registrations.

Group-aware tool closures called by ``RelationshipModule.register_tools``
via ``register_tools(mcp, module, config)``.  When ``config.groups`` is set,
only tools in the listed groups are registered on the MCP server.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any


def register_tools(mcp: Any, module: Any, config: Any = None) -> None:  # noqa: C901
    """Register all relationship MCP tools as closures over *module*."""

    from butlers.modules.base import group_enabled

    def _tool(group: str):
        if group_enabled(config, group):
            return mcp.tool()
        return lambda fn: fn

    # Import sub-modules (deferred to avoid import-time side effects)
    from butlers.tools.relationship import addresses as _addr
    from butlers.tools.relationship import channel as _ci
    from butlers.tools.relationship import contacts as _contacts
    from butlers.tools.relationship import dates as _dates
    from butlers.tools.relationship import dunbar as _dunbar
    from butlers.tools.relationship import facts as _facts
    from butlers.tools.relationship import feed as _feed
    from butlers.tools.relationship import gifts as _gifts
    from butlers.tools.relationship import groups as _groups
    from butlers.tools.relationship import interactions as _inter
    from butlers.tools.relationship import labels as _labels
    from butlers.tools.relationship import life_events as _life
    from butlers.tools.relationship import loans as _loans
    from butlers.tools.relationship import notes as _notes
    from butlers.tools.relationship import relationships as _rels
    from butlers.tools.relationship import resolve as _resolve
    from butlers.tools.relationship import stay_in_touch as _sit
    from butlers.tools.relationship import tasks as _tasks
    from butlers.tools.relationship import vcard as _vcard

    # =================================================================
    # Address tools (group: contacts_extended)
    # =================================================================

    @_tool("contacts_extended")
    async def address_add(
        contact_id: uuid.UUID,
        line_1: str,
        label: str = "Home",
        line_2: str | None = None,
        city: str | None = None,
        province: str | None = None,
        postal_code: str | None = None,
        country: str | None = None,
        is_current: bool = False,
    ) -> dict[str, Any]:
        """Add an address for a contact.

        If is_current is True, clears the is_current flag on all other
        addresses for this contact first.
        """
        return await _addr.address_add(
            module._get_pool(),
            contact_id,
            line_1,
            label=label,
            line_2=line_2,
            city=city,
            province=province,
            postal_code=postal_code,
            country=country,
            is_current=is_current,
        )

    @_tool("contacts_extended")
    async def address_list(
        contact_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        """List all addresses for a contact, current address first."""
        return await _addr.address_list(module._get_pool(), contact_id)

    @_tool("contacts_extended")
    async def address_update(
        address_id: uuid.UUID,
        label: str | None = None,
        line_1: str | None = None,
        line_2: str | None = None,
        city: str | None = None,
        province: str | None = None,
        postal_code: str | None = None,
        country: str | None = None,
        is_current: bool | None = None,
    ) -> dict[str, Any]:
        """Update an address's fields.

        Supported fields: label, line_1, line_2, city, province,
        postal_code, country, is_current. If is_current is set to True,
        clears the flag on all other addresses for the same contact.
        """
        fields: dict[str, Any] = {
            k: v
            for k, v in {
                "label": label,
                "line_1": line_1,
                "line_2": line_2,
                "city": city,
                "province": province,
                "postal_code": postal_code,
                "country": country,
                "is_current": is_current,
            }.items()
            if v is not None
        }
        return await _addr.address_update(module._get_pool(), address_id, **fields)

    @_tool("contacts_extended")
    async def address_remove(address_id: uuid.UUID) -> None:
        """Remove an address by ID."""
        await _addr.address_remove(module._get_pool(), address_id)

    # =================================================================
    # Channel tools (group: contacts / contacts_extended)
    # =================================================================

    @_tool("contacts")
    async def channel_add(
        contact_id: uuid.UUID,
        type: str,
        value: str,
        label: str | None = None,
        is_primary: bool = False,
        context: str | None = None,
    ) -> dict[str, Any]:
        """Add a channel-identity fact (email, phone, etc.) for a contact.

        When ``type='email'`` and ``context`` is omitted, the work-domain
        heuristic runs automatically: if the email domain is in the
        configured work-domain list the row is stored with
        ``context='work'``.  Pass ``context='personal'`` explicitly to
        suppress the heuristic for a known personal address.
        """
        return await _ci.channel_add(
            module._get_pool(),
            contact_id,
            type,
            value,
            label=label,
            is_primary=is_primary,
            context=context,
        )

    @_tool("contacts_extended")
    async def channel_list(
        contact_id: uuid.UUID,
        type: str | None = None,
    ) -> list[dict[str, Any]]:
        """List channel-identity facts for a contact, optionally filtered
        by type."""
        return await _ci.channel_list(module._get_pool(), contact_id, type=type)

    @_tool("contacts")
    async def channel_search(
        value: str,
        type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search contacts by channel value (reverse lookup).

        Finds all contacts that have a matching channel fact.
        Optionally filter by info type (email, phone, etc.).
        Uses ILIKE for case-insensitive partial matching.
        """
        return await _ci.channel_search(module._get_pool(), value, type=type)

    # =================================================================
    # Contact tools (group: contacts)
    # =================================================================

    @_tool("contacts")
    async def contact_create(
        name: str | None = None,
        details: dict[str, Any] | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        nickname: str | None = None,
        company: str | None = None,
        job_title: str | None = None,
        gender: str | None = None,
        pronouns: str | None = None,
        avatar_url: str | None = None,
        listed: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a contact with compatibility for both legacy and
        spec schemas."""
        return await _contacts.contact_create(
            module._get_pool(),
            name,
            details,
            first_name=first_name,
            last_name=last_name,
            nickname=nickname,
            company=company,
            job_title=job_title,
            gender=gender,
            pronouns=pronouns,
            avatar_url=avatar_url,
            listed=listed,
            metadata=metadata,
        )

    @_tool("contacts")
    async def contact_get(
        contact_id: uuid.UUID,
        allow_missing: bool = False,
    ) -> dict[str, Any] | None:
        """Get a contact by ID."""
        return await _contacts.contact_get(
            module._get_pool(),
            contact_id,
            allow_missing=allow_missing,
        )

    @_tool("contacts")
    async def contact_update(
        contact_id: uuid.UUID,
        name: str | None = None,
        details: dict[str, Any] | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        nickname: str | None = None,
        company: str | None = None,
        job_title: str | None = None,
        gender: str | None = None,
        pronouns: str | None = None,
        avatar_url: str | None = None,
        metadata: dict[str, Any] | None = None,
        listed: bool | None = None,
    ) -> dict[str, Any]:
        """Update a contact's fields across legacy/spec schemas."""
        fields: dict[str, Any] = {
            k: v
            for k, v in {
                "name": name,
                "details": details,
                "first_name": first_name,
                "last_name": last_name,
                "nickname": nickname,
                "company": company,
                "job_title": job_title,
                "gender": gender,
                "pronouns": pronouns,
                "avatar_url": avatar_url,
                "metadata": metadata,
                "listed": listed,
            }.items()
            if v is not None
        }
        return await _contacts.contact_update(module._get_pool(), contact_id, **fields)

    @_tool("contacts")
    async def contact_search(
        query: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Search contacts by legacy and spec fields."""
        return await _contacts.contact_search(module._get_pool(), query, limit, offset)

    @_tool("contacts_extended")
    async def contact_archive(
        contact_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Archive a contact across legacy/spec schemas."""
        return await _contacts.contact_archive(module._get_pool(), contact_id)

    @_tool("contacts_extended")
    async def contact_merge(
        source_id: uuid.UUID,
        target_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Merge source contact into target contact.

        The target contact survives; the source is archived. All related
        records (notes, interactions, reminders, etc.) are re-pointed to
        the target.
        """
        chronicler_pool = await module._get_or_create_chronicler_pool()
        # chronicler_pool is None when the DB is not initialised. When provided,
        # episode_entities rows are re-pointed as part of the entity_merge call.
        return await _contacts.contact_merge(
            module._get_pool(),
            source_id,
            target_id,
            chronicler_pool=chronicler_pool,
        )

    # =================================================================
    # Date tools (group: social)
    # =================================================================

    @_tool("social")
    async def date_add(
        contact_id: uuid.UUID,
        label: str,
        month: int,
        day: int,
        year: int | None = None,
    ) -> dict[str, Any]:
        """Add an important date for a contact. Skips duplicate
        contact+label+month+day."""
        return await _dates.date_add(module._get_pool(), contact_id, label, month, day, year)

    @_tool("social")
    async def date_list(
        contact_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        """List all important dates for a contact."""
        return await _dates.date_list(module._get_pool(), contact_id)

    @_tool("management")
    async def upcoming_dates(
        days_ahead: int = 30,
    ) -> list[dict[str, Any]]:
        """Get upcoming important dates within the next N days using
        month/day matching."""
        return await _dates.upcoming_dates(module._get_pool(), days_ahead)

    # =================================================================
    # Fact tools (group: interactions)
    # =================================================================

    @_tool("interactions")
    async def fact_set(
        contact_id: uuid.UUID,
        key: str,
        value: str,
    ) -> dict[str, Any]:
        """Set a quick fact for a contact (UPSERT)."""
        return await _facts.fact_set(module._get_pool(), contact_id, key, value)

    @_tool("interactions")
    async def fact_list(
        contact_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        """List all quick facts for a contact."""
        return await _facts.fact_list(module._get_pool(), contact_id)

    # =================================================================
    # Gift tools (group: social)
    # =================================================================

    @_tool("social")
    async def gift_add(
        contact_id: uuid.UUID,
        description: str,
        occasion: str | None = None,
    ) -> dict[str, Any]:
        """Add a gift idea for a contact."""
        return await _gifts.gift_add(module._get_pool(), contact_id, description, occasion)

    @_tool("social")
    async def gift_list(
        contact_id: uuid.UUID,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List gifts for a contact, optionally filtered by status."""
        return await _gifts.gift_list(module._get_pool(), contact_id, status)

    @_tool("social")
    async def gift_update_status(
        gift_id: uuid.UUID,
        status: str,
    ) -> dict[str, Any]:
        """Update gift status, validating pipeline order."""
        return await _gifts.gift_update_status(module._get_pool(), gift_id, status)

    # =================================================================
    # Group tools (group: social)
    # =================================================================

    @_tool("social")
    async def group_create(
        name: str,
        type: str | None = None,
    ) -> dict[str, Any]:
        """Create a contact group."""
        return await _groups.group_create(module._get_pool(), name, type)

    @_tool("social")
    async def group_add_member(
        group_id: uuid.UUID,
        contact_id: uuid.UUID,
        role: str | None = None,
    ) -> dict[str, Any]:
        """Add a contact to a group."""
        return await _groups.group_add_member(module._get_pool(), group_id, contact_id, role)

    @_tool("social")
    async def group_list() -> list[dict[str, Any]]:
        """List all groups."""
        return await _groups.group_list(module._get_pool())

    @_tool("social")
    async def group_members(
        group_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        """List all members of a group."""
        return await _groups.group_members(module._get_pool(), group_id)

    # =================================================================
    # Interaction tools (group: interactions)
    # =================================================================

    @_tool("interactions")
    async def interaction_log(
        contact_id: uuid.UUID,
        type: str,
        summary: str | None = None,
        occurred_at: datetime | None = None,
        direction: str | None = None,
        duration_minutes: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Log an interaction with a contact.

        Resolves the contact's entity_id internally before writing the fact.
        The fact is stored under subject='entity:{entity_id}' in the facts table.
        """
        from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id

        pool = module._get_pool()
        entity_id = await resolve_contact_entity_id(pool, contact_id)
        return await _inter.interaction_log(
            pool,
            entity_id,
            type,
            summary=summary,
            occurred_at=occurred_at,
            direction=direction,
            duration_minutes=duration_minutes,
            metadata=metadata,
        )

    @_tool("interactions")
    async def interaction_list(
        contact_id: uuid.UUID,
        limit: int = 20,
        direction: str | None = None,
        type: str | None = None,
    ) -> list[dict[str, Any]]:
        """List interactions for a contact, most recent first.

        Resolves the contact's entity_id internally before querying.
        Optionally filter by direction and/or type.
        """
        from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id

        pool = module._get_pool()
        entity_id = await resolve_contact_entity_id(pool, contact_id)
        return await _inter.interaction_list(
            pool,
            entity_id,
            limit=limit,
            direction=direction,
            type=type,
        )

    @_tool("interactions")
    async def interaction_log_group(
        group_id: uuid.UUID,
        type: str = "group_interaction",
        direction: str = "mutual",
        occurred_at: datetime | None = None,
        summary: str | None = None,
        duration_minutes: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Log an interaction with all members of a contact group in a single call.

        Resolves group membership and fans out interaction_log() calls for each
        member with group_size injected into the fact metadata.

        Returns:
            {"logged": N, "skipped": M, "group_size": G, "status": "ok"} on success.
            {"logged": 0, "skipped": 0, "group_size": G, "status": "group_too_large"} if >20.
            {"logged": 0, "skipped": 0, "group_size": 0, "status": "ok"} if the group is empty.
        """
        return await _inter.interaction_log_group(
            module._get_pool(),
            group_id,
            type=type,
            direction=direction,
            occurred_at=occurred_at,
            summary=summary,
            duration_minutes=duration_minutes,
            metadata=metadata,
        )

    @_tool("interactions")
    async def feed_get(
        contact_id: uuid.UUID,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Unified temporal activity feed for a contact, most recent first.

        Aggregates the contact's interactions (interaction_*), life events,
        notes, and activity facts into a single stream ordered by when each
        event occurred (valid_at descending). Resolves the contact's entity_id
        internally before querying.
        """
        from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id

        pool = module._get_pool()
        entity_id = await resolve_contact_entity_id(pool, contact_id)
        return await _feed.feed_get(pool, entity_id, limit=limit)

    # =================================================================
    # Label tools (group: notes)
    # =================================================================

    @_tool("notes")
    async def label_create(
        name: str,
        color: str | None = None,
    ) -> dict[str, Any]:
        """Create a label."""
        return await _labels.label_create(module._get_pool(), name, color)

    @_tool("notes")
    async def label_assign(
        label_id: uuid.UUID,
        contact_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Assign a label to a contact."""
        return await _labels.label_assign(module._get_pool(), label_id, contact_id)

    @_tool("notes")
    async def contact_search_by_label(
        label_name: str,
    ) -> list[dict[str, Any]]:
        """Search contacts by label name."""
        return await _labels.contact_search_by_label(module._get_pool(), label_name)

    # =================================================================
    # Life Event tools (group: relationships)
    # =================================================================

    @_tool("relationships")
    async def life_event_types_list() -> list[dict[str, Any]]:
        """List all available life event types with their
        categories."""
        return await _life.life_event_types_list(module._get_pool())

    @_tool("relationships")
    async def life_event_log(
        contact_id: uuid.UUID,
        type_name: str,
        summary: str | None = None,
        description: str | None = None,
        happened_at: str | None = None,
        occurred_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Log a life event for a contact.

        Args:
            contact_id: UUID of the contact
            type_name: Name of the life event type (e.g., 'promotion',
                'married')
            summary: Short summary of the event
            description: Optional longer description
            happened_at: Optional date string (YYYY-MM-DD format)
        """
        return await _life.life_event_log(
            module._get_pool(),
            contact_id,
            type_name,
            summary=summary,
            description=description,
            happened_at=happened_at,
            occurred_at=occurred_at,
        )

    @_tool("relationships")
    async def life_event_list(
        contact_id: uuid.UUID | None = None,
        type_name: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List life events, optionally filtered by contact and/or
        type.

        Args:
            contact_id: Optional filter by contact UUID
            type_name: Optional filter by life event type name
            limit: Maximum number of events to return
        """
        return await _life.life_event_list(
            module._get_pool(),
            contact_id=contact_id,
            type_name=type_name,
            limit=limit,
        )

    # =================================================================
    # Loan tools (group: tracking)
    # =================================================================

    @_tool("tracking")
    async def loan_create(
        contact_id: uuid.UUID | None = None,
        amount: Decimal | None = None,
        direction: str | None = None,
        description: str | None = None,
        lender_contact_id: uuid.UUID | None = None,
        borrower_contact_id: uuid.UUID | None = None,
        amount_cents: int | None = None,
        currency: str = "USD",
    ) -> dict[str, Any]:
        """Create a loan record with legacy + spec-compatible fields."""
        return await _loans.loan_create(
            module._get_pool(),
            contact_id,
            amount,
            direction,
            description,
            lender_contact_id=lender_contact_id,
            borrower_contact_id=borrower_contact_id,
            amount_cents=amount_cents,
            currency=currency,
        )

    @_tool("tracking")
    async def loan_settle(
        loan_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Settle a loan."""
        return await _loans.loan_settle(module._get_pool(), loan_id)

    @_tool("tracking")
    async def loan_list(
        contact_id: uuid.UUID | None = None,
    ) -> list[dict[str, Any]]:
        """List loans, optionally filtered by contact."""
        return await _loans.loan_list(module._get_pool(), contact_id)

    # =================================================================
    # Note tools (group: notes)
    # =================================================================

    @_tool("notes")
    async def note_create(
        contact_id: uuid.UUID,
        content: str | None = None,
        body: str | None = None,
        title: str | None = None,
        emotion: str | None = None,
    ) -> dict[str, Any]:
        """Create a note about a contact."""
        return await _notes.note_create(
            module._get_pool(),
            contact_id,
            content=content,
            body=body,
            title=title,
            emotion=emotion,
        )

    @_tool("notes")
    async def note_list(
        contact_id: uuid.UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List all notes for a contact."""
        return await _notes.note_list(module._get_pool(), contact_id, limit, offset)

    @_tool("notes")
    async def note_search(
        query: str,
        contact_id: uuid.UUID | None = None,
    ) -> list[dict[str, Any]]:
        """Search notes by body/title content (ILIKE), optionally
        scoped to a contact."""
        return await _notes.note_search(module._get_pool(), query, contact_id)

    # =================================================================
    # Relationship tools (group: relationships)
    # =================================================================

    @_tool("relationships")
    async def relationship_add(
        contact_a: uuid.UUID,
        contact_b: uuid.UUID,
        type: str | None = None,
        type_id: uuid.UUID | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Create a bidirectional relationship (two rows).

        Accepts either:
          - type_id: UUID of a relationship_type (preferred)
          - type: freetext label for backward compat (matched against
            taxonomy)

        The reverse row automatically gets the correct reverse_label.
        """
        return await _rels.relationship_add(
            module._get_pool(),
            contact_a,
            contact_b,
            type=type,
            type_id=type_id,
            notes=notes,
        )

    @_tool("relationships")
    async def relationship_list(
        contact_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        """List all relationships for a contact."""
        return await _rels.relationship_list(module._get_pool(), contact_id)

    @_tool("relationships")
    async def relationship_remove(
        contact_a: uuid.UUID,
        contact_b: uuid.UUID,
    ) -> None:
        """Remove both directions of a relationship."""
        await _rels.relationship_remove(module._get_pool(), contact_a, contact_b)

    @_tool("relationships")
    async def relationship_type_get(
        type_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        """Get a single relationship type by ID."""
        return await _rels.relationship_type_get(module._get_pool(), type_id)

    @_tool("relationships")
    async def relationship_types_list(
        group: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """List relationship types, grouped by category.

        Returns a dict keyed by group name, each value is a list of
        type dicts with id, forward_label, and reverse_label.
        If group is specified, returns only types in that group.
        """
        return await _rels.relationship_types_list(module._get_pool(), group)

    # =================================================================
    # Resolve tools (group: contacts)
    # =================================================================

    @_tool("contacts")
    async def contact_resolve(
        name: str,
        context: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a name string to a contact_id.

        Resolution strategy (in order):
        1. Exact full-name match (case-insensitive) -> HIGH confidence
        2. Multiple candidates -> compute salience scores, apply
           entity-resolve score=100 promotion
        3. Partial match -> MEDIUM confidence
        4. No match -> confidence: "none"
        """
        return await _resolve.contact_resolve(module._get_pool(), name, context)

    # =================================================================
    # Dunbar tier tools (group: management)
    # =================================================================

    @_tool("management")
    async def dunbar_tier_set(
        contact_id: uuid.UUID,
        tier: int | None,
    ) -> dict[str, Any]:
        """Set or clear a manual Dunbar tier override for a contact.

        Dunbar tiers represent concentric social circles based on interaction
        patterns. Valid tier values: 5 (support clique), 15 (sympathy group),
        50 (good friends), 150 (meaningful contacts), 500 (acquaintances),
        1500 (recognizable).

        Pass tier=None to clear the override and revert to rank-based
        tier assignment from interaction history.

        The override is stored as a permanent SPO fact and takes precedence
        over the computed rank-based tier.
        """
        return await _dunbar.dunbar_tier_set(module._get_pool(), contact_id, tier)

    # =================================================================
    # Stay-in-Touch tools (group: management)
    # =================================================================

    @_tool("management")
    async def stay_in_touch_set(
        contact_id: uuid.UUID,
        frequency_days: int | None,
    ) -> dict[str, Any]:
        """Set or clear the stay-in-touch cadence for a contact.

        Pass frequency_days=None to clear the cadence (removes from
        overdue list).
        """
        return await _sit.stay_in_touch_set(module._get_pool(), contact_id, frequency_days)

    @_tool("management")
    async def contacts_overdue() -> list[dict[str, Any]]:
        """Return contacts whose last interaction exceeds their
        stay-in-touch cadence.

        Contacts with a cadence but no interactions are always overdue.
        Contacts with no cadence (NULL) are never returned.
        Archived contacts are excluded.
        """
        return await _sit.contacts_overdue(module._get_pool())

    # =================================================================
    # Task tools (group: tracking)
    # =================================================================

    @_tool("tracking")
    async def task_create(
        contact_id: uuid.UUID,
        title: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a task/to-do scoped to a contact."""
        return await _tasks.task_create(module._get_pool(), contact_id, title, description)

    @_tool("tracking")
    async def task_list(
        contact_id: uuid.UUID | None = None,
        include_completed: bool = False,
    ) -> list[dict[str, Any]]:
        """List tasks, optionally filtered by contact and completion
        status."""
        return await _tasks.task_list(
            module._get_pool(),
            contact_id=contact_id,
            include_completed=include_completed,
        )

    @_tool("tracking")
    async def task_complete(
        task_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Mark a task as completed."""
        return await _tasks.task_complete(module._get_pool(), task_id)

    @_tool("tracking")
    async def task_delete(
        task_id: uuid.UUID,
    ) -> None:
        """Delete a task."""
        await _tasks.task_delete(module._get_pool(), task_id)

    # =================================================================
    # vCard tools (group: contacts_extended)
    # =================================================================

    @_tool("contacts_extended")
    async def contact_export_vcard(
        contact_id: uuid.UUID | None = None,
    ) -> str:
        """Export one or all contacts as vCard 3.0.

        Args:
            contact_id: Optional contact ID. If None, exports all
                listed contacts.

        Returns:
            vCard 3.0 formatted string (multiple vCards if exporting
            all)
        """
        return await _vcard.contact_export_vcard(module._get_pool(), contact_id)

    @_tool("contacts_extended")
    async def contact_import_vcard(
        vcf_content: str,
    ) -> list[dict[str, Any]]:
        """Import vCard data and create contacts.

        Parses vCard 3.0/4.0 content and creates contacts with:
        - FN/N -> first_name / last_name
        - TEL -> contact_info(type=phone)
        - EMAIL -> contact_info(type=email)
        - ADR -> addresses
        - BDAY -> important_dates (birthday)
        - ORG -> facts (company)
        - TITLE -> facts (job_title)
        - NOTE -> notes
        """
        return await _vcard.contact_import_vcard(module._get_pool(), vcf_content)

    # =================================================================
    # Entity tools (group: entity — from memory module, exposed for
    # entity-first workflows)
    # =================================================================

    from butlers.modules.memory.tools import entities as _entities

    @_tool("entity")
    async def entity_resolve(
        name: str,
        entity_type: str | None = None,
        context_topic: str | None = None,
        context_mentioned_with: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Resolve a name to ranked entity candidates.

        Use this BEFORE contact_create to check if a person/org already
        exists.  Returns a ranked list of candidates with entity_id,
        canonical_name, score, and match quality.

        Args:
            name: Name string to resolve.
            entity_type: Optional filter (person/organization/place/other).
            context_topic: Optional topic hint for graph-based scoring.
            context_mentioned_with: Optional list of co-mentioned names.
        """
        hints: dict[str, Any] | None = None
        if context_topic or context_mentioned_with:
            hints = {}
            if context_topic:
                hints["topic"] = context_topic
            if context_mentioned_with:
                hints["mentioned_with"] = context_mentioned_with
        return await _entities.entity_resolve(
            module._get_pool(),
            name,
            entity_type=entity_type,
            context_hints=hints,
        )

    @_tool("entity")
    async def entity_get(
        entity_id: str,
    ) -> dict[str, Any] | None:
        """Get full entity record by ID (canonical name, aliases, metadata)."""
        return await _entities.entity_get(
            module._get_pool(),
            entity_id,
        )

    @_tool("entity")
    async def entity_update(
        entity_id: str,
        canonical_name: str | None = None,
        aliases: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Update entity fields (name, aliases, metadata)."""
        return await _entities.entity_update(
            module._get_pool(),
            entity_id,
            canonical_name=canonical_name,
            aliases=aliases,
            metadata=metadata,
        )

    @_tool("entity")
    async def entity_neighbors(
        entity_id: str,
        max_depth: int = 2,
        predicate_filter: list[str] | None = None,
        direction: str = "both",
    ) -> list[dict[str, Any]]:
        """Traverse the entity graph and return neighboring entities.

        Follows edge-facts (facts with object_entity_id) to discover
        related entities up to max_depth hops.

        Args:
            entity_id: Starting entity UUID string.
            max_depth: Maximum traversal depth (1-5, default 2).
            predicate_filter: Optional predicates to restrict traversal.
            direction: Edge direction: outgoing, incoming, or both.
        """
        return await _entities.entity_neighbors(
            module._get_pool(),
            entity_id,
            max_depth=max_depth,
            predicate_filter=predicate_filter,
            direction=direction,
        )

    # =================================================================
    # Facts triple-store tools (group: entity — central writer)
    # =================================================================

    from butlers.tools.relationship import relationship_assert_fact as _raf

    @_tool("entity")
    async def relationship_assert_fact(
        subject: uuid.UUID,
        predicate: str,
        object: str,
        object_kind: str = "literal",
        conf: float = 1.0,
        last_seen: datetime | None = None,
        weight: int | None = None,
        verified: bool = False,
        primary: bool | None = None,
    ) -> dict[str, Any]:
        """Assert a fact triple in relationship.entity_facts (central writer).

        This is the SINGLE authoritative ingress point for all writes to
        ``relationship.entity_facts``.  Endpoints for contacts CRUD, entity merge,
        archive, dunbar-tier, queue/dismiss, the dual-write shim, and the
        backfill job all go through this tool.

        Args:
            subject: UUID of the subject entity (FK to public.entities.id).
            predicate: Predicate identifier (must exist in
                relationship.entity_predicate_registry, e.g. 'has-email', 'knows').
            object: Object value — a literal string for contact predicates or
                an entity UUID as text for relational predicates.
            object_kind: 'literal' (default) or 'entity'.
            conf: Confidence in [0.0, 1.0] (default 1.0).
            last_seen: Timestamp of the most recent observation (nullable).
            weight: Relational aggregation weight (nullable).
            verified: Owner-confirmed flag (default False).
            primary: Primary-of-kind flag for multi-valued contact predicates.

        Returns:
            Dict with keys: outcome ('inserted' | 'unchanged' | 'superseded' |
            'pending_approval'), fact_id (UUID or null), action_id (UUID or null).

        Owner carve-out (RFC 0017 §2.3): when subject resolves to the owner
        entity, a pending_actions row is created for approval instead of
        writing the triple directly.

        Security note: ``src`` is intentionally NOT exposed as a parameter here.
        It is hardcoded to ``"relationship"`` (the untrusted LLM-session default)
        to prevent privilege escalation via trusted-source spoofing (bu-vj46x).
        Trusted sources (``owner-self``, ``owner-bootstrap``) are reachable ONLY
        from internal daemon/bootstrap code that calls the underlying
        :func:`relationship_assert_fact` library function directly.
        """
        result = await _raf.relationship_assert_fact(
            module._get_pool(),
            subject,
            predicate,
            object,
            src="relationship",
            object_kind=object_kind,
            conf=conf,
            last_seen=last_seen,
            weight=weight,
            verified=verified,
            primary=primary,
        )
        return result.as_dict()

    from butlers.tools.relationship import relationship_lookup as _rlu

    @_tool("entity")
    async def relationship_lookup(
        entity_id: uuid.UUID | None = None,
        entity_ref: str | None = None,
    ) -> dict[str, Any]:
        """Read the owner's relationship knowledge for one entity.

        READ-ONLY: this tool never writes, mutates, touches last_seen, or
        schedules anything; repeated calls leave the database unchanged. It is
        the symmetric read path to relationship_assert_fact.

        IN-SESSION-ONLY: call this only from an already-running session in
        response to a live need. Do NOT build any cron entry, scheduled task,
        or spawn trigger around it (the per-call LLM cost lives at the caller).

        Pass EXACTLY ONE of:
            entity_id: UUID of a known entity.
            entity_ref: a name / alias / contact-value string. Resolved with the
                same deterministic ranking as the entity search endpoint
                (prefix > contact-value > substring > predicate; no model call).

        Passing both or neither raises a validation error. An unresolved ref is
        a structured miss (entity=null, candidates=[]), not an error. When a ref
        is ambiguous (tied top score) entity is null and up to 3 candidates are
        returned so you re-invoke with an explicit entity_id.

        Returns a dict:
            entity: {id, canonical_name, entity_type, aliases, roles, tier
                (null unless a Dunbar tier override is pinned), state} or null.
            facts: active facts from both stores (identity rows first, then
                narrative), each {store, predicate, object, object_kind, src,
                conf, verified, primary, observed_at, last_seen (identity only),
                staleness_band: fresh|aging|stale}.
            recency: {last_seen, last_interaction_at, staleness_band} or null.
            resolution: when entity_ref was used — {matched_on, score,
                ambiguous, candidates}; null for entity_id lookups.
        """
        return await _rlu.relationship_lookup(
            module._get_pool(),
            entity_id=entity_id,
            entity_ref=entity_ref,
        )
