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
| **Runtime** | codex (gpt-5.4-mini) |

## Schedule

| Task | Cron | Description |
|------|------|-------------|
| `memory_consolidation` | `0 */6 * * *` | Consolidate episodic memory into durable facts |
| `memory_episode_cleanup` | `0 4 * * *` | Prune expired episodic memory entries |
| `eod-tomorrow-prep` | `0 15 * * *` | End-of-day briefing: fetch tomorrow's calendar events, compose a preparation summary with timeline, free blocks, and heads-up notes, and deliver via Telegram |

## Tools

The General Butler exposes collection and item management tools alongside standard core tools:

- **`collection_create / list / delete`** -- Manage named collections that group related items.
- **`item_create`** -- Store any freeform JSON data in a collection, creating the collection first when needed.
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

## Verification

To confirm the General Butler's collection store, schedule, and fallback routing are operating as described:

```bash
# 1. Confirm the butler is listening on the expected port
curl -s http://localhost:41101/health | python3 -m json.tool
# Expected: {"status": "ok", ...}

# 2. Verify the general schema has collections and items tables
psql -h localhost -U butlers -d butlers -c \
  "SELECT table_name FROM information_schema.tables
   WHERE table_schema = 'general'
   ORDER BY table_name;"
# Expected: collections, items (plus core tables: state, scheduled_tasks, sessions, session_process_logs)

# 3. Confirm items store arbitrary JSON (schema-free)
psql -h localhost -U butlers -d butlers -c \
  "SELECT column_name, data_type FROM information_schema.columns
   WHERE table_schema = 'general' AND table_name = 'items'
   AND column_name = 'data';"
# Expected: data_type = 'jsonb' -- not a fixed typed column

# 4. Verify the eod-tomorrow-prep task is seeded at 15:00 UTC (23:00 SGT)
psql -h localhost -U butlers -d butlers -c \
  "SELECT name, cron, enabled FROM general.scheduled_tasks
   WHERE name = 'eod-tomorrow-prep';"
# Expected: cron = '0 15 * * *', enabled = true

# 5. Confirm Switchboard routes unknown messages to General as fallback
# Inspect routing_log for rows where target_butler = 'general' from uncertain classifications
psql -h localhost -U butlers -d butlers -c \
  "SELECT target_butler, routing_reason, COUNT(*)
   FROM switchboard.routing_log
   WHERE target_butler = 'general'
   GROUP BY target_butler, routing_reason
   ORDER BY count DESC LIMIT 5;"
# Expected: rows present showing General as the fallback target for unclassified requests
```

## Related Pages

- [Switchboard Butler](switchboard.md) -- routes messages here as the default fallback
- [Relationship Butler](relationship.md) -- handles contact-specific data that goes beyond General's freeform model
