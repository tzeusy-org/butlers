# Switchboard Butler

The Switchboard is the public-facing ingress butler for the Butlers framework. It listens on Telegram (bot) and Email (IMAP/webhook), classifies incoming messages using an ephemeral LLM CLI instance, and routes them to the correct specialist butler(s) via MCP. When a message spans multiple butler domains, the runtime instance decomposes it into sub-messages and dispatches each sequentially. It owns the butler registry and serves as the single entry point for all external communication.

**Modules:** `telegram`, `email`

## Database Schema

The Switchboard butler's dedicated database (`butler_switchboard`) contains the standard core tables (`state`, `scheduled_tasks`, `sessions`) plus two Switchboard-specific tables:

```sql
CREATE TABLE butler_registry (
    name TEXT PRIMARY KEY,
    endpoint_url TEXT NOT NULL,
    description TEXT,
    modules JSONB NOT NULL DEFAULT '[]',
    last_seen_at TIMESTAMPTZ,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE routing_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_channel TEXT NOT NULL,       -- 'telegram', 'email', 'mcp'
    source_id TEXT,                     -- chat_id, email address, etc.
    routed_to TEXT NOT NULL,            -- butler name
    prompt_summary TEXT,
    trace_id TEXT,
    group_id UUID,                      -- links sub-routes from a decomposed message
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_routing_log_group_id ON routing_log (group_id);
```

## MCP Tools

The Switchboard exposes all core MCP tools (state, scheduler, sessions, trigger, tick, status) plus three Switchboard-specific tools:

- `route(butler_name, tool_name, args)` -- forward a tool call to a target butler via MCP client, propagating trace context
- `list_butlers()` -- return the full butler registry with names, descriptions, modules, and endpoints
- `discover()` -- re-scan butler config directories, update the registry (add new butlers, update changed ones, mark missing ones as stale)

## ADDED Requirements

### Requirement: Switchboard-specific table provisioning

The `butler_registry` and `routing_log` tables SHALL be created during Switchboard database provisioning as Alembic revisions in the `switchboard` version chain, applied after the core Alembic chain.

#### Scenario: Switchboard starts with a fresh database

WHEN the Switchboard butler starts up against a newly provisioned database
THEN the `butler_registry` table MUST exist with columns `name` (TEXT PRIMARY KEY), `endpoint_url` (TEXT NOT NULL), `description` (TEXT), `modules` (JSONB NOT NULL DEFAULT '[]'), `last_seen_at` (TIMESTAMPTZ), and `registered_at` (TIMESTAMPTZ NOT NULL DEFAULT now())
AND the `routing_log` table MUST exist with columns `id` (UUID PRIMARY KEY), `source_channel` (TEXT NOT NULL), `source_id` (TEXT), `routed_to` (TEXT NOT NULL), `prompt_summary` (TEXT), `trace_id` (TEXT), `group_id` (UUID, nullable), and `created_at` (TIMESTAMPTZ NOT NULL DEFAULT now())
AND an index `idx_routing_log_group_id` MUST exist on the `group_id` column

#### Scenario: Core tables are also present

WHEN the Switchboard butler starts up against a newly provisioned database
THEN the core tables (`state`, `scheduled_tasks`, `sessions`) MUST also be present
AND the core Alembic chain MUST have been applied before the Switchboard Alembic chain

---

### Requirement: discover populates the butler registry on startup

The Switchboard SHALL call `discover()` on startup to populate the butler registry by scanning butler config directories.

#### Scenario: Startup discovery with multiple butler config directories

WHEN the Switchboard starts up and butler config directories exist for `general`, `relationship`, and `health`
THEN `discover()` MUST be invoked automatically
AND the `butler_registry` table MUST contain one row for each discovered butler with the correct `name`, `endpoint_url`, `description`, and `modules` parsed from each butler's `butler.toml`

#### Scenario: Startup discovery sets registered_at

