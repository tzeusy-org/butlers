## Why

The owner can see an event attendee, the entity's activity, and the owner's
commitments, but the three surfaces do not form one continuous path. Meeting
Prep renders a resolved attendee name as plain text. Entity detail has no
bounded view of the Chronicler episodes shared with that entity. Commitments
are visible only inside calendar Meeting Prep even though the commitment
ledger already supports entity-scoped reads across originating domains.

The existing contracts also constrain the repair. Entity detail already owns
one unified ActivityTimeline, and the Relationship-owned `/activity`
aggregator already obtains Chronicler episodes through MCP. A second direct
Chronicler read path inside Relationship would weaken that boundary and could
present the same episode as a competing history.

## What Changes

### Modified Capabilities

- `dashboard-relationship`: Meeting Prep attendee names become
  query-preserving links to the canonical entity route; the existing activity
  aggregator gains an additive source filter used by a bounded Shared
  Chronicles summary; and a new read-only entity commitments endpoint exposes
  the existing safe commitment projection outside calendar context.
- `dashboard-chronicles`: the existing episode drawer gains a stable
  query-string door so a Shared Chronicles row can open the exact episode on
  the canonical `/chronicles` route.

## In Scope

- Preserve the full Calendar workspace query when linking a resolved Meeting
  Prep attendee to `/entities/{entity_id}`.
- Show at most five recent Chronicler activity rows on entity detail, sourced
  through the Relationship activity aggregator's existing MCP boundary.
- Add `GET /api/relationship/entities/{id}/commitments` for active commitments
  in the `owner_to_other` and `other_to_owner` directions.
- Define exact response, filtering, ordering, pagination, authorization,
  privacy, empty, degraded, retry, compatibility, and rollback behavior.
- Add an exact episode door at
  `/chronicles?date=<YYYY-MM-DD>&episode=<episode-id>`.

## Out of Scope

- Inline entity-fact correction.
- A consumer for `entity_graph_edges`.
- Any Relationship-to-Chronicler SQL or other direct schema access.
- A person-to-Chronicler write path.
- Commitment creation, resolution, editing, or new persistence.
- Replacing, filtering, or removing the unified ActivityTimeline.
- A request-time LLM call, provider call, notification, or other runtime
  effect.

## Compatibility and Authority

All wire changes are additive. Omitting the new activity `source` query keeps
the current merged response. Existing `/chronicles?date=...` links keep their
current behavior when `episode` is absent. Removing the new filter, episode
door, endpoint, and panels restores the prior UI without a migration or data
rewrite; the activity aggregator, Meeting Prep commitment rows, Chronicler
storage, and commitment ledger remain intact.

This change is an unapproved proposal produced under `bu-ryglp`. Publication
as a draft PR authorizes review only. Implementation remains blocked until the
owner approves the exact artifact after independent semantic review, and must
also wait for foreign-owned `bu-2jtfw.12` to release
`roster/relationship/api/router.py` and its relationship endpoint tests.

## Impact

- Future frontend work:
  `frontend/src/components/calendar/MeetingPrepRail.tsx`,
  `frontend/src/pages/EntityDetailPage.tsx`, existing Relationship and
  Chronicles hooks/types, and the Chronicles episode drawer query state.
- Future API work: `roster/relationship/api/router.py` and its co-located
  models, reusing `butlers.core.commitments.list_entity_commitments` and the
  existing `_assert_owner_role` / `_assert_entity_exists` gates.
- No implementation, migration, configuration, deployment, provider, live
  data, or Beads lifecycle mutation is part of this proposal.

Tests: +0 ~0 -0 for this spec-only prerequisite.
