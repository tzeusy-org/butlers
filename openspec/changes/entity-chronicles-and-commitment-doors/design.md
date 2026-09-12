## Context

The Calendar workspace keeps its owner-visible state in the URL. The current
keys are `view`, `range`, `anchor`, `source`, `calendar`, `status`, `kind`, and
`overlays`, while the page also preserves unknown query pairs when it updates
known keys. `MeetingPrepRail` already receives a required `entity_id` for each
resolved attendee but renders the name as text.

The baseline requires one unified ActivityTimeline and separately defines
`GET /api/relationship/entities/{id}/activity` as its Relationship-owned
aggregator. Current source still renders `ActivityTimeline` from the older
`/timeline` hook, while `/activity` supplies the merged Relationship and
Chronicler rows used by the entity activity bins. That pre-existing wiring gap
is neither resolved nor waived here, and the new preview must not be credited
as satisfying it. The aggregator calls `chronicler_list_episodes` through MCP,
returns content-blind episode rows, and marks an unavailable or unreadable
Chronicler contribution with `degraded=true` and
`degraded_reason="chronicler_activity_unavailable"`. This is the established
boundary and truthful failure vocabulary.

Commitments are active `public.owner_conditions` rows with
`metadata.class="commitment"`. The core
`list_entity_commitments(entity_id)` query already spans originating domains,
filters by `counterparty_entity_id`, and excludes resolved rows by default.
Meeting Prep has also established the safe render projection: `kind`,
`direction`, `summary`, nullable `deadline`, `L0` through `L3`
`escalation_level`, and `fingerprint`.

## Goals and Non-Goals

### Goals

- Make a resolved attendee a direct door to the canonical entity while
  retaining the Calendar URL context carried by the current query.
- Add a small, honest Shared Chronicles summary without creating a second
  history authority or cross-butler access path.
- Make the same safe commitment projection available on entity detail without
  requiring a calendar event.
- Keep every new read deterministic, owner-gated, content-bounded, and free of
  side effects.

### Non-Goals

- Changing commitment identity, lifecycle, confidence, escalation, or storage.
- Returning commitment opening or closing evidence, arbitrary metadata, or
  condition-ledger internals.
- Loading Chronicler's full episode payload into the entity summary.
- Changing the existing ActivityTimeline's default data set or pagination.
- Adding a new page, daemon job, MCP tool, LLM path, or mutation.

## Decisions

### Preserve the complete Calendar query on the attendee door

For a nonempty attendee `entity_id`, the link target is the canonical entity
path plus the complete current `location.search`, preserving every query pair,
including repeated and currently unknown keys, in its existing order. An empty
query produces no trailing `?`. The entity ID is URI encoded. Entity detail
continues to interpret only query keys it owns and ignores the retained
Calendar keys. A missing or blank ID produces plain attendee text and no link,
which avoids a broken canonical route if retained or test-supplied legacy data
bypasses the current response model.

This defines query preservation literally and avoids a second, lossy mapping
whose list of Calendar keys would drift as the workspace evolves. Browser Back
continues to restore the original Calendar URL through ordinary history.

### Reuse the activity aggregator for Shared Chronicles

The existing Relationship activity route gains optional
`source=all|relationship|chronicler`, defaulting to `all`. Invalid values fail
FastAPI query validation with HTTP 422 before source execution. Selection is
an execution boundary, not only a response filter:

| Selection | Executed contributions | Failure and empty semantics |
|---|---|---|
| `all` or omitted | Relationship and Chronicler | Relationship failure retains the existing non-2xx failure. Total or mixed-row Chronicler failure retains readable rows and sets the fixed Chronicler degraded state. Only two successful zero-row contributions are empty. |
| `relationship` | Relationship only; no Chronicler client or tool call | Relationship failure is non-2xx, never empty. A successful zero-row Relationship read is empty and cannot be degraded by Chronicler. |
| `chronicler` | Chronicler only; no Relationship activity-fact query | Total failure is degraded empty. Mixed readable/malformed evidence retains readable rows and is degraded. Only a valid zero-row Chronicler response is empty. |

Selection is applied before totals, bins, ordering, and pagination. The
omitted/default case otherwise keeps the current unified stream and response
shape byte-for-byte compatible at the field level.

Entity detail requests
`/relationship/entities/{id}/activity?source=chronicler&limit=5&offset=0`.
That response remains the existing unwrapped `ActivityResponse`:

