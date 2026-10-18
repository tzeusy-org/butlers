## Baseline and placement

General owns collection and item management; Finance keeps transactions and email receipts; Travel owns trip documents; `src/butlers/core/commitments.py` requires closure evidence and a preparation receipt does not itself prove submission. Checklist preparation is a General-local opt-in administrative packet, not a replacement for any of those stores. General's first slice reads owner-entered text and explicitly selected General-local sources. Finance and Travel sources may be shown as selected but cannot become `covered` until their owners ship and review a commit-valid version and read fence. There is no peer-schema SQL from General.

Under RFC 0038 General is a capability service (D2): its checklist operations are catalog capabilities, and a goal reaches them only inside a grant (D4) with a verified broker assertion (D7). The owner decides through the owner surface (D19), never through a tool a model can call.

## State and source vocabulary

(Carried over unchanged from the adopted text.) The owner supplies one checklist with a server-generated packet ID and an immutable ordered revision. Each revision stores exact owner-entered clause text, stable clause IDs within that revision, source time, and a canonical digest over its active clauses and its explicit exclusion manifest. An edit, reorder, exclusion or rewording creates a successor revision. Active clauses alone take one of four coverage states; an excluded clause is not a fifth state but stays visible in a separate manifest with its prior ID and text, the exclusion decision, the owner's approval reference and server time. A model may segment pasted text and propose matches, but segmentation and proposals are not owner-approved clauses or proof.

| State | Meaning |
| --- | --- |
| `missing` | No owner-accepted source match exists. |
| `needs_review` | A proposed, ambiguous or changed match needs explicit owner acceptance. |
| `unavailable` | An accepted source cannot be read, authorized, pinned to an immutable version, or fenced through commit; typed reason, including `version_unverifiable`. |
| `covered` | At the last evaluation every accepted match for the clause is readable under current authority, pinned to an immutable version, and served by a source that can give a commit-valid fence. |

The first slice caps a revision at 200 active-plus-excluded clauses and 500 selected links, at most 10 accepted per clause; exceeding a cap refuses with a typed limit result.

## Clause-by-clause mapping

The table is the only place the superseded mechanism is named. Left column: the adopted clause (behavior, unchanged). Middle: the mechanism #4217 used. Right: the RFC 0038 seam that replaces it.

