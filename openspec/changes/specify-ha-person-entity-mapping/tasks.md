## 1. Draft the contract

- [x] 1.1 Specify the exact owner-control prerequisite, body/header limits,
  validation, mapping-specific advisory lock, one-transaction decision,
  durable idempotency, identical no-op, bidirectional no-remap rule, aggregate
  receipt, and content-blind failure behavior.
- [x] 1.2 Specify request-capture, URL, response/error, audit, log, telemetry,
  prompt/session, MCP, provider-read, and browser-persistence exclusions.
- [x] 1.3 Name `bu-pb6oy` as the unresolved browser-auth prerequisite and keep
  `authenticated_principal()` limited to server-derived attribution.

## 2. Approval gates

- [ ] 2.1 Obtain independent privacy/security review of the exact draft head.
- [ ] 2.2 After review passes, obtain separate owner approval naming the exact
  artifact. Any semantic change requires fresh review and approval.
- [ ] 2.3 Keep `bu-q364q`, `bu-pvapy`, deployment, mapping submission, and
  natural-transition verification blocked until their own prerequisites and
  authorities are satisfied.

## 3. Future implementation after approval (`bu-q364q`)

- [ ] 3.1 Add the fail-closed dashboard route and browser client only after the
  separately approved `bu-pb6oy` credential-transport mechanism exists. Prove
  authentication finishes before body buffering, receipt creation, pool
  acquisition, or protected reads.
- [ ] 3.2 Add the content-blind durable idempotency/receipt representation and
  transaction implementation without adding a mapping read/list API, MCP tool,
  direct-SQL operator path, entity creation, remap, update, or delete.
- [ ] 3.3 Exempt the exact route from generic audit body capture and implement
  field-by-field response, error, audit, log, metric, and trace allowlists.
- [ ] 3.4 Add the dashboard form with ephemeral-only private fields and no URL,
  navigation-state, browser-storage, query-key, retained mutation-cache,
  analytics, console, or rendered-error copy of submitted values.

## 4. Future verification after approval (`bu-q364q`)

- [ ] 4.1 Real PostgreSQL tests run the real migrations and prove a new batch,
  a mixed new/identical batch, a fresh-key identical no-op, same-key exact
  replay, and same-key/different-body conflict against the actual mapping and
  receipt tables.
- [ ] 4.2 Real PostgreSQL tests cover duplicate Home Assistant IDs, duplicate
  entity UUIDs, missing/tombstoned/merged/wrong-type entities, a legacy null
  target, conflicts in both mapping directions, and a database failure after an
  attempted insert; every refusal and injected failure leaves zero partial
  mapping writes and no false success receipt.
- [ ] 4.3 Real PostgreSQL concurrency tests force two competing batches through
  the fixed advisory lock and prove that at most one complete non-conflicting
  mapping set commits, the loser is a content-blind `409`, and no partial or
  crossed mapping survives.
- [ ] 4.4 API tests prove `503` when owner control is unconfigured, `401` for a
  missing/wrong credential, no pre-auth body/pool access, exact size/count/field
  validation, standard envelopes, aggregate-only `200/409/422/503` bodies, and
  byte-for-byte replay of the stored terminal receipt.
- [ ] 4.5 Privacy absence-sentinel tests plant distinct synthetic sentinels in
  both identifiers and assert absence from response body/headers, error details,
  generic and explicit audit rows, captured logs, rendered exception text,
  metric labels, span attributes/events/baggage, request URLs, session/prompt
  stores, browser persistence/query state, and mounted MCP/runtime tool
  registries. Assert the explicit audit field set positively so an empty-audit
  bug cannot make the absence test pass.
- [ ] 4.6 Run targeted API/real-PostgreSQL/frontend tests, repo guards, strict
  OpenSpec and overwrite checks, fresh independent exact-head privacy/security
  review, and terminal hosted CI. Report the implementation PR's actual test
  delta separately.

## 5. Archive only after implementation

- [ ] 5.1 After the separately approved implementation is merged, sync the new
  capability to `openspec/specs/home-assistant-person-mapping/spec.md` and
  archive this change. Archival does not authorize deployment or mapping use.
