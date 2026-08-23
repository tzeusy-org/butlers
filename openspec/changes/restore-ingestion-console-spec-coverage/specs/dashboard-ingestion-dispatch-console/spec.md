## MODIFIED Requirements

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

Normalization SHALL be implemented by stripping the `tab` key and carrying
every remaining query parameter through unchanged, rather than by an allowlist,
so a parameter added later cannot be silently dropped by a stale list. An
unrecognized `tab` value SHALL normalize to `/ingestion` rather than error. When
no `tab` parameter is present the Timeline SHALL render in place with no
navigation, so the compatibility shim cannot loop.

Every legacy normalization SHALL replace the current history entry rather than
push a new one, so the back button returns to wherever the owner came from and
not to the legacy URL that would immediately redirect again. In addition to the
`?tab=` shim, the following bookmark-compatibility routes SHALL exist and SHALL
likewise replace rather than push:

- `/ingestion/history` → `/ingestion`. This is a redirect only; there SHALL be
  no `/ingestion/history` page component.
- `/connectors` → `/ingestion/connectors`.
- `/connectors/:connectorType/:endpointIdentity` →
  `/ingestion/connectors/:connectorType/:endpointIdentity`, preserving the full
  query string.

The ingestion sub-navigation SHALL be route-driven links whose active state is
derived from the current route, not a tab component holding local selection
state.

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

#### Scenario: Unknown tab value normalizes rather than errors

- **WHEN** the owner opens `/ingestion?tab=<unrecognized>`
- **THEN** the dashboard normalizes to `/ingestion` with `tab` stripped

#### Scenario: Absent tab renders in place

- **WHEN** the owner opens `/ingestion` with no `tab` parameter
- **THEN** the Timeline renders without any navigation being issued

#### Scenario: Every non-tab parameter survives normalization

- **WHEN** a legacy URL carries query parameters beyond the compatible set
  named above
- **THEN** all of them are carried through to the normalized URL unchanged
- **AND** only the `tab` key is removed

#### Scenario: Normalization replaces rather than pushes

- **WHEN** any legacy path or `?tab=` URL normalizes
- **THEN** the current history entry is replaced
- **AND** pressing back does not return to the legacy URL

#### Scenario: Legacy connector paths redirect under /ingestion

- **WHEN** the owner opens `/connectors` or
  `/connectors/:connectorType/:endpointIdentity`
- **THEN** the dashboard replaces history state with the corresponding
  `/ingestion/connectors` path
- **AND** the full query string is preserved on the detail redirect

## ADDED Requirements

### Requirement: Filter control contract on sub-routes

Filter controls that write their selection into the URL SHALL do so through the
query string so a link can be shared and reloaded to the same view. The
parameter name `tab` SHALL NOT be reused for any filter, sort, or pagination
control; it is reserved for the legacy compatibility shim.

On the Timeline route the URL SHALL carry the time range, the free-text search
term, the selected channel set, the scoped-minute selection with its bucket
width, and the expanded event. Each SHALL be read back from the URL on mount so
a reload restores the same view. The free-text term SHALL be debounced before
being written so that typing does not produce a history entry per keystroke;
every other control SHALL write immediately. An empty search term SHALL remove
its parameter rather than write an empty value.

Filter writes SHALL push a history entry rather than replace one, so browser
back and forward step through the owner's filter changes. Housekeeping writes
that strip an inbound one-shot parameter — an OAuth error marker, a
trace-scoping deep link — SHALL replace instead, because they represent no
navigational intent.

Not all view state is URL-backed today, and this requirement records that
boundary rather than overstating it: the Timeline's status-chip selection is
component state, and the active saved view is persisted per-browser in local
storage. Neither appears in the URL, so a shared Timeline link reproduces range,
search, channels, scope, and open event but not the sender's status filter or
saved view. The Connectors roster, connector detail, and Filters pipeline routes
hold no URL-backed filter state at all. Closing those gaps is deliberately out
of scope here; this clause exists so a reader does not assume a shareability
guarantee the surface does not provide.

#### Scenario: Filter state preserved in URL

- **WHEN** the owner changes the range, search term, channel set, or minute
  scope on the Timeline
- **THEN** the URL query string is updated with the selected value
- **AND** reloading the page restores the same selection

#### Scenario: Tab key is not reused for filter state

- **WHEN** any filter, sort, or pagination control writes to the URL
- **THEN** the parameter name `tab` is not used

#### Scenario: Search term is debounced

- **WHEN** the owner types continuously into the Timeline search box
- **THEN** the URL is written once after the input settles, not once per
  keystroke

#### Scenario: Empty search removes its parameter

- **WHEN** the owner clears the search box
- **THEN** the search parameter is removed from the URL rather than set empty

#### Scenario: Filter changes are navigable

- **WHEN** the owner changes a URL-backed filter and presses back
- **THEN** the previous filter selection is restored

#### Scenario: One-shot parameter strips replace

- **WHEN** an inbound OAuth error marker or trace deep-link parameter is
  consumed and removed
- **THEN** the history entry is replaced rather than pushed

#### Scenario: Non-URL-backed state is documented, not implied

- **WHEN** a Timeline link is shared
- **THEN** the recipient sees the sender's range, search, channels, scope, and
  open event
- **AND** the recipient does not inherit the sender's status-chip selection or
  active saved view

### Requirement: Connector roster list summary-only polling

The Connectors roster SHALL populate its list from a single summary endpoint
and SHALL NOT mount a per-connector detail query for any row. A roster of N
connectors SHALL therefore cost one request per poll, not N. The summary
endpoint SHALL be database-sourced with no Prometheus dependency, so the roster
still renders when the metrics backend is down. Polling SHALL be on a fixed
interval; the roster SHALL NOT poll faster than once per 30 seconds.

The available-connector catalogue, which describes connector types rather than
live state, SHALL NOT be polled at all — it SHALL be fetched once and served
from cache for the duration of the view.

#### Scenario: Roster loads from the summary endpoint

- **WHEN** the Connectors roster mounts
- **THEN** it issues one request for the connector summaries
- **AND** it issues no per-connector detail request

#### Scenario: Roster polling interval

- **WHEN** the roster is left open
- **THEN** the summary query refetches on a fixed interval no faster than once
  per 30 seconds

#### Scenario: Roster renders without Prometheus

- **WHEN** the metrics backend is unavailable
- **THEN** the roster still renders, because every summary field is
  database-sourced

#### Scenario: Catalogue is fetched, not polled

- **WHEN** the available-connector catalogue is loaded
- **THEN** it is served from cache on subsequent renders with no refetch
  interval
