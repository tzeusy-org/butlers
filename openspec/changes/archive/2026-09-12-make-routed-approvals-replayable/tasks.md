## 1. Canonical Routed Commands

- [x] 1.1 Add failing exact-payload tests for parked email, Telegram, and WhatsApp routed deliveries
- [x] 1.2 Add a failing test that email reply without authoritative thread identity parks nothing and sends nothing
- [x] 1.3 Implement one native route-delivery command used by rule matching, parking, and immediate execution
- [x] 1.4 Add a behavior test proving approved email reply replay executes the registered handler exactly once

## 2. Truthful Retry Outcomes

- [x] 2.1 Add failing API tests for unreachable dispatch and reachable executor rejection at both Retry endpoints
- [x] 2.2 Implement classified internal dispatch outcomes with bounded safe failure detail
- [x] 2.3 Preserve approved status, null execution result, and failure audit events on executor rejection

## 3. Verification and Delivery

- [x] 3.1 Validate the OpenSpec change and run focused routing, daemon, and API tests
- [x] 3.2 Run Ruff on touched Python and broader relevant tests
- [x] 3.3 Complete independent exact-head review and resolve actionable findings
- [x] 3.4 Commit, push, open the pull request, and update the Bead with verification evidence
