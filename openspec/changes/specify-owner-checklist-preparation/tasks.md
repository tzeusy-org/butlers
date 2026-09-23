## 0. Governance and ownership gates

- [ ] 0.1 Independently review this exact proposed OpenSpec and bounded
  General manifesto/routing amendment. Obtain explicit owner adoption of the
  named artifact; the run-14 planning release is not behavior adoption.
- [ ] 0.2 Wait for foreign `bu-2jtfw.9` to settle capture, vocabulary,
  ownership refusal, search, and item-update semantics. Reconcile the proposed
  General wording and any General-local source version path with its landed
  exact head before allocating shared files.
- [ ] 0.3 Reconcile owner-confirmation boundaries with draft custody PR #4211
  without treating its proposed receipt as adopted or conflating physical
  custody with checklist coverage. Allocate a Switchboard-owned, separately
  reviewed owner-confirmation/validation seam and authenticated Dashboard
  forwarding contract before any complete receipt is enabled.
- [ ] 0.4 Allocate Finance and Travel source reads/fences to their owning
  butlers under separate bounded MCP contracts. General cannot mark those
  sources covered until each owner proves immutable version, current read
  authority, and a commit-valid fence; no peer-schema SQL or shared DB lock.

## 1. General-local owner checklist and review

- [ ] 1.1 Add opt-in General-local packet/revision/ordered-clause storage
  through an approved module and migration, preserving immutable prior
  revisions. Enforce 200 clauses, 500 selected links, and 10 accepted links
  per clause without truncation. No canonical General capture rewrite.
- [ ] 1.2 Add owner-only draft/review/status tool surfaces. Keep model match
  proposals non-authoritative and derive only `missing`, `needs_review`,
  `unavailable`, or `covered` with typed next steps. Declined clauses require
  a new owner-confirmed checklist revision.
- [ ] 1.3 Add General-local immutable source version snapshots and a row-lock
  or CAS admission seam compatible with the settled collection-item update
  path. A mutable item ID, filename, or timestamp alone is unavailable.
- [ ] 1.4 Add the server-held owner confirmation record, binding exact
  checklist revision, ordered accepted match manifest, source versions,
  canonical digest, operation ID, owner provenance and expiry. An LLM-supplied
  actor or receipt-shaped payload has no confirmation authority.

## 2. Specialist evidence and immutable receipt

- [ ] 2.1 If Finance/Travel evidence is in an adopted implementation slice,
  add owner-selected, read-only, minimal source resolution through Switchboard
  MCP. Source-owning tests must prove no raw receipt/document copy and no
  direct General peer-schema access. Absence of an immutable source version
  yields `unavailable` with reason `version_unverifiable`.
- [ ] 2.2 Specify and independently review each source owner's version/read
  fence, source-side mutation/revocation serialization, bounded lease/ack,
  crash expiry, and failure categories before enabling `covered` for that
  specialist. A row ID or a successful read without commit-valid authority is
  not enough. Keep unsupported sources unavailable rather than loosening the
  transaction contract.
- [ ] 2.3 Implement the General prepare transaction under a packet revision
  lock/CAS, with exact source version/fence and owner-confirmation checks.
  Store one immutable receipt/index only for current complete coverage; keep
  partial status as typed evidence without a complete receipt.
- [ ] 2.4 Implement idempotent operation identity, owner-only bounded index
  export, archive/reopen, historical receipt/supersession read, and rollback
  preservation. Do not create a PDF, external send, or submission status as a
  side effect.
- [ ] 2.5 Link an existing deadline or commitment only on explicit owner
  request. Only a preparation-specific commitment may close from the exact
  receipt; submission and outcome obligations retain separate closure proof.

## 3. Future behavior-executing verification and handoff

- [ ] 3.1 Extend the nearest General tool tests for exact owner-entered
  revision/order, four clause states, ambiguous proposal, typed limits,
  owner-only export, and forged confirmation refusal. Prefer one
  parametrized failure matrix over duplicated fixture setup.
- [ ] 3.2 Run real-PostgreSQL revision races: r1 versus r2 preparation has one
  current winner; keep r1 unchanged while one accepted General-local source
  changes or is revoked before commit and prove no current-complete receipt.
  Exercise source-owning specialist fences in their own real-DB tests before
  enabling remote coverage.
- [ ] 3.3 Prove identical prepare retries return one immutable receipt/index
  and no duplicate downstream commitment; changed-payload operation reuse
  fails. Prove lease expiry/crash, unreadable source, mutable-locator-only
  source, and Switchboard refusal cannot produce `covered`.
- [ ] 3.4 Extend `tests/core/test_deadlines_db.py`,
  `tests/core/test_commitments.py`, and
  `tests/integration/test_commitments_roundtrip.py` only if linkage is
  implemented: preparation may close its own commitment, while an external
  submission deadline and promise remain open. Use
  `tests/modules/test_document_renderer.py` only if a separately approved
  cover-sheet slice exists; it is not a first-slice gate.
- [ ] 3.5 Record `Tests: +a ~b -c` for each implementation PR; target about
  eight focused collected cases by reusing existing fixtures and gate species.
  Run the dirty-worktree test planner, affected unit/API, real-PostgreSQL
  concurrency/authority, collection where topology changes, Ruff, strict
  OpenSpec, overwrite/countable/guards, test budget, and terminal hosted CI
  at the exact implementation head.

## 4. Adoption, documentation, and rollback

- [ ] 4.1 Only after the owner adopts the exact text and `bu-2jtfw.9` is
  reconciled, apply one canonical General manifesto/tool-inventory amendment
  and any separately approved Switchboard routing contract. Update user-facing
  copy to say complete against the supplied list, never official/eligible.
- [ ] 4.2 Before archive, audit other active MODIFIED blocks for whole-
  requirement overwrite risk, verify source-owner contracts and migrations,
  stage a read-compatible rollback target that preserves old receipt
  readability, and reconcile the
  implemented behavior to the adopted scenarios. No external action is
  inferred from preparation.
