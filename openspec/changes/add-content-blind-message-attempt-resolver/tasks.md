## 1. Approval and Prerequisite Gates

- [ ] 1.1 Record exact owner approval of this proposal, design, and delta-spec commit/digest, including all seven choices in `design.md` § Owner Review Gate; draft publication and PR #4056 approval do not authorize implementation.
- [ ] 1.2 Wait for `durable-dashboard-terminal-action-recovery` to land a reusable trusted owner-facing turn projection with the required outcome vocabulary, `updated_at` version, update-on-state-change guarantee, and content-blind access path; do not substitute direct private-table reads or `dashboard_turn_dispatch_status()`.
- [ ] 1.3 Revalidate active `dashboard-conversations` changes, related Beads outcomes, open PRs, and ownership on the exact implementation base; extend the landed projection seam and avoid duplicate allocation.

## 2. Exact Content-Blind Read Surface

- [ ] 2.1 Add a closed response model whose data fields are exactly `message_id`, `conversation_id`, `outcome`, and `version`, with the specified owner-facing outcome enum and deterministic opaque V1 version digest.
- [ ] 2.2 Add a dedicated data-layer helper that selects only message ID and conversation ID through the exact user-role and conversation-butler join; do not call or widen `message_get_by_id()`, select body/page-context fields, use text predicates, or scan history.
- [ ] 2.3 Implement the resolver inside one read-only repeatable-read shared-pool snapshot, combining the exact identity with the landed trusted turn projection and performing no mutation lock or side effect.
- [ ] 2.4 Add `GET /api/butlers/{name}/conversation-turns/{message_id}` with HTTP 200 exact data, fixed 404 `MESSAGE_ATTEMPT_NOT_OBSERVED`, fixed 503 `MESSAGE_ATTEMPT_SOURCE_UNAVAILABLE`, normal UUID validation, and `Cache-Control: no-store`.
- [ ] 2.5 Preserve `ApiKeyMiddleware` as the access gate, derive any internal principal context from `authenticated_principal()`, accept no caller identity input, and make wrong-butler lookup indistinguishable from true absence.

## 3. Behavior and Privacy Verification

- [ ] 3.1 Add focused route tests for every allowed found outcome, exact four-field serialization, version equality/change behavior, `no-store`, and repeated side-effect-free reads (REQ-dashboard-conversations-009).
- [ ] 3.2 Add absence/race tests proving nonexistent, assistant-role, and wrong-butler rows return the same fixed 404 with `Cache-Control: no-store`; a later first persistence of the same ID becomes found; neither response performs or advertises retry/replay/new-identity authority.
- [ ] 3.3 Add unavailable tests for shared-pool acquisition/query failure and missing, incomplete, invalid, or unversioned turn projection; each must return fixed 503 with `Cache-Control: no-store` rather than 404, guessed state, or raw detail.
- [ ] 3.4 Add authorization tests proving an invalid API key returns 401 before either content-blind helper runs, and prove no owner/actor/principal/user input exists on the route.
- [ ] 3.5 Add a query-shape/privacy gate that fails if the resolver path selects or returns message content, page context, title, source, request/session/route identity, errors, targets, terminal-action details, credentials, or other fields outside the four-field allowlist.
- [ ] 3.6 Add side-effect spies/contract checks proving the GET opens no write transaction, audit append, MCP/LLM/provider/connector call, Stop, retry, ingress claim, replay, notification, or identity-bearing telemetry/log path.

## 4. Documentation and Delivery Verification

- [ ] 4.1 Update the dashboard API inventory with the route, exact response allowlist, point-in-time absence semantics, unavailable behavior, access authority, and explicit no-replay/no-body-read boundary.
- [ ] 4.2 Run the exact new route/helper/model test nodes first, then their owning files; record the actual implementation test delta as `Tests: +a ~b -c`.
- [ ] 4.3 Run strict/source-trace checks, `make check-spec-overwrites`, `make check-countable-tasks`, `make check-guards`, applicable formatting/lint/type gates, and terminal hosted CI on the exact clean implementation head; this spec-only draft is expected to have one authoring warning for REQ-dashboard-conversations-009's absent implementation-test citation, while implementation closeout must add the citation and remove that warning.
- [ ] 4.4 Require fresh independent semantic/privacy review with zero unresolved threads before merge; only after this resolver and PR #4056 are each owner-approved may a separately allocated client implementation consume the endpoint.
