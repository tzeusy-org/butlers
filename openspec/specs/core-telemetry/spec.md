# Telemetry

## Purpose
Provides OpenTelemetry tracing initialization, structured logging with butler identity and trace context injection, metric instruments for spawner concurrency and route processing, and defense-in-depth credential redaction in log output.

## ADDED Requirements

### Requirement: OpenTelemetry Tracer Initialization
`init_telemetry(service_name)` configures a `TracerProvider` with OTLP gRPC exporter when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. When the endpoint is not set, a no-op tracer is returned. The provider is installed once per process; subsequent calls for additional butlers reuse the existing provider and return a correctly-named tracer.

#### Scenario: OTLP endpoint configured
- **WHEN** `OTEL_EXPORTER_OTLP_ENDPOINT` is set and `init_telemetry("butler-health")` is called
- **THEN** a `TracerProvider` with `BatchSpanProcessor` and `OTLPSpanExporter` is configured
- **AND** the resource carries `service.name="butlers"`
- **AND** a real `Tracer` is returned

#### Scenario: No OTLP endpoint
- **WHEN** `OTEL_EXPORTER_OTLP_ENDPOINT` is not set
- **THEN** a no-op tracer is returned and no provider is installed

#### Scenario: Multiple butlers in same process
- **WHEN** `init_telemetry()` is called a second time for a different butler
- **THEN** the existing provider is reused (no "Overriding of current TracerProvider" warning)
- **AND** a tracer with the new service name is returned

### Requirement: Butler Span Attribution
`tag_butler_span(span, butler_name)` sets `butler.name` and `service.name` (as `butler.<name>`) on any span. This enables per-butler filtering in observability backends when all butlers share a single TracerProvider.

#### Scenario: Span tagged with butler identity
- **WHEN** `tag_butler_span(span, "health")` is called
- **THEN** the span has attributes `butler.name="health"` and `service.name="butler.health"`

### Requirement: Tool Span Wrapper
`tool_span(tool_name, butler_name)` creates an OpenTelemetry span named `butler.tool.<tool_name>` usable as both a context manager and async decorator. Each invocation creates a fresh span instance (safe for concurrent async calls). Exceptions are recorded with stack trace and span status set to ERROR.

#### Scenario: Context manager usage
- **WHEN** `with tool_span("state_get", butler_name="switchboard"):` is used
- **THEN** a span `butler.tool.state_get` is created with butler attribution

#### Scenario: Decorator usage on async function
- **WHEN** `@tool_span("state_get", butler_name="switchboard")` decorates an async function
- **THEN** each invocation creates a fresh span (no concurrency bugs from shared state)

#### Scenario: Exception recorded on span
- **WHEN** an exception is raised within a tool_span context
- **THEN** the span's status is set to ERROR with the exception message and a stack trace is recorded

### Requirement: Active Session Context Propagation
The spawner stores the active LLM session's OTel context in a `ContextVar` before invoking the runtime. Tool handlers (running in separate HTTP handler tasks that don't inherit contextvars) read this context to parent their spans to the session span.

#### Scenario: Tool span parents to session span
- **WHEN** a tool handler runs during an active session and `get_active_session_context()` returns a context
- **THEN** the tool span is created as a child of the session span (shared trace ID)

#### Scenario: No active session
- **WHEN** `get_active_session_context()` returns `None`
- **THEN** tool spans create root spans (new trace IDs)

### Requirement: W3C Trace Context Propagation
`inject_trace_context()` serializes the current context into a dict with `traceparent`/`tracestate` keys. `extract_trace_context(dict)` deserializes a carrier dict into an OTel `Context`. `get_traceparent_env()` returns `{"TRACEPARENT": "..."}` for passing to spawned subprocess environments.

#### Scenario: Inject and extract round-trip
- **WHEN** `inject_trace_context()` is called within an active span
- **THEN** a dict containing `traceparent` is returned
- **AND** `extract_trace_context(dict)` can reconstruct the parent context

#### Scenario: TRACEPARENT for subprocess
- **WHEN** `get_traceparent_env()` is called within an active span
- **THEN** it returns `{"TRACEPARENT": "<value>"}` suitable for subprocess environment

### Requirement: Structured Logging with Butler Context
`configure_logging(level, fmt, log_root, butler_name)` sets up structlog-based logging with two formats: `text` (colored console, HH:MM:SS timestamps) and `json` (JSON lines, ISO timestamps). Processors inject `butler` (from ContextVar), `trace_id`, and `span_id` (from current OTel span) into every log record.

