## Context

Reading capture is the last of the three remaining `bu-27dxl.15` outcomes requiring a spec-first
prerequisite (the others are `bu-dlelt`/GitHub and `bu-o5lok`/YouTube — out of scope here). The
run-6 coordinator review retired the option of closing this outcome from existing primitives: the
`ReadingInferredAdapter` truthfully infers reading blocks from calendar titles and an optional
`health.facts` `reading_session` predicate, and its own docstring names Readwise/Pocket as a
"future extension path (not in v1)" — it is not, and was never meant to be, Readwise integration.

Official facts used to ground this draft (fetched 2026-09-09):

- Mozilla Pocket: shut down 2025-07-08, API transactions ended 2025-10-08
  (https://blog.mozilla.org/en/mozilla/building-whats-next/). No consumer or API path remains.
- Readwise public API docs (https://readwise.io/api_deets):
  - Auth: `Authorization: Token <access_token>` bearer header; validated via
    `GET /api/v2/auth/` → 204.
  - `GET /api/v2/export/` (the endpoint this connector uses): cursor pagination via `pageCursor`
    request param / `nextPageCursor` response field (iterate until null); `updatedAfter` (ISO 8601)
    for incremental sync; `ids` (comma-separated `user_book_id`) and `includeDeleted` (bool) filters.
    Readwise's own documented recommended pattern is "initial full sync with no parameters, then
    subsequent requests using `updatedAfter` with a stored timestamp."
  - `GET /api/v2/highlights/` (list endpoint — this connector does NOT use it, see D-below):
    page/page_size pagination, `updated__gt`/`updated__lt`/`highlighted_at__gt`/`highlighted_at__lt`
    filters, `book_id` filter.
  - Rate limits: base 240 requests/minute per token; `GET /api/v2/highlights/` and
    `GET /api/v2/books/` (the LIST endpoints, not export) are separately capped at 20/minute. A 429
    response carries `Retry-After`.
  - Dedup identity on the provider side: Readwise itself deduplicates highlight
    creation/re-submission by (title, author, text, source_url); this connector is read-only and
    does not create/update highlights, so provider-side dedup is not invoked, but it explains why a
    given `highlight_id` is stable across the highlight's lifetime.
  - Deletion: `DELETE /api/v2/highlights/<id>/` returns 204. Export responses carry
    `is_deleted: false` by default; `includeDeleted=true` is required to see deleted highlights.
    There is no separate tombstone/webhook feed — deletion is only observable by asking for it.

## Goals / Non-Goals

**Goals:**

- Ground every behavioral claim in Readwise's official public documentation or this repo's
  existing connector-base-spec / RFC 0003 / RFC 0004 contracts; no invented provider behavior.
- Specify a connector that satisfies every `connector-base-spec` obligation named in
  `bu-27dxl.15`'s acceptance criterion 5: ingest.v1 routing, idempotence, retry/replay,
  rate-limit/backoff, health/metrics, filtered-event buffering, account isolation (trivially, since
  this is single-account — see D2), credential absence/revocation, and truthful empty/unavailable
  behavior.
- Keep captured highlight/note text — the owner's private reading annotations — out of the
  LLM-classification path by default, mirroring the existing Spotify spoken-session and Steam
  status-change metadata-tier precedents.
- Make the subscription/token/content-consent execution gate explicit without letting it block
  drafting.

**Non-Goals:**

- No implementation: no code, migration, credential, dashboard, or provider call.
- No Pocket integration (retired — see RFC 0018 Amendment 1).
- No browser-history or manual reading-log capture.
- No writes back to Readwise (no highlight creation/update/deletion from this side).
- No full article/book body fetching — only highlight/note/location metadata Readwise already
  returns from the export endpoint.
- No fabricated reading-duration or completion inference from highlight creation/update. The
  existing `ReadingInferredAdapter`'s `duration_ms`-based signal is a different source and is
  unmodified.
- No decision here about whether captured highlight text should ever be exposed to an
  LLM-classified session (e.g. a future "discuss my reading" feature) — see "Reserved for Owner
  Review" below.

## Decisions

### D1: Connector, not a module-only integration

Reading capture is modeled as a standalone connector process implementing the full
`connector-base-spec` contract, not a butler-internal module poller. This follows the same shape
as every other external-provider integration (Steam, Spotify, Google Drive) and satisfies
`bu-27dxl.15` acceptance criterion 5 directly rather than invoking its "module-only, connector
obligations not applicable" carve-out. `[decision]` chose connector over module: reading capture is
an external-provider poll with its own auth/rate-limit/pagination lifecycle — exactly the shape
`connector-base-spec` exists for — and a module-only path would have to reinvent checkpointing,
heartbeat, and filtered-event buffering ad hoc. Reversible: yes (a future change could fold it into
a module, but nothing here forecloses that).

### D2: Single owner account only

Unlike Steam (`public.steam_accounts`, multi-account by design), Readwise is modeled as a single
owner-only account: one `readwise_token` on the owner's companion entity, one connector instance,
`endpoint_identity = "readwise:owner"`. `[decision]` chose single-account over Steam's
multi-account pattern: Readwise's own account model has no concept of shared/family libraries (a
token authenticates exactly one personal reading library), so multi-account plumbing would be
speculative infrastructure for a case that cannot occur. Reversible: yes, at the cost of a later
migration if that assumption is ever wrong.

### D3: Auth model mirrors `steam_api_key`, not Google OAuth

Readwise access tokens are long-lived bearer tokens (`readwise.io/access_token`), not an OAuth
flow. The credential is modeled exactly like `steam_api_key` in RFC 0004's registered
`entity_info` type table: `info_type = "readwise_token"`, `secured = true`, resolved via the
owner's companion entity through the existing `resolve_owner_entity_info()` pattern used by Steam
and Spotify. No refresh-token lifecycle, no scope negotiation, no re-consent flow — a revoked or
rotated token simply fails auth (see the "Credential Absence and Revocation" requirement).

### D4: Cursor uses the generic `cursor_store`, not a dedicated table

Unlike Steam (which needs a rich per-data-type state-diff snapshot in
`connectors.steam_cursors`), Readwise's incremental sync is a single opaque value: the
`updatedAfter` watermark. This uses the shared `cursor_store.save_cursor()` /
`cursor_store.load_cursor()` (`src/butlers/connectors/cursor_store.py`), keyed
`(connector_type="readwise", endpoint_identity="readwise:owner")`, `parent_endpoint_identity =
NO_PARENT` (the cursor key IS the connector's own runtime identity — same ownership shape as a
single-account connector's own checkpoint). The stored value is a small JSON object
`{"updated_after": "<ISO 8601>"}`.

### D5: First-poll baseline is a full backfill

On first connect (no stored cursor), the connector calls `/v2/export/` with no `updatedAfter`,
which Readwise's own documentation describes as the recommended initial-sync pattern, paginating
via `pageCursor` until `nextPageCursor` is null. `[decision]` chose full backfill over a
metadata-only or windowed baseline: this is the provider's own documented recommended integration
pattern, not an invented behavior, and reading highlights (unlike a high-frequency activity feed)
have no unbounded-flood risk — a personal Readwise library is bounded by years of manual
highlighting, not machine-generated events. Reversible: yes: a future change can add a bounded
window if a real library proves too large.

### D6: Update/dedup event identity uses Readwise's own `updated` timestamp

`event.external_event_id = "readwise:highlight:<highlight_id>:<updated>"`, where `<updated>` is
the ISO 8601 `updated` field Readwise returns on each highlight row (not connector-observed time).
An unchanged highlight re-polled at a later cursor produces an identical event ID (the Switchboard
dedup layer harmlessly no-ops it, per `connector-base-spec`'s "at-least-once delivery" contract). A
genuinely edited highlight carries a new provider-assigned `updated` value and is therefore a
distinct, truthful event — never a client-side guess. This also resolves the "equal timestamps"
tie-break concern from the shaping packet: because the id already includes the granular provider
`updated` value (not just the poll-boundary date), two highlights that happen to share an
`updatedAfter` page boundary still get distinct, individually-idempotent event IDs from their own
`updated` fields — no separate tie-break bookkeeping is needed beyond what `connector-base-spec`'s
existing dedup-key contract already provides.

### D7: Deletion is a bounded, separate reconciliation poll — not inferred from absence

Because the default export silently omits deleted highlights (they simply stop appearing — no
tombstone, no webhook), the connector cannot infer a deletion from one poll's absence without a
second explicit query. A periodic reconciliation poll (default: once per 24h, configurable) calls
`/v2/export/?includeDeleted=true&ids=<previously-captured user_book_ids, batched>` restricted to
already-captured book IDs, and any highlight now reporting `is_deleted: true` has its
`connectors.readwise_highlights` row tombstoned (`deleted_at` set). **No new ingest.v1 event is
submitted for a deletion** — a deletion is a retraction of previously captured evidence, not new
content, and fabricating a "deleted" content event would misrepresent what happened to any
downstream reader. `[decision]` chose a bounded reconciliation poll over either (a) always passing
`includeDeleted=true` on every incremental poll (would also return every already-tombstoned
highlight forever, unbounded growth) or (b) never detecting deletion at all (silently wrong once
the owner deletes a highlight from Readwise for a reason — e.g. accidental capture, a book removed
from their library). Reversible: yes, cadence is a config value, not a structural commitment.

### D8: Content tier keeps highlight text out of LLM classification by default

The `ingest.v1` envelope for each highlight event carries `control.ingestion_tier = "metadata"`
(`payload.raw = null`, `payload.normalized_text` limited to a short non-sensitive summary such as
`"Captured highlight from <book title>"`). A migration seeds a global `substring` policy rule on
the stable `readwise:highlight:` `external_event_id` prefix that pre-resolves `metadata_only`
triage — identical in shape to the Spotify `spotify:spoken:` rule
(`030_switchboard_spotify_spoken_metadata_only.py`) and the Steam `steam:status:` rule
(`025_switchboard_steam_status_skip.py`). The full highlight text, note, book title/author,
location, category, tags, and source URL are captured only in the connector-owned
`connectors.readwise_highlights` table, readable by `butler_chronicler_rw` /
`butler_education_rw` (whichever a future adapter needs) and writable only by
`connector_writer` — the same least-privilege ACL shape as `connectors.spotify_spoken_sessions`.
`[decision]` chose the metadata-tier/connector-owned-storage split over routing full highlight text
through Switchboard's normal Tier-1 path: highlight capture needs no LLM classification or butler
routing (there is no reply to send, no conversational intent to resolve — it is passive evidence
capture, structurally identical to Spotify's spoken-session and Steam's status-change cases), and
routing a personal reading annotation's full text through LLM classification by default would be a
privacy-relevant choice this draft is not authorized to make silently. Reversible: yes, and
explicitly flagged for owner review below rather than treated as foreclosed.

### D9: Rate limiting follows the base 240/min budget, not the 20/min LIST restriction

The connector exclusively uses `/v2/export/`, which Readwise's documentation does not list among
the endpoints separately restricted to 20 requests/minute (that restriction names `GET
/api/v2/highlights/` and `GET /api/v2/books/` specifically). The connector therefore budgets
against the base 240 requests/minute per token and, regardless of which budget actually applies,
honors any `Retry-After` header on a 429 response — so an incorrect assumption about which budget
governs the export endpoint degrades to correct behavior rather than a hard failure. Exponential
backoff (60s initial, doubling to a 3600s cap) applies on repeated 429/5xx, mirroring Steam's
existing backoff shape.

### D10: RFC 0003 / RFC 0004 amendments are proposed here, applied at implementation time

This draft proposes a `reading`/`readwise` RFC 0003 canonical channel/provider pairing and a
`readwise_token` RFC 0004 `entity_info` registered type (both specified fully in
`specs/connector-readwise/spec.md`), but does not hand-edit `0003-switchboard-routing-and-ingestion.md`
or `0004-identity-and-contact-resolution.md` themselves. Every existing amendment to those two RFCs
(RFC 0003 Amendments 1-2 for Home Assistant/ActivityWatch; RFC 0004 Amendment 2/3) was applied
together with the change that made the corresponding enum values real in code — amending the RFC
ahead of an implementation that does not exist yet would describe a `SourceChannel`/`SourceProvider`
Pydantic value that does not exist, creating exactly the spec-vs-code drift this repo's own
tooling (`check_spec_overwrites.py`, `check_archived_requirements_landed.py`) exists to catch. RFC
0018 is different: it is itself a scope/deferral record, so amending it now to say "spec-first" is
the record staying accurate, not drift. `[decision]` deferring the RFC 0003/0004 hand-edits:
reversible, and consistent with existing repo practice rather than a novel exception.

## Risks / Trade-offs

- [Readwise API behavior drifts from what the fetched docs describe] → the connector's own 429
  `Retry-After` handling and export-endpoint 5xx backoff degrade gracefully regardless; a future
  implementer should re-verify the endpoint/rate-limit facts in this design against current docs
  before writing code, since this draft's evidence is a point-in-time fetch (2026-09-09).
- [Reconciliation poll misses a deletion between cycles] → the row stays un-tombstoned for up to
  one reconciliation interval; this is a staleness bound, not a correctness failure, and is
  strictly better than never detecting deletion.
- [A future adapter wants highlight text in a butler session] → blocked by design (D8) until an
  explicit, separately-reviewed decision changes the tier; flagged below, not silently precluded
  forever.
- [Single-account assumption (D2) proves wrong] → requires a later migration to a
  `public.readwise_accounts`-shaped multi-account model; no data loss, since `endpoint_identity`
  would gain a suffix rather than change shape.

## Migration Plan (drafting-only — nothing below is executed by this change)

1. Owner provisions a Readwise account/subscription and generates an access token; the token is
   stored via the credential store as `readwise_token` on the owner's companion entity (D3).
2. A future implementation carrier applies: the core migration creating
   `connectors.readwise_highlights` (guarded `IF NOT EXISTS`, conditional grants, mirroring
   `core_195_spotify_spoken_sessions.py`), the Switchboard migration seeding the
   `readwise:highlight:` `metadata_only` policy rule (mirroring
   `030_switchboard_spotify_spoken_metadata_only.py`), and the RFC 0003/0004 amendments named in
   D10.
3. Connector deploys, runs the D5 full backfill, then steady-state incremental polling plus the D7
   reconciliation poll.
4. Rollback: stop the connector process first, then the migrations can be downgraded
   independently (dropping the evidence table does not affect any other connector's data; removing
   the policy rule does not affect any other channel).

## Reserved for Owner Review

These are explicitly **not** decided by this draft, per the decision-autonomy hard gates
(product/privacy/auth calls and the execution gate itself):

1. **The actual subscription/token/content-consent decision.** Nothing in this draft creates an
   account, generates a token, or reads any real highlight content. That is a separate owner act.
2. **Whether captured highlight text should ever be exposed to an LLM-classified session** (e.g. a
   future "ask about my highlights" feature). D8's default keeps it out; changing that default is
   a privacy-relevant product decision reserved for the owner, not inferred here.
3. **Whether the future Chronicler/Education projection adapter (reading
   `connectors.readwise_highlights` the way `ReadingInferredAdapter` already reads
   `google_calendar.completed`/`health.facts`) is a companion bead in the same implementation
   carrier or a separately scoped follow-up.** Left to the work-allocation cohesion scan named in
   `bu-27dxl.15` acceptance criterion 6, not decided here.
4. **The exact reconciliation-poll cadence** (default proposed: 24h) — an engineering default, not
   a hard gate, but named here since it trades staleness against API call volume and an owner may
   have an opinion once real usage is observed.
