## 1. Draft the contract

- [x] 1.1 Ground the draft at source commit `d8b1924635a04f3159dffa5e3e47e83633e79693` and inventory the implemented plus already-specified owner-gated browser routes.
- [x] 1.2 Define configured-key sessions, host-authority proof invariants, single-use/replay/concurrency, expiry/revocation/restart/recovery, CSRF, corrupt/absent state, arbitrary-first-visitor denial, compatibility, and rollback.
- [x] 1.3 Record the host-proof and HTTPS choices without selecting a mechanism, generating authority, provisioning credentials, or changing runtime state.
- [x] 1.4 Amend the whole dashboard-admin-gateway API-key requirement while preserving every baseline scenario heading and clause not intentionally changed.
- [x] 1.5 Add the whole dashboard-relationship owner-only requirement, preserving every endpoint and scenario while resolving its non-dev absent-key startup rule against adopted keyless-unenrolled startup.
- [x] 1.6 Rebuild the canonical/unarchived owner-only browser-route inventory, including the mounted path `POST /api/secrets/cli/{credential_id:path}/rotate`, and define exact positive issuance allowlists versus absence-sentinel checks.

## 2. Exact-artifact review and adoption gates

- [ ] 2.1 Obtain independent semantic and security review of the exact draft commit; any semantic correction requires a fresh exact-head review.
- [ ] 2.2 Obtain separate owner selection of E1 and E2 plus adoption naming the exact independently reviewed commit. Silence keeps keyless enrollment and cutover disabled.
- [ ] 2.3 Before implementation, amend this change with the selected mechanism's exact host command/control boundary, endpoint and payload shapes, request/flood bounds, HTTPS topology, recovery procedure, and threat analysis; review and owner-adopt that new exact head again.
- [ ] 2.4 Rebuild every active owner-control requirement against this adopted contract, including `harden-runtime-auth-and-breaker-attention`, `specify-home-presence-owner-entity-configuration`, `specify-roster-identity-owner-operations-overlay`, `durable-dashboard-terminal-action-recovery`, `generation-fenced-codex-auth-rotation-provenance`, and `memory-honesty-last-mile`; do not archive a stale header-only, unconfigured-503, implicit-owner, or incomplete-route clause afterward.

## 3. Future server foundation after adoption and allocation

- [ ] 3.1 Add additive durable auth-state, session-digest, CSRF-digest, enrollment-epoch, proof/challenge receipt, expiry, revocation, and audit storage with fail-closed migrations and rollback compatibility.
- [ ] 3.2 Implement one centralized owner-auth boundary that accepts configured-key headers or valid server sessions, protects keyless dashboard APIs before enrollment, and runs before body buffering, pool access, protected reads, or domain owner checks.
- [ ] 3.3 Implement configured-key session issuance and logout/revoke surfaces with exact HTTPS Origin enforcement, structural audit-body exclusion, fixed errors, key rotation fencing, and no secret reflection or persistence in JavaScript.
- [ ] 3.4 Implement only the separately selected E1 mechanism, with host-local authority, bounded expiry, atomic single use, replay receipts, concurrent one-winner semantics, and epoch-fenced recovery.
- [ ] 3.5 Implement restart-preserving absolute session expiry, per-session/all-session revocation, emergency host revocation, and corrupt/unavailable-state denial without a permissive fallback.

## 4. Future browser and Compose work after adoption and allocation

- [ ] 4.1 Implement only the selected E2 HTTPS path in default and hot-reload Compose, keeping loopback/Tailscale exposure within RFC 0008 and proving the browser accepts and returns the exact Secure cookie.
- [ ] 4.2 Add the shell-level owner-auth gate with configured-key entry and the selected keyless host-authorization flow; keep authority out of persistent JS storage and expose one accessible, repeat-safe commit action.
- [ ] 4.3 Wire the session and in-memory CSRF token through every inventoried owner-gated browser call, then make route metadata fail closed so future owner-only routes cannot omit the central boundary.
- [ ] 4.4 Update operator docs and stale environment/API-auth text in the same implementation change, distinguishing API-key middleware, browser sessions, keyless enrollment, host recovery, and forbidden legacy rollback.

## 5. Future verification after adoption and implementation

- [ ] 5.1 Extend mounted full-app API tests for configured-key session issuance, header compatibility, exact cookie attributes, session fixation, expiry/revocation, route coverage, and pre-access denial.
- [ ] 5.2 Add real-PostgreSQL concurrency/restart tests for one enrollment winner, durable consume receipt, replay denial, expiry, epoch recovery, and unavailable/corrupt state.
- [ ] 5.3 Add browser tests over the selected exact HTTPS Compose path for cookie acceptance, HttpOnly absence from JS, reload rehydration with no Origin plus exact Fetch Metadata, cross-site/forwarded-authority denial, CSRF mutation success/denial, restart, logout, and revocation.
- [ ] 5.4 Add D7 positive-issuance and non-vacuous absence-sentinel tests across responses, audit, logs, telemetry, prompts, MCP, connectors, notifications, frontend bundles, source maps, and service-worker caches, including the separately sanctioned one-time CLI rotate response.
- [ ] 5.5 Run targeted tests first, topology collection where applicable, real API/database/browser/Compose security lanes, `make check-guards`, strict OpenSpec and overwrite checks, diff hygiene, fresh exact-head semantic/security review, and terminal hosted CI.

## 6. Staged cutover and guarded rollback

- [ ] 6.1 Ship additive storage and configured-key session compatibility dark before enabling keyless enrollment or changing the no-key pass-through behavior.
- [ ] 6.2 Prove the selected host authority and HTTPS path, then cut keyless mode to fail closed while preserving configured-key header and session clients.
- [ ] 6.3 Exercise session revocation, epoch recovery, key add/remove/rotation, restart, and the guarded legacy rollback procedure without exposing authority values.
- [ ] 6.4 Archive only after separately authorized implementation is merged and verified; archive, deployment, enrollment, proof generation, and runtime verification remain separate acts.

Spec-draft test delta: `Tests: +0 ~0 -0`.
