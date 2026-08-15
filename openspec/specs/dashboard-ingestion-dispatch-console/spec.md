# dashboard-ingestion-dispatch-console

## Purpose

`/ingestion` is the operator's audit surface for external signals. This
capability is the binding page-level contract for that surface in the Dispatch
visual language. It defines first-class ingestion routes — a Timeline ledger, a
Connectors roster, a per-connector detail page, and a Filters pipeline view —
rendered with bespoke hairline layouts rather than card/table/tab chrome.

The redesign prototype has graduated into shipped `frontend/` code; this
capability is now the long-lived contract (the binding design language and
handoff are preserved at `openspec/specs/dashboard-design-language/spec.md` (the Dispatch spec) and
`docs/redesigns/ingestion-handoff.md`). The contract requires real data behind every surface (no stubbed,
synthetic, or forever-loading sections), audited raw-payload access, explicit
data states (loading, empty, partial-error, unavailable), and committed visual
and route verification evidence before closure.

The legacy tabbed `IngestionPage` shell (behind the now-removed
`INGESTION_DISPATCH_CONSOLE` flag) was deleted once this surface became the
unconditional default (bu-4utdw.2). The owner explicitly accepted losing the
legacy-only capabilities that were never ported: the backfill job manager tab,
the fanout distribution matrix, the volume time-series chart (and its 30d
period option), the tier-breakdown donut, and the legacy connector-card
delete button. Thread-affinity settings and Gmail label filters (previously
in the legacy Filters tab) are also not yet present in the Filters Pipeline
below; their source component is preserved unmounted pending a rehoming
follow-up discovered from bu-4utdw.2. See
`docs/frontend/feature-inventory.md` §"Ingestion" for the full accounting.

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
`openspec/specs/dashboard-design-language/spec.md` and
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

### Requirement: Connectors Roster

The `/ingestion/connectors` route SHALL render every listening channel as a
dense roster, not as a card grid.

It SHALL include:

- attention strip when any connector has auth issues, health issues, or an
  additive operational warning;
- rows with health dot, channel glyph/name/kind, function gloss, last-event
  meta, 24h sparkline, auth pill, event/session/cost totals, and disclosure;
- dormant or available connector section with connect actions;
- footer KPI band and add-connector action.

The whole row SHALL be the navigation target to connector detail (click or
keyboard Enter/Space while the row has focus), with the disclosure chevron
kept as a visual cue rather than a separate click target. When a row's auth
pill reads `reauth`, the pill itself SHALL use the typed connector recovery
resolver (see "Ingestion-Originated OAuth page_of_origin Contract"), and it
remains independently clickable above the row's navigation target.

#### Scenario: Connector with auth issue appears in attention strip

- **WHEN** at least one connector has `auth.status` requiring action or
  degraded health
- **THEN** the roster renders a compact attention strip above the table
- **AND** each attention item links to the affected connector detail route
- **AND** the connector row displays the same auth state consistently
- **AND** if the row's auth status is `needs_reauth`, its auth pill is itself
  a reauth action rather than a static label

#### Scenario: Dormant connectors are discoverable

- **WHEN** the connector discovery endpoint reports available but unconnected
  connectors
- **THEN** the roster renders an `available` or `dormant` section
- **AND** each dormant row includes the connector's display name, its channel,
  and a connect action
