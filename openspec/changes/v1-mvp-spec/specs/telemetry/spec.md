# Telemetry

The telemetry capability provides distributed tracing across butlers and CC sessions using OpenTelemetry. Every butler daemon initializes a TracerProvider on startup via `init_telemetry(service_name)`, and all MCP tool handlers, CC sessions, scheduler ticks, and inter-butler calls are instrumented with spans. Trace context is propagated across butler boundaries (via `_trace_context` in MCP args) and into CC instances (via the `TRACEPARENT` environment variable). The LGTM stack (Alloy gateway, Tempo for traces, Grafana for visualization) is used for local dev trace visualization.

## Core Components

- `init_telemetry(service_name)` — initializes the OTel `TracerProvider` with an OTLP exporter, sets the `service.name` resource attribute
- `tracer = trace.get_tracer("butlers")` — shared tracer instance used by all instrumentation points
- Span wrappers for every MCP tool handler
- Trace context propagation across inter-butler MCP calls and CC sessions
- LGTM stack (Alloy OTLP gateway, Tempo for traces, Grafana for visualization) for local dev trace visibility

## Instrumentation Points

| Event | Span Name | Key Attributes |
|-------|-----------|----------------|
| Message received | `switchboard.receive` | `channel`, `source_id` |
| Routing decision | `switchboard.classify` | `routed_to` |
| Inter-butler call | `switchboard.route` | `target`, `tool_name` |
| CC spawned | `butler.cc_session` | `session_id`, `prompt_length` |
| MCP tool called | `butler.tool.<name>` | tool-specific attributes |
| Scheduler tick | `butler.tick` | `tasks_due`, `tasks_run` |
| Heartbeat cycle | `heartbeat.cycle` | `butlers_ticked`, `failures` |

## Trace Context Propagation

1. **Switchboard to Butler:** W3C traceparent passed via `_trace_context` in MCP tool call args. Target butler extracts it and creates a child span.
2. **Butler to CC Instance:** Trace context passed via `TRACEPARENT` environment variable. Session log stores `trace_id` for correlation.
3. **CC to Butler MCP tools:** Each MCP tool call from CC creates a child span under the CC session span.

## Local Dev Setup

- LGTM stack: Alloy OTLP gateway and Tempo for trace collection
- OTLP endpoint: `otel.parrot-hen.ts.net:4318`
- Grafana Explore with Tempo datasource for trace visualization
- OTel sampling: 100% in dev, configurable in prod via `OTEL_TRACES_SAMPLER` env var

## ADDED Requirements

### Requirement: Telemetry initialization

The `init_telemetry(service_name)` function SHALL initialize an OpenTelemetry `TracerProvider` with an OTLP exporter and set the `service.name` resource attribute to the given service name. It SHALL be called once per butler daemon on startup.

#### Scenario: Butler daemon starts with OTLP endpoint configured

WHEN a butler daemon starts and `OTEL_EXPORTER_OTLP_ENDPOINT` is set to `http://localhost:4317`
THEN `init_telemetry(service_name)` MUST be called with the butler's name as the service name
AND a `TracerProvider` MUST be configured with a `Resource` whose `service.name` attribute equals the butler's name
AND the `TracerProvider` MUST use an OTLP gRPC exporter pointing to the configured endpoint
AND `trace.get_tracer("butlers")` MUST return a functional tracer instance

#### Scenario: Butler daemon starts without OTLP endpoint configured

WHEN a butler daemon starts and `OTEL_EXPORTER_OTLP_ENDPOINT` is not set
THEN `init_telemetry(service_name)` MUST still be called without raising an error
AND telemetry MUST operate as a no-op (no spans exported, no crashes)
AND the butler MUST start and function normally

#### Scenario: init_telemetry is called exactly once per daemon

WHEN a butler daemon starts
THEN `init_telemetry(service_name)` MUST be called exactly once during the startup sequence
AND subsequent calls to `trace.get_tracer("butlers")` MUST return the same tracer instance

---

### Requirement: Shared tracer instance

All instrumentation points within a butler process SHALL use a single shared tracer obtained via `trace.get_tracer("butlers")`.

#### Scenario: Multiple components use the same tracer

WHEN the CC Spawner, tick handler, and MCP tool handlers all create spans
THEN they MUST all use the tracer returned by `trace.get_tracer("butlers")`
AND all spans MUST share the same instrumentation scope name `"butlers"`

---

