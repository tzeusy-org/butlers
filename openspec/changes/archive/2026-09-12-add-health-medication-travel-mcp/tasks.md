## 1. Typed Contract and Health Provider

- [x] 1.1 Add failing tests for strict versioning, field minimization, and success/error invariants
- [x] 1.2 Implement the shared `health.medication-travel.v1` Pydantic contract
- [x] 1.3 Add failing Health provider tests for active-only projection, private-field exclusion, and empty success
- [x] 1.4 Implement and register the Health `medication_travel_snapshot` MCP provider

## 2. Travel Consumer and Routing

- [x] 2.1 Add failing Travel consumer tests for Switchboard routing, permission denial, unavailable Health, malformed response, and empty success
- [x] 2.2 Implement the Travel consumer, permission gate, response validation, and runtime wiring
- [x] 2.3 Register the parameterless Travel `health_medication_snapshot` MCP tool

## 3. Cross-Butler Proof and Verification

- [x] 3.1 Add an in-process cross-butler MCP integration test proving Travel reaches Health through Switchboard without Health-schema SQL
- [x] 3.2 Run strict OpenSpec validation and focused provider, consumer, integration, and architecture contract tests
- [x] 3.3 Run right-sized Ruff and pytest gates and inspect the final diff for scope and privacy safety
