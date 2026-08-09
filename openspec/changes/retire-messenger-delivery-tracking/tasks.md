## 1. Retirement contract

- [x] 1.1 Record the safe deletion boundary in an OpenSpec proposal, design, and delta specs.
- [x] 1.2 Add `msg_003` with an all-table transaction-scoped lock, non-empty-table protection, and an exact empty-only downgrade.

## 2. Surface removal

- [x] 2.1 Remove the unwired Messenger module, tracking/reliability tools, and fabricated API.
- [x] 2.2 Remove Messenger dashboard API client/types/cache hooks and bespoke tab registration.
- [x] 2.3 Update Messenger and dashboard contracts, frontend inventory, topology/runbook documentation, and generated flow/schema diagrams.

## 3. Verification

- [x] 3.1 Add real-PostgreSQL migration race/schema tests plus constructed-app, actual-FastMCP, route, and frontend absence tests.
- [x] 3.2 Run targeted local migration and schema-matrix, route, MCP/API, frontend, diagram-render, and strict OpenSpec quality gates on the delivered head.
- [ ] 3.3 Obtain terminal hosted CI for the delivered PR head.
