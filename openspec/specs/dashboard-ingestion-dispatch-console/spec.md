# dashboard-ingestion-dispatch-console

## Purpose

`/ingestion` is the operator's audit surface for external signals. This
capability is the binding page-level contract for that surface in the Dispatch
visual language. It defines first-class ingestion routes — a Timeline ledger, a
Connectors roster, a per-connector detail page, and a Filters pipeline view —
rendered with bespoke hairline layouts rather than card/table/tab chrome.

The redesign prototype has graduated into shipped `frontend/` code; this
capability is now the long-lived contract (the binding design language and
handoff are preserved at `docs/redesigns/ingestion-design-language.md` and
`docs/redesigns/ingestion-handoff.md`). The contract requires real data behind every surface (no stubbed,
synthetic, or forever-loading sections), audited raw-payload access, explicit
data states (loading, empty, partial-error, unavailable), and committed visual
and route verification evidence before closure.

## Requirements

### Requirement: Ingestion Dispatch Route Architecture

The dashboard SHALL expose the redesigned ingestion surface as first-class
routes, not as a page-level tab switcher.

The route hierarchy SHALL be:

- `/ingestion`: Timeline ledger.
- `/ingestion/connectors`: Connectors roster.
- `/ingestion/connectors/:connectorType/:endpointIdentity`: Connector detail.
- `/ingestion/filters`: Filters pipeline.

Legacy `?tab=timeline|connectors|filters|history` URLs SHALL redirect or
normalize into the route hierarchy while preserving compatible range, channel,
status, saved-view, and expanded-event query parameters. `history` SHALL map to
the Timeline route with an equivalent range or saved view; it SHALL NOT remain
a fourth redesigned tab.

#### Scenario: Timeline route replaces legacy tab landing

- **WHEN** the owner navigates to `/ingestion`
- **THEN** the dashboard renders the Timeline ledger route
- **AND** the page-level `Timeline`, `Connectors`, `Filters`, `History`
  tab-switcher is not rendered as the route architecture
- **AND** the ingestion sub-nav links to `/ingestion`, `/ingestion/connectors`,
  and `/ingestion/filters`

#### Scenario: Legacy connectors tab normalizes to roster route

- **WHEN** the owner opens `/ingestion?tab=connectors&range=24h`
- **THEN** the dashboard redirects or replaces history state to
  `/ingestion/connectors?range=24h`
- **AND** no compatible query parameter is discarded

#### Scenario: History tab normalizes to Timeline state

- **WHEN** the owner opens `/ingestion?tab=history`
- **THEN** the dashboard renders `/ingestion` with the closest equivalent
  Timeline range or saved view
- **AND** no `/ingestion/history` primary redesigned route is required

### Requirement: Dispatch Visual Language

The ingestion surface SHALL follow the Dispatch visual language from
`docs/redesigns/ingestion-design-language.md` and
`docs/redesigns/ingestion-handoff.md`.

The primary ingestion surfaces SHALL use hairline-divided, rhythm-based
layouts rather than card chrome. shadcn primitives MAY be used for low-level
behavior when appropriate, but the primary Timeline, Connectors, connector
detail, and Filters regions SHALL NOT be composed as visible shadcn `Card`
containers or the old page-level `TabsTrigger` control.

The surface SHALL preserve these visual contracts:

- mono uppercase eyebrows;
- tabular numeric cells;
- display headline only where the prototype calls for it;
- state colors as foreground or border signals, not broad background fills;
- butler hues only on letter marks;
- no emoji in interface chrome;
- empty states as one serif italic sentence.

#### Scenario: Old card shell is absent

- **WHEN** the owner loads `/ingestion` with event data available
- **THEN** the primary Timeline surface is a ledger with hour groups and
  hairline row separators
- **AND** it does not render a visible card headed `Ingestion Events`

#### Scenario: Typography and numeric cells are operational

- **WHEN** the owner views a ledger row, connector row, KPI strip, or pipeline
  gate count
- **THEN** counts, costs, durations, and token totals use tabular numerals
- **AND** status or section labels use the prototype's mono/eyebrow treatment

### Requirement: Timeline Ledger

The `/ingestion` Timeline SHALL render external events as a ledger stream.

It SHALL include:

- header band with eyebrow, live freshness/status pill, range-aware headline,
  one-sentence serif summary, and event/session/cost KPIs;
- sticky toolbar with range picker, search, saved views, channel chips, and
  status filters;
- bulk-action bar when rows are selected;
- hour-group headers with event count and cost rollup;
- ledger rows with selection, short request id, time, channel glyph, sender
  summary, pipeline flame/duration, token totals, cost, replay, and expand
  controls;
- in-place expanded drawer with step ledger, raw payload, replay history,
  request metadata, session index, and copy/open actions;
