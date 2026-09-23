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
- [ ] 3.2a In L3, extract the pure production target-selection/policy/
  compatibility/endpoint resolver and expose Switchboard's internal read-only
  `GET /internal/control-plane/route-preflight` for a server-selected fixed
  domain target. Coalesce concurrent reads and rate-bound the internal identity
  GET; stale cache fails closed. Reuse the shared identity verifier without reserve/record;
  make no target tool call or durable evidence write. Test ready, no target,
  denied policy, stale epoch, DB failure, mismatch, timeout, and zero writes
  to routing log, registry, inbox, session, ingestion, and notification seams.
  Q4 consumes this producer; D3 must preserve its read-only boundary.
  (REQ-butler-switchboard-004; REQ-dashboard-api-063)
- [ ] 3.3 Remove derived remote staleness from local cron/deadline and QA patrol admission while retaining explicit administrative stop. Test QA and eligibility-sweep progress during forced registry expiry. (REQ-staffer-qa-006)
- [ ] 3.4 After the replacement passes cutover evidence, retire daemon heartbeat reporter and `POST /api/switchboard/heartbeat` mutation; retain owner auth and prove a legacy POST writes no observation or policy. (REQ-butler-control-plane-liveness-006)

## 4. QA, conditions, and owner visibility

- [ ] 4.1 Migrate `public.qa_patrols` with nullable origin, enabled-source snapshot/config digest, and discovery-complete evidence; update scheduled and synthetic writers so genuine completed `suppressed` patrols with all current enabled sources successful qualify, synthetic and ambiguous legacy rows do not. Add independent controller checks for complete fleet snapshots and qualifying QA patrol age, one versioned fleet condition with per-daemon impact, and incomplete-snapshot non-resolution. Migrate active legacy per-butler condition impact into fleet coverage without false recovery or duplicate pages; test common-cause correlation, recurrence, partial recovery, all six statuses, config change, and synthetic/legacy ambiguity against real PostgreSQL. (REQ-staffer-qa-008; REQ-butler-control-plane-liveness-005/007; REQ-dashboard-api-063)
- [ ] 4.2 Extend runtime-attention outbox source/edge-key, fixed payload and snapshot allowlists, unique condition-episode identity, narrow producer grant, forced-RLS/bootstrap parity, and durable condition-side emission marker/status without changing existing breaker/fleet-halt rows. Verify real-PostgreSQL concurrent appends, retention, and rollback. (REQ-butler-control-plane-liveness-007)
- [ ] 4.3 Wire the independently supervised controller to append or repair one due attention intent per fleet/QA condition episode within its attention window. Test QA absent, concurrent scans, controller restart, interrupted append, and condition resolution without duplicate rows. (REQ-butler-control-plane-liveness-007)
- [ ] 4.4 Extend Switchboard's fenced worker formatter for fixed content-blind fleet/QA messages and expose linked pending/sending/sent/failed/uncertain/worker-unavailable condition status. Test confirmed, definitive pre-send, ambiguous, dead-claim, and worker-down outcomes; prohibit automatic uncertain resend. (REQ-butler-control-plane-liveness-007)
- [ ] 4.5 Link QA `infra_state` findings to the fleet condition before investigation claim; preserve discovery evidence without one case per daemon, including when LLM/GitHub authority is unavailable. (REQ-staffer-qa-007)
- [ ] 4.6 Update System Overview's heartbeat projection: legacy `last_heartbeat_at`/age refer only to the last successfully verified healthy receiver observation, new `last_probe_at` records the latest attempt, and row/UI status derives from observation plus policy/eligibility rather than age alone. Retain session DTO and degraded-schema behavior; test a fresh failed probe after an earlier healthy one. (REQ-system-overview-page-007)
- [ ] 4.7 Reconcile QA's binding manifesto with the five shipped discovery sources and remove its legacy direct-self-healing fallback. (REQ-staffer-qa-006/007)

## 5. Readiness and deployment

- [ ] 5.1 Q4 alone implements canonical exact public `GET /ready` and its
  OwnerAuthMiddleware method/path exception in `src/butlers/api/app.py`. Its
  Dashboard controller consumes L3's cached content-blind Switchboard
  preflight along with DB, exact roster, observer, fleet, qualifying QA patrol,
  and supervised-loop checks; the public request causes no network fanout or
  write. Test mounted owner auth, fixed Boolean projection, stale/missing
  preflight, no private content, process-only `/health`, and no transactional
  target-acceptance claim. K3s/Compose only consume this route.
  (REQ-dashboard-api-063; REQ-butler-switchboard-004)
- [ ] 5.2 Q5 gates deployment completion on two distinct complete observer
  cycles and two distinct current-config all-source-success scheduled QA
  patrol completions after window start, while every sampled `/ready` result
  remains true beyond the longest fleet TTL. Derive the finite timeout from
  two configured patrol cadences plus that TTL and a ten-minute margin
  (35 minutes at current defaults); reject shorter overrides. Test repeated
  reads of one patrol, nonqualifying patrols, false-sample reset, timeout,
  and the five-minute post-restart false-green window. Record only fixed
  content-blind failure categories and rollback classification.
  (REQ-dashboard-api-064)
- [ ] 5.3 Reconcile the active k3s `Readiness probe endpoint` delta with the
  stronger `/ready` semantics before either change archives. The k3s worker
  wires its chart probes to Q4's route and does not author a second endpoint,
  auth exception, or duplicate behavior gate. Preserve the existing external
  minimal health monitor and require separate owner adoption for any external
  functional target. (REQ-dashboard-api-063/065)

## 6. Contract and terminal verification

- [ ] 6.1 Align RFCs 0001, 0003, 0007, 0008, 0015, topology, operator runbooks, and affected API/roster spec consumers with this adopted behavior; audit active deltas for whole-requirement overwrite risk. (All requirements)
- [ ] 6.2 Run mounted owner-auth plus daemon/route integration tests, real-PostgreSQL migration and race tests, QA scheduler fault injection, semantic readiness and deployment canary tests, and content-blind attention/worker failure checks; then complete protected CI and a terminal adversarial reconciliation without claiming monitor provisioning or live historical delivery. (All requirements)
