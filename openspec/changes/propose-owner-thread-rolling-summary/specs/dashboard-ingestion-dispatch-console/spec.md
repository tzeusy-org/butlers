# Draft candidate: private rolling-summary custody

Status: NOT ADOPTED. Future additions require exact review/adoption and owning integrations. Full current requirements and prior scenarios are retained.

## MODIFIED Requirements

### Requirement: Timeline Ledger

The `/ingestion` Timeline SHALL render external events as a ledger stream.

It SHALL include:

- header band with eyebrow, live freshness/status pill, range-aware headline,
  one-sentence serif summary, and event/session/cost KPIs;
- sticky toolbar with range picker, search, saved views (with a
  filters-diverged indicator and a re-apply/update path), status filter chips
  (the badge vocabulary exactly), and an "add channel" control alongside any
  active channel chips;
- bulk-action bar when rows are selected, including a select-all-visible
  action (capped at the bulk replay batch limit) and, on a replay-unsafe
  (409) rejection, a one-click action to deselect exactly the ineligible
  events;
- hour-group headers with an honest event/error/replay count sourced from the
  histogram endpoint (correct even when only some pages of that hour have
  loaded) and a status-stacked, clickable, keyboard-operable per-minute
  activity strip;
- ledger rows with time (leftmost column, mono `HH:mm:ss` via the shared Time
  primitive), a click-to-filter channel glyph, sender summary with an inline
  filter/error reason, quiet dot-and-word status, a per-butler dispatch-ticks
  cell, cost, and an expand control; a demoted selection checkbox (hidden by
  default, revealed on hover/focus or once selection mode is active); token
  totals live in the expanded drawer, not the row;
- in-place expanded drawer with step ledger, raw payload, replay history,
  request metadata, session index, and copy/open actions;
- footer rollup band for the active filter window.

After independent exact adoption and all owned custody integrations, for a private-summary turn only, the raw-payload door SHALL display only permitted source projection, never hydrated summary/provenance/private binding. GET /api/ingestion/events/{id}/payload and ingestion_event_get_payload exclude private material before whole-envelope formatting, byte count, truncation/download/audit/trace. Generic auth/audit is not exposure authority. Unknown designated copy state is omitted/unavailable, never stale/partial successful private content. Lists/detail/replay/export/search/SSE/copy/linked sessions share the rule. Existing raw source, drawer/a11y/status/lineage/audit/missing/degraded behavior stays; owning payload/frontend integration precedes summary use.

ID: REQ-dashboard-ingestion-dispatch-console-008
Source: dashboard-ingestion-dispatch-console § Timeline Ledger; private summary custody R1; RFC 0003/0007 unchanged wire/source identity
Scope: proposed-owner-gated


#### Scenario: Every ledger row can expand into full request detail

- **WHEN** the owner clicks or keyboard-activates (focus + Enter) any event
  row, regardless of its status — including `filtered` and `error` rows
- **THEN** an in-place drawer opens below that row
- **AND** the drawer includes a step-ledger tab for every session associated
  with the event
- **AND** each session block exposes status, session id, model, duration, cost,
  token totals, and step rows
- **AND** the drawer includes raw-payload and replay-history tabs
- **AND** the right rail exposes request metadata and a session index
- **AND** for `filtered` or `error` rows with no sessions, the drawer states
  the honest reason (skip-triage rule, filter reason, or dispatch failure)
  instead of a bare "no sessions" message

#### Scenario: Row status never renders as a filled pill

- **WHEN** the owner views the ledger
- **THEN** each row's status renders as a small dot plus a mono status word
  (state color as foreground/border only)
- **AND** no row renders a background-filled status badge
- **AND** the status word matches the badge vocabulary exactly: `ingested`,
  `skipped`, `filtered`, `error`, `failed`, `replay pending`,
  `replay complete`, `replay failed`
- **AND** `filtered` rows are visually de-emphasized (reduced opacity) rather
  than distinguished by a gray pill