| Adopted clause | Old mechanism (#4217) | RFC 0038 seam |
| --- | --- | --- |
| 001 First intake needs no existing revision or selection | Switchboard routing of a bounded intake to General | D5 goal admission of the owner's message; D2 General drafting capability reachable inside a lead's grant. The classify-and-route step is deleted by the cutover and is not a dependency. |
| 001 A draft stays a draft until the owner confirms | Switchboard-held owner-confirmation record | D13 per-event approval of the revision-confirm capability, which needs the new always-per-event approval mode (see Required RFC 0038 additions); an owner-authored revision is confirmed by the owner's own dashboard submit (REQ-capability-authorization-007). |
| 001 Model-segmented text and proposals are not proof | Convention in the packet store | D4 `record` effect class plus D6 taint: drafts and proposals are stamped; confirmation does not clear taint (exposure never clears), so a goal-confirmed revision keeps the goal's stamp and only an owner-authored dashboard revision (clauses the owner enters, or edits starting from an owner-authored revision) is untainted; editing a goal-drafted revision is an RFC 0021 edit of that goal's stored call and keeps its lineage and taint. |
| 002 No specialist label, fact, existence signal or version before the owner's exact selection | Switchboard-owned, server-held pre-read selection grant | D4 grants and D7: the call is inside a grant whose resource scope names the selected source (`resource_params`), and the owning domain verifies the broker assertion binding the argument digest. Open owner question 1 (control-plane selection record). |
| 002 A caller-supplied source id or selection claim never authorizes a read | Narrow Switchboard server-to-server MCP validator consuming a request-bound nonce | D7 assertion: signature, audience, expiry, capability, argument digest and single-use call identifier checked before the handler runs (REQ-capability-authorization-007); D4 grant check refuses a name outside scope (REQ-capability-authorization-004). |
| 002 Forged, unselected, revoked or fabricated authority returns only a content-blind refusal | Validator refusal | Broker refusal without contacting the domain, or domain refusal before the handler; recorded in the goal record. |
| 002 Version and read fence valid through commit | Source-owned prepare operation reached through Switchboard MCP | Non-model-reachable source-owned fence tools called by General's deterministic code over a named allowlisted service-plane endpoint (REQ-butler-switchboard-008). Decided here, not asked: named service-plane endpoints are the seam (D7 service-plane relays; REQ-butler-switchboard-008 precedent), added when Finance and Travel opt in; until then those sources stay `unavailable`. |
| 003 An LLM cannot mint the receipt or confirm the owner | Switchboard confirmation record plus validator | D4: owner-decision tools are never grantable (REQ-capability-authorization-014); the receipt capability declares the new always-per-event approval mode, so it always waits for the owner's per-event approval (D13) whatever the origin. Park-only is otherwise an origin-derived grant flag (D5), so this is an addition, not an existing seam. |
| 003 The owner sees the exact revision, clauses, matches, versions and exclusions before confirming | Confirmation presented from Switchboard or the dashboard | D13 parked effect shown on the D19 owner surface with exact call, goal, invocation and exposure lineage; the call arguments carry the revision digest, clause decisions, exclusions and source versions, so the executed digest the owner approved binds them (REQ-capability-authorization-010). |
| 003 Caller-supplied actor or confirmed flag has no authority | Validator rejects forged records | The domain accepts only a verified assertion whose `approval_ref` covers the exact call digest. |
| 003 An exposed goal prepares but never confirms | Not stated in the adopted text | D6: the goal's drafts are tainted and stay tainted after approval; the always-per-event mode makes confirmation per-event for every origin and exposure; approvals of exposed requests do not count toward standing rules. |
| 004 Replay is idempotent; changed input under the same operation fails | Operation ID under a packet-row lock | D12: the receipt capability declares `idempotency = key_required`, receives the effect id as key, and returns the original receipt for a repeat; the approval id is the replay identity. |
| 004 Unknown receipt outcome is held until reconciled, never replayed on expiry | Not in #4217; from the bead behavior matrix and D12 | D12: an effect in `unknown` is refused on repeat and cannot be replayed without a fresh owner approval (REQ-capability-authorization-009). |
| 004 Stale revision, source change or privacy change refuses completion | Fence checked at commit | The approved stored call executes at approval time (D13), so General compares the approved call arguments with current revision, source versions, read authority and privacy inside that execution and refuses, telling the owner to prepare again. |
| 005 History survives archive, supersession and rollback; later privacy withholds disclosure | Immutable receipts | Unchanged General-owned records; the receipt keeps versions and times and withholds source-derived labels and facts once private (decided: versions and times stay, source-derived labels are withheld, which satisfies both adopted sentences and is the privacy-preserving reading). |
| 005 No scheduled prompt-mode work | Not stated | D2 and D5: deterministic jobs are unchanged; a model-backed obligation is not needed and is not specified. |
| Manifesto wording | Separate manifesto and Switchboard amendment file | Removed; General manifesto text goes through the manifesto pass (bu-2ckast). |

## Behavior matrix

1. **Happy path.** Eligible General evidence, the owner approves the exact revision, General executes the stored call and returns one immutable receipt and index.
2. **Stale revision or privacy change between preparation and confirmation.** Refuse at execution and re-prepare; no receipt (REQ-004).
3. **Replay of a confirmation.** Idempotent on the approval; no second receipt (REQ-004).
4. **Unknown outcome of a General write.** Held until reconciled; never replayed on expiry (REQ-004).
5. **Exposed goal.** Prepares only; confirmation always needs the owner (REQ-003).
6. **Historical receipts never disclose evidence that has since become private** (REQ-005).

## Evidence authority and commit fence

A source door has `source_owner`, opaque `source_id`, a safe owner-visible label only after the source authorizes its disclosure, and the version descriptor the owning source returns: an append-only version id, a versioned reference or an owner-authorized content digest. A row id, filename, URL, `updated_at` or `fetched_at` alone is not one. General stores the minimal fact, door and version needed for the reviewed match, not a Finance receipt image, a Travel document body, or a transaction copy.

For a General-local source the receipt execution locks the source row and packet revision together, verifies its immutable snapshot version and commits while the locks hold. Without a stable versioned read and locking seam the source is `unavailable` for completion.

For a specialist source the owner-facing read happens inside a grant and a verified assertion (REQ-002). Before General may mark a link `covered`, the owning butler defines a version-fence contract with three operations on named planes. Resolution is a goal-plane capability inside the owner-selected grant returning the least clause-relevant fact, an immutable version, current read authority and a bounded label; General treats what a goal relays as a proposal. Prepare and release are service-plane operations that accept only a version General holds and return only an opaque fence or a content-blind refusal, never a label, fact, existence signal or version. Prepare atomically checks that version and issues an opaque request-bound short-lived fence which all source edits, revocations and deletions must serialize with. Release follows commit or abort, and a crash leaves a bounded lease that expires without minting a receipt. If either cannot be implemented without a new trust exception the source remains `unavailable`; this change authorizes no cross-schema lock, shared SQL view, credential or alternate transport.

## Future allocation

General: packet store, deterministic evaluator, receipt and index persistence, idempotency. Control plane: catalog entries for the new capabilities (record, always-per-event approval mode, key_required, resource parameters), grant derivation from the control-plane selection record, owner-surface approval presentation. Finance and Travel, separately: minimal resolution and fence operations. Commitments: optional explicit linkage only.

## Required RFC 0038 additions

This change depends on two additions that it cannot make itself, because it adds no block to the generalist capabilities:

1. **Always-per-event approval mode** (bu-n7zmbp.2). RFC 0038 has park-only only as an origin-derived grant flag (D5), and an owner standing rule can approve a non-park-only effect for an unexposed goal. Revision-confirm and receipt-issue need a catalog-declared `approval_policy` value that the broker applies as park-only in every grant. It belongs in generalist capability-authorization and module-approvals (or an RFC 0038 amendment) and must land before this change archives.
2. **Control-plane selection record** (bu-n7zmbp.3). See owner question 1.

## Decisions on the former owner questions 2-4

- Service-plane endpoints: named, allowlisted endpoints are the sanctioned seam (D7; REQ-butler-switchboard-008). Added when Finance and Travel opt in; sources stay `unavailable` until then.
- Historical receipts: versions and prepared-at time stay; source-derived labels and facts are withheld once the source is private. Versions are opaque, so keeping them discloses nothing.
- Revision confirmation: both paths exist under REQ-capability-authorization-007. A goal-drafted revision is confirmed through a parked effect; an owner-authored revision is confirmed by the owner's own dashboard submit.

## Overlap with the generalist ledger

`generalist-lead-focused-delegation/collision-dispositions.md` has no row for this change. That ledger records collisions between the generalist change and existing specs and active changes that touch the capabilities it modifies. This change adds a new capability, modifies none, and shares no requirement ID with it, so there is nothing to dispose. Its only same-ID overlap is the owner's #4217, handled in the proposal.
