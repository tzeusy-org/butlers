## Context

The approved source-stage packet preserves all 34 nonblank RFC clause groups as 14 mandatory requirements and 42 scenarios. `bu-hycu7t` and serialized predecessor `bu-lsxqb0.15` are closed; the coordinator reviewed the full packet and applied the exact seven source acceptance criteria before tracked work. See proposal.md for motivation and the Fleet case spec for required behavior.

## Goals / Non-Goals

Transfer the complete existing contract, retain the decision rationale, and expose proof debt without changing production or test behavior. Source completion and archive do not certify Fleet runtime conformance or release neighboring feature, grant, backup or activation holds.

## Decisions

1. **One canonical home.** Use the CLI-created `document-rfc0032-fleet-case-file` change with complete ADDED blocks, normal archive and one `fleet-case-file` main spec. Existing main and active neighboring bodies are preserved. The OpenSpec new-capability template suggests a delta Purpose section, while the governing authoring format/parser permits only operation H2s in deltas. Keep the delta ADDED-only, then populate the normal archive's canonical Purpose placeholder from the approved source draft, including its observed-gap note. Do not add validator exceptions or skip spec application.
2. **Keep mandatory gaps mandatory.** Evidence immutability (REQ005) is unfenced and uncited. Honest list availability (REQ007) is required by the response convention, but the current catch-all empty page violates it. Record that current behavior as an observed defect, preserving the existing test without a false citation. Partial coverage of other requirements remains explicit; neither source archive nor presence-only trace declares it complete.
3. **Correct historical wording without inventing behavior.** Source has seven tools, typed `case` evidence, all shipped slices and pre-S7 link repair. Generic storage still admits another-case references; the S7 tool admits exactly three ledger kinds. Attention creates a ledger record rather than proving provider sending, and the broker remains unwired. Avoid a fourth tool kind, new producer, richer posture arbitration or concurrent-backfill guarantee.
4. **Comment-only test trace.** Add adjacent qualified comments for the audited executing clauses in six owning test files. Preserve docstrings and full AST, assertions, function names, decorators and collected nodes. Cite SQL controls only for their actual direct migrated-role/storage behavior, never post-migration bootstrap, actual registered transport or genuine remote refs that their setup does not exercise. REQ005 and the current generic-error empty-page control receive no requirement citation.
5. **Preserve allocation and existing owner gates.** Retained proposal A belongs to existing `bu-yfd9v1`, whose unadopted Switchboard-only evidence INSERT proposal conflicts with all-butler contributions. Proposal B keeps composition/lifecycle conformance cohesive and serial, linked to `bu-h40h2b.8` AC3 without closing its feature. Read supplement C stays under that owner's existing paired safe-read/availability prerequisite and AC1/2/8. No new Beads or owner decisions are created here.

## Risks / Trade-offs

- A shipped-source label can obscure incomplete behavior or proof: retain uncited REQ005, honest-outage REQ007 debt and per-requirement scope limits in this design and the RFC pointers.
- Trace checks detect citation presence rather than scenario coverage: qualify comments, execute named owning tests and distinguish local mock behavior from hosted actual PostgreSQL evidence.
- README file adjacency with foreign PR4064 can hide another row's change: edit only RFC0032's row and recheck exact ownership before writing.
- Bootstrap replay is not positioned by the existing migration fixture: it bootstraps before migrations. The real-PG controls do not establish post-migration replay, every role or peer-private sentinels.
- A normal archive can lose requirement bodies: compare all complete blocks against the reviewed draft, prove canonical/archive parity and preserve neighboring body digests.

## Source Migration And Recovery

Complete the source tasks, run named proportional tests and authoring/OpenSpec/parity/hygiene checks, normally archive this change, then push one own PR. Revert only this source translation if necessary; production state, grant policy and runtime APIs are untouched. Full authoring/strict findings are retained without ratchet edits, fake global green or post-v1 demotion. Actual exact-head hosted SQL receipts and the protected terminal merge-group remain separate from local tests and source completion.

## Remaining Mandatory Proof

| Requirements | Present evidence and precise limit |
| --- | --- |
| 001–004 | Lookup, lifecycle, constraints and two-contributor storage controls exist; no forced competing-open race or every-role/source-authenticated contribution proof. |
| 005 | Existing INSERT/dedup tests do not refuse UPDATE. Broad runtime evidence UPDATE access is an implementation gap under A. |
| 006 | Real Switchboard/Health SET ROLE controls exist; bootstrap occurs before migration, not after, with no exhaustive role/shared-read/private-sentinel replay. |
| 007 | ASGI controls use mocked rows. SQL filtering/order/keyset/500 caps remain partial. Catch-all clean empty on genuine failure is a defect; the generic RuntimeError table-missing control does not classify legitimate absence. C preserves honest availability debt. |
| 008 | Fake tool registries and patched dispatchers prove registration/forwarding choices, not real registered transport. The existing typed-reference test uses task, not case. |
| 009 | Direct real-PG attention controls force two advisory-lock waiters and preserve independent-key progress, but do not establish source-owned case eligibility through registered transport or provider sending. |
| 010 | PG sweep seeds silent/routine/fresh/urgent/closed cases; no active posture, forced fresh-evidence/posture overlap or actual 04:10 native scheduler invocation. |
| 011 | Resolved owner episodes, closed first writes and sequential reruns have controls; concurrent operator backfill safety is not an adopted guarantee. |
| 012–013 | Storage controls use fake remote IDs and producer-link controls patch handlers/attention/dispatch; no genuine existing ledger-reference path through actual transport. |
| 014 | Unit-mocked old-case missing-link repair exists; PG rerun covers an already-present new link, not planted pre-S7 repair or failure/restart between case and link writes. |

Native Fleet backup option B (`bu-10bbqg`), conditional offline restore (`bu-o0wk6p`), capture core259/AC5, foreign QA/routing/PR3960/PR4170 and neighboring active contracts remain unchanged. No provider, live/private data, credentials/settings, migration, grant, peer-schema access, deployment or activation operation is part of this source delivery.