WHEN `discover()` inserts a new butler into the registry
THEN `registered_at` MUST be set to the current timestamp

---

### Requirement: discover updates existing and detects new butlers at runtime

The `discover()` MCP tool SHALL re-scan butler config directories when called, updating existing entries and adding new ones.

#### Scenario: A new butler config directory appears at runtime

WHEN `discover()` is called and a new butler config directory exists that is not yet in the `butler_registry`
THEN a new row MUST be inserted into `butler_registry` with the butler's `name`, `endpoint_url`, `description`, and `modules` from its `butler.toml`

#### Scenario: An existing butler's config has changed

WHEN `discover()` is called and an existing butler's `butler.toml` has a different `description` or `modules` list than what is stored in the registry
THEN the corresponding row in `butler_registry` MUST be updated to reflect the current config values

#### Scenario: A previously registered butler's config directory no longer exists

WHEN `discover()` is called and a butler in the `butler_registry` no longer has a corresponding config directory
THEN the butler MUST NOT be deleted from the registry
AND the butler's `last_seen_at` SHALL NOT be updated (it retains its previous value, marking it as stale)

---

### Requirement: list_butlers returns the full registry

The `list_butlers()` MCP tool SHALL return the contents of the `butler_registry` table.

#### Scenario: Registry contains multiple butlers

WHEN `list_butlers()` is called and the `butler_registry` contains entries for `general`, `relationship`, and `health`
THEN it MUST return a list containing all three entries
AND each entry MUST include `name`, `endpoint_url`, `description`, `modules`, `last_seen_at`, and `registered_at`

#### Scenario: Registry is empty

WHEN `list_butlers()` is called and the `butler_registry` table is empty
THEN it MUST return an empty list
AND it MUST NOT raise an error

---

### Requirement: route forwards a tool call to a target butler via MCP

The `route(butler_name, tool_name, args)` MCP tool SHALL look up the target butler in the registry, establish an MCP client connection to its endpoint, call the specified tool with the given arguments, and return the result.

#### Scenario: Routing a trigger to an existing butler

WHEN `route("health", "trigger", {"prompt": "Log my weight: 75kg"})` is called
AND `health` exists in the `butler_registry` with a valid `endpoint_url`
THEN the Switchboard MUST establish an MCP client connection to the `health` butler's endpoint
AND it MUST call the `trigger` tool on the `health` butler with the provided args
AND it MUST return the result from the `health` butler

#### Scenario: Routing to a butler not in the registry

WHEN `route("nonexistent", "trigger", {"prompt": "hello"})` is called
AND `nonexistent` does not exist in the `butler_registry`
THEN the tool MUST return an error indicating the butler was not found
AND it MUST NOT attempt an MCP connection

#### Scenario: Target butler is unreachable

WHEN `route("health", "trigger", {"prompt": "hello"})` is called
AND the `health` butler's endpoint is unreachable (connection refused, timeout)
THEN the tool MUST return an error indicating the butler is unreachable
AND the error MUST include the butler name and endpoint URL for diagnostics

---

### Requirement: route propagates W3C trace context

The `route()` tool SHALL propagate the current W3C traceparent context to the target butler by including `_trace_context` in the tool call arguments.

#### Scenario: Trace context is propagated on route

WHEN `route(butler_name, tool_name, args)` is called within an active OpenTelemetry span
THEN the tool call to the target butler MUST include a `_trace_context` field in the args containing the current W3C traceparent string
AND the target butler MUST be able to extract the trace context and create a child span under the same trace

#### Scenario: Route call creates a span

WHEN `route(butler_name, tool_name, args)` is called
THEN a span named `switchboard.route` MUST be created
AND the span MUST have attributes `target` (the butler name) and `tool_name` (the tool being called)

---

### Requirement: route updates last_seen_at on successful response

The Switchboard SHALL update the `last_seen_at` timestamp in the `butler_registry` when a butler responds successfully to a `route()` call.

#### Scenario: Successful route updates last_seen_at

