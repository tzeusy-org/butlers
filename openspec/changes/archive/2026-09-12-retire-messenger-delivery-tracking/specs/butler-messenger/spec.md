## REMOVED Requirements

### Requirement: Idempotent Delivery Admission
**Reason**: The admission table and its implementation have no live egress caller.
**Migration**: `msg_003` retires the empty legacy tracking schema and refuses
non-empty data before DDL.

### Requirement: Rate Limiting
**Reason**: This standalone limiter is unwired from the live channel-adapter path.
**Migration**: Provider and approval behavior remain on the existing direct path.

### Requirement: Circuit Breaker
**Reason**: The circuit state is not connected to production delivery.
**Migration**: The fabricated health surface is removed rather than approximated.

### Requirement: Retry with Exponential Backoff
**Reason**: The retry stack is unwired from egress.
**Migration**: Existing caller/provider recovery behavior is unchanged.

### Requirement: Dead Letter Management
**Reason**: Dead letters cannot be created by the live path.
**Migration**: The retired tables and MCP tools are removed by `msg_003`.

### Requirement: Delivery Tracking and Tracing
**Reason**: The tracking tables do not observe direct adapter egress.
**Migration**: Live outcomes continue through Switchboard routing and attention-ledger paths.

### Requirement: Operational Health Monitoring
**Reason**: The health API returned empty or DB-approximated data for an unwired stack.
**Migration**: REST and MCP endpoints are absent; no replacement health API is introduced.
