## 1. Draft the contract

- [x] 1.1 Specify the fine-grained-PAT-only credential policy, the `public.github_accounts`
  + companion-entity account model, and account cardinality.
- [x] 1.2 Specify the exact polling endpoint, pagination, ETag/304, and `X-Poll-Interval`
  cadence behavior, grounded in the official docs fetched live on 2026-09-09.
- [x] 1.3 Specify `id`-based cursor/dedup semantics, first-poll baseline behavior, and the
  provider's 300-event/30-day history-window limit.
- [x] 1.4 Specify the event-type allowlist, private-repository redaction tiers, rate-limit
  (primary and secondary) handling, and transient-failure/account-isolation behavior.
- [x] 1.5 Specify deterministic Switchboard `ingest.v1` routing (no direct Chronicler call)
  and the global skip-rule / durable-evidence-table mechanism, without adding a new
  requirement to `connector-base-spec` or `chronicler-source-compatibility`.
- [x] 1.6 Specify content-blind Secrets Passport inventory/probe behavior for `github_pat`.
- [x] 1.7 Draft (without applying) the exact RFC 0018/0003/0004 amendment text a future
  implementation PR must apply.
- [x] 1.8 Confirm `bu-dlelt`'s disposition: retained unmodified as the implementation
  carrier per the binding 2026-09-06 coordinator correction; this draft performs no Beads
  mutation.

## 2. Approval gates

- [ ] 2.1 Obtain independent exact-head semantic review of this draft.
- [ ] 2.2 After review passes, obtain separate owner approval naming the exact reviewed
  artifact. Any semantic edit invalidates that review and requires a fresh pass.
- [ ] 2.3 Keep `bu-dlelt`, account connection, PAT creation, and any live GitHub API call
  blocked until their own prerequisites and authorities are satisfied.

## 3. Future implementation after approval (`bu-dlelt` or an approved successor)

- [ ] 3.1 Apply the drafted RFC 0018/0003/0004 amendment text verbatim once the
  corresponding code exists — not before.
- [ ] 3.2 Add `public.github_accounts`, `connectors.github_cursors`, and
  `connectors.github_events` via migration, plus `github_pat` in `ENTITY_INFO_TYPES` and a
  `github_account` companion-entity role.
- [ ] 3.3 Implement `src/butlers/connectors/github.py`: multi-account discovery, per-account
  polling loop, ETag/pagination/cursor logic, event-allowlist mapping, private-repo
  redaction, rate-limit handling, heartbeat/metrics, filtered-event flush, and replay-queue
  drain per the connector base contract.
- [ ] 3.4 Register the `github`/`github` channel/provider pair and the global skip rule in
  `roster/switchboard/tools/routing/contracts.py` and a new seed migration (mirroring
  `sw_018_switchboard_activitywatch_skip.py`).
- [ ] 3.5 Add the `POST /api/secrets/user/github/probe` handler calling `GET /rate_limit`,
  reusing the existing generic Secrets Passport probe surface.
- [ ] 3.6 File a future, separate `chronicler-source-compatibility` delta for the
  `github.activity` projection adapter — explicitly not part of this change or its
  implementation task set.

## 4. Future verification after approval (`bu-dlelt` or an approved successor)

- [ ] 4.1 Real-Postgres tests cover: no credential, invalid credential, and mid-operation
  revocation (401/403) for an account, without affecting other accounts.
- [ ] 4.2 Real-Postgres/API tests cover: first-poll baseline (no synthetic historical
  events emitted), a `200` page response, a `304` conditional response, multi-page
  pagination, and the 300-event/30-day window boundary.
- [ ] 4.3 Tests cover duplicate event `id` across polls (Switchboard dedup returns the
  original `request_id`) and a late-arriving event whose `created_at` precedes an
  already-processed event's `created_at` but whose `id` is greater than the cursor.
- [ ] 4.4 Tests cover private-repository redaction (metadata tier, bounded
  `normalized_text`, no commit/PR/issue text) versus public-repository full-tier ingestion,
  including the `connectors.filtered_events` redaction path.
- [ ] 4.5 Tests cover primary-quota soft degradation (extended poll interval below 10%
  remaining) and secondary abuse-detection `403`/`Retry-After` handling, both scoped
  per-account.
- [ ] 4.6 Tests cover transient failure/retry with exponential backoff, and cursor-based
  restart resumption after a simulated crash.
- [ ] 4.7 Tests cover independent multi-account isolation: one account's revocation/backoff
  does not affect another account's polling loop, cursor, or health.
- [ ] 4.8 Tests prove the global `source_channel='github'` skip rule prevents LLM
  classification/butler-session spawning, and that events land in
  `connectors.github_events` without any direct Chronicler call existing in the connector
  code.
- [ ] 4.9 Tests prove the connector's heartbeat/health payload always carries the
  provider-latency disclosure field, and that no allowlisted-vs-non-allowlisted event-type
  boundary silently drops an intended activity type.
- [ ] 4.10 API tests prove `github_pat` participates in the generic Secrets Passport
  inventory/probe surfaces exactly like `steam_api_key`, and that probe never returns or
  logs the token value, `github_login`, or raw provider response body.
- [ ] 4.11 Run targeted connector/API/real-Postgres tests, repo guards
  (`make check-guards`), strict OpenSpec and overwrite checks, lint/format, a fresh
  independent exact-head review, and terminal hosted CI. Report the implementation PR's
  actual test delta separately.

## 5. Archive only after implementation

- [ ] 5.1 After the separately approved implementation is merged, sync the `connector-
  github` capability to `openspec/specs/connector-github/spec.md` and archive this change.
  Archival does not authorize deployment, account connection, or any live GitHub API call.