#### Scenario: Text format logging
- **WHEN** `configure_logging(level="INFO", fmt="text")` is called
- **THEN** console output uses colored, human-readable format with HH:MM:SS timestamps

#### Scenario: JSON format logging
- **WHEN** `configure_logging(level="INFO", fmt="json")` is called
- **THEN** console output uses JSON lines format with ISO timestamps

#### Scenario: Butler identity in logs
- **WHEN** a log record is emitted by a butler
- **THEN** the `butler` field in the log record matches the butler name from the ContextVar

#### Scenario: OTel trace context in logs
- **WHEN** a log record is emitted within an active OTel span
- **THEN** `trace_id` and `span_id` are injected into the log event dict

### Requirement: File Logging with Directory Layout
When `log_root` is configured, structured JSON log files are written to `{log_root}/butlers/{name}.log` for application logs and `{log_root}/uvicorn/{name}.log` for transport logs. A `connectors/` subdirectory is also created.

#### Scenario: File logs created
- **WHEN** `configure_logging(log_root=Path("logs"), butler_name="health")` is called
- **THEN** `logs/butlers/health.log` and `logs/uvicorn/health.log` file handlers are created

### Requirement: Credential Redaction Filter
A `CredentialRedactionFilter` is attached to the root logger, scrubbing Telegram bot tokens (`/bot<id>:<token>/`) and Bearer tokens (`Bearer <token>`) from all log records before they reach any handler. This fires on every record including third-party libraries.

#### Scenario: Telegram bot token redacted
- **WHEN** a log message contains `/bot12345:ABCdef123/`
- **THEN** it is replaced with `/bot[REDACTED]/`

#### Scenario: Bearer token redacted
- **WHEN** a log message contains `Bearer eyJhbGciOi...`
- **THEN** it is replaced with `Bearer [REDACTED]`

### Requirement: OpenTelemetry Metrics Initialization
`init_metrics(service_name)` configures a `MeterProvider` with periodic OTLP gRPC exporter when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. Otherwise, a no-op meter is used. Installed once per process.

#### Scenario: Metrics endpoint configured
- **WHEN** `OTEL_EXPORTER_OTLP_ENDPOINT` is set
- **THEN** a real `MeterProvider` with 15-second export interval is installed

### Requirement: Spawner Metric Instruments
Three spawner instruments: `butlers.spawner.active_sessions` (UpDownCounter), `butlers.spawner.queued_triggers` (UpDownCounter), `butlers.spawner.session_duration_ms` (Histogram). All carry a `butler` label.

#### Scenario: Session duration recorded
- **WHEN** `metrics.record_session_duration(duration_ms)` is called
- **THEN** the duration is recorded on the `butlers.spawner.session_duration_ms` histogram with the `butler` attribute

### Requirement: Route Metric Instruments
Three route instruments: `butlers.route.accept_latency_ms` (Histogram), `butlers.route.queue_depth` (UpDownCounter), `butlers.route.process_latency_ms` (Histogram). All carry a `butler` label.

#### Scenario: Route accept latency recorded
- **WHEN** a route.execute call is accepted
- **THEN** `record_route_accept_latency(latency_ms)` records the accept phase duration

### Requirement: Buffer Metric Instruments
Six buffer instruments: `butlers.buffer.queue_depth` (UpDownCounter), `butlers.buffer.enqueue_total` (Counter with path=hot|cold), `butlers.buffer.backpressure_total` (Counter), `butlers.buffer.scanner_recovered_total` (Counter), `butlers.buffer.process_latency_ms` (Histogram), `butlers.switchboard.queue.dequeue_by_tier` (Counter with policy_tier and starvation_override labels).

#### Scenario: Hot path enqueue recorded
- **WHEN** a message is enqueued via the hot path
- **THEN** `buffer_enqueue_hot()` increments the counter with `path="hot"`

### Requirement: Metric Namespace Convention
All metric instruments use the `butlers.` namespace prefix. Instruments are lazily created from the global MeterProvider (safe to construct before `init_metrics` is called).

#### Scenario: Lazy instrument creation
- **WHEN** a `ButlerMetrics` instance records before `init_metrics()` is called
- **THEN** recordings are silent no-ops (no errors)
