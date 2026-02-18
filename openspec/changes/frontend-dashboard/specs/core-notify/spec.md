## ADDED Requirements

### Requirement: Notify tool registration
Every butler daemon SHALL register a `notify(channel, message, recipient?)` MCP tool during startup. runtime instances MUST be able to call this tool to send outbound notifications. The tool MUST be available in every butler's MCP tool surface regardless of which modules are enabled.

#### Scenario: Tool available to runtime instance
- **WHEN** a runtime instance spawned by a butler lists available MCP tools
- **THEN** the `notify` tool MUST appear in the tool list with parameters `channel` (required string), `message` (required string), and `recipient` (optional string)

#### Scenario: Tool registered at startup
- **WHEN** a butler daemon starts up
- **THEN** the `notify` tool MUST be registered as a core MCP tool before the butler accepts any triggers

---

### Requirement: Notify implementation via Switchboard
The `notify` tool MUST route notifications through the Switchboard's `deliver()` tool via the butler daemon's MCP client connection. The call MUST be synchronous from the runtime instance's perspective: the runtime instance calls `notify()`, the butler daemon forwards to Switchboard `deliver()`, waits for the response, and returns the result to the runtime instance.

#### Scenario: Successful notification delivery
- **WHEN** a runtime instance calls `notify(channel='telegram', message='Hello')` and the Switchboard is reachable
- **THEN** the butler daemon MUST call the Switchboard's `deliver()` tool with the corresponding parameters and return a success result to the runtime instance

#### Scenario: Synchronous behavior
- **WHEN** a runtime instance calls `notify()`
- **THEN** the runtime instance MUST block until the butler daemon receives a response from the Switchboard and returns it

---

### Requirement: Switchboard connection
Each butler daemon SHALL hold an MCP client connection to the Switchboard. The connection URL MUST be configured via `switchboard_url` in `butler.toml`, or derived from the Switchboard's own configuration if running in the same deployment. The connection MUST be established during butler startup and maintained for the daemon's lifetime.

#### Scenario: Connection via butler.toml
- **WHEN** a butler daemon starts and `butler.toml` contains a `switchboard_url` value
- **THEN** the daemon MUST establish an MCP client connection to the Switchboard at that URL

#### Scenario: Connection established before accepting triggers
- **WHEN** a butler daemon completes startup
- **THEN** the MCP client connection to the Switchboard MUST be established before the butler begins processing triggers

---

### Requirement: Notify error handling
If the Switchboard is unreachable or returns an error, the `notify()` tool MUST return an error result (not raise an exception). The error result MUST include a human-readable message describing the failure. The runtime instance can then decide on fallback behavior (e.g., storing the message in state for later retry).

#### Scenario: Switchboard unreachable
- **WHEN** a runtime instance calls `notify()` and the Switchboard connection is down or times out
- **THEN** the `notify` tool MUST return an error result with a message indicating the Switchboard is unreachable, without raising an exception

#### Scenario: Switchboard returns error
- **WHEN** a runtime instance calls `notify()` and the Switchboard's `deliver()` tool returns an error
- **THEN** the `notify` tool MUST return an error result containing the Switchboard's error message

#### Scenario: CC decides fallback
- **WHEN** the `notify` tool returns an error result
- **THEN** the runtime instance MUST be able to inspect the error and take alternative action (e.g., store the notification in the butler's state store for later retry)

---

### Requirement: Supported channel values
The `notify` tool MUST accept `channel` values of `'telegram'` and `'email'`. If a runtime instance provides any other channel value, the tool MUST return an error result indicating the channel is unsupported.

#### Scenario: Valid telegram channel
- **WHEN** a runtime instance calls `notify(channel='telegram', message='Test')`
- **THEN** the tool MUST accept the call and route it to the Switchboard

#### Scenario: Valid email channel
- **WHEN** a runtime instance calls `notify(channel='email', message='Test')`
- **THEN** the tool MUST accept the call and route it to the Switchboard

#### Scenario: Invalid channel value
- **WHEN** a runtime instance calls `notify(channel='sms', message='Test')`
- **THEN** the tool MUST return an error result with a message indicating `'sms'` is not a supported channel

---

### Requirement: Default recipient
The `recipient` parameter of the `notify` tool is optional. If omitted, the notification MUST be delivered to the system owner as configured in the Switchboard. If provided, the notification MUST be delivered to the specified recipient.

#### Scenario: Omitted recipient defaults to system owner
- **WHEN** a runtime instance calls `notify(channel='telegram', message='Alert')` without a `recipient` parameter
- **THEN** the butler daemon MUST forward the call to the Switchboard without a recipient, and the Switchboard MUST deliver to the system owner

#### Scenario: Explicit recipient provided
- **WHEN** a runtime instance calls `notify(channel='email', message='Report', recipient='user@example.com')`
- **THEN** the butler daemon MUST forward the call to the Switchboard with `recipient='user@example.com'`