- **AND** `filtered`/`error`/`failed` rows show their
  `filter_reason`/`error_detail` inline next to the sender, truncated with a
  title tooltip, instead of only on hover of the status control
- **AND** `failed` (a routing failure recorded after the event was already
  ingested — see `ingestion_event_mark_failed`) renders with the same
  destructive-red treatment as `error` and is replayable straight back to
  `ingested`, alongside `filtered`/`error`/`replay_failed`

#### Scenario: Ledger row shows a dispatch-ticks summary without opening the drawer

- **WHEN** the owner views a ledger row for an event with one or more butler
  sessions
- **THEN** the row's dispatch column renders one tick per session (from the
  list-provided, API-capped session summary), each tick's width proportional
  to that session's duration, with a minimum width and a total width bounded
  to the column
- **AND** a failed session's tick renders in the destructive color; other
  ticks render as a neutral foreground color (butler hue is not used on the
  tick fill, consistent with "butler hues only on letter marks"; the butler
  name appears in the tick's hover/focus tooltip instead)
- **AND** a trailing mono session count appears once more than one session
  fired
- **AND** the dispatch column as a whole is keyboard-focusable and activating
  it (click or Enter) opens the row's drawer at the sessions tab, without
  toggling any other row control
- **AND** an event with no sessions renders a muted em-dash instead of an
  interactive cell
- **AND** rendering the cell issues no additional network request beyond the
  events list response

#### Scenario: Raw payload access is audited

- **WHEN** the owner opens or downloads an event raw payload
- **THEN** the backend records an audit entry for that payload access
- **AND** the UI shows loading, unavailable, and permission/error states
  without exposing stale or partial PII as successful content

#### Scenario: Hour strip renders status-stacked activity and reads honestly

- **WHEN** the owner views an hour-group header
- **THEN** the header's event/error/replay counts come from
  `GET /api/ingestion/events/histogram` for that hour and its active
  filters, and are correct even when only some pages of that hour have
  loaded into the ledger
- **AND** the event and error counts include `failed` and `replay_failed`
  events (terminal failures recorded after ingestion or replay) — each counts
  as both an event and an error, the same as `error`, so it never silently
  vanishes from the honest hourly total
- **AND** the per-minute strip renders each minute as a status-stacked bar:
  ingested at a low foreground alpha, filtered/skipped at a lower foreground
  alpha, error/failed/replay failed together in the destructive color, replay
  pending in neutral, and replay complete in green
- **AND** a minute where every event errored, failed, or replay-failed renders
  as solid destructive color
- **AND** the strip exposes an `aria-label` summarizing the hour's activity
  instead of being hidden from assistive technology

#### Scenario: Hour strip minutes are keyboard-operable and route to the ledger or a scoped view

- **WHEN** the owner activates a minute in the strip (click or keyboard)
- **THEN** if a loaded ledger row falls within that minute, the ledger
  scrolls that row into view
- **AND** otherwise the ledger's window narrows to that exact minute,
  reflected in the URL like every other filter
- **AND** every minute is reachable via keyboard focus with a visible focus
  state
- **AND** hovering or focusing a minute shows its time and per-status counts

#### Scenario: Timeline URL opens an event drawer

- **WHEN** the owner loads `/ingestion?event=<event-id>`
- **THEN** the matching ledger row scrolls into view when present
- **AND** that row opens its drawer
- **AND** closing the drawer removes the `event` query parameter

#### Scenario: Raw payload door never dereferences private context

- **WHEN** owner opens/downloads payload of an event whose runtime used private history
- **THEN** content/bytes/truncated/channel describe only permitted source projection and audit still precedes egress
- **AND** whole-envelope fallback/nested normalized_text expose no derivative/provenance/reference and perform no private resolution

#### Scenario: Unavailable copy custody is not successful payload content

- **WHEN** payload projection encounters unclassified/retired designated private state
- **THEN** private material is omitted or existing honest unavailable path used before serialization/truncation
- **AND** safe raw source/permission/audit/missing semantics remain without unsafe error echo