```json
{
  "items": [
    {
      "id": "<uuid>",
      "ts": "<ISO-8601 timestamp>",
      "kind": "episode",
      "src": "chronicler",
      "predicate": null,
      "episode_id": "<same UUID as this row's id field>",
      "summary": "<corrected title or null>"
    }
  ],
  "total": 1,
  "limit": 5,
  "offset": 0,
  "degraded": false,
  "degraded_reason": null
}
```

`episode_id` is the Chronicler episode UUID and equals the `id` on its own
activity row. It never denotes the route entity UUID. `limit` keeps the
existing range `1..200` and default `50`; `offset` keeps the existing minimum
`0` and default `0`. The selected stream is ordered by
`ts DESC NULLS LAST, id DESC` before the slice so a stable data set produces
stable offset pages. `total` counts the selected readable rows before that
slice. When daily bins are requested, they are also computed from the complete
selected readable set before pagination; `limit`/`offset` affect stream items
only. `bins_only=true` retains the same execution and degradation semantics but
returns no item page. The summary intentionally renders only the first five
rows and offers no local pagination control. `total` remains available for
future navigation copy but is not presented as a completeness claim while the
response is degraded.

The Shared Chronicles section is a bounded preview of Chronicler rows already
present in the Relationship-owned aggregate. It does not replace the baseline
unified ActivityTimeline requirement, alter the current ActivityTimeline
wiring, change the default `/activity` response, or remove Chronicler rows from
the aggregate. Showing an episode once in the preview and once in the required
unified history is deliberate contextual duplication, not two authorities.

Relationship continues to obtain Chronicler rows only through
`chronicler_list_episodes(participant_entity_id=<entity-id>)` over MCP. The
entity page does not call `/api/chronicler/episodes`, and Relationship does not
query `chronicler.*`. This also keeps the summary projection limited to episode
ID, corrected title, and timestamp instead of exposing the full episode
payload.

The `source=chronicler` filter narrows the aggregator's output only after the
Chronicler contribution has been selected by `participant_entity_id`. It does
not substitute the legacy owner-only `entity_id` filter. Episodes therefore
remain eligible when the entity is linked as owner, organizer, or participant,
which preserves the original Shared Chronicles outcome for meeting attendees.
Implementation evidence must execute that boundary end to end for both
non-owner roles: seed one episode with an `episode_entities` organizer link and
one with a participant link, let the real
`chronicler_list_episodes` tool resolve both, and consume that tool result
through the Relationship source-filtered activity route. A parameterized test
over the two roles or two rows in one integration case satisfies the single
gate species. A pre-shaped episode list injected only at the Relationship test
seam is useful unit coverage but is not the required participant-filter
integration evidence.

### Make empty and degraded Chronicles states mutually exclusive

A valid `source=chronicler` response with `degraded=false` and `items=[]`
renders the exact empty copy `No shared episodes yet.` A response with
`degraded=true`, a transport or request error, a timeout, or an unreadable
response renders `SourceDegradedNote` for `Shared Chronicles` and suppresses
the empty copy. A non-object row, invalid or missing episode UUID, or missing
or unparseable selected timestamp makes the contribution degraded. The selected
timestamp is nonempty `canonical_start_at`, otherwise `start_at`. Such a row is
excluded, while other valid rows remain visible beside the degraded note;
their presence must not turn the response, `total`, or bins into a
complete-history claim. A null title remains valid and uses the established
fallback. The rest of entity detail continues to render and retry is limited
to this read.

Rows render the corrected summary, falling back to `Untitled episode` when it
is null, and the episode timestamp as a date. No payload, source reference,
participant UUID set, correction note, or privacy metadata is rendered in the
summary.

### Add a stable episode drawer door on the existing Chronicles route

A Shared Chronicles row links to
`/chronicles?date=<owner-local-YYYY-MM-DD>&episode=<episode-id>`. The date is
derived from the row timestamp in the same owner/browser timezone used by the
Chronicles page. `date` continues to select the day, and `episode` opens the
existing `EpisodeDrawer` for that exact ID. Closing the drawer removes only
`episode`, preserving `date` and all unrelated query pairs. An absent
`episode` parameter leaves the existing date-only page unchanged. A missing or
unreadable episode uses the drawer's existing error state and does not replace
the day page.

This adds query state to an existing drawer. It does not add a
`/chronicles/episodes/{id}` route or a second episode-detail component.

### Expose a minimal, read-only commitment response