- footer rollup band for the active filter window.

#### Scenario: Event row expands into full request detail

- **WHEN** the owner expands an event row
- **THEN** an in-place drawer opens below that row
- **AND** the drawer includes a step-ledger tab for every session associated
  with the event
- **AND** each session block exposes status, session id, model, duration, cost,
  token totals, and step rows
- **AND** the drawer includes raw-payload and replay-history tabs
- **AND** the right rail exposes request metadata and a session index

#### Scenario: Raw payload access is audited

- **WHEN** the owner opens or downloads an event raw payload
- **THEN** the backend records an audit entry for that payload access
- **AND** the UI shows loading, unavailable, and permission/error states
  without exposing stale or partial PII as successful content

#### Scenario: Timeline URL opens an event drawer

- **WHEN** the owner loads `/ingestion?event=<event-id>`
- **THEN** the matching ledger row scrolls into view when present
- **AND** that row opens its drawer
- **AND** closing the drawer removes the `event` query parameter

### Requirement: Connectors Roster

The `/ingestion/connectors` route SHALL render every listening channel as a
dense roster, not as a card grid.

It SHALL include:

- attention strip when any connector has auth or health issues;
- rows with health dot, channel glyph/name/kind, function gloss, last-event
  meta, 24h sparkline, auth pill, event/session/cost totals, and disclosure;
- dormant or available connector section with connect actions;
- footer KPI band and add-connector action.

#### Scenario: Connector with auth issue appears in attention strip

- **WHEN** at least one connector has `auth.status` requiring action or
  degraded health
- **THEN** the roster renders a compact attention strip above the table
- **AND** each attention item links to the affected connector detail route
- **AND** the connector row displays the same auth state consistently

#### Scenario: Dormant connectors are discoverable

- **WHEN** the connector discovery endpoint reports available but unconnected
  connectors
- **THEN** the roster renders an `available` or `dormant` section
- **AND** each dormant row includes a purpose gloss and connect action

### Requirement: Connector Detail

The connector detail route SHALL render a two-zone operational detail page for
one connector endpoint.

It SHALL include:

- header band with large channel glyph, display headline, mono meta line, and
  purpose paragraph;
- reauth callout when the connector requires reauthorization;
- KPI strip, 24h histogram, recent events, and incident list;
- OAuth scope list when the connector supports OAuth scope introspection;
- schedule, routing rules, config fields, and safe action controls.

#### Scenario: Reauth callout follows connector auth state

- **WHEN** the connector detail response says auth requires reauthorization
- **THEN** the detail page renders a bordered reauth callout with explanatory
  copy and a reauthorize action
- **AND** successful reauthorization updates the auth state and clears the
  callout on refresh
- **AND** unsupported or unavailable OAuth scope state is rendered explicitly
  rather than hidden

#### Scenario: Scope list consumes the OAuth scope capability

- **WHEN** `connector-oauth-scope-surface` fields are available on the connector
  detail response
- **THEN** the detail page renders per-scope status, scope name, verdict, and
  explanatory note
- **AND** no access token, refresh token, or credential secret appears in the
  response or UI

### Requirement: Ingestion-Originated OAuth page_of_origin Contract

Any OAuth dance initiated from `/ingestion/connectors` SHALL stamp
`page_of_origin=ingestion` in the OAuth state token by passing it as a query
parameter to the begin endpoint (`GET /api/oauth/<provider>/start`).

This requirement is **co-owned** with the in-flight `redesign-secrets-passport` change,
which defines the `/secrets`-side callback behaviour. This change owns the
`/ingestion/connectors`-side contract.

The generalised OAuth callback handler (specified in `redesign-secrets-passport §dashboard-api
§OAuth Per-Provider Generalisation`) routes the post-dance redirect based on
`state.page_of_origin`. For this contract to function:

1. The ingestion reauth initiation MUST pass `page_of_origin=ingestion` as a query
   parameter to the OAuth start endpoint.
2. The OAuth state token MUST carry `page_of_origin` through the dance (the
   `redesign-secrets-passport` change extends `_StateEntry` and `_store_state` to
   support this field; this change may not land before that extension is in place).
3. The callback MUST redirect to `/ingestion/connectors` when `state.page_of_origin`
   is `ingestion` (callback routing table is defined in `redesign-secrets-passport
   §dashboard-api`; no duplication required here).

**Implementation gate:** SATISFIED. The generalised `GET /api/oauth/<provider>/start`
endpoint has landed (`src/butlers/api/routers/oauth.py`) and accepts `page_of_origin`.
The ingestion reauth callout calls it with `page_of_origin=ingestion`; no Google-only
fallback remains.

#### Scenario: Ingestion reauth stamps page_of_origin

- **WHEN** the owner clicks the reauthorize action on a connector detail page under
  `/ingestion/connectors`