WHEN `route("health", "trigger", {"prompt": "hello"})` is called
AND the `health` butler returns a successful response
THEN the `last_seen_at` column for the `health` row in `butler_registry` MUST be updated to the current timestamp

#### Scenario: Failed route does not update last_seen_at

WHEN `route("health", "trigger", {"prompt": "hello"})` is called
AND the `health` butler is unreachable or returns an error
THEN the `last_seen_at` column for the `health` row in `butler_registry` MUST NOT be updated

---

### Requirement: route logs every routing decision

Every `route()` call SHALL be recorded in the `routing_log` table.

#### Scenario: Successful route is logged

WHEN `route("health", "trigger", {"prompt": "Log my weight"})` is called successfully from a Telegram message
THEN a new row MUST be inserted into `routing_log` with `source_channel` set to the originating channel (e.g., `'telegram'`), `source_id` set to the identifier of the sender (e.g., chat ID), `routed_to` set to `'health'`, `prompt_summary` containing a summary of the routed prompt, and `trace_id` set to the current OpenTelemetry trace ID

#### Scenario: Failed route is still logged

WHEN `route("health", "trigger", {"prompt": "hello"})` is called and the target butler is unreachable
THEN a row MUST still be inserted into `routing_log` recording the attempted routing decision

---

### Requirement: Message classification via LLM CLI spawner

When a message arrives via the Telegram or Email module, the Switchboard SHALL spawn an ephemeral LLM CLI instance to classify the message and determine which butler should handle it.

#### Scenario: Telegram message triggers classification

WHEN a Telegram message is received by the Switchboard's Telegram module
THEN the Switchboard MUST spawn a runtime instance via the LLM CLI spawner
AND the runtime instance MUST receive a classification prompt containing the list of available butlers (from `list_butlers()`) and the message text
AND the classification prompt MUST follow the format: "Classify this message and route it. Available butlers: [{butler list with descriptions}]. Message: {text}"

#### Scenario: Email message triggers classification

WHEN an email is received by the Switchboard's Email module
THEN the Switchboard MUST spawn a runtime instance via the LLM CLI spawner
AND the runtime instance MUST receive a classification prompt containing the list of available butlers and the email content (subject and body)

#### Scenario: CC decides the target butler and routes

WHEN the runtime instance determines that a message should be handled by the `health` butler
THEN CC MUST call `route("health", "trigger", {"prompt": <constructed prompt>})` via the Switchboard's MCP tools
AND the Switchboard MUST forward the call to the `health` butler

---

### Requirement: Default routing to General butler when uncertain

When the runtime instance is uncertain which specialist butler should handle a message, it SHALL default to routing to the General butler.

#### Scenario: Ambiguous message is routed to General

WHEN a message arrives that does not clearly match any specialist butler's domain
AND the runtime instance cannot determine the correct target with confidence
THEN the runtime instance MUST route the message to the `general` butler via `route("general", "trigger", {"prompt": ...})`

#### Scenario: Classification prompt instructs default behavior

WHEN the Switchboard constructs the classification prompt for CC
THEN the prompt MUST include an instruction that if the message does not clearly fit a specialist butler, it SHALL be routed to the `general` butler

---

### Requirement: Response delivery via originating channel

After a routed butler returns a result, the Switchboard SHALL send the response back to the user via the same channel (Telegram or Email) that the original message arrived on.

#### Scenario: Telegram message gets Telegram response

WHEN a message arrives via Telegram, is routed to a specialist butler, and the butler returns a result
THEN the Switchboard MUST send the result back to the originating Telegram chat using the Telegram module's `bot_telegram_send_message` tool

#### Scenario: Email message gets email response

WHEN a message arrives via Email, is routed to a specialist butler, and the butler returns a result
THEN the Switchboard MUST send the result back to the originating email address using the Email module's `bot_email_send_message` tool

---

### Requirement: Inter-butler communication uses MCP-over-SSE

