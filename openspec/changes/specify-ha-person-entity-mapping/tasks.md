## 1. Draft the contract

- [x] 1.1 Specify the exact owner-control prerequisite, body/header limits,
  validation, mapping-specific advisory lock, one-transaction decision,
  deterministic referenced-entity row locks, durable idempotency, identical
  no-op, bidirectional no-remap rule, aggregate receipt, and content-blind
  failure behavior.
- [x] 1.2 Specify request-capture, URL, response/error, audit, log, telemetry,
  prompt/session, MCP, provider-read, and browser-persistence exclusions.
- [x] 1.3 Record and consume the closed/adopted central dashboard owner-auth
  boundary for configured-key and host-authorized passkey sessions over
  canonical Tailscale Serve HTTPS, without altering enrollment, and keep
  `authenticated_principal()` limited to server-derived attribution.

## 2. Approval gates

- [x] 2.1 Obtain independent privacy/security review of the exact draft head.
- [x] 2.2 After review passes, obtain separate owner approval naming the exact
  artifact. Any semantic change requires fresh review and approval.
- [x] 2.3 Keep `bu-q364q`, `bu-pvapy`, deployment, mapping submission, and
  natural-transition verification blocked until their own prerequisites and
  authorities are satisfied.

## 3. Future implementation after approval (`bu-q364q`)

- [x] 3.1 Add the fail-closed dashboard route and browser client only behind
  the landed central owner-auth boundary for configured-key or host-authorized
  passkey sessions over canonical Tailscale Serve HTTPS. Prove authentication
  finishes before body buffering, receipt creation, pool acquisition, or
  protected reads. After authentication, enforce the 32,768-octet raw encoded
  body maximum with a bounded streamed reader before UTF-8/JSON decoding and
  without trusting `Content-Length`; oversize returns only fixed
  `413 REQUEST_BODY_TOO_LARGE` and touches no receipt, actor, pool, protected
  state, or audit path.
- [x] 3.2 Add the content-blind durable idempotency/receipt representation and
  transaction implementation without adding a mapping read/list API, MCP tool,
  direct-SQL operator path, entity creation, remap, update, or delete.
- [x] 3.3 Exempt the exact route from generic audit body capture and implement
  field-by-field response, error, audit, log, metric, and trace allowlists.
- [x] 3.4 Add the dashboard form with ephemeral-only private fields and no URL,
  navigation-state, browser-storage, query-key, retained mutation-cache,
  analytics, console, or rendered-error copy of submitted values.

## 4. Future verification after approval (`bu-q364q`)

- [x] 4.1 Real PostgreSQL tests run the real migrations and prove a new batch,
  a mixed new/identical batch, a fresh-key identical no-op, same-key exact
  replay, and same-key/different-body conflict against the actual mapping and
  receipt tables.
- [x] 4.2 Real PostgreSQL tests cover duplicate Home Assistant IDs, duplicate
  entity UUIDs, missing/tombstoned/merged/wrong-type entities, a legacy null
  target, conflicts in both mapping directions, and a database failure after an
  attempted insert; every refusal and injected failure leaves zero partial
  mapping writes and no false success receipt.
  Evidence (PR #4255): `tests/api/test_home_person_mappings.py` nodes
  `test_mounted_duplicates_and_legacy_null_target_refuse_without_writes`,
  `test_real_postgres_batch_is_atomic_idempotent_and_content_blind`,
  `test_real_postgres_entity_lifecycle_races_in_both_orders`, and
  `test_real_postgres_serializes_competing_batches_and_rolls_back`.
- [x] 4.3 Real PostgreSQL concurrency tests force two competing batches through
  the fixed advisory lock and prove that at most one complete non-conflicting
  mapping set commits, the loser is a content-blind `409`, and no partial or
  crossed mapping survives.
- [x] 4.4 Real PostgreSQL entity-lifecycle races force both orders for merge,
  `metadata.deleted_at` tombstone, physical delete, and `entity_type` change.
  When the entity mutation commits first, the mapping waits and returns
  `INVALID_REFERENCE`; when mapping validation locks first, the mutation waits
  until mapping/receipt/audit commit. Assert the actual
  `metadata->>'merged_into'` / `metadata->>'deleted_at'` predicates, deterministic
  UUID lock order, zero partial writes, and no false success receipt.
  Evidence (PR #4255): `tests/api/test_home_person_mappings.py::test_real_postgres_entity_lifecycle_races_in_both_orders`;
  falsy-marker predicates in `test_real_postgres_batch_is_atomic_idempotent_and_content_blind`.
- [x] 4.5 Real PostgreSQL idempotency races force overlap before lookup/insert.
  Same key plus the same canonical request must create one durable terminal
  record and return identical receipt/counts to both callers. Same key plus
  different requests must create exactly one winning terminal record; the loser
  must return fixed `IDEMPOTENCY_CONFLICT`, create no second terminal record,
  and perform zero mapping writes.
- [x] 4.6 API tests prove `503` when central owner-auth state/configuration is
  unavailable, `401` for a missing/wrong credential, no pre-auth body/pool
  access, exact size/count/field validation, standard envelopes, aggregate-only
  `200/409/422/503` bodies, and
  byte-for-byte replay of the stored terminal receipt. At the raw-body seam,
  prove exactly 32,768 octets may proceed while 32,769 cannot; oversized bodies
  composed from whitespace or escaped JSON spellings are rejected before decode;
  absent, understated, overstated, and conflicting `Content-Length` values cannot
  bypass or falsely trigger the measured bound; and chunked delivery follows the
  same rule. Assert the fixed `413 REQUEST_BODY_TOO_LARGE` envelope positively and
  zero JSON-decoder, receipt, actor, pool, protected-state, generic-audit, or
  explicit-audit interaction.
  Evidence (PR #4255, PR #4273): #4255 proves every clause except "exact
  size/count/field validation"; #4273 closes it in
  `tests/api/test_home_person_mappings.py`:
  `test_mounted_structural_refusals_are_fixed_422_without_writes` (empty and
  51-member batches, unknown/missing/mistyped fields, invalid UTF-8/JSON,
  malformed `ha_person_id`/`entity_id`/`Idempotency-Key`, and query strings are
  each one audited `422 INVALID_REQUEST` with zero writes) and
  `test_mounted_count_and_length_edges_pass_structural_validation` (exactly 50
  members and a 255-byte identifier clear validation).
- [ ] 4.7 Privacy absence-sentinel tests plant distinct synthetic sentinels in
  both identifiers and assert absence from response body/headers, error details,
  generic and explicit audit rows, captured logs, rendered exception text,
  metric labels, span attributes/events/baggage, request URLs, session/prompt
  stores, browser persistence/query state, and mounted MCP/runtime tool
  registries. Assert the explicit audit field set positively so an empty-audit
  bug cannot make the absence test pass.
  Open: PR #4255 proves every clause except span baggage; exported span
  attributes and events are scanned, but OpenTelemetry baggage is not asserted.
- [x] 4.8 Run targeted API/real-PostgreSQL/frontend tests, repo guards, strict
  OpenSpec and overwrite checks, fresh independent exact-head privacy/security
  review, and terminal hosted CI. Report the implementation PR's actual test
  delta separately.

## 5. Archive only after implementation

- [ ] 5.1 After the separately approved implementation is merged, sync the new
  capability to `openspec/specs/home-assistant-person-mapping/spec.md` and
  archive this change. Archival does not authorize deployment or mapping use.
  Not yet done: the capability is not synced and the change is not archived.
  Archive once 4.6 and 4.7 are complete.
