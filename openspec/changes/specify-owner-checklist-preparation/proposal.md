## Why

An owner can supply a list of documents or proofs for an administrative task, yet today must repeatedly open each source and compare it with each clause. A freeform collection cannot say which exact list revision was checked, which source version supported a clause, or whether an apparent gap is genuinely missing rather than unreadable. The useful outcome is a reviewed preparation index, not a declaration by Butlers that an application is eligible or filed.

The owner adopted a checklist-preparation contract on 2026-09-28 (owner choice A, decision bead bu-ijzgu6; text in draft PR #4217, `REQ-owner-checklist-preparation-001..005`). That text puts owner selection and confirmation on a Switchboard-held mechanism that the RFC 0038 hard cutover (owner direction 2026-10-14) removes. A spec must describe only the target system, so this change re-authors the same behavior onto RFC 0038 seams. It is bu-p5umi2.4 and supersedes the text of #4217; the owner decides #4217.

## What Changes

- New capability `owner-checklist-preparation` with the five adopted requirements, behavior unchanged: honest clause states, source-owned version and read authority, owner-confirmed bounded receipts, revision-fenced replay-safe completion, and external outcomes kept separate from history.
- Only the trust mechanism moves. Specialist reads are bounded by D4 grants and D7 broker assertions; owner confirmation is a D13 per-event approval presented on the D19 owner surface; replay safety is D12 idempotency on the approval; taint is D6. design.md has the clause-by-clause mapping table.
- The proposed General manifesto amendment file from #4217 is dropped (it also proposed Switchboard changes that no longer apply). Any General manifesto wording goes through the manifesto pass (bu-2ckast); this change only states the requirements that wording must satisfy.
- Scheduled or background work is stated in the requirement itself (REQ-005): deterministic job mode only, no prompt-mode schedule, no dependence on classify-and-route.

## Capabilities

### New Capabilities
- `owner-checklist-preparation` (ADDED REQ-owner-checklist-preparation-001..005)

### Modified Capabilities
None. Every block is ADDED, so no baseline text is rebuilt and no `scripts/spec-overwrite-baseline.json` clause is frozen.

## Impact

- Spec only: no code, migration, manifesto, runtime wiring, live data, credential, deploy, message or submission. Implementation is epic bu-737xln and is re-derived from the adopted text.
- Tests: +0 ~0 -0.
- Behavior the owner adopted is unchanged. Where an RFC 0038 seam does not fit a clause cleanly, it is an owner question below rather than a silent change.

## Overlaps and ID checks

- **#4217** (owner's open draft, same change name, same IDs) is the superseded text. It is not touched. If the owner adopts this change instead, #4217 should be closed; if both stay open they cannot both archive, because they mint the same `REQ-owner-checklist-preparation-001..005`.
- **generalist-lead-focused-delegation** (RFC 0038, pending) supplies the seams cited here (`REQ-capability-authorization-004`, `-007`, `-009`, `-010`, `-014`; goal-orchestration; butler-switchboard service-plane relay). This change adds no block to those capabilities, but it depends on an addition to them: a catalog-declared always-per-event approval mode (bu-n7zmbp.2, see design.md "Required RFC 0038 additions"). It also depends on that change being adopted; if a cited requirement is renumbered there, the Source lines here follow it.
- No canonical spec or other active change defines `owner-checklist-preparation`. IDs checked against canonical specs and every active change on 2026-10-18.
- bu-2jtfw.9 (General capture) remains the owner of capture and vocabulary; nothing here is credited to it. Custody (draft PR #4211) is a separate proposal and is not equated with checklist coverage.

## Decisions taken in this draft ([decision])

- Confirming a revision and issuing a receipt are two `record` capabilities that declare the new always-per-event approval mode: they always need the owner's per-event approval and can never be satisfied by a standing rule. The owner's own dashboard submit is that approval for an owner-authored revision.
- Confirmation does not clear taint: a goal-confirmed revision keeps the goal's stamp; only an owner-authored dashboard revision (entered by the owner, or edited starting from an owner-authored revision) is untainted. Editing a goal-drafted revision is an RFC 0021 edit of that goal's stored call and keeps its lineage and taint.
- The specialist read is a goal-plane capability inside the owner-selected grant; service-plane prepare and release return only an opaque fence or a content-blind refusal.
- The confirm and issue call arguments carry the revision digest, clause decisions, exclusions and versions, so the approval binds them; execution refuses on drift.
- Named allowlisted service-plane endpoints are the seam for Finance and Travel fences (added on opt-in; sources stay `unavailable` until then).
- A historical receipt keeps versions and times and withholds source-derived labels and facts once private; versions are opaque.
- Both revision-confirmation paths exist under REQ-capability-authorization-007, so revision confirmation is not a separate question.
- Operation identity is the approval plus the effect idempotency key (D12), replacing the adopted operation ID.
- Source-owned fence operations are non-model-reachable service-plane endpoints called by General's deterministic code.
## Open owner questions

1. **Granting a read to exactly the owner-selected source.** The adopted text lets a specialist return even a label only after the owner's exact selection. RFC 0038 grants carry a resource scope and the broker assertion binds the call arguments, but nothing binds that scope to an owner selection. Recommended: a selection record held by the control plane (the placement #4217 gave Switchboard) and written only from a D19 owner action, which the broker applies as the capability's resource scope. This is an RFC 0038 addition (bu-n7zmbp.3). Alternative: a General-owned selection record that the control plane reads, which moves the trust anchor from the control plane to General. A per-event approval of the read is not an option as RFC 0038 stands: reads carry no effect id and D13 defines no way to return a parked read's result to the goal. Until chosen, Finance and Travel sources cannot return a source-derived label, which is the adopted default.

The adoption ask also covers the always-per-event approval mode (bu-n7zmbp.2): adopting this change asks for that addition too.
