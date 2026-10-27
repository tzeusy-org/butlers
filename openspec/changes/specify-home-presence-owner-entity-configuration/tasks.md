## 1. Draft the contract

- [x] 1.1 Specify the exact owner-control prerequisite, GET/PUT envelopes,
  `no-store`, bounded/deduplicated/locally-observed validation preserving the
  non-person room-sensor case, version-CAS write semantics (including the
  absent-row `version=0` insert path), atomic whole-row visibility, and
  malformed-value fail-closed behavior.
- [x] 1.2 Specify audit/log/metric/trace allowlists, the structural
  `DashboardAuditMiddleware` path/method exemption, and the MCP/LLM/provider
  absence requirement.
- [x] 1.3 Enumerate the pre-existing generic state API and state MCP tool
  paths for this exact key and specify, in both design and spec, that this
  contract's guarantees do not extend to them.
- [x] 1.4 Name `bu-pb6oy` as the unresolved browser-auth prerequisite and
  keep `authenticated_principal()` limited to server-derived attribution.

## 2. Approval gates

- [ ] 2.1 Obtain independent privacy/security review of the exact draft head.
- [ ] 2.2 After review passes, obtain separate owner approval naming the
  exact artifact. Any semantic change requires fresh review and approval.
- [ ] 2.3 Keep `bu-bdlr3`, deployment, and real identifier submission blocked
  until their own prerequisites and authorities are satisfied.

## 3. Future implementation after approval (`bu-bdlr3` or an approved successor)

- [ ] 3.1 Add the fail-closed dashboard routes only after the separately
  approved `bu-pb6oy` credential-transport mechanism exists. Prove
  authentication finishes before body buffering, pool acquisition, or
  protected reads.
- [ ] 3.2 Implement the version-CAS transaction (advisory lock, insert-if-
  absent, unchanged-no-op, stale-version conflict) directly against the
  existing `state` row -- no migration, no parallel idempotency-key table.
- [ ] 3.3 Exempt the exact PUT route from generic audit body capture in
  `DashboardAuditMiddleware` and implement the field-by-field response,
  error, audit, log, metric, and trace allowlists.
- [ ] 3.4 Add the dashboard form only after `bu-pb6oy` ships, keeping
  submitted values out of navigation state, browser persistence, query
  keys, retained mutation cache, analytics, console output, and rendered
  errors.

## 4. Future verification after approval (`bu-bdlr3` or an approved successor)

- [ ] 4.1 Real PostgreSQL tests at the migrated Home schema prove the no-row,
  cleared-row, and configured GET responses; the first-ever insert at
  version 1; a matching-version commit; and an exact-resubmission no-op.
- [ ] 4.2 Real PostgreSQL tests cover duplicate members, out-of-bounds
  count/length, non-array/unknown-field bodies, unobserved
  `ha_entity_snapshot` references (including a stale-but-observed row as a
  valid reference), and the non-person room-sensor acceptance case.
- [ ] 4.3 Real PostgreSQL concurrency tests force two competing PUTs through
  the fixed advisory lock and prove at most one commits on a divergent
  submission (the other returns `409 VERSION_CONFLICT`), while concurrent
  identical submissions converge on one committed version.
- [ ] 4.4 An injected pre-commit failure test proves the prior value/version
  survive unchanged and no response claims success.
- [ ] 4.5 API tests prove `503` when owner control is unconfigured and `401`
  for a missing/wrong credential, in both cases before body read or pool
  access; and prove the fixed `503 PRESENCE_CONFIG_UNAVAILABLE` response for
  a malformed pre-existing stored value on both GET and PUT.
- [ ] 4.6 A real-Postgres end-to-end test configures a synthetic `person`
  plus a synthetic room-sensor entity through the API, then runs
  `run_home_presence_context_producer` and asserts it consumes exactly the
  committed list for `at_home`/`in_space`, and that the PUT itself produced
  no producer invocation or context change.
- [ ] 4.7 Extend `DashboardAuditMiddleware`'s nearest existing unit test to
  assert the exact PUT route bypasses body collection (including for
  unauthenticated and malformed-JSON calls) while unrelated mutations remain
  audited.
- [ ] 4.8 Privacy absence-sentinel tests plant distinct synthetic entity-id
  sentinels and assert their absence from response bodies (on every error
  path), explicit and generic audit rows, captured logs, metric
  labels/span attributes, and any mounted MCP/runtime tool registry; assert
  the explicit audit allowlist positively so an empty-audit bug cannot pass
  the absence check vacuously.
- [ ] 4.9 A companion test (or an assertion in 4.8) demonstrates that a
  concurrent write via the generic `PUT /api/butlers/home/state/...` route
  or the `state_set` MCP tool is not blocked by, or serialized against, this
  capability's advisory lock -- proving the D7/honest-boundary claim rather
  than merely asserting it in prose.
- [ ] 4.10 Run targeted API/real-Postgres tests, repo guards
  (`make check-guards`), strict OpenSpec and overwrite checks, lint/format,
  a fresh independent exact-head privacy/security review, and terminal
  hosted CI. Report the implementation PR's actual test delta separately.

## 5. Archive only after implementation

- [ ] 5.1 After the separately approved implementation is merged, sync the
  new capability to `openspec/specs/home-presence-configuration/spec.md` and
  archive this change. Archival does not authorize deployment or real
  identifier submission.
