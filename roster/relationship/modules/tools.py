"""Relationship MCP tool registrations.

All ``@mcp.tool()`` closures extracted from the monolithic ``module.py``.
Called by ``RelationshipModule.register_tools`` via
``register_tools(mcp, module)``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any


def register_tools(mcp: Any, module: Any) -> None:  # noqa: C901
    """Register all relationship MCP tools as closures over *module*."""

    # Import sub-modules (deferred to avoid import-time side effects)
    from butlers.tools.relationship import addresses as _addr
    from butlers.tools.relationship import contact_info as _ci
    from butlers.tools.relationship import contacts as _contacts
    from butlers.tools.relationship import dates as _dates
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
    from butlers.tools.relationship import reminders as _remind
    from butlers.tools.relationship import resolve as _resolve
    from butlers.tools.relationship import stay_in_touch as _sit
    from butlers.tools.relationship import tasks as _tasks
    from butlers.tools.relationship import vcard as _vcard

    # =================================================================
    # Address tools
    # =================================================================

    @mcp.tool()
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

    @mcp.tool()
    async def address_list(
        contact_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        """List all addresses for a contact, current address first."""
        return await _addr.address_list(module._get_pool(), contact_id)

    @mcp.tool()
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

    @mcp.tool()
    async def address_remove(address_id: uuid.UUID) -> None:
        """Remove an address by ID."""
        await _addr.address_remove(module._get_pool(), address_id)

    # =================================================================
    # Contact Info tools
    # =================================================================

    @mcp.tool()
    async def contact_info_add(
        contact_id: uuid.UUID,
        type: str,
        value: str,
        label: str | None = None,
        is_primary: bool = False,
    ) -> dict[str, Any]:
        """Add a piece of contact information (email, phone, etc.)
        for a contact."""
        return await _ci.contact_info_add(
            module._get_pool(),
            contact_id,
            type,
            value,
            label=label,
            is_primary=is_primary,
        )

    @mcp.tool()
    async def contact_info_list(
        contact_id: uuid.UUID,
        type: str | None = None,
    ) -> list[dict[str, Any]]:
        """List contact info for a contact, optionally filtered
        by type."""
        return await _ci.contact_info_list(module._get_pool(), contact_id, type=type)

    @mcp.tool()
    async def contact_info_remove(
        contact_info_id: uuid.UUID,
    ) -> None:
        """Remove a piece of contact information by its ID."""
        await _ci.contact_info_remove(module._get_pool(), contact_info_id)

    @mcp.tool()
    async def contact_search_by_info(
        value: str,
        type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search contacts by contact info value (reverse lookup).

        Finds all contacts that have a matching contact info entry.
        Optionally filter by info type (email, phone, etc.).
        Uses ILIKE for case-insensitive partial matching.
        """
        return await _ci.contact_search_by_info(module._get_pool(), value, type=type)

    # =================================================================
    # Contact tools
    # =================================================================

    @mcp.tool()
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

    @mcp.tool()
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

    @mcp.tool()
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

    @mcp.tool()
    async def contact_search(
        query: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Search contacts by legacy and spec fields."""
        return await _contacts.contact_search(module._get_pool(), query, limit, offset)

    @mcp.tool()
    async def contact_archive(
        contact_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Archive a contact across legacy/spec schemas."""
        return await _contacts.contact_archive(module._get_pool(), contact_id)

    @mcp.tool()
    async def contact_merge(
        source_id: uuid.UUID,
        target_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Merge source contact into target contact.

        The target contact survives; the source is archived. All related
        records (notes, interactions, reminders, etc.) are re-pointed to
        the target.
        """
        return await _contacts.contact_merge(module._get_pool(), source_id, target_id)

    # =================================================================
    # Date tools
    # =================================================================

    @mcp.tool()
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

    @mcp.tool()
    async def date_list(
        contact_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        """List all important dates for a contact."""
        return await _dates.date_list(module._get_pool(), contact_id)

    @mcp.tool()
    async def upcoming_dates(
        days_ahead: int = 30,
    ) -> list[dict[str, Any]]:
        """Get upcoming important dates within the next N days using
        month/day matching."""
        return await _dates.upcoming_dates(module._get_pool(), days_ahead)

    # =================================================================
    # Fact tools
    # =================================================================

    @mcp.tool()
    async def fact_set(
        contact_id: uuid.UUID,
        key: str,
        value: str,
    ) -> dict[str, Any]:
        """Set a quick fact for a contact (UPSERT)."""
        return await _facts.fact_set(module._get_pool(), contact_id, key, value)

    @mcp.tool()
    async def fact_list(
        contact_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        """List all quick facts for a contact."""
        return await _facts.fact_list(module._get_pool(), contact_id)

    # =================================================================
    # Feed tools
    # =================================================================

    @mcp.tool()
    async def feed_get(
        contact_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Get activity feed entries, optionally filtered by contact."""
        return await _feed.feed_get(
            module._get_pool(),
            contact_id=contact_id,
            limit=limit,
            offset=offset,
        )

    # =================================================================
    # Gift tools
    # =================================================================

    @mcp.tool()
    async def gift_add(
        contact_id: uuid.UUID,
        description: str,
        occasion: str | None = None,
    ) -> dict[str, Any]:
        """Add a gift idea for a contact."""
        return await _gifts.gift_add(module._get_pool(), contact_id, description, occasion)

    @mcp.tool()
    async def gift_list(
        contact_id: uuid.UUID,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List gifts for a contact, optionally filtered by status."""
        return await _gifts.gift_list(module._get_pool(), contact_id, status)

    @mcp.tool()
    async def gift_update_status(
        gift_id: uuid.UUID,
        status: str,
    ) -> dict[str, Any]:
        """Update gift status, validating pipeline order."""
        return await _gifts.gift_update_status(module._get_pool(), gift_id, status)

    # =================================================================
    # Group tools
    # =================================================================

    @mcp.tool()
    async def group_create(
        name: str,
        type: str | None = None,
    ) -> dict[str, Any]:
        """Create a contact group."""
        return await _groups.group_create(module._get_pool(), name, type)

    @mcp.tool()
    async def group_add_member(
        group_id: uuid.UUID,
        contact_id: uuid.UUID,
        role: str | None = None,
    ) -> dict[str, Any]:
        """Add a contact to a group."""
        return await _groups.group_add_member(module._get_pool(), group_id, contact_id, role)

    @mcp.tool()
    async def group_list() -> list[dict[str, Any]]:
        """List all groups."""
        return await _groups.group_list(module._get_pool())

    @mcp.tool()
    async def group_members(
        group_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        """List all members of a group."""
        return await _groups.group_members(module._get_pool(), group_id)

    # =================================================================
    # Interaction tools
    # =================================================================

    @mcp.tool()
    async def interaction_log(
        contact_id: uuid.UUID,
        type: str,
        summary: str | None = None,
        occurred_at: datetime | None = None,
        direction: str | None = None,
        duration_minutes: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Log an interaction with a contact."""
        return await _inter.interaction_log(
            module._get_pool(),
            contact_id,
            type,
            summary=summary,
            occurred_at=occurred_at,
            direction=direction,
            duration_minutes=duration_minutes,
            metadata=metadata,
        )

    @mcp.tool()
    async def interaction_list(
        contact_id: uuid.UUID,
        limit: int = 20,
        direction: str | None = None,
        type: str | None = None,
    ) -> list[dict[str, Any]]:
        """List interactions for a contact, most recent first.

        Optionally filter by direction and/or type.
        """
        return await _inter.interaction_list(
            module._get_pool(),
            contact_id,
            limit=limit,
            direction=direction,
            type=type,
        )

    # =================================================================
    # Label tools
    # =================================================================

    @mcp.tool()
    async def label_create(
        name: str,
        color: str | None = None,
    ) -> dict[str, Any]:
        """Create a label."""
        return await _labels.label_create(module._get_pool(), name, color)

    @mcp.tool()
    async def label_assign(
        label_id: uuid.UUID,
        contact_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Assign a label to a contact."""
        return await _labels.label_assign(module._get_pool(), label_id, contact_id)

    @mcp.tool()
    async def contact_search_by_label(
        label_name: str,
    ) -> list[dict[str, Any]]:
        """Search contacts by label name."""
        return await _labels.contact_search_by_label(module._get_pool(), label_name)

    # =================================================================
    # Life Event tools
    # =================================================================

    @mcp.tool()
    async def life_event_types_list() -> list[dict[str, Any]]:
        """List all available life event types with their
        categories."""
        return await _life.life_event_types_list(module._get_pool())

    @mcp.tool()
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

    @mcp.tool()
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
    # Loan tools
    # =================================================================

    @mcp.tool()
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

    @mcp.tool()
    async def loan_settle(
        loan_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Settle a loan."""
        return await _loans.loan_settle(module._get_pool(), loan_id)

    @mcp.tool()
    async def loan_list(
        contact_id: uuid.UUID | None = None,
    ) -> list[dict[str, Any]]:
        """List loans, optionally filtered by contact."""
        return await _loans.loan_list(module._get_pool(), contact_id)

    # =================================================================
    # Note tools
    # =================================================================

    @mcp.tool()
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

    @mcp.tool()
    async def note_list(
        contact_id: uuid.UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List all notes for a contact."""
        return await _notes.note_list(module._get_pool(), contact_id, limit, offset)

    @mcp.tool()
    async def note_search(
        query: str,
        contact_id: uuid.UUID | None = None,
    ) -> list[dict[str, Any]]:
        """Search notes by body/title content (ILIKE), optionally
        scoped to a contact."""
        return await _notes.note_search(module._get_pool(), query, contact_id)

    # =================================================================
    # Relationship tools
    # =================================================================

    @mcp.tool()
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

    @mcp.tool()
    async def relationship_list(
        contact_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        """List all relationships for a contact."""
        return await _rels.relationship_list(module._get_pool(), contact_id)

    @mcp.tool()
    async def relationship_remove(
        contact_a: uuid.UUID,
        contact_b: uuid.UUID,
    ) -> None:
        """Remove both directions of a relationship."""
        await _rels.relationship_remove(module._get_pool(), contact_a, contact_b)

    @mcp.tool()
    async def relationship_type_get(
        type_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        """Get a single relationship type by ID."""
        return await _rels.relationship_type_get(module._get_pool(), type_id)

    @mcp.tool()
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
    # Reminder tools
    # =================================================================

    @mcp.tool()
    async def reminder_create(
        contact_id: uuid.UUID | None = None,
        message: str | None = None,
        reminder_type: str | None = None,
        cron: str | None = None,
        due_at: datetime | None = None,
        label: str | None = None,
        type: str | None = None,
        next_trigger_at: datetime | None = None,
        timezone: str | None = None,
        until_at: datetime | None = None,
        calendar_event_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Create a reminder for a contact."""
        return await _remind.reminder_create(
            module._get_pool(),
            contact_id,
            message,
            reminder_type,
            cron,
            due_at,
            label=label,
            type=type,
            next_trigger_at=next_trigger_at,
            timezone=timezone,
            until_at=until_at,
            calendar_event_id=calendar_event_id,
        )

    @mcp.tool()
    async def reminder_list(
        contact_id: uuid.UUID | None = None,
        include_dismissed: bool = False,
    ) -> list[dict[str, Any]]:
        """List reminders, optionally filtered by contact."""
        return await _remind.reminder_list(
            module._get_pool(),
            contact_id=contact_id,
            include_dismissed=include_dismissed,
        )

    @mcp.tool()
    async def reminder_dismiss(
        reminder_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Dismiss a reminder across legacy/spec schemas."""
        return await _remind.reminder_dismiss(module._get_pool(), reminder_id)

    # =================================================================
    # Resolve tools
    # =================================================================

    @mcp.tool()
    async def contact_resolve(
        name: str,
        context: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a name string to a contact_id.

        Resolution strategy (in order):
        1. Exact full-name match (case-insensitive) -> HIGH confidence
        2. Multiple candidates -> compute salience scores, apply
           30-point gap threshold
        3. Partial match -> MEDIUM confidence
        4. No match -> confidence: "none"
        """
        return await _resolve.contact_resolve(module._get_pool(), name, context)

    # =================================================================
    # Stay-in-Touch tools
    # =================================================================

    @mcp.tool()
    async def stay_in_touch_set(
        contact_id: uuid.UUID,
        frequency_days: int | None,
    ) -> dict[str, Any]:
        """Set or clear the stay-in-touch cadence for a contact.

        Pass frequency_days=None to clear the cadence (removes from
        overdue list).
        """
        return await _sit.stay_in_touch_set(module._get_pool(), contact_id, frequency_days)

    @mcp.tool()
    async def contacts_overdue() -> list[dict[str, Any]]:
        """Return contacts whose last interaction exceeds their
        stay-in-touch cadence.

        Contacts with a cadence but no interactions are always overdue.
        Contacts with no cadence (NULL) are never returned.
        Archived contacts are excluded.
        """
        return await _sit.contacts_overdue(module._get_pool())

    # =================================================================
    # Task tools
    # =================================================================

    @mcp.tool()
    async def task_create(
        contact_id: uuid.UUID,
        title: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a task/to-do scoped to a contact."""
        return await _tasks.task_create(module._get_pool(), contact_id, title, description)

    @mcp.tool()
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

    @mcp.tool()
    async def task_complete(
        task_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Mark a task as completed."""
        return await _tasks.task_complete(module._get_pool(), task_id)

    @mcp.tool()
    async def task_delete(
        task_id: uuid.UUID,
    ) -> None:
        """Delete a task."""
        await _tasks.task_delete(module._get_pool(), task_id)

    # =================================================================
    # vCard tools
    # =================================================================

    @mcp.tool()
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

    @mcp.tool()
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
        - ORG -> quick_facts (company)
        - TITLE -> quick_facts (job_title)
        - NOTE -> notes
        """
        return await _vcard.contact_import_vcard(module._get_pool(), vcf_content)