All communication between the Switchboard and target butlers SHALL use MCP over SSE (Server-Sent Events) transport.

#### Scenario: Route call uses SSE transport

WHEN `route(butler_name, tool_name, args)` establishes a connection to a target butler
THEN it MUST use the SSE transport protocol as specified by the butler's `endpoint_url` in the registry
AND the connection MUST be established as an MCP client to the target butler's MCP server

#### Scenario: SSE endpoint URL format

WHEN a butler is registered in the `butler_registry`
THEN its `endpoint_url` MUST be an HTTP(S) URL pointing to the butler's SSE endpoint (e.g., `http://localhost:8103/sse`)

---

### Requirement: Switchboard loads Telegram and Email modules

The Switchboard butler SHALL load the `telegram` and `email` modules as specified in its `butler.toml` configuration.

#### Scenario: Modules are loaded on startup

WHEN the Switchboard butler starts up with `[modules.telegram]` and `[modules.email]` sections in its `butler.toml`
THEN both the Telegram and Email modules MUST be loaded and initialized
AND both modules MUST have their tools registered on the Switchboard's MCP server

#### Scenario: Telegram module provides message intake

WHEN the Telegram module is loaded on the Switchboard
THEN it MUST listen for incoming Telegram messages (via polling or webhook as configured)
AND it MUST invoke the Switchboard's classification flow when a message arrives

#### Scenario: Email module provides message intake

WHEN the Email module is loaded on the Switchboard
THEN it MUST listen for incoming emails (via IMAP polling or webhook as configured)
AND it MUST invoke the Switchboard's classification flow when an email arrives

---

### Requirement: Routing log records source channel and source identifier

Every entry in the `routing_log` SHALL record which channel the message arrived on and the identity of the sender.

#### Scenario: Telegram message source is recorded

WHEN a Telegram message from chat ID `12345` is routed to the `health` butler
THEN the `routing_log` entry MUST have `source_channel` set to `'telegram'` and `source_id` set to `'12345'`

#### Scenario: Email message source is recorded

WHEN an email from `user@example.com` is routed to the `relationship` butler
THEN the `routing_log` entry MUST have `source_channel` set to `'email'` and `source_id` set to `'user@example.com'`

#### Scenario: Direct MCP call source is recorded

WHEN a routing request arrives via a direct MCP call (not via Telegram or Email)
THEN the `routing_log` entry MUST have `source_channel` set to `'mcp'`
AND `source_id` MAY be null

---

### Requirement: Switchboard does not route to itself

The Switchboard SHALL NOT appear in the list of routable butlers presented to the CC classification instance and SHALL NOT accept self-routing.

#### Scenario: Switchboard is excluded from classification prompt

WHEN the Switchboard constructs the classification prompt listing available butlers
THEN the Switchboard itself MUST NOT be included in the list of available butlers

#### Scenario: Route to Switchboard is rejected

WHEN `route("switchboard", tool_name, args)` is called
THEN the tool MUST return an error indicating that routing to the Switchboard is not permitted

---

### Requirement: Concurrent message handling

The Switchboard SHALL handle incoming messages serially in v1 (one CC classification instance at a time), consistent with the framework's serial CC dispatch constraint.

#### Scenario: Two messages arrive in quick succession

WHEN two Telegram messages arrive in quick succession
THEN the Switchboard MUST process the first message's classification and routing to completion before starting the second
AND no messages SHALL be dropped; they MUST be queued for processing

---

### Requirement: Routing log includes trace_id for distributed tracing

Each `routing_log` entry SHALL include the OpenTelemetry trace ID for correlation with traces in the telemetry backend (Grafana Tempo).

#### Scenario: Trace ID is recorded in routing log

WHEN a message is classified and routed within an active OpenTelemetry trace
THEN the `routing_log` entry's `trace_id` column MUST contain the trace ID of the span encompassing the routing operation

#### Scenario: Trace ID links to end-to-end trace

