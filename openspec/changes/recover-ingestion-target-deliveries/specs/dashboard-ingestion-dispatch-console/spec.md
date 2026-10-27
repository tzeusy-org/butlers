## MODIFIED Requirements

### Requirement: Timeline Ledger

The `/ingestion` Timeline SHALL render external events as a ledger stream.
- It SHALL include:
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

ID: REQ-dashboard-ingestion-dispatch-console-001
Source: heart-and-soul/vision.md Rule 3; design.md Decision 5
Scope: v1-mandatory

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
  destructive-red treatment as `error`; its recovery action SHALL remain
  disabled when no durable eligible target-delivery work can be queued and
  SHALL never relabel the row `ingested` merely because it was invoked

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

## ADDED Requirements

### Requirement: Target delivery recovery is legible and actionable

The Timeline SHALL expose the server-derived state of each non-dashboard ingestion-to-domain target delivery and offer recovery only when a durable eligible intent can be queued. It SHALL show waiting, accepted, ambiguous, and terminal failure distinctly, with an accessible explanation and a stable response to repeated actions.

ID: REQ-dashboard-ingestion-dispatch-console-002
Source: heart-and-soul/vision.md Rule 3; design.md Decision 5
Scope: v1-mandatory

#### Scenario: Partial fan-out has no false all-clear

- **WHEN** an event has one accepted target and another waiting, ambiguous, or terminally failed target
- **THEN** the row and drawer SHALL show the non-complete aggregate and per-target states
- **AND** the accepted target SHALL not display a retry action

#### Scenario: Ingested source may still have pending target work

- **WHEN** an `ingested` source row has one unresolved classified target
- **THEN** the Timeline SHALL show its non-complete delivery summary alongside the coarse source status
- **AND** the owner SHALL not have to infer delivery from the `ingested` word alone

#### Scenario: Recovery control follows server authority

- **WHEN** the owner activates recovery for an eligible failed target
- **THEN** the control SHALL promptly show pending feedback and SHALL resolve to the server's queued identity or an actionable refusal
- **AND** a repeat activation SHALL not queue a second intent or hide the first result

#### Scenario: Ambiguous and legacy failures explain their limits

- **WHEN** a target is ambiguous or a legacy failed event lacks durable recovery evidence
- **THEN** the control SHALL be disabled or absent with a concise, keyboard and screen-reader accessible reason
- **AND** the row SHALL remain visibly unresolved rather than presenting a successful replay

#### Scenario: Evidence read fails

- **WHEN** delivery state cannot be loaded while the ledger row remains cached
- **THEN** the drawer SHALL show unavailable recovery evidence and preserve the prior row without a false all-clear
