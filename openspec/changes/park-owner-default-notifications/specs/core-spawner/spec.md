# Core Spawner — Delta

## MODIFIED Requirements

### Requirement: Interactive Reply Delivery Accounting

On the normal-completion path, the spawner SHALL evaluate whether a
route-triggered interactive session attempted a reply through `notify()` but
delivered nothing, and SHALL persist that session with `success=False` and a
human-readable error when no attempt reached a delivered status. This remains a
third session outcome: the runtime completed successfully, so it SHALL not
trigger same-tier failover or self-healing and the in-memory
`SpawnerResult.success` SHALL remain `True`.

A captured notify result counts as delivered only when it is a dict with status
in `{ok, deferred}`. `deferred` counts because a concrete durable queue row and
`deliver_at` exist. Every other status or missing result is undelivered. Direct
eligible owner-default quiet-hours/context calls now return `deferred`, not the
retired `suppressed_quiet_hours` or `suppressed_context_bus` outcomes; legacy or
other non-delivered statuses remain outside the delivered set.

Accounting SHALL run only for `route`-triggered sessions from an interactive
source channel, shall leave zero-notify sessions unchanged, and shall treat any
one delivered attempt as sufficient.

#### Scenario: Undelivered interactive reply is recorded without healing

- **WHEN** an interactive route-triggered session made notify attempts and none
  has a delivered status
- **THEN** the persisted session has `success=False`
- **AND** the spawner does not raise, fail over, or self-heal

#### Scenario: Deferred owner-default reply remains delivered

- **WHEN** an interactive route-triggered session's eligible owner-default
  notify call returns `status="deferred"` with a queued notification id and
  `deliver_at`
- **THEN** delivery accounting leaves the session successful

#### Scenario: Null-result notify attempt is undelivered

- **WHEN** an interactive route-triggered session has a notify tool-call record
  with no result dict or an `outcome` of `error`
- **THEN** the attempt is treated as undelivered and the session is recorded
  with `success=False`

#### Scenario: Zero or exempt sessions remain unchanged

- **WHEN** a session made no notify attempt, is not route-triggered, or is not
  from an interactive source channel
- **THEN** delivery accounting does not change its ordinary success path