WHEN a `routing_log` entry has a `trace_id` value
THEN that trace ID MUST correspond to a trace in the telemetry backend (Grafana Tempo) that includes spans for message receipt, classification, routing, and target butler execution

---

### Requirement: Message decomposition via CC classification

When an incoming message spans multiple butler domains, the CC classification instance SHALL decompose it into distinct sub-messages, each targeting a specific butler. The classification prompt MUST instruct CC to identify all relevant butler targets from a single message and construct a separate `route()` call for each.

#### Scenario: Multi-domain message is decomposed

WHEN a Telegram message arrives with content "Remind me to call Mom on Tuesday and log my weight at 75kg"
AND the butler registry contains `general`, `relationship`, and `health` butlers
THEN the CC classification instance MUST identify that the message contains two sub-intents
AND it MUST decompose the message into sub-messages: one targeting the `relationship` butler (about calling Mom) and one targeting the `health` butler (about logging weight)
AND it MUST call `route()` once for each sub-message with the appropriate butler name and a prompt containing the relevant sub-intent

#### Scenario: Classification prompt includes decomposition instructions

WHEN the Switchboard constructs the classification prompt for the runtime instance
THEN the prompt MUST instruct CC that a single user message MAY contain multiple intents for different butlers
AND the prompt MUST instruct CC to identify all distinct intents and route each to the appropriate butler via separate `route()` calls
AND the prompt MUST instruct CC that each `route()` call's prompt argument SHALL contain only the sub-intent relevant to that butler, not the entire original message

#### Scenario: Single-domain message produces one route call (no regression)

WHEN a message arrives with content "Log my weight at 75kg"
AND the runtime instance determines that only the `health` butler is relevant
THEN the runtime instance MUST call `route()` exactly once, targeting the `health` butler
AND the behavior MUST be identical to the existing single-target classification flow

---

### Requirement: Sequential fan-out dispatch of decomposed sub-messages

When the runtime instance decomposes a message into multiple sub-messages, it SHALL dispatch each `route()` call sequentially (one at a time), consistent with the framework's serial CC dispatch constraint. The runtime instance itself orchestrates the fan-out by making multiple `route()` tool calls within a single runtime session.

#### Scenario: Two sub-messages are dispatched sequentially

WHEN a message is decomposed into sub-messages targeting the `relationship` and `health` butlers
THEN the runtime instance MUST call `route("relationship", "trigger", ...)` first
AND it MUST wait for the response before calling `route("health", "trigger", ...)`
AND both calls MUST occur within the same runtime session (no additional runtime spawns)

#### Scenario: Fan-out respects serial dispatch constraint

WHEN a message is decomposed into N sub-messages targeting N distinct butlers
THEN the runtime instance MUST issue exactly N sequential `route()` calls
AND at no point SHALL more than one `route()` call be in-flight simultaneously
AND the total number of runtime instances spawned for the original message MUST be exactly one (the classification instance)

#### Scenario: Order of dispatch follows message order

WHEN a message is decomposed into multiple sub-messages
THEN the runtime instance SHOULD dispatch `route()` calls in the order the sub-intents appear in the original message

---

### Requirement: Response aggregation for multi-butler replies

When the runtime instance has dispatched multiple `route()` calls for a decomposed message, it SHALL aggregate the responses from all targeted butlers into a single coherent reply before returning. The aggregated reply is then delivered to the user via the originating channel.

#### Scenario: Successful multi-butler response aggregation

WHEN a message is decomposed into sub-messages targeting `relationship` and `health`
AND both `route()` calls return successful responses
THEN the runtime instance MUST combine the responses into a single aggregated reply
AND the aggregated reply MUST clearly attribute each part of the response to the relevant domain or butler
AND the Switchboard MUST deliver the aggregated reply to the user via the originating channel (Telegram or Email)

#### Scenario: Partial failure during fan-out

