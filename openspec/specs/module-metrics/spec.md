# Metrics Module

## Purpose

The Metrics module is a reusable, opt-in module that gives a butler LLM-facing access to Prometheus-based operational metrics: it can define named metrics, emit samples, and query historical data via PromQL. Write-side emission goes through the OpenTelemetry SDK already wired in the framework; read-side queries hit the Prometheus HTTP API directly. Metric definitions are persisted in the butler's state store so they survive restarts.

## ADDED Requirements

### Requirement: Module conforms to Module ABC

MetricsModule SHALL subclass `butlers.modules.base.Module` and implement all abstract members: `name` (returns `"metrics"`), `config_schema`, `dependencies` (empty list), `register_tools`, `migration_revisions` (returns `None` — no DB migrations), `on_startup`, and `on_shutdown`.

#### Scenario: Module is auto-discovered by registry

- **WHEN** `ModuleRegistry.default_registry()` is called
- **THEN** a module named `"metrics"` MUST appear in `available_modules`

#### Scenario: Module declares no migrations

- **WHEN** `migration_revisions()` is called on MetricsModule
- **THEN** it MUST return `None`

---

### Requirement: Module configuration

MetricsModule's `config_schema` SHALL return a Pydantic model with one field: `prometheus_query_url` (str, required) — the base URL of the Prometheus HTTP query endpoint (e.g., `http://prometheus:9090`). The module SHALL NOT use a separate write-side URL; writes go through the global OTEL MeterProvider already configured by `init_metrics`.

#### Scenario: Missing prometheus_query_url raises validation error

- **WHEN** the module is loaded with a config block that omits `prometheus_query_url`
- **THEN** Pydantic validation MUST raise a `ValidationError`

#### Scenario: Valid config passes validation

- **WHEN** `prometheus_query_url` is provided as a non-empty string
- **THEN** the config object MUST be created successfully

---

### Requirement: Metric naming and namespace isolation

All metrics defined through MetricsModule SHALL be auto-prefixed with `butler_<butler_name>_` where `<butler_name>` is the hosting butler's name with hyphens replaced by underscores. The butler provides its name to the module at `on_startup`. Metric names provided by the LLM via MCP tools MUST be validated against the pattern `^[a-z][a-z0-9_]*$` (lowercase, alphanumeric plus underscore, starting with a letter) before the prefix is applied.

#### Scenario: Metric name is auto-namespaced

- **WHEN** a butler named `"finance"` calls `metrics_define` with name `"api_calls"`
- **THEN** the Prometheus metric name MUST be `"butler_finance_api_calls"`

#### Scenario: Invalid metric name is rejected

- **WHEN** `metrics_define` is called with a name containing uppercase letters, spaces, or leading digits
- **THEN** the tool MUST return an error without registering anything

---

### Requirement: Metric catalogue cap

The module SHALL enforce a hard limit of **1,000 defined metrics per butler**. Attempting to define a metric when the catalogue already contains 1,000 entries MUST return an error without registering anything. The `metrics_define` tool description MUST include a standing advisory warning the caller against using high-cardinality values as label values (e.g. user IDs, UUIDs, request IDs) because each unique label combination creates a new Prometheus time series.

#### Scenario: Cap blocks registration at 1000

- **WHEN** the catalogue already contains 1,000 metric definitions and `metrics_define` is called
- **THEN** the tool MUST return an error stating the cap has been reached
- **AND** no new instrument MUST be created or persisted

#### Scenario: Registration succeeds below cap

- **WHEN** fewer than 1,000 metrics are defined and `metrics_define` is called with a valid new name
- **THEN** registration MUST proceed normally

---

### Requirement: Metric definition and registration (metrics_define)

The `metrics_define` MCP tool SHALL register a new metric definition. Parameters: `name` (str, user-supplied, pre-namespace), `type` (enum: `counter` | `gauge` | `histogram`), `help` (str, human-readable description), `labels` (list[str], optional, default `[]`). On success it SHALL:

1. Create the corresponding OTEL instrument via `get_meter()` (`Counter`, `UpDownCounter`, or `Histogram`)
2. Persist the definition to the butler's state store under key `metrics_catalogue:<name>` as a JSON object `{name, type, help, labels, registered_at}`
3. Cache the live instrument in-process for subsequent `metrics_emit` calls

#### Scenario: Counter is defined and registered

- **WHEN** `metrics_define` is called with `type="counter"`, `name="emails_processed"`, `help="Total emails processed"`
- **THEN** an OTEL `Counter` named `butler_<butler>_emails_processed` MUST be created via `get_meter().create_counter()`
- **AND** the definition MUST be persisted to the state store

#### Scenario: Gauge is defined and registered

- **WHEN** `metrics_define` is called with `type="gauge"`
- **THEN** an OTEL `UpDownCounter` MUST be created via `get_meter().create_up_down_counter()`

#### Scenario: Histogram is defined and registered

- **WHEN** `metrics_define` is called with `type="histogram"`
- **THEN** an OTEL `Histogram` MUST be created via `get_meter().create_histogram()`

#### Scenario: Redefining an existing metric returns the cached instrument

- **WHEN** `metrics_define` is called with a name that is already registered
- **THEN** the existing in-process instrument MUST be returned without creating a new one
- **AND** the state store entry MUST be left unchanged

