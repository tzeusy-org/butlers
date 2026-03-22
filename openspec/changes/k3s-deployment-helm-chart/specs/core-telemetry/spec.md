## ADDED Requirements

### Requirement: OTEL endpoint as required in k8s

When running in k8s, the `OTEL_EXPORTER_OTLP_ENDPOINT` env var SHALL be set via Helm values (default: `http://alloy.lgtm:4318`). The telemetry module already supports this env var; no code change is needed. This requirement is fulfilled entirely by the Helm chart configuration.

#### Scenario: OTEL endpoint set by Helm values
- **WHEN** a butler Deployment is rendered by the Helm chart
- **THEN** the `OTEL_EXPORTER_OTLP_ENDPOINT` env var is set to the value from `otel.endpoint` in `values.yaml`

#### Scenario: OTEL endpoint defaults to Alloy in LGTM namespace
- **WHEN** `otel.endpoint` is not overridden in `values.yaml`
- **THEN** the default value `http://alloy.lgtm:4318` is used