### Requirement: MCP tool handler span wrapping

Every MCP tool handler registered on a butler's MCP server SHALL be wrapped with a span. The span MUST capture the tool name, butler name, and tool-specific attributes.

#### Scenario: A tool handler is called and a span is created

WHEN the `state_get` MCP tool handler is invoked on a butler named `health`
THEN a span named `butler.tool.state_get` MUST be created
AND the span MUST have attribute `butler.name` set to `health`

#### Scenario: All registered tools have span wrappers

WHEN a butler named `health` starts with tools `state_get`, `state_set`, `trigger`, `tick`, and `status` registered
THEN every one of these tool handlers MUST be wrapped with a span
AND each span MUST follow the naming convention `butler.tool.<name>`

#### Scenario: Module tools are also wrapped

WHEN a butler has module-provided tools registered (e.g., `bot_telegram_send_message` from the Telegram module)
THEN the module tools MUST also be wrapped with spans following the same `butler.tool.<name>` convention
AND the span MUST include the `butler.name` attribute

---

### Requirement: Span attributes include butler.name

Every span created by the telemetry instrumentation SHALL include the `butler.name` attribute identifying which butler produced the span.

#### Scenario: Tool span includes butler.name

WHEN any MCP tool handler span is created on a butler named `relationship`
THEN the span MUST have attribute `butler.name` set to `relationship`

#### Scenario: CC session span includes butler.name

WHEN a `butler.cc_session` span is created on a butler named `general`
THEN the span MUST have attribute `butler.name` set to `general`

#### Scenario: Tick span includes butler.name

WHEN a `butler.tick` span is created on a butler named `health`
THEN the span MUST have attribute `butler.name` set to `health`

---

### Requirement: Error recording on spans

When an operation fails, the span MUST record the exception and set the span status to ERROR.

#### Scenario: MCP tool handler raises an exception

WHEN the `state_get` tool handler raises a `KeyError` exception
THEN the active span MUST record the exception via `span.record_exception()`
AND the span status MUST be set to `ERROR`
AND the span MUST include the exception message in its events

#### Scenario: CC session fails

WHEN a CC instance fails with an SDK timeout error
THEN the `butler.cc_session` span MUST record the exception
AND the span status MUST be set to `ERROR`

#### Scenario: Successful operation does not set error status

WHEN an MCP tool handler completes successfully
THEN the span status MUST NOT be set to `ERROR`
AND no exception MUST be recorded on the span

---

### Requirement: Switchboard receive span

When the Switchboard receives a message from an external channel (Telegram, Email, or direct MCP), it SHALL create a `switchboard.receive` span as the root span of the trace for that message.

#### Scenario: Telegram message triggers a receive span

WHEN a Telegram message arrives from chat ID `12345`
THEN a span named `switchboard.receive` MUST be created
AND the span MUST have attribute `channel` set to `telegram`
AND the span MUST have attribute `source_id` set to `12345`

#### Scenario: Email message triggers a receive span

WHEN an email arrives from `user@example.com`
THEN a span named `switchboard.receive` MUST be created
AND the span MUST have attribute `channel` set to `email`
AND the span MUST have attribute `source_id` set to `user@example.com`

#### Scenario: Direct MCP call triggers a receive span

WHEN a direct MCP call arrives at the Switchboard
THEN a span named `switchboard.receive` MUST be created
AND the span MUST have attribute `channel` set to `mcp`

---

### Requirement: Switchboard classify span

When the Switchboard spawns a CC instance to classify a message, it SHALL create a `switchboard.classify` span as a child of the `switchboard.receive` span.

#### Scenario: Classification determines target butler

WHEN the CC classification instance determines that a message should be routed to butler `health`
THEN a span named `switchboard.classify` MUST exist as a child of the `switchboard.receive` span
AND the span MUST have attribute `routed_to` set to `health`

#### Scenario: Classification defaults to general

WHEN the CC classification instance cannot determine a specialist butler and defaults to `general`
THEN the `switchboard.classify` span MUST have attribute `routed_to` set to `general`

---

### Requirement: Switchboard route span

When the Switchboard routes a tool call to a target butler, it SHALL create a `switchboard.route` span.

#### Scenario: Route to target butler creates a span

WHEN `route("health", "trigger", {"prompt": "Log weight"})` is called
THEN a span named `switchboard.route` MUST be created
AND the span MUST have attribute `target` set to `health`
AND the span MUST have attribute `tool_name` set to `trigger`