- **AND** the connect action links to `/secrets?focus=u:<provider>` (the
  catalog's own `provider` field), deep-linking straight to that provider's
  credential entry instead of the bare `/secrets` page
- **AND** no per-row description line restates the section eyebrow (the
  discovery catalog carries no per-connector one-liner to show instead)

#### Scenario: Operational warning does not rewrite connector health

- **WHEN** a connector summary carries an `operational_warnings` entry while
  its transport state and liveness remain healthy/online
- **THEN** the connector appears in the attention strip with an operational
  warning label
- **AND** the full warning is readable on the roster row
- **AND** the row's health verdict remains online rather than being rewritten
  as degraded or error
- **AND** if the diagnostic source's additive availability flag is explicitly
  `false`, the roster names that degraded source instead of treating missing
  warnings as an all-clear

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

Any recovery control initiated from `/ingestion/connectors` SHALL first resolve
the connector's real recovery capability through one shared typed resolver.
The resolver SHALL be an allowlist: a `connector_type` is registry data, not an
OAuth provider identifier, and SHALL NOT be interpolated into an OAuth URL.

The resolver SHALL return exactly one of the following outcomes:

- **Generic Google OAuth:** `google`, `gmail`, `google_calendar`,
  `google_drive`, and `google_health` use the registered `google` OAuth
  provider. Google Health SHALL include `scope_set=health`.
- **Connector-owned Passport:** `spotify` navigates in-app to
  `/secrets?focus=u:spotify`. Its Passport projection owns the connect or
  reconnect control and delegates that control to the Spotify connector PKCE
  endpoint; it SHALL NOT construct a generic OAuth URL.
- **Passport pairing:** `whatsapp` and `whatsapp_user_client` navigate in-app
  to `/secrets?focus=u:whatsapp`; they SHALL NOT construct an OAuth URL.
- **Unsupported:** every other or unknown connector type renders a clear
  unavailable explanation with no recovery link and no network request.

The API SHALL normalize the scope carrier's stored
`expired | rotation-needed` → `needs_reauth` before this typed recovery
resolver runs, while preserving the stored cause as `auth.recovery_reason`.
Only the normalized `needs_reauth` SHALL create an interactive recovery
control. Generic Google OAuth then follows the registered generic flow;
Spotify follows its connector-owned Passport/PKCE flow. `unsupported`
non-OAuth or unknown connector types remain unavailable with no recovery link
and no network request. Other auth states remain informational.

A generic Google OAuth outcome SHALL stamp `page_of_origin=ingestion` in the
OAuth state token by passing it as a query parameter to
`GET /api/oauth/google/start`. It SHALL preserve an available
`connector_detail_path`, and it SHALL preserve `force_consent` when the
initiating surface requests fresh consent. Spotify's connector-owned PKCE
state and return target are not generic OAuth state.

This requirement is **co-owned** with the in-flight `redesign-secrets-passport` change,
which defines the `/secrets`-side callback behaviour for generic Google OAuth.
This change owns the `/ingestion/connectors`-side contract.

The generic Google OAuth callback handler (specified in
`redesign-secrets-passport §dashboard-api §OAuth Per-Provider Generalisation`)
routes the post-dance redirect based on `state.page_of_origin`. For this
Google contract to function:

1. The ingestion reauth initiation MUST pass `page_of_origin=ingestion` as a query
   parameter to `GET /api/oauth/google/start`.
2. The OAuth state token MUST carry `page_of_origin` through the dance (the
   `redesign-secrets-passport` change extends `_StateEntry` and `_store_state` to
   support this field; this change may not land before that extension is in place).
3. The callback MUST redirect to `/ingestion/connectors` when `state.page_of_origin`
   is `ingestion` (callback routing table is defined in `redesign-secrets-passport
   §dashboard-api`; no duplication required here).

**Authority boundary:** The generic provider surface is Google-only in
production. Spotify SHALL NOT be a registered generic OAuth provider, SHALL
NOT expose `/api/oauth/spotify/*`, and SHALL NOT use generic callback routing.
The serialized `bu-fj7lx` then `bu-3ifcj` implementation lane reconciles the
transitional source to this canonical contract; it does not authorize a
generic Spotify compatibility alias.

#### Scenario: Ingestion Google reauth stamps page_of_origin

- **WHEN** the owner clicks the reauthorize action on a connector detail page under
  `/ingestion/connectors` for a Google-backed connector
- **THEN** the generic Google OAuth recovery outcome calls
  `GET /api/oauth/google/start?...&page_of_origin=ingestion`
- **AND** the OAuth state token carries `page_of_origin=ingestion` through the dance
- **AND** on successful OAuth callback the browser is redirected to `/ingestion/connectors`
  (NOT to `/secrets`)

### Requirement: Connector-owned Spotify recovery

Spotify recovery from `/ingestion/connectors` SHALL open the content-blind,
connector-owned Passport projection at `/secrets?focus=u:spotify`. The
projection's action SHALL call `POST /api/connectors/spotify/oauth/start`,
and the connector callback is `GET /api/connectors/spotify/oauth/callback`.
This is the sole production Spotify recovery route. Spotify access and refresh
tokens are identity-bound RFC 0006 Tier 2 credentials: the connector-owned
callback stores them in `public.entity_info` on the owner entity, and
connector/runtime reads use `resolve_owner_entity_info()`. The Passport
projection presents closed recovery state only; it is not a secret authority.

#### Scenario: Spotify recovery enters Passport before PKCE

- **WHEN** the owner clicks a Spotify recovery control from
  `/ingestion/connectors`
- **THEN** the dashboard SHALL navigate in-app to `/secrets?focus=u:spotify`
- **AND** it SHALL NOT invoke or construct `/api/oauth/spotify/start` or any
  generic OAuth Spotify URL
- **AND** the Passport projection SHALL render only its content-blind
  connector-owned state and capability evidence before the owner chooses a
  connector action

#### Scenario: Passport action delegates to the connector

- **WHEN** the owner chooses the Spotify projection's connect or reconnect
  action
- **THEN** the action SHALL call `POST /api/connectors/spotify/oauth/start`
- **AND** the resulting callback SHALL be handled by
  `GET /api/connectors/spotify/oauth/callback`
- **AND** the callback SHALL persist identity-bound access and refresh tokens
  only to the secured owner `public.entity_info` authority, with subsequent
  reads through `resolve_owner_entity_info()` rather than `CredentialStore`
- **AND** neither action SHALL create or use a generic OAuth Spotify state
  entry, route, or callback

#### Scenario: WhatsApp recovery opens Passport pairing

- **WHEN** a `needs_reauth` WhatsApp or WhatsApp user-client connector requests recovery
- **THEN** the dashboard SHALL navigate in-app to `/secrets?focus=u:whatsapp`
- **AND** it SHALL NOT construct or request `/api/oauth/whatsapp/start` (or an
  OAuth URL derived from the connector type)

#### Scenario: Unsupported connector recovery is static and truthful

- **WHEN** a `needs_reauth` connector has no registered generic OAuth,
  connector-owned Passport, or Passport-pairing recovery capability
- **THEN** the dashboard SHALL render an unavailable explanation
- **AND** it SHALL render no recovery link or button
- **AND** it SHALL issue no recovery network request

#### Scenario: Non-reauth status is not a recovery action

- **WHEN** a connector auth state is not `needs_reauth`
- **THEN** its roster and detail status remain noninteractive

#### Scenario: Post-recovery connector state reflects new credential

- **WHEN** a generic Google OAuth callback redirects back to
  `/ingestion/connectors`, or the owner later returns there after a successful
  Spotify connector callback
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

### Requirement: Connector Archive Review Queue

The connectors surface SHALL offer a flag-only archive REVIEW QUEUE that
suggests superseded endpoint identities for archiving, without ever
auto-archiving them. `GET /api/ingestion/connectors/summaries` SHALL compute a
read-only `archive_candidate` boolean per connector, `true` only for an active
(non-archived) identity that BOTH:

- last heartbeated strictly more than 30 days ago, AND
- has at least one other identity of the same `connector_type` that is currently
  `online` and not archived (a "newer online sibling").

The queue is a SUGGESTION and SHALL NOT change the fleet signal:

- `archive_candidate` SHALL NOT contribute to the fleet-health rollups
  (`GET /api/ingestion/connectors/cross-summary`,
  `GET /api/switchboard/connectors/summary`) or to alerting — those exclude only
  `archived` identities.
- A candidate SHALL remain in the active roster with its true (offline) liveness
  and SHALL NOT be filed as merely an archive candidate; a genuinely-failing
  live connector (not offline for 30+ days) SHALL never be flagged.
- The degraded-mode envelope (`aggregates_available` /
  `device_liveness_available` / `hourly_events_available`) SHALL keep its
  existing shape and genuine-failure-only semantics.

The dashboard SHALL surface candidates as a review queue distinct from the
active roster and the archived section, each candidate offering a one-click
archive that reuses the existing audit-logged archive endpoint
(`POST /api/ingestion/connectors/{type}/{identity}/archive`) — archival stays a
human action.

#### Scenario: Offline identity with a newer online sibling is a candidate

- **WHEN** an active identity last heartbeated more than 30 days ago **AND**
  another identity of the same `connector_type` is currently `online`
- **THEN** the `summaries` endpoint flags that identity `archive_candidate: true`
- **AND** the roster lists it in the archive review queue with a one-click
  archive action wired to the archive endpoint
- **AND** the identity still appears in the active roster with its true offline
  liveness

#### Scenario: Quiet identity with no online sibling is not a candidate

- **WHEN** an active identity is offline for more than 30 days but no other
  identity of the same `connector_type` is currently `online`
- **THEN** it is NOT flagged `archive_candidate`, so a merely-quiet connector is
  never suggested for archiving

#### Scenario: Review queue does not affect fleet health

- **WHEN** the fleet-health rollup endpoints aggregate connector liveness
- **THEN** `archive_candidate` has no effect on the online/stale/offline counts
- **AND** the degraded-mode envelope flags are unchanged by the candidate
  computation

#### Scenario: Exactly 30 days offline is not yet a candidate

- **WHEN** an active identity's last heartbeat is exactly 30 days old
- **THEN** it is NOT flagged `archive_candidate` (the offline-age test is strict
  `> 30d`)

### Requirement: Archived Connector Identities

Superseded or dead connector endpoint identities SHALL be archivable via a soft
`archived_at` state on `connector_registry`, distinct from the `deleted_at`
disconnect soft-delete. An archived identity is retained (never deleted, because
ingestion history still references it) but is separated from the active fleet:

- The `/ingestion/connectors` roster SHALL group archived identities into a
  collapsed "archived" section, distinct from the active roster and the dormant
  section, with each archived row linking to that identity's connector detail so
  its history stays reachable.
- Archived identities SHALL NOT contribute to the active roster's attention
  strip or KPI band.
- The fleet-health rollups (`GET /api/ingestion/connectors/cross-summary` and
  `GET /api/switchboard/connectors/summary`) SHALL exclude archived identities
  from their online/stale/offline counts, so a permanently-offline superseded
  identity stops dragging fleet health down.
- Archiving SHALL be reversible (an unarchive path restores the identity to the
  active roster) and SHALL be a distinct state from `degraded`/`offline`:
  archiving SHALL NOT be applied to, and SHALL NOT mask, a genuinely-failing
  *live* connector, which remains in the active roster.

#### Scenario: Archived identity is grouped, not deleted

- **WHEN** a connector endpoint identity has `archived_at` set and `deleted_at`
  is null
- **THEN** the `summaries` endpoint still returns it, flagged `archived: true`
  with its `archived_at` timestamp
- **AND** the roster renders it in a collapsed "archived" section rather than an
  active row
- **AND** its row links to the connector detail route so events and incidents
  remain reachable

#### Scenario: Archived identities do not drag fleet health down

- **WHEN** the fleet-health rollup endpoints aggregate connector liveness
- **THEN** archived identities are excluded from the online/stale/offline counts
- **AND** a genuinely-failing live connector is NOT archived and still counts
  toward (and surfaces in) the fleet-health signal

#### Scenario: Degraded-mode envelope is unchanged by archiving

- **WHEN** the `summaries` or `cross-summary` endpoints respond
- **THEN** the `aggregates_available` / `device_liveness_available` /
  `hourly_events_available` degraded-mode flags keep their existing shape and
  genuine-failure-only semantics
- **AND** archiving never causes a genuinely-unreachable source to render as an
  honest empty/all-clear result

## Source References

- Non-Negotiable Rule 3 (MCP-only inter-butler communication)
- Non-Negotiable Rule 7 (transport and connectors are responsible for external APIs)
- RFC 0003 (Switchboard routing and ingestion)
- `about/heart-and-soul/design-language.md`
- `docs/redesigns/ingestion-handoff.md`
- `openspec/specs/dashboard-design-language/spec.md`
- `openspec/changes/archive/2026-05-19-redesign-ingestion-dispatch-console/`
- `openspec/changes/add-connector-oauth-scope-surface/`
- `openspec/changes/redesign-secrets-passport/specs/dashboard-api/spec.md`
  (generic Google OAuth callback endpoint and `page_of_origin` routing table)
- `openspec/changes/redesign-secrets-passport/specs/butler-secrets/spec.md`
  (generic-Google Cross-Page Reauth Bookkeeping requirement)
- `openspec/specs/dashboard-spotify-setup/spec.md` and
  `openspec/specs/butler-secrets/spec.md` (connector-owned Spotify Passport
  recovery and content-blind projection boundary)
