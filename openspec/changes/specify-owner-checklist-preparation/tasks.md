## 0. Governance and ordering

- [ ] 0.1 Obtain owner adoption of this change and the answer to the open owner question in proposal.md; the owner then closes or keeps PR #4217 (the superseded text).
- [ ] 0.2 Adopt after the RFC 0038 change `generalist-lead-focused-delegation` (the capability-authorization, goal-orchestration and butler-switchboard requirements cited by Source lines); re-check the cited IDs before archive. The catalog-declared always-per-event approval mode (bu-n7zmbp.2) must land in capability-authorization and module-approvals before this change archives.
- [ ] 0.3 Reconcile with `bu-2jtfw.9` (General capture and vocabulary) and keep draft custody PR #4211 distinct.
- [ ] 0.4 Route any General manifesto wording through the manifesto pass (bu-2ckast); this change edits no manifesto.
- [ ] 0.5 Re-derive the implementation packets bu-737xln.2, .3 and .4 from the adopted text.

Implementation lands after the RFC 0038 cutover, or dark behind the cutover switch; nothing here ships against the pre-cutover Switchboard mechanism.

## 1. General-local checklist and review (implementation epic bu-737xln)

- [ ] 1.1 Packet, immutable revision, ordered clause, exclusion, evidence-link and receipt storage with unique `(packet, revision)` and receipt idempotency constraints; caps of 200 clauses, 500 links and 10 accepted links per clause refuse rather than truncate.
- [ ] 1.2 Register the drafting, revision and match-proposal capabilities as `record` in the catalog with taint stamping; register revision-confirm and receipt-issue as `record` capabilities with the always-per-event approval mode and a required idempotency key, their call arguments carrying revision digest, clause decisions, exclusions and source versions.
- [ ] 1.3 Derive the four clause states deterministically; owner-only status and index reads, bounded and paginated.

## 2. Evidence authority

- [ ] 2.1 Specialist read capabilities are goal-plane capabilities declaring the source id as a resource parameter; the owner-selection to grant derivation per owner question 1 (bu-n7zmbp.3).
- [ ] 2.2 Source-owned prepare, fence and release operations as non-model-reachable service-plane tools, returning only an opaque fence or a content-blind refusal, with named allowlisted relay endpoints added when Finance and Travel opt in.
- [ ] 2.3 Commit-time revalidation inside the approved receipt call: compare the approved arguments with current revision, accepted source versions, read authority and privacy.

## 3. Owner surface and lifecycle

- [ ] 3.1 Approval presentation on the owner surface showing the exact revision, ordered clauses, matches, versions, exclusions and digest, with goal lineage and exposure.
- [ ] 3.2 Deterministic job for re-evaluating the current projection after a source change; no prompt-mode schedule.
- [ ] 3.3 Archive, reopen and rollback keep revisions, decisions and receipts readable; preparation-specific commitment linkage only.

## 4. Verification

- [ ] 4.1 Real-PostgreSQL race tests: revision r1 versus r2, source change while r1 is current, privacy change before execution, equal-revision contenders, replay on the same approval, unknown outcome held, exclusion visibility (A,C with B excluded).
- [ ] 4.2 Contract tests: a forged, unselected, mismatched or revoked read returns nothing and does not mutate the packet; an exposed goal cannot confirm; a standing rule never satisfies confirmation.
- [ ] 4.3 `openspec validate specify-owner-checklist-preparation --strict`, `openspec validate --all --strict`, `make check-spec-overwrites`, `make check-guards`, and terminal hosted CI at the exact implementation head.