#### Scenario: Route span is a child of the classify span

WHEN a message is received, classified, and routed
THEN the `switchboard.route` span MUST be a child of the `switchboard.classify` span
AND the full trace MUST show the hierarchy: `switchboard.receive` > `switchboard.classify` > `switchboard.route`

---

### Requirement: CC session span

When the CC Spawner spawns an ephemeral Claude Code instance, it SHALL create a `butler.cc_session` span that encompasses the entire CC execution.

#### Scenario: CC session span is created with correct attributes

WHEN the CC Spawner spawns a CC instance with session ID `abc-123` and a prompt of 150 characters
THEN a span named `butler.cc_session` MUST be created
AND the span MUST have attribute `session_id` set to `abc-123`
AND the span MUST have attribute `prompt_length` set to `150`

#### Scenario: CC session span duration matches session duration

WHEN a CC instance runs for 8 seconds
THEN the `butler.cc_session` span's duration MUST reflect approximately 8 seconds

---

### Requirement: Scheduler tick span

When `tick()` is called on a butler, it SHALL create a `butler.tick` span recording how many tasks were due and how many were run.

#### Scenario: Tick with due tasks

WHEN `tick()` is called on a butler and 3 tasks are due, of which 3 are run
THEN a span named `butler.tick` MUST be created
AND the span MUST have attribute `tasks_due` set to `3`
AND the span MUST have attribute `tasks_run` set to `3`

#### Scenario: Tick with no due tasks

WHEN `tick()` is called on a butler and no tasks are due
THEN a span named `butler.tick` MUST be created
AND the span MUST have attribute `tasks_due` set to `0`
AND the span MUST have attribute `tasks_run` set to `0`

---

### Requirement: Heartbeat cycle span

When the Heartbeat butler executes a heartbeat cycle, it SHALL create a `heartbeat.cycle` span recording the number of butlers ticked and failures.

#### Scenario: Heartbeat cycle with all butlers healthy

WHEN the heartbeat cycle ticks 4 butlers and all succeed
THEN a span named `heartbeat.cycle` MUST be created
AND the span MUST have attribute `butlers_ticked` set to `4`
AND the span MUST have attribute `failures` set to `0`

#### Scenario: Heartbeat cycle with partial failures

WHEN the heartbeat cycle ticks 4 butlers and 1 fails
THEN the `heartbeat.cycle` span MUST have attribute `butlers_ticked` set to `4`
AND the span MUST have attribute `failures` set to `1`

---

### Requirement: Trace context propagation from Switchboard to Butler

When the Switchboard routes a tool call to a target butler, it SHALL propagate the W3C trace context by including a `_trace_context` field in the MCP tool call arguments. The target butler SHALL extract the trace context and create a child span under the same trace.

#### Scenario: _trace_context is injected into MCP tool call args

WHEN `route("health", "trigger", {"prompt": "hello"})` is called within an active span
THEN the tool call forwarded to the `health` butler MUST include a `_trace_context` field in the args
AND the `_trace_context` value MUST be a valid W3C traceparent string (e.g., `00-<trace_id>-<span_id>-01`)

#### Scenario: Target butler creates a child span from received trace context

WHEN the `health` butler receives a tool call with `_trace_context` set to a valid W3C traceparent
THEN the butler MUST extract the trace context from `_trace_context`
AND it MUST create a new span that is a child of the span identified by the traceparent
AND the child span's `trace_id` MUST match the `trace_id` in the received traceparent

#### Scenario: Missing _trace_context does not break the call

WHEN a butler receives a tool call without a `_trace_context` field in the args
THEN the butler MUST still process the tool call normally
AND it MUST create a new root span (not a child span)

---

### Requirement: Trace context propagation from Butler to CC Instance

When the CC Spawner spawns a Claude Code instance, it SHALL propagate trace context by setting the `TRACEPARENT` environment variable on the spawned process. The session log SHALL store the `trace_id` for correlation.

#### Scenario: TRACEPARENT is set for the CC process

WHEN the CC Spawner spawns a CC instance within an active `butler.cc_session` span
THEN the `TRACEPARENT` environment variable MUST be set on the spawned CC process
AND the value MUST conform to the W3C Trace Context format (`00-<trace_id>-<span_id>-<flags>`)

#### Scenario: Session log records trace_id

WHEN a CC session is logged in the `sessions` table and an active trace exists
THEN the session record MUST include the `trace_id` from the active span
AND the `trace_id` MUST match the trace ID in the `TRACEPARENT` passed to the CC process

