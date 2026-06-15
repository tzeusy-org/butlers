# Dashboard Relationship — Entity v3 View Depth Delta

This delta deepens the shipped entity views additively. It does not modify the existing route map, mode-toggle persistence, archetype assignments, token discipline, queue bucket definitions, Finder ranking rules, or the activity-aggregator MCP boundary — all of those requirements stand. It adds: the Workbench layout the toggle was always meant to reach, provenance *rendering* (the existing "Provenance contract" requirement covers the API; the shipped UI never rendered the fields — observed drift), quick-refresh affordances (sparkline, delta-since-last-visit, core dates), depth on Concentration/Hop/Columns/Finder/Index, keyboard maps, and the supporting endpoints. All rendering is deterministic; design-language hard-don'ts (no cards, no chartjunk, canned glosses only) bind every requirement below.

## ADDED Requirements

### Requirement: Workbench three-rail layout

Workbench mode at `/entities/:entityId?mode=workbench` SHALL render a three-rail layout (left context rail ~240px, middle column, right action rail ~280px) inside the existing `archetype="overview"` shell:

1. **Left rail**: top relations by weight; "introduced via" line (serif gloss, canned); "shares identifiers with" hint (amber, mono) listing entities with shared contact-fact values — clicking it opens the compare view for that pair (per `relationship-merge-review`).
2. **Middle column**: a four-cell KPI strip (`relations`, `touches 90d`, `sources`, `contacts`) in tabular nums; below it the raw facts view — the existing ProvenanceGrid requirement satisfied as a dense mono grid over **both stores** (identity triples and narrative facts, labeled by store), sortable by any provenance column.
3. **Right rail**: the curation action list (merge / promote / demote / edit aliases / edit contacts / archive / forget-in-red — same actions as Editorial's curation rail); below it the **confidence/staleness inspector**: per-fact 4px bars on two separate axes — `conf` (amber when < 0.85) and `staleness_band` (dim when stale) — never blended into one score.
4. **Duplicate warning panel**: when the entity's state is `duplicate-candidate`, a panel (amber 1px border) at the top of the right rail shows the deterministic evidence string and a commit button opening the compare view.

#### Scenario: Workbench is a real layout, not a re-skin
- **WHEN** the owner toggles to Workbench on an entity with facts in both stores
- **THEN** the three rails MUST render with the KPI strip, the sortable provenance grid, and the action rail
- **AND** the grid MUST label each row's store of origin

#### Scenario: Duplicate panel routes to compare
- **WHEN** the entity is a duplicate-candidate and the owner clicks the panel's commit button
- **THEN** the compare view for the evidenced pair MUST open (no direct merge without compare)

#### Scenario: Confidence and staleness are separate axes
- **WHEN** the inspector renders a fact with `conf = 1.0` observed 300 days ago
- **THEN** the confidence bar MUST render full and the staleness indicator MUST render `stale`
- **AND** no single blended "score" MUST be rendered

### Requirement: Provenance rendering in the UI

The shipped surface fetches provenance and renders none of it (observed drift against the existing Provenance contract requirement). Rendering SHALL be: **Workbench** — always-on (the grid + inspector above); **Editorial** — on-demand (a hover/expand affordance per fact row revealing `src`, `verified`, `observed_at`-derived staleness; the row chrome itself stays clean per the existing Editorial requirement); **Concentration** — each row carries its `src` and `verified` marks and a staleness dim treatment on `last_seen`. No view MAY invent provenance values; all render from the API fields that the Provenance contract already mandates.

#### Scenario: Editorial reveals provenance on demand
- **WHEN** the owner activates a fact row's provenance affordance in Editorial mode
- **THEN** `src`, `verified`, and the staleness band MUST be revealed for that row
- **AND** the default row chrome MUST remain free of provenance clutter

### Requirement: 90-day activity sparkline

The entity detail hero SHALL render a 90-day activity sparkline sourced from `GET /api/relationship/entities/{id}/activity?bins=daily&window=90d` (see endpoint requirement below): 90 vertical sticks, one per day; days with no activity render at 4% opacity (never collapsed out); no axes, no tooltips required, no charting-library chrome; tabular-num count caption. Absence of any activity in the window renders the canned serif line, not an empty chart.

#### Scenario: Sparkline renders quiet days honestly
- **WHEN** an entity has activity on 3 of the last 90 days
- **THEN** 90 sticks MUST render with 87 at 4% opacity
- **AND** no day MUST be omitted or interpolated

### Requirement: Delta-since-last-visit

The detail page SHALL surface what changed since the owner last looked: on load it calls `GET /api/relationship/entities/{id}/delta-facts` — facts in either store changed since the entity's view mark, computed per store as: identity store `GREATEST(created_at, updated_at) > marked_at`; narrative store `GREATEST(created_at, COALESCE(last_confirmed_at, created_at)) > marked_at` and renders a deterministic banner ("N new facts since <date>" — tabular nums, canned copy) plus a highlight treatment on the delta rows; after render it calls `POST /api/relationship/entities/{id}/view-mark` to upsert the mark. Backing table: `relationship.entity_view_marks` (DDL home: the `relationship-facts` delta). Both endpoints are gated by the existing owner-only authorization (standing clauses 12a/12b — `delta-facts` returns raw contact-fact values). No generated narration of the delta (binding rejection).

#### Scenario: Delta is read before the mark moves
- **WHEN** the owner opens an entity last marked 10 days ago with 2 facts asserted since
- **THEN** the banner MUST report 2 new facts and the rows MUST be highlighted
- **AND** the view mark MUST be updated only after the delta was computed for this load

#### Scenario: First visit has no banner
- **WHEN** an entity has no view mark row
- **THEN** no delta banner MUST render
- **AND** a mark MUST be created for subsequent visits

### Requirement: Latest interactions per channel

The detail page (both modes) SHALL render a latest-interactions block as a first-class quick-refresh section: the most recent interaction per channel/kind (message thread, call, in-person, email — as available from the existing interactions and message-thread tab endpoints, read-through to their current store), each row showing kind, deterministic summary, `occurred_at` with staleness treatment, and `src`. This resolves brief open question 9 by **read-through**: the block consumes the existing endpoints; store consolidation is explicitly not part of this change.

#### Scenario: Quick-refresh shows the latest touch per channel
- **WHEN** an entity has a Telegram thread from yesterday and an interaction logged 40 days ago
- **THEN** the block MUST render both rows, most recent first, with `occurred_at` and staleness treatment
- **AND** no generated summary prose MUST be produced at render time (stored summaries only)

### Requirement: Core dates block

The detail page (both modes) SHALL render a core-dates block as a first-class section — date-kind facts (`has-birthday`, anniversaries, and future date predicates from the registry) with the owner-relevant next occurrence, sourced from the facts API (server-extracted, not client-side string-matching on the generic facts list). Each date row carries provenance per the rendering requirement above.

#### Scenario: Birthday surfaces with next occurrence
- **WHEN** an entity has an active `has-birthday` fact
- **THEN** the core-dates block MUST render the date and the days-until-next occurrence in tabular nums

### Requirement: Concentration depth — bars, KPIs, drill

The Concentration view SHALL additionally render: (1) weight bars per row (width = `weight / max_weight × 100%`, 6px height, no animation) replacing the bare numeral presentation; (2) a footer KPI strip (`total touches`, `entity count`, `top entity`, `tail share` — the share held by entities below 1%) in a hairline-divided grid, tabular nums; (3) row click navigating to `/entities/:entityId` (the read-mode no-hover rule stands; click is the only affordance and the row cursor communicates it).

#### Scenario: Bars are proportional and quiet
- **WHEN** the view renders entities with weights 10 and 5 under the active predicate
- **THEN** the second bar MUST be half the width of the first
- **AND** no count-up or width animation MUST occur

#### Scenario: Row click drills to detail
- **WHEN** the owner clicks a concentration row
- **THEN** navigation MUST go to that entity's detail page

### Requirement: Neighbour ranking and truncation (Hop and Columns)

`GET /api/relationship/entities/{id}/neighbours` SHALL gain optional `rank=weight` and `per_predicate=<N>` parameters returning, per predicate group, the top-N neighbours by weight plus a `remainder` count of unreturned neighbours. This extends the existing endpoint with optional params — the standing Columns requirement's option (a) (client-side chaining of `/neighbours`, no new server endpoint) stands unchanged. Hop and Columns MUST request ranked, truncated groups (default N=6) and render the remainder as a "+N more" row (inert in v1 — the side-sheet expansion stays out of scope per the bundle recipe). Hop SHALL additionally render its breadcrumb trail (clickable past segments; `reset` pill at depth > 1), which the shipped view lacks.

#### Scenario: High-degree entity stays bounded
- **WHEN** a hop centre has 40 `knows` neighbours and `per_predicate=6` is requested
- **THEN** the response MUST contain the 6 highest-weight neighbours and `remainder: 34`
- **AND** the view MUST render "+34 more" as an inert row

#### Scenario: Hop trail is navigable
- **WHEN** the owner re-centres twice (owner → A → B)
- **THEN** the trail MUST render `owner › A › B` with `owner` and `A` clickable
- **AND** a `reset` pill MUST be present and absent again after reset

### Requirement: Facts drill endpoint

The dashboard API SHALL extend the **existing** `GET /api/relationship/entities/{id}/facts` endpoint with `predicate=` filter, `validity=` filter (default `active`), and `store=` selection, returning full provenance plus `staleness_band` per row from the identity store, and labeled narrative facts when `store=all` is requested. Pagination migrates from the shipped offset/limit to keyset (an intentional behavior change to align with the repo's cursor-pagination convention; the dashboard is the only consumer). The endpoint is gated by the existing owner-only authorization (standing clauses 12a/12b — it returns raw contact-fact values). This is the canonical fact-level read for the Workbench grid and Editorial provenance reveals; tab endpoints (`notes`, `interactions`, ...) remain unchanged.

#### Scenario: Drill defaults to active identity facts
- **WHEN** the endpoint is called with no filters
- **THEN** only `validity='active'` identity-store rows MUST return, with provenance and staleness on each

#### Scenario: Superseded history is reachable by filter
- **WHEN** `validity=superseded` is requested
- **THEN** superseded rows MUST return, enabling the Workbench grid's history view

### Requirement: Finder preview pane and Tab-to-hop

The Cmd-K Finder SHALL gain: (1) a right-hand preview pane for the active result — entity mark, name, type/tier, canned gloss, top-5 relations — inert (no links), sourced from data already in the search response plus at most one debounced `GET /entities/{id}/neighbours` call for the active row (permitted by the MODIFIED Finder requirement below); (2) `Tab` as "hop into" — dismissing the Finder and navigating to `/entities/hop?center=<active_id>`; (3) the keyboard footer documenting `↑↓ · ↵ open · ⇥ hop · esc`. Ranking rules are unchanged (deterministic, one search call per keystroke).

#### Scenario: Tab hops, Enter opens
- **WHEN** a result row is active and the owner presses `Tab`
- **THEN** the Finder MUST close and navigation MUST go to `/entities/hop?center=<id>`
- **WHEN** the owner presses `Enter` instead
- **THEN** navigation MUST go to `/entities/<id>` detail

### Requirement: Finder empty-query state — owner-pinned set

With an empty query, the Finder SHALL render the owner-pinned set: the owner's neighbours from the ranked `/neighbours` extension, aggregated cross-predicate — flatten the predicate groups, dedupe by entity (an entity reachable via multiple predicates appears once), sum `COALESCE(weight, 1)` across its edges, sort descending, take 8 (`me` excluded). Typing replaces the set with search results; clearing the query restores it. (Resolves design OQ1 with the bundle's weight-top-N reading.)

#### Scenario: Empty query shows the inner circle
- **WHEN** the Finder opens with no query
- **THEN** the top-8 owner neighbours by weight MUST render as the result list
- **AND** the owner entity itself MUST NOT appear

### Requirement: Index toolbar search uses the search endpoint

The Index toolbar search input SHALL query `GET /api/relationship/entities/search` (same deterministic ranking as the Finder) instead of client-side string filtering, debounced per keystroke. One search path serves both surfaces; results filter the Index table in place. (Resolves brief open question 7.)

#### Scenario: Toolbar search matches Finder semantics
- **WHEN** the owner types "alice@x.com" in the Index toolbar search
- **THEN** the table MUST filter to the entity holding that contact-fact value
- **AND** the match MUST come from the search endpoint, not a client-side substring pass

### Requirement: Index bulk-select gutter

The Index SHALL support row selection (checkbox/Space) materializing a slim gutter between toolbar and table when ≥ 1 row is selected: selected-count caption (mono), actions `archive`, `forget` (red), `merge` (enabled only at exactly 2 selected, routing through the compare view per `relationship-merge-review`), and `clear`. The gutter vanishes when selection empties. Bulk archive/forget confirm with the canned serif gloss before the destructive call.

#### Scenario: Gutter appears and constrains merge
- **WHEN** the owner selects three rows
- **THEN** the gutter MUST show count 3 with `merge` disabled
- **WHEN** the selection is reduced to two
- **THEN** `merge` MUST enable and clicking it MUST open the compare view

### Requirement: Queue evidence drill

Queue cards SHALL make their evidence actionable: duplicate-candidate cards render the shared value and peer names as links — the card's merge action opens the compare view for the pair; unidentified cards link to the entity's detail, and their standing merge action opens the compare view for the entity and an owner-selected target (per `relationship-merge-review` entry point 5); stale cards show the staleness age and link to detail. No card gains a second commit button (the one-commit-per-card rule stands).

#### Scenario: Duplicate card drills to compare
- **WHEN** the owner clicks merge on a duplicate-candidate card evidencing entities X and Y
- **THEN** the compare view for (X, Y) MUST open with the shared evidence pre-highlighted

### Requirement: Keyboard maps per view

The entity views SHALL implement these keyboard maps, attached to the focused list container (never global, so the app-wide `⌘K` and `/` retain priority): **Index** — `↑↓` cursor, `Space` toggle select, `Shift+↑↓` extend, `Esc` clear selection, `Enter` open detail; **Columns** — `↑↓` within column, `→` deepen on cursored row, `←` pop rightmost column, `Enter` open detail; **Hop** — `↑↓` cursor the relations pane, `Enter` re-centre, `Esc` pop the trail, `r` reset; **Detail** — `k/j` step siblings within the most recent list scope (Index order by default), `Esc` back to `/entities`, `m` open merge/compare when duplicate evidence exists. Focus states MUST be visible per the design language (2px left border, no glow).

#### Scenario: Columns navigates without a pointer
- **WHEN** the owner uses `↑↓` then `→` in column 0
- **THEN** a new column MUST open for the cursored neighbour
- **AND** `←` MUST close it again

#### Scenario: View-local keys never shadow the Finder
- **WHEN** the owner presses `⌘K` while an Index row is focused
- **THEN** the Finder MUST open (view-local handlers MUST NOT consume it)

### Requirement: Extracted entity UI primitives

`EntityMark`, `Row`, `TierBadge`, and `StateDot` SHALL exist as single-source components under `frontend/src/components/ui/`, consumed by Index, Hop, Columns, Concentration, Detail, and Finder. The shipped inline duplicates (Index and Hop each carry a private mark implementation) MUST be replaced by the shared components; review MUST reject new inline copies.

#### Scenario: One mark implementation serves all views
- **WHEN** the component tree is searched for entity-mark rendering logic
- **THEN** exactly one implementation MUST exist, under `frontend/src/components/ui/`

### Requirement: Activity binning parameter

`GET /api/relationship/entities/{id}/activity` SHALL gain optional `bins=daily&window=90d` parameters returning `{bins: [{date, count}]}` alongside (or instead of, when `bins_only=true`) the merged stream. Chronicler rows continue to arrive exclusively via chronicler MCP tools; the existing chronicler-boundary guardrail test MUST be extended to cover the binning code path.

#### Scenario: Binning stays behind the MCP boundary
- **WHEN** the binned activity endpoint executes
- **THEN** chronicler data MUST arrive via `chronicler_list_episodes` MCP calls only
- **AND** the boundary guardrail test MUST cover the binning implementation

## MODIFIED Requirements

### Requirement: App-wide Cmd-K Finder

The dashboard SHALL expose an app-wide command palette opened via `⌘K` (macOS) / `Ctrl-K` (other platforms) on any page. The Finder MUST:

1. Hit exactly one endpoint per keystroke for the **result list**: `GET /api/relationship/entities/search?q=<query>`. No surface other than the Index toolbar search (see Requirement: Index toolbar search uses the search endpoint) MUST call this endpoint to assemble result lists. **Exception (v3):** the preview pane MAY additionally issue at most one debounced `GET /api/relationship/entities/{id}/neighbours` call for the currently active result row (and the empty-query owner-pinned set MAY use the same ranked neighbours endpoint); no other relationship endpoint MAY be called from the Finder.
2. Resolve entities first, then other record kinds (per Phase 1 Open Question 14).
3. Show results in <300ms for local datasets (Brief §0 success criterion).
4. Search across: entity canonical_name, aliases, contact-fact values (`has-email | has-phone | has-handle | has-address`), and predicate labels.
5. Render keyboard-driven (arrow keys navigate; Enter opens detail; Tab hops; Esc closes).
6. Render kbd capsules in mono (KbMono primitive).

**Ranking is rule-based per `prompts/07-finder.md §7.5`:**
- Exact prefix match on canonical_name → score 100
- Substring match on canonical_name → score 80
- Exact match on alias → score 70
- Match on contact-fact value (email/phone/handle/address) → score 70
- Substring match on predicate label → score 30
- Tie-break by `lastSeen DESC`, then `tier ASC`.

**No embedding service, no reranker LLM, no model call at any stage of Finder ranking in v1** — see Requirement: Finder is deterministic.

**Reconciliation against existing `/api/search`:** the top-level `/api/search` (RFC 0007:122) returns a grouped `SearchResults` shape covering sessions/state/contacts. The entity Finder endpoint is intentionally separate — scoping ranking logic to the relationship butler preserves schema isolation. The top-level `/api/search` MAY later add an `entities` group that fans out to this endpoint, but that is out of scope here.

#### Scenario: Finder returns ranked entities within 300ms
- **WHEN** a user presses ⌘K from any page and types "alice"
- **THEN** the Finder MUST call `GET /api/relationship/entities/search?q=alice` exactly once per keystroke for the result list
- **AND** results MUST render in <300ms for a local dataset of <10000 entities
- **AND** entities MUST appear before other result kinds

#### Scenario: Finder matches contact-fact values
- **WHEN** the query is "alice@example.com" and a triple `(entity=X, has-email, "alice@example.com")` exists
- **THEN** entity X MUST appear in the results with `matchedOn: "has-email"` populated

#### Scenario: Preview is the only extra call
- **WHEN** the owner steps through five results with arrow keys
- **THEN** the Finder MAY issue at most one debounced neighbours call per active-row change
- **AND** no relationship endpoint other than `search` and `neighbours` MUST be called from the Finder