`GET /api/relationship/entities/{id}/commitments` returns an unwrapped typed
response consistent with the Relationship activity route:

```json
{
  "items": [
    {
      "kind": "promise",
      "direction": "owner_to_other",
      "summary": "Send Sam the book",
      "deadline": "2026-09-08T00:00:00Z",
      "escalation_level": "L1",
      "fingerprint": "<stable commitment fingerprint>"
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0,
  "degraded": false,
  "degraded_reason": null
}
```

The endpoint accepts `limit` in `1..200` (default `50`) and `offset >= 0`
(default `0`). It selects rows where:

- `metadata->>'class' = 'commitment'`;
- `metadata->>'counterparty_entity_id'` equals the path entity UUID;
- `state IN ('open', 'aging')`; and
- `metadata->>'direction' IN ('owner_to_other', 'other_to_owner')`.

It spans every originating `source`, matching the core entity commitment query.
Rows are ordered by `first_detected_at DESC, fingerprint ASC` before pagination.
The response deliberately omits source, condition ID, episode number, state,
confidence, timestamps, `evidence_opened`, `evidence_closed`,
`resolution_reason`, `identity_payload`, and all other metadata. The rendered
rows reuse the Meeting Prep meanings `Owner owes` and
`Counterparty owes owner`; there is no create, resolve, edit, or retry-as-write
control.

### Fail closed on authorization and fail open honestly on read availability

The global dashboard API-key middleware remains the outer authentication
boundary. Both the source-filtered activity request and the commitments
endpoint also run the existing Relationship owner-role assertion before entity
or source data is returned. If owner status is absent or cannot be confirmed,
the current Relationship response is retained: HTTP 403 with
`{"code":"owner_required","message":"Owner entity not found"}`. An invalid
UUID remains FastAPI's HTTP 422. A confirmed unknown entity returns HTTP 404
with `{"detail":"Entity not found"}` through the existing entity-existence
helper.

For a known entity, an atomic commitment query that succeeds with no matching
rows returns HTTP 200 with `items=[]`, `total=0`, `degraded=false`, and
`degraded_reason=null`. A missing table, permission error, timeout, dropped
connection, or other commitment-source failure returns HTTP 200 with
`items=[]`, `total=0`, `degraded=true`, and the fixed content-blind reason
`commitment_source_unavailable`. No upstream exception or row content appears
in the response. Entity detail renders `SourceDegradedNote` for `Commitments`
and suppresses `No open commitments.` while degraded.

### Reads remain side-effect free under retries and concurrency

All three paths are reads. Repeated calls and TanStack Query retries do not
write state, invoke an LLM, send a notification, call a provider, or mutate a
commitment or episode. Identical requests against an unchanged data set return
the same ordered page. Offset pagination is intentionally the existing
Relationship convention: concurrent inserts may move a later offset page, and
the API does not claim snapshot-token or cursor isolation.

## Risks and Trade-offs

- The same episode appears in a small preview and the unified activity stream.
  The spec names that duplication so neither surface is mistaken for a new
  authority.
- Offset pagination may shift under concurrent writes. This preserves the
  current Relationship convention and is acceptable for an owner-operated
  read view; deterministic tie-breaking covers unchanged data.
- Owner-role assertion is an instance configuration gate rather than a
  multi-user session identity. This matches the current single-user system and
  does not introduce a new caller identity model.
- `bu-2jtfw.12` owns the Relationship router and endpoint tests. Implementation
  must begin from its landed tree and re-check the helper, response, and auth
  seams instead of merging concurrent router edits by assumption.
- Baseline requires the entity ActivityTimeline to render the unified activity
  aggregate, while current source still wires that component to `/timeline`.
  This proposal records but does not repair that pre-existing gap. `bu-bbwur`
  owns the correction independently and has no dependency on approval of this
  preview; it cannot be closed by pointing at the new preview.

## Rollback

Rollback removes the attendee link treatment, Shared Chronicles and
Commitments sections, the optional activity `source` filter, the commitments
endpoint, and the `episode` query handling. There is no migration, stored data,
backfill, configuration, or compatibility alias to reverse. The default
activity response, unified timeline, date-only Chronicles route, Meeting Prep
commitment rows, and commitment ledger remain as they were.

## Approval State

This document records a proposed contract, not implementation authority. The
owner approval must identify the exact reviewed commit or artifact digest.
Until that approval and a fresh independent semantic GO exist, no task below
the approval gate may begin and `bu-2jtfw.14` remains blocked on this spec.