#### Scenario: No active trace does not block CC spawn

WHEN the CC Spawner is triggered without an active trace context
THEN the CC instance MUST still be spawned successfully
AND no `TRACEPARENT` environment variable SHALL be set
AND the session log's `trace_id` field MAY be null

---

### Requirement: Trace context propagation from CC to Butler MCP tools

When a CC instance calls an MCP tool on its owning butler, each tool call SHALL create a child span under the CC session span.

#### Scenario: CC tool call creates a child span

WHEN a CC instance spawned under a `butler.cc_session` span calls the `state_get` tool
THEN a span named `butler.tool.state_get` MUST be created
AND the span MUST be a child of the `butler.cc_session` span
AND both spans MUST share the same `trace_id`

#### Scenario: Multiple CC tool calls produce sibling spans

WHEN a CC instance calls `state_get` and then `state_set` during a single session
THEN both `butler.tool.state_get` and `butler.tool.state_set` spans MUST be children of the same `butler.cc_session` span
AND all three spans MUST share the same `trace_id`

---

### Requirement: End-to-end trace hierarchy

A complete message flow from the Switchboard through to a target butler's CC session and tool calls SHALL produce a single trace with the correct parent-child span hierarchy.

#### Scenario: Full trace from message receipt to tool execution

WHEN a Telegram message is received by the Switchboard, classified, routed to the `health` butler, and the `health` butler spawns a CC instance that calls `state_set`
THEN a single trace MUST exist containing the following span hierarchy:
- `switchboard.receive` (root)
  - `switchboard.classify` (child of receive)
    - `switchboard.route` (child of classify)
      - `butler.cc_session` (child of route, on the `health` butler)
        - `butler.tool.state_set` (child of cc_session)
AND all spans in the hierarchy MUST share the same `trace_id`

---

### Requirement: Testing with InMemorySpanExporter

Tests SHALL use the OpenTelemetry `InMemorySpanExporter` to capture and assert on spans without requiring a running telemetry backend.

#### Scenario: Test verifies span creation

WHEN a test invokes an MCP tool handler after configuring an `InMemorySpanExporter`
THEN the exporter's `get_finished_spans()` MUST contain a span with the expected name and attributes

#### Scenario: Test verifies parent-child relationships

WHEN a test triggers a flow that produces parent and child spans
THEN the child span's `parent_id` MUST equal the parent span's `span_id`
AND both spans MUST have the same `trace_id`

#### Scenario: Test verifies trace_id propagation across butler boundaries

WHEN a test simulates a Switchboard-to-Butler routing flow using `InMemorySpanExporter`
THEN the spans on the Switchboard side and the spans on the target butler side MUST share the same `trace_id`

---

### Requirement: LGTM stack integration

The local development environment SHALL include the LGTM stack (Alloy OTLP gateway, Tempo for traces, and Grafana for visualization) for distributed trace collection and visualization.

#### Scenario: OTLP gateway accepts telemetry

WHEN the local dev environment is started with the LGTM stack running
THEN the Alloy OTLP gateway MUST be accessible on port `4318`
AND the gateway MUST accept OTLP gRPC telemetry from butler instances
AND traces MUST be stored in Tempo for later querying

#### Scenario: Butler spans appear in Grafana Tempo

WHEN a butler daemon is running with `OTEL_EXPORTER_OTLP_ENDPOINT` set to `http://otel.parrot-hen.ts.net:4318`
AND MCP tool calls are made to the butler
THEN the corresponding spans MUST be visible in Grafana Explore with the Tempo datasource under the service name matching the butler's name

---

### Requirement: Sampling configuration

OTel trace sampling SHALL be 100% in local development and configurable in production via the `OTEL_TRACES_SAMPLER` environment variable.

#### Scenario: Default sampling in dev is 100%

WHEN a butler daemon starts without the `OTEL_TRACES_SAMPLER` environment variable set
THEN the trace sampler MUST default to `always_on` (100% sampling)
AND every span MUST be exported

#### Scenario: Production sampling is configurable

WHEN the `OTEL_TRACES_SAMPLER` environment variable is set to `parentbased_traceidratio`
AND `OTEL_TRACES_SAMPLER_ARG` is set to `0.1`
THEN the tracer MUST sample approximately 10% of traces
AND the sampling decision MUST be respected by the configured `TracerProvider`
