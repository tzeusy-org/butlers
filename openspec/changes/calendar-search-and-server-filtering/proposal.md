## Why

The calendar workspace read surface (`GET /api/calendar/workspace`) is
**time-range-only** today: it accepts `view`, `start`, `end`, `timezone`, and
the coarse `butlers`/`sources` filters, fans out across butler schemas, and
returns the **entire** `entries` list for the window in one
`ApiResponse[CalendarWorkspaceReadResponse]` payload — there is no pagination
and no server-side faceting. The list view hardcodes a 30-day window that can
return thousands of `UnifiedCalendarEntry` rows, and every status /
source-type / editable filter is applied client-side after the full payload
ships. There is also **no search**: finding an event by title, description, or
location is impossible without scrolling the grid.

Two gaps follow from this:

- **No full-text search.** Users cannot jump to an event by typing part of its
  title/description/location. The projection table `calendar_events` already
  stores `title TEXT NOT NULL`, `description TEXT`, and `location TEXT`, but
  there is no index or query path over them.
- **Unbounded, client-filtered reads.** The workspace read returns the whole
  window and filters `status` / `source_type` / `editable` in the browser,
  which does not scale and ships data the user never sees.

## What Changes

- **New search endpoint.** Add `GET /api/calendar/workspace/search?q=` that does
  full-text (substring/trigram) search over `calendar_events` `title`,
  `description`, and `location`, fanned out across butler schemas like the
  existing workspace read. It returns ranked `UnifiedCalendarEntry`-shaped
  matches with their dates so the UI can group by day and jump-to. Results
  respect the same lane (`view`) and `butlers`/`sources` scoping as the read
  endpoint. An empty/blank `q` returns no matches (not the whole calendar).
- **pg_trgm GIN index migration.** Add a core Alembic migration (next in the
  `core_*` chain) that ensures the `pg_trgm` extension and creates a GIN
  trigram index over `calendar_events(title, description, location)` in each
  butler schema so substring search is index-backed rather than a sequential
  scan. The migration is idempotent (`IF NOT EXISTS`) and reversible.
- **Server-side facets on the workspace read.** Extend
  `GET /api/calendar/workspace` with optional `status`, `source_type`, and
  `editable` query params applied **server-side** over the already-projected
  columns (instance/event status, the computed entry kind, and `s.writable`),
  replacing the client-side filtering for those facets.
- **Keyset (cursor) pagination on the workspace read.** Add `limit` and
  `cursor` params and an opaque `next_cursor` / `has_more` to the read response,
  following the repo's pagination convention (keyset order, no `total`). The
  workspace entries already order by `(starts_at, id)`; the cursor encodes the
  last `(starts_at, id)` seen. `has_more=false` means the last page.

## Capabilities

### New Capabilities

_None — this extends existing capabilities (calendar projection search +
the dashboard calendar workspace read surface)._

### Modified Capabilities

- `dashboard-api`: the **Calendar Workspace** read surface gains a new
  `GET /api/calendar/workspace/search` endpoint, server-side
  `status`/`source_type`/`editable` facets on `GET /api/calendar/workspace`,
  and keyset (cursor) pagination (`limit`/`cursor` →
  `next_cursor`/`has_more`, no `total`).
- `module-calendar`: the calendar projection gains a full-text search contract
  over `calendar_events(title, description, location)` backed by a pg_trgm GIN
  index, with defined empty-query and degraded (missing-extension/index)
  behavior.

## Impact

- **Migration (`alembic/versions/core/core_135_*`):** new core migration
  ensuring `pg_trgm` and creating the GIN trigram index over
  `calendar_events(title, description, location)`. No table/column change; no
  data backfill. Reversible (drops the index; leaves the extension).
- **API router (`src/butlers/api/routers/calendar_workspace.py`):** new
  `search` route; `get_workspace` gains `status`/`source_type`/`editable`/
  `limit`/`cursor` params and a paginated response envelope.
- **Read model (`src/butlers/api/read_models/calendar_workspace_v1.py`):** the
  fan-out query gains server-side `WHERE` predicates for the facets and a keyset
  `WHERE (starts_at, id) > (cursor)` clause with `LIMIT limit + 1`; a new
  search query function over `calendar_events`. Existing `WORKSPACE_COLUMNS` is
  unchanged (changing it is a breaking `v2` per the file's own contract).
- **Response model (`src/butlers/api/models/calendar_workspace.py`):**
  `CalendarWorkspaceReadResponse` gains `next_cursor: str | null` and
  `has_more: bool`; existing `entries`/`source_freshness`/`lanes` unchanged.
- **No LLM.** Pure DB-backed read/query work.
- **Frontend (out of scope here):** the search palette and the
  status/source_type facet UI consume these endpoints; the spec change is the
  backend contract.

## Out of Scope

- An optional `calendar_search` MCP tool for agent use (noted in the bead as
  optional; not specified here — this change is the HTTP + projection contract).
- Changing `WORKSPACE_COLUMNS` / the `UnifiedCalendarEntry` shape (a breaking
  change deferred to a hypothetical `calendar_workspace_v2`).
- Frontend implementation of the command-palette UI and facet dropdown.
- Ranking beyond trigram similarity / recency (semantic search, LLM ranking).
