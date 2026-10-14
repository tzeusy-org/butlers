## MODIFIED Requirements

### Requirement: Active Session Context Propagation

A tool handler serving a broker-forwarded call SHALL tag its span with the `goal_id`, `invocation_id` and `depth` of the verified assertion lineage and the goal's lead `epoch` read from the invocation record (the assertion carries no epoch), and SHALL parent the span to the broker's forwarding span when the forwarded call carries W3C trace context. Goal invocations run on the session host, so no spawner-held `ContextVar` supplies a goal invocation's span context to a domain tool handler. Spans SHALL store identifiers and digests, never argument or result values.

Source: RFC 0038 D3, D7 and D17
Scope: v1-mandatory

#### Scenario: Tool span parents to session span
- **WHEN** a domain tool handler runs for a broker-forwarded call whose assertion verified and whose request carries W3C trace context
- **THEN** the tool span is created as a child of the broker's forwarding span (shared trace ID)
- **AND** the span carries `goal_id`, `invocation_id`, `depth` and `epoch` and no argument or result values

#### Scenario: No active session
- **WHEN** a tool handler runs for a call without goal lineage, such as a service-plane call or a dashboard call whose assertion has a null `goal_id`
- **THEN** tool spans create root spans (new trace IDs) unless the call carries W3C trace context
- **AND** the span carries no goal attributes

## REMOVED Requirements

### Requirement: Route Metric Instruments
**Reason**: The three route instruments measure the domain-side `route.execute` session-dispatch path and its `route_inbox` queue, which are deleted at cutover; domain butlers no longer spawn reasoning sessions (RFC 0038 D2, D7).
**Migration**: Goal work is observed through the goal ledger and through spans carrying `goal_id`, `invocation_id`, `depth` and `epoch` (RFC 0038 D17; `REQ-capability-authorization-012`). Messenger's surviving `notify.v1` delivery termination is a deterministic service-plane endpoint and records no route-dispatch metrics.
