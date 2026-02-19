# Heartbeat Butler

## REMOVED Requirements

### Requirement: Heartbeat butler configuration

**Reason**: The Heartbeat Butler has been replaced by two decentralized mechanisms: (1) an internal scheduler loop in each butler daemon that calls `tick()` every 60 seconds, and (2) push-based liveness reporting where each butler sends heartbeats to the Switchboard. A centralized butler for driving schedules and monitoring liveness is no longer needed.

**Migration**: Remove the `roster/heartbeat/` directory and all associated files. Drop the `butler_heartbeat` PostgreSQL database. Delete the heartbeat spec from `openspec/specs/heartbeat/`. Close all Heartbeat-related beads.

### Requirement: Heartbeat cycle is a scheduled task

**Reason**: Each butler now drives its own scheduled tasks via the internal scheduler loop. External tick invocation is no longer needed.

**Migration**: No action required — the heartbeat-cycle task was only defined in the Heartbeat Butler's `butler.toml`, which is being deleted.

### Requirement: CC enumerates butlers via the Switchboard

**Reason**: The heartbeat cycle (which enumerated and ticked butlers) has been eliminated. Butler enumeration for liveness is replaced by push-based reporting.

**Migration**: No action required.

### Requirement: CC ticks each butler via the Switchboard

**Reason**: External ticking of butlers is replaced by each butler's internal scheduler loop calling its own `tick()` function.

**Migration**: No action required.

### Requirement: Heartbeat butler does not tick itself

**Reason**: With the Heartbeat Butler removed, self-tick prevention is no longer relevant.

**Migration**: No action required.

### Requirement: Error resilience during the tick cycle

**Reason**: The cross-butler tick cycle is eliminated. Error resilience for individual `tick()` calls is handled by the internal scheduler loop spec (continues after exceptions).

**Migration**: No action required.

### Requirement: Heartbeat cycle results are logged

**Reason**: The heartbeat cycle no longer exists. Individual butlers log their own scheduled task results via the session log.

**Migration**: No action required.

### Requirement: Heartbeat butler exposes only core MCP tools

**Reason**: The Heartbeat Butler is being removed entirely.

**Migration**: No action required.

### Requirement: Heartbeat butler uses a dedicated database

**Reason**: The Heartbeat Butler is being removed entirely.

**Migration**: Drop the `butler_heartbeat` database.

### Requirement: Heartbeat butler runtime instance has Switchboard access

**Reason**: The heartbeat cycle that required Switchboard access is eliminated.

**Migration**: No action required.