---

### Requirement: Metric emission (metrics_emit)

The `metrics_emit` MCP tool SHALL record a value against a previously defined metric. Parameters: `name` (str, pre-namespace), `value` (float), `labels` (dict[str, str], optional, default `{}`). The tool SHALL reject calls where `labels` keys do not match the label names declared in the metric's definition.

For `counter` metrics, `value` MUST be non-negative (counters cannot decrease); the tool SHALL return an error if a negative value is passed. For `gauge` (`UpDownCounter`) metrics, any float is valid. For `histogram` metrics, any non-negative float is valid.

#### Scenario: Emit counter increment

- **WHEN** `metrics_emit` is called with a defined counter metric and `value=1.0`
- **THEN** `instrument.add(1.0, attributes)` MUST be called on the cached OTEL Counter

#### Scenario: Emit gauge

- **WHEN** `metrics_emit` is called with a defined gauge metric and `value=-5.0`
- **THEN** `instrument.add(-5.0, attributes)` MUST be called on the cached OTEL UpDownCounter

#### Scenario: Emit histogram observation

- **WHEN** `metrics_emit` is called with a defined histogram metric and `value=120.5`
- **THEN** `instrument.record(120.5, attributes)` MUST be called on the cached OTEL Histogram

#### Scenario: Emit on undefined metric returns error

- **WHEN** `metrics_emit` is called with a name that has not been defined via `metrics_define`
- **THEN** the tool MUST return an error message and MUST NOT call any OTEL instrument

#### Scenario: Negative counter value is rejected

- **WHEN** `metrics_emit` is called with a counter metric and a negative `value`
- **THEN** the tool MUST return an error

#### Scenario: Label mismatch is rejected

- **WHEN** `metrics_emit` is called with label keys that differ from the metric's declared label names
- **THEN** the tool MUST return an error

---

### Requirement: Catalogue listing (metrics_list)

The `metrics_list` MCP tool SHALL return all metric definitions persisted in the state store for the hosting butler. It reads all state store keys matching the prefix `metrics_catalogue:` and returns the list of definition objects `{name, type, help, labels, registered_at}`.

#### Scenario: Returns all defined metrics

- **WHEN** three metrics have been defined via `metrics_define`
- **THEN** `metrics_list` MUST return exactly three entries

#### Scenario: Returns empty list when none defined

- **WHEN** no metrics have been defined
- **THEN** `metrics_list` MUST return an empty list without error

---

### Requirement: Instant PromQL query (metrics_query)

The `metrics_query` MCP tool SHALL execute an instant PromQL query against the configured `prometheus_query_url`. Parameters: `query` (str, PromQL expression), `time` (str, optional RFC3339 or Unix timestamp; defaults to now). It SHALL call `GET <prometheus_query_url>/api/v1/query` with `query` and `time` parameters and return the parsed result vector.

#### Scenario: Successful instant query

- **WHEN** `metrics_query` is called with a valid PromQL expression
- **THEN** the tool MUST return a list of `{metric: dict, value: [timestamp, value_str]}` objects matching the Prometheus API `vector` result type

#### Scenario: PromQL parse error is surfaced

- **WHEN** `metrics_query` is called with a syntactically invalid PromQL expression
- **THEN** the Prometheus API returns a 400 with `error` field
- **AND** the tool MUST return that error message to the caller

#### Scenario: Network error returns descriptive error

- **WHEN** the Prometheus endpoint is unreachable
- **THEN** the tool MUST return an error string describing the failure rather than raising an unhandled exception

---

### Requirement: Range PromQL query (metrics_query_range)

The `metrics_query_range` MCP tool SHALL execute a range PromQL query. Parameters: `query` (str), `start` (str, RFC3339 or Unix timestamp), `end` (str), `step` (str, duration string e.g. `"1m"`, `"30s"`). It SHALL call `GET <prometheus_query_url>/api/v1/query_range` and return the parsed matrix result.

#### Scenario: Successful range query

- **WHEN** `metrics_query_range` is called with valid parameters
- **THEN** the tool MUST return a list of `{metric: dict, values: [[timestamp, value_str], ...]}` objects matching the Prometheus API `matrix` result type

#### Scenario: Step defaults validation

- **WHEN** `step` is provided as an invalid duration string
- **THEN** the Prometheus API returns a 400
- **AND** the tool MUST surface the error to the caller

---

### Requirement: Startup re-registration of persisted metric definitions

On `on_startup`, MetricsModule SHALL read all `metrics_catalogue:*` keys from the state store and recreate the corresponding OTEL instruments in-process, so that `metrics_emit` works immediately after a daemon restart without requiring the LLM to re-call `metrics_define`.

#### Scenario: Metrics survive daemon restart

- **WHEN** a daemon restarts after metrics have been defined in a previous run
- **THEN** `on_startup` MUST restore all definitions from the state store into the in-process instrument cache
- **AND** subsequent `metrics_emit` calls MUST succeed without a preceding `metrics_define`

#### Scenario: Startup with no persisted definitions is a no-op

- **WHEN** the state store contains no `metrics_catalogue:*` keys
- **THEN** `on_startup` MUST complete without error and the instrument cache MUST be empty