- **THEN** the frontend calls `GET /api/oauth/<provider>/start?...&page_of_origin=ingestion`
- **AND** the OAuth state token carries `page_of_origin=ingestion` through the dance
- **AND** on successful OAuth callback the browser is redirected to `/ingestion/connectors`
  (NOT to `/secrets`)

#### Scenario: Post-reauth connector state reflects new credential

- **WHEN** the OAuth callback redirects back to `/ingestion/connectors`
- **THEN** the connectors roster and the previously-reauthorizing connector detail
  both reflect the updated auth state within the standard TanStack Query refresh
  interval
- **AND** the reauth callout is no longer rendered (auth state is now `ok`)

### Requirement: Filters Pipeline

The `/ingestion/filters` route SHALL explain how events earn dispatch through
the ingestion pipeline.

It SHALL include:

- header with event count and range;
- five-gate diagram for `accept`, `dedupe`, `tier`, `route`, and `execute`;
- honest proportional funnel that distinguishes drops from preserved events;
- one gate section per pipeline stage;
- rule rows grouped under the appropriate gate;
- code-resident behavior notes for stages without rules;
- priority senders data block;
- channel defaults data block;
- archived or disabled rules section;
- add-rule and open-DSL actions.

#### Scenario: Gate diagram explains losses and preserved events

- **WHEN** pipeline stats are available
- **THEN** each gate displays input count, output count, and any drop or
  preserve delta
- **AND** the route gate distinguishes preserved-without-dispatch events from
  hard drops
- **AND** the funnel proportions correspond to the returned counts

#### Scenario: Priority senders are data, not hidden rules

- **WHEN** priority contacts exist
- **THEN** the route renders them in a first-class priority senders block
- **AND** each row shows contact, channel, target butler, added timestamp, last
  seen state, and edit/remove controls
- **AND** mutations emit audit entries

#### Scenario: Channel defaults are explicit

- **WHEN** channel-default data exists
- **THEN** the route renders each channel's unmatched-event policy and note
- **AND** edits validate the per-channel schema before mutation
- **AND** mutation failures are visible and do not optimistically hide the
  previous policy

### Requirement: Data States and Robustness

Every ingestion redesigned surface SHALL have explicit loading, empty,
partial-error, and unavailable states. Skeletons may only be transient loading
states. A surface SHALL NOT be considered complete if it remains a skeleton or
fake fixture when live data is unavailable.

#### Scenario: Partial backend failure preserves usable sections

- **WHEN** the Timeline events endpoint succeeds but replay history fails for
  one expanded event
- **THEN** the ledger remains usable
- **AND** only the replay-history tab shows an error or unavailable state
- **AND** the error state identifies the failed surface

#### Scenario: Metrics unavailable is distinct from zero

- **WHEN** aggregate metrics cannot be loaded
- **THEN** KPI, sparkline, and pipeline surfaces render an unavailable state
- **AND** they do not render zero values unless the API explicitly reports zero

### Requirement: Visual and Route Verification

The ingestion redesign SHALL NOT be accepted without committed verification
evidence.

Verification SHALL include:

- route smoke coverage for all ingestion routes;
- legacy `?tab=` redirect coverage;
- component or DOM assertions that old card/tab shells are absent from the
  redesigned primary routes;
- desktop and mobile screenshots of the live implementation;
- prototype reference screenshots or a documented deterministic fallback if
  the prototype bundle cannot render in headless automation;
- an epic report mapping each prototype obligation to pass, deliberate
  deviation, or follow-up.

#### Scenario: Final reconciliation report gates closure

- **WHEN** the implementation beads are complete
- **THEN** a report under `docs/reports/` maps the prototype obligations to live
  evidence
- **AND** the report includes links or paths to screenshot artifacts
- **AND** any deliberate deviation has a spec-backed reason or an open follow-up
  bead
- **AND** the OpenSpec change is not archived until this report exists

## Source References

- Non-Negotiable Rule 3 (MCP-only inter-butler communication)
- Non-Negotiable Rule 7 (transport and connectors are responsible for external APIs)
- RFC 0003 (Switchboard routing and ingestion)
- `about/heart-and-soul/design-language.md`
- `docs/redesigns/ingestion-handoff.md`
- `docs/redesigns/ingestion-design-language.md`
- `openspec/changes/archive/2026-05-19-redesign-ingestion-dispatch-console/`
- `openspec/changes/add-connector-oauth-scope-surface/`
- `openspec/changes/redesign-secrets-passport/specs/dashboard-api/spec.md` (generalised OAuth callback endpoint and `page_of_origin` routing table)
- `openspec/changes/redesign-secrets-passport/specs/butler-secrets/spec.md` (Cross-Page Reauth Bookkeeping requirement)
