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

Changing the range, committing a settled debounced search (including clearing
it), changing the channel set, selecting or clearing a minute scope, and
opening or closing an event drawer SHALL push a URL history entry. Explicitly
clearing the trace filter SHALL also push an entry. Browser back SHALL return
to the preceding URL selection; mount or reload SHALL hydrate its controls as
specified above. This does not assert additional same-mounted popstate
synchronization for local range or search state.

The status-chip and active saved-view mirroring effect SHALL replace the
current entry. That mirroring creates no back step for the immediately prior
mirrored statuses or view identity. A custom saved view may separately apply
range, search or channels through the push interactions above; its subsequent
statuses/view mirror still replaces that resulting entry. Legacy route/tab
normalization and consumed OAuth markers SHALL likewise replace the current
entry, rather than add a redirect or one-shot-marker back step.

The Timeline SHALL also carry `statuses` and `view` in the URL, parse them on
mount, and mirror subsequent changes with replacement history. Default
statuses and the default `all` view SHALL be omitted from the canonical URL.
Unknown statuses SHALL be named and ignored, never expanded into invented
known statuses. A link to an unresolved custom saved-view id SHALL retain its
explicit filters and expose the unresolved view honestly; the id does not
transfer the other owner's saved-view contents. The Connectors roster,
connector detail and Filters pipeline routes have no additional URL-backed
filter state specified here.

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

- **WHEN** the owner changes the range, commits a settled debounced search,
  changes channels, selects or clears minute scope, opens or closes an event
  drawer, or explicitly clears trace, and then presses back
- **THEN** the pushed history entry returns to the preceding URL selection
- **AND** mounting or reloading that URL hydrates its specified controls
- **AND** this push-navigation species excludes replacement-only statuses/view
  mirroring, legacy normalization and consumed OAuth markers

#### Scenario: One-shot parameter strips replace

- **WHEN** an inbound OAuth error marker is consumed and removed
- **THEN** the history entry is replaced rather than pushed
- **WHEN** the owner explicitly clears the trace filter
- **THEN** the history change pushes so back can restore the trace

#### Scenario: URL-backed status and view identity are restored honestly

- **WHEN** a Timeline link carries `statuses` and `view`
- **THEN** the recipient restores the supported statuses and view identity,
  alongside range, search, channels, scope and open event
- **AND** an unresolved custom view is named without fabricating its contents
- **WHEN** the status-chip or active saved-view mirroring effect updates
  `statuses` or `view`
- **THEN** it replaces the current history entry rather than pushes a new one
- **AND** back does not restore the immediately prior mirrored statuses or
  view identity through an entry that mirroring did not create

### Requirement: Connector roster list summary-only polling

The Connectors roster SHALL populate its list from a single summary endpoint
and SHALL NOT mount a per-connector detail query for any row. A roster of N
connectors SHALL therefore cost one request per poll, not N. The summary
endpoint SHALL be database-sourced with no Prometheus dependency, so the roster
still renders when the metrics backend is down. Polling SHALL be on a fixed
interval; the roster SHALL NOT poll faster than once per 30 seconds.

The available-connector catalogue, which describes connector types rather than
live state, SHALL NOT use a refetch interval. Its query SHALL use the existing
60-second stale time and 120-second garbage-collection time. This is absence of
polling, not a prohibition on the query library's focus/remount/invalidation
refetches once data is stale.

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
- **THEN** it uses the query cache with no refetch interval
- **AND** ordinary stale-query refetch semantics remain supported