WHEN a message is decomposed into sub-messages targeting `relationship` and `health`
AND the `route()` call to `relationship` succeeds but the `route()` call to `health` fails (butler unreachable or returns an error)
THEN the runtime instance MUST still aggregate a response
AND the aggregated reply MUST include the successful response from `relationship`
AND the aggregated reply MUST inform the user that the `health`-related part of their request could not be processed, along with a brief reason
AND the Switchboard MUST deliver this partial aggregated reply to the user

#### Scenario: All sub-routes fail

WHEN a message is decomposed into multiple sub-messages and all `route()` calls fail
THEN the runtime instance MUST return a reply informing the user that none of the requested actions could be processed
AND the reply MUST include a summary of which actions failed and why
AND the Switchboard MUST deliver this error reply to the user via the originating channel

---

### Requirement: Routing log entries per sub-route with group linkage

When a message is decomposed into multiple sub-messages, each `route()` call SHALL produce its own independent `routing_log` entry. All entries originating from the same decomposed message SHALL share a common `group_id` to enable correlation.

#### Scenario: Multi-target message produces multiple routing log entries

WHEN a Telegram message from chat ID `12345` is decomposed into sub-messages targeting `relationship` and `health`
THEN the `routing_log` table MUST contain two entries
AND both entries MUST have `source_channel` set to `'telegram'` and `source_id` set to `'12345'`
AND the entry for `relationship` MUST have `routed_to` set to `'relationship'` and `prompt_summary` reflecting the relationship sub-intent
AND the entry for `health` MUST have `routed_to` set to `'health'` and `prompt_summary` reflecting the health sub-intent

#### Scenario: Sub-route entries share a group_id

WHEN a message is decomposed into N sub-messages
THEN all N resulting `routing_log` entries MUST share the same `group_id` value (a UUID)
AND the `group_id` MUST be unique per original incoming message

#### Scenario: Single-target message has null group_id

WHEN a message is classified to a single butler (no decomposition)
THEN the resulting `routing_log` entry MUST have `group_id` set to NULL
AND this preserves backward compatibility with existing single-route log entries

#### Scenario: Each sub-route entry has its own trace context

WHEN a decomposed message produces multiple `routing_log` entries
THEN each entry MUST have its own `trace_id` corresponding to the span of its individual `route()` call
AND all entries in the same group SHOULD share a parent trace that encompasses the entire decomposition flow

---

### Requirement: routing_log schema addition for group_id

The `routing_log` table SHALL include a `group_id` column to link entries that originate from the same decomposed message.

#### Scenario: routing_log table includes group_id column

WHEN the Switchboard database is provisioned
THEN the `routing_log` table MUST include a `group_id` column of type `UUID`, which is nullable
AND the column MUST default to NULL
AND an index MUST exist on `group_id` for efficient group lookups

---

### Requirement: Backward compatibility with single-target classification

The message decomposition flow SHALL be fully backward compatible with existing single-target classification. When a message maps to exactly one butler, the behavior MUST be identical to the pre-decomposition flow: one runtime session, one `route()` call, one `routing_log` entry (with `group_id` NULL), one response delivered to the user.

#### Scenario: Single-target message flow is unchanged

WHEN a message arrives with content "What's on my calendar today?"
AND the runtime instance determines that only the `general` butler is relevant
THEN exactly one `route()` call MUST be made to the `general` butler
AND exactly one `routing_log` entry MUST be created with `group_id` set to NULL
AND the response MUST be delivered directly to the user without aggregation logic

#### Scenario: Default-to-General still works for ambiguous messages

WHEN a message arrives that does not clearly match any specialist butler
AND the runtime instance cannot determine the correct target with confidence
THEN the runtime instance MUST route the message to the `general` butler as a single-target route (not a decomposition)
AND `group_id` in the `routing_log` MUST be NULL

#### Scenario: Existing routing log queries are not broken

WHEN a query is executed against `routing_log` without filtering on `group_id`
THEN all entries (both single-target and decomposed) MUST be returned
AND existing queries that do not reference `group_id` MUST continue to function without modification
