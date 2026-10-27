## ADDED Requirements

### Requirement: Answer-lane decline may record a knowledge gap

The answer-lane instruction block SHALL tell the routed butler that an honest decline about a named
entity and predicate MAY pass a typed `gap` (`entity_id`, `predicate`) to `conversation_reply`.
The tool SHALL accept `gap` only on a reply without `sources` in a dashboard-routed session, SHALL
take the origin conversation, request id and channel from the server-side routing context rather
than from tool arguments, and SHALL record the gap through the local memory hook. A gap failure
SHALL be reported in the tool result and SHALL NOT prevent the decline reply from persisting.

#### Scenario: A gap on a sourced reply is refused

- **WHEN** `conversation_reply` carries both non-empty `sources` and a `gap`
- **THEN** the reply SHALL persist, no gap SHALL be recorded, and the result SHALL report the gap as refused

#### Scenario: A decline without a memory module still replies

- **WHEN** the routed butler has no memory module and passes a `gap`
- **THEN** the decline reply SHALL persist and the result SHALL report the gap as unavailable
