# General Butler

> **Purpose:** Flexible catch-all assistant for freeform data storage, retrieval, and organization using collections and items.
> **Audience:** Contributors and operators.
> **Prerequisites:** [Concepts](../concepts/butler-lifecycle.md), [Architecture](../architecture/butler-daemon.md).

## Overview

The General Butler is the system's digital second brain -- the place where anything that does not belong to a specialist domain can be stored, organized, and retrieved. Lists, notes, random data, ideas, recipes, contacts, dreams, book recommendations -- whatever the user needs to remember, the General Butler holds it in typed collections.

It also serves as the safe fallback for Switchboard routing. When the classifier is uncertain about which specialist should handle a request, the General Butler receives it and stores the information in a freeform collection rather than letting it drop.

## Profile

| Property | Value |
|----------|-------|
| **Port** | 41101 |
| **Schema** | `general` |
| **Modules** | calendar, contacts, general, memory |
| **Runtime** | codex (gpt-5.1) |

## Schedule

| Task | Cron | Description |
|------|------|-------------|
| `memory_consolidation` | `0 */6 * * *` | Consolidate episodic memory into durable facts |
| `memory_episode_cleanup` | `0 4 * * *` | Prune expired episodic memory entries |
| `eod-tomorrow-prep` | `0 15 * * *` | End-of-day briefing: fetch tomorrow's calendar events, compose a preparation summary with timeline, free blocks, and heads-up notes, and deliver via Telegram |

## Tools

The General Butler exposes collection and item management tools alongside standard core tools:

- **`collection_create / list / delete`** -- Manage named collections that group related items.
- **`item_create`** -- Store any freeform JSON data in a collection.
- **`item_get / update / delete`** -- CRUD on individual items within collections.
- **`item_search`** -- Find items using JSONB containment queries.
- **`collection_export`** -- Export all items from a collection.
- **Calendar tools** -- `calendar_list_events`, `get_event`, `create_event`, `update_event` for catch-all scheduling that does not belong to a specialist domain.

Items support deep-merge on update, meaning nested objects merge recursively rather than being replaced wholesale.

## Key Behaviors

**Flexible Storage.** Collections can hold any JSON-structured data. The General Butler does not impose a fixed schema on stored items -- it adapts to whatever the user provides.

**Proactive Organization.** When running in interactive mode (Telegram), the General Butler suggests collections, tagging, and grouping when it detects patterns in the data being stored. It extracts facts liberally from casual notes.

**Contacts Sync.** The contacts module syncs with Google Contacts every 15 minutes (with a full sync every 6 days), making contact data available for cross-domain use.

**End-of-Day Prep.** Every day at 15:00 (SGT), the General Butler fetches the next day's calendar events and sends a structured preparation summary via Telegram with a timeline, free blocks, and heads-up notes for anything unusual.

## Interaction Patterns

**Direct user interaction** via Telegram or email for general-purpose data storage and retrieval. Users say things like "add milk to the shopping list" or "save this recipe" and the General Butler stores the data in the appropriate collection.

**Switchboard fallback target.** When the routing classifier is uncertain, requests land here. The General Butler captures the information rather than losing it.

**Interactive response modes** range from quick emoji reactions (for simple additions) to substantive answers (for queries about stored data) to follow-up suggestions (for organizing patterns).

## Related Pages

- [Switchboard Butler](switchboard.md) -- routes messages here as the default fallback
- [Relationship Butler](relationship.md) -- handles contact-specific data that goes beyond General's freeform model
