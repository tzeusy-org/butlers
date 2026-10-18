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
- **generalist-lead-focused-delegation** (RFC 0038, pending) supplies the seams cited here (`REQ-capability-authorization-004`, `-007`, `-009`, `-010`, `-014`; goal-orchestration; butler-switchboard service-plane relay). This change adds no block to those capabilities. It depends on that change being adopted; if a cited requirement is renumbered there, the Source lines here follow it.
- No canonical spec or other active change defines `owner-checklist-preparation`. IDs checked against canonical specs and every active change on 2026-10-18.
- bu-2jtfw.9 (General capture) remains the owner of capture and vocabulary; nothing here is credited to it. Custody (draft PR #4211) is a separate proposal and is not equated with checklist coverage.

## Decisions taken in this draft ([decision])

- Confirming a revision and issuing a receipt are two park-only `record` capabilities that always need the owner's per-event approval and can never be satisfied by a standing rule.
- Operation identity is the approval plus the effect idempotency key (D12), replacing the adopted operation ID.
- Source-owned fence operations are non-model-reachable service-plane endpoints called by General's deterministic code.
- A historical receipt keeps its versions and prepared-at time but withholds a source-derived label or fact once the source has become private (see owner question 3).

## Open owner questions

1. **Granting a read to exactly the owner-selected source.** The adopted text lets a specialist return even a label only after the owner's exact selection. RFC 0038 gives grants a resource scope and binds the call arguments in the broker assertion, but does not say how a goal's grant resource scope is derived from an owner selection recorded by General. Options: (a) a General-owned owner selection record that the control plane reads when issuing or narrowing the grant; (b) the owner's selection is itself a per-event approval of the read. Until chosen, a Finance or Travel source cannot return a source-derived label, which is the adopted default.
2. **How General obtains a Finance or Travel version fence.** The adopted commit fence needs General's deterministic code to call a source-owned prepare operation. The Switchboard service-plane relay is a set of named, allowlisted endpoints; this needs new endpoints (General as caller, Finance and Travel as targets). Confirm that adding them is in scope, or keep Finance/Travel `unavailable`, which is the adopted default.
3. **Historical receipts and later privacy.** The adopted text says a receipt remains retrievable with its original versions; the bead's behavior matrix also says a historical receipt must never disclose evidence that has since become private. This draft reconciles them (versions and times stay, source-derived labels and facts are withheld). Confirm.
4. **Is a revision confirmation an approval too?** The adopted text confirms both the revision and the receipt. This draft makes both per-event approvals. If the owner wants revision confirmation to be lighter (an owner-origin dashboard action with no goal), that needs a different seam.
