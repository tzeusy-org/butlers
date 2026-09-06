## ADDED Requirements

### Requirement: Unified Navigation Intent Warmup

The dashboard shell SHALL expose one navigation-intent primitive for route-aware navigation and
disclosure controls. For the current destination, one intent cycle SHALL warm every resource that
the shell capability authority maps: the lazy route chunk and, when declared, the destination's
query data. Both projections SHALL be coordinated by one pending timer per mounted primitive
instance.

Pointer intent SHALL require an uninterrupted 120 ms dwell. Keyboard focus and activation by click
or Enter SHALL warm immediately. Activation handlers MUST start warmup before router navigation or
an imperative activation callback begins.

Navigation warmup MUST remain speculative, side-effect-free, and failure-contained. It MUST use the
destination's existing authenticated read, exact query key, stale time, and TanStack Query cache
policy; it MUST NOT add an authorization path, persistent cache, mutation, provider effect, URL
state, telemetry payload, or event-bus payload. Route chunks SHALL use the browser module cache.

#### Scenario: Deliberate pointer intent warms every mapped resource

- **WHEN** a route-aware control targets `/sessions/abc` with both a route-chunk loader and query
  warmup mapping
- **AND** pointer presence remains uninterrupted for 120 ms
- **THEN** the primitive starts the mapped route-chunk loader
- **AND** it submits the mapped query through the current `QueryClient` with the destination's exact
  query key, query function, and stale time
- **AND** both resources start from the same intent cycle

#### Scenario: Pointer sweep ends before intent is established

- **WHEN** pointer enter is followed by pointer leave or pointer cancellation before 120 ms
- **THEN** the pending intent cycle is cancelled
- **AND** neither the chunk loader nor query prefetch starts

#### Scenario: Keyboard focus warms immediately

- **WHEN** a route-aware control receives keyboard focus for a mapped destination
- **THEN** every available mapped resource starts without waiting for the pointer delay
- **AND** the control remains keyboard-operable under its existing accessibility contract

#### Scenario: Click or Enter warms before navigation

- **WHEN** the owner activates a route-aware control by click or Enter while a pointer timer may be
  pending
- **THEN** the primitive cancels the pending timer and starts every available mapped resource once
  for that intent cycle
- **AND** warmup starts before router navigation or the caller's imperative activation callback

#### Scenario: Only a route chunk is mapped

- **WHEN** intent is established for a destination with a route-chunk loader and no query warmup
  mapping
- **THEN** the route chunk starts
- **AND** the absent query mapping causes no error, placeholder query, or alternate request

#### Scenario: Only query data is available

- **WHEN** intent is established for a destination with query warmup but no available route-chunk
  loader
- **THEN** the query warmup is submitted through the current `QueryClient`
- **AND** the absent chunk mapping does not suppress or delay the query warmup

#### Scenario: Query provider is unavailable

- **WHEN** intent is established for a destination whose chunk is mapped but no current
  `QueryClient` is available
- **THEN** the chunk warmup may proceed
- **AND** query warmup is a contained no-op without a render error or substitute cache

#### Scenario: Destination has no warmup mapping

- **WHEN** the target is null, invalid, or unmapped by the shell capability authority
- **THEN** navigation intent is a no-op
- **AND** it issues no resource request and does not alter navigation behavior

#### Scenario: Speculative resource rejection is contained

- **WHEN** the mapped chunk loader, query prefetch, or both reject
- **THEN** no rejection escapes the navigation-intent boundary
- **AND** failure of one resource does not cancel the other
- **AND** navigation is neither blocked nor replaced by a speculative toast or error state
- **AND** actual destination loading retains authority over its normal authorization, loading,
  empty, and error presentation

#### Scenario: Pending work is cancelled when its control is no longer current

- **WHEN** a pointer timer is pending and the target changes, the control loses focus, or the
  primitive unmounts before 120 ms
- **THEN** the pending cycle is cancelled
- **AND** no resource for the stale target starts
- **AND** a replacement target requires a new intent signal

#### Scenario: Repeated signals preserve resource-native deduplication

- **WHEN** pointer scheduling is followed by focus, click, or Enter for the same mounted control and
  destination
- **THEN** the pointer timer is cancelled before immediate warmup
- **AND** each mapped resource starts at most once for that intent cycle
- **AND** later cycles and other controls continue to use TanStack Query freshness and in-flight
  deduplication and the browser module cache rather than a second navigation-intent cache

#### Scenario: Warmed query preserves trust and cache boundaries

- **WHEN** navigation intent submits a destination query before navigation
- **THEN** it uses only the same authenticated, side-effect-free read and exact query identity the
  destination normally consumes
- **AND** the response may enter only the existing in-memory TanStack Query cache under its normal
  stale-time and garbage-collection policy
- **AND** warmup does not mint or refresh credentials, broaden authorization, persist a second copy,
  mutate server state, invoke a provider effect, or place response content in URL state, telemetry,
  logs, or event-bus payloads

#### Scenario: Predecessor hooks are retired atomically

- **WHEN** the unified primitive is implemented
- **THEN** `RowLink`, `DisclosureRow`, `SessionTable`, `Sidebar`, and `EntityFinder` migrate in the
  same implementation change
- **AND** `usePrefetchOnIntent` and `useRouteChunkPrefetchOnIntent`, their duplicate timing
  constants, and obsolete focused tests are removed without temporary compatibility aliases
- **AND** one consolidated behavior suite verifies the primitive while caller tests verify wiring
  and warm-before-activation ordering

#### Scenario: Implementation rollback requires no data repair

- **WHEN** the future unified-hook implementation is reverted before adoption
- **THEN** predecessor hook wiring can be restored atomically
- **AND** no server schema, query key, persisted cache format, credential, or provider state needs
  migration or repair
