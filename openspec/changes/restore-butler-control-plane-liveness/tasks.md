## 1. State and migration

- [ ] 1.1 Add role-bound server-allocated durable boot epoch registration, separated observation/policy/compatibility schema, and shared DB-reserved probe sequence; retain legacy eligibility read projection. Verify monotonic registration, repeated UUID, old-epoch rejection despite newer probe sequence, rollback, GRANT/RLS replay, and concurrency on real PostgreSQL. (REQ-butler-control-plane-liveness-001/002/008)
- [ ] 1.2 Classify all legacy quarantines from operator versus TTL provenance; retain source evidence and fail ambiguous rows closed as `review_required`. Verify restart and rollback cannot activate them. (REQ-butler-control-plane-liveness-002/003)
- [ ] 1.3 Audit every existing writer and reader of `eligibility_state`, `quarantined_at`, heartbeat, registration, and route success; route each through its permitted dimension before policy cutover. (REQ-butler-control-plane-liveness-002/003)

## 2. Receiver observation

- [ ] 2.1 Register the daemon boot after exact-port binding and before route acceptance; expose boot UUID and committed monotonic epoch with bounded identity/acceptance facts. Verify registration failure and old-binary rollback fail closed without authoring liveness. (REQ-butler-base-spec-002/003/004; REQ-butler-control-plane-liveness-001)
- [ ] 2.2 Add a separately supervised roster-bound Dashboard periodic observer in shadow mode and shared Switchboard verifier/DB reserve-record functions, including exact endpoint validation, bounded fanout, deadline and response-size limits, DB-server time, and epoch-plus-sequence fencing. Verify wrong name/port, malformed response, forbidden daemon timestamp, older boot with higher sequence, and observer failure. (REQ-butler-control-plane-liveness-001/005/008)
- [ ] 2.3 Compare old and new eligibility under auth-enabled operation across multiple TTL windows; publish content-blind mismatch evidence before switching route authority. (REQ-butler-control-plane-liveness-001/002)

## 3. Route and scheduler cutover

- [ ] 3.1 Make Switchboard derive routing eligibility from receiver observation, administrative policy, and compatibility for butlers and staffers; confirm startup, route success, and probes cannot clear owner quarantine. (REQ-butler-control-plane-liveness-002; REQ-butler-switchboard-002)
- [ ] 3.2 Add one bounded cross-process Switchboard stale-target recheck using its own exact-roster probe and narrow DB-reserved sequence/CAS, returning canonical `not_attempted` evidence when no target call occurs. Test Dashboard/Switchboard races, recovery without restart, no owner-auth bypass, and refusal without side effects. (REQ-butler-control-plane-liveness-004/008; REQ-butler-switchboard-002)
- [ ] 3.3 Remove derived remote staleness from local cron/deadline and QA patrol admission while retaining explicit administrative stop. Test QA and eligibility-sweep progress during forced registry expiry. (REQ-staffer-qa-006)
- [ ] 3.4 After the replacement passes cutover evidence, retire daemon heartbeat reporter and `POST /api/switchboard/heartbeat` mutation; retain owner auth and prove a legacy POST writes no observation or policy. (REQ-butler-control-plane-liveness-006)

## 4. QA, conditions, and owner visibility

- [ ] 4.1 Add independent controller checks for complete fleet snapshots and QA patrol age, one versioned fleet condition with per-daemon impact, complete-source successful-patrol evidence, and incomplete-snapshot non-resolution. Migrate active legacy per-butler condition impact into fleet coverage without false recovery or duplicate pages; test common-cause correlation, recurrence, partial recovery, and all six patrol statuses. (REQ-butler-control-plane-liveness-005/007; REQ-dashboard-api-063)
- [ ] 4.2 Extend runtime-attention outbox source/edge-key, fixed payload and snapshot allowlists, unique condition-episode identity, narrow producer grant, forced-RLS/bootstrap parity, and durable condition-side emission marker/status without changing existing breaker/fleet-halt rows. Verify real-PostgreSQL concurrent appends, retention, and rollback. (REQ-butler-control-plane-liveness-007)
- [ ] 4.3 Wire the independently supervised controller to append or repair one due attention intent per fleet/QA condition episode within its attention window. Test QA absent, concurrent scans, controller restart, interrupted append, and condition resolution without duplicate rows. (REQ-butler-control-plane-liveness-007)
- [ ] 4.4 Extend Switchboard's fenced worker formatter for fixed content-blind fleet/QA messages and expose linked pending/sending/sent/failed/uncertain/worker-unavailable condition status. Test confirmed, definitive pre-send, ambiguous, dead-claim, and worker-down outcomes; prohibit automatic uncertain resend. (REQ-butler-control-plane-liveness-007)
- [ ] 4.5 Link QA `infra_state` findings to the fleet condition before investigation claim; preserve discovery evidence without one case per daemon, including when LLM/GitHub authority is unavailable. (REQ-staffer-qa-007)
- [ ] 4.6 Update System Overview's heartbeat projection to receiver-observed timestamps and separate health/policy/compatibility fields; retain legacy timestamp/session DTO behavior and degraded schema reads. (REQ-system-overview-page-007)
- [ ] 4.7 Reconcile QA's binding manifesto with the five shipped discovery sources and remove its legacy direct-self-healing fallback. (REQ-staffer-qa-006/007)

## 5. Readiness and deployment

- [ ] 5.1 Strengthen canonical public `GET /ready` with cached content-blind checks for DB, exact roster, observer progress, entire fleet, QA patrol, supervised loops, and effect-free route canary; retain process-only `/health` and narrow owner-auth allowlist. Test no private response data or canary side effects. (REQ-dashboard-api-063)
- [ ] 5.2 Gate canonical deployment completion on multiple observer/QA advances and sustained semantic readiness beyond one liveness TTL; record a bounded failing-category receipt and rollback classification. Test the five-minute post-restart false-green window. (REQ-dashboard-api-064)
- [ ] 5.3 Reconcile the active k3s `Readiness probe endpoint` delta with the stronger `/ready` semantics before either change archives; preserve the existing external minimal health monitor and require separate owner adoption for any external functional target. (REQ-dashboard-api-063/065)

## 6. Contract and terminal verification

- [ ] 6.1 Align RFCs 0001, 0003, 0007, 0008, 0015, topology, operator runbooks, and affected API/roster spec consumers with this adopted behavior; audit active deltas for whole-requirement overwrite risk. (All requirements)
- [ ] 6.2 Run mounted owner-auth plus daemon/route integration tests, real-PostgreSQL migration and race tests, QA scheduler fault injection, semantic readiness and deployment canary tests, and content-blind attention/worker failure checks; then complete protected CI and a terminal adversarial reconciliation without claiming monitor provisioning or live historical delivery. (All requirements)
