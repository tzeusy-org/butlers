## 0. Governance and integration gates

- [ ] 0.1 Obtain independent semantic review of this exact OpenSpec and
  proposed General manifesto/routing amendment. Confirm the owner adopts the
  exact artifact; closing the run-14 planning gate is not adoption.
- [ ] 0.2 Wait for `bu-2jtfw.9` to settle its capture, vocabulary, search,
  ownership-refusal, and generic item paths. Reconcile this package with its
  landed General manifesto/spec and serialize edits to shared item storage.
- [ ] 0.3 Allocate any minimal Finance receipt-resolution read to Finance's
  owner under a separate bounded MCP contract. Do not gate owner-statement
  locate on receipt integration or grant General Finance SQL access.
- [ ] 0.4 Allocate the owner-report confirmation and read-only validation
  seam to Switchboard's owner before any typed transition can be enabled.
  Reconcile with its durable message inbox and state store; the LLM proposal
  path must never mint an owner confirmation receipt.

## 1. General-local locate and profile behavior after adoption

- [ ] 1.1 Extend the existing collection item read seam to return an
  owner-only, qualified last-report projection for an explicitly selected
  `collection_items.id`. Preserve unknown location and custody separately;
  reject name/model collisions as ambiguous.
- [ ] 1.2 Implement the opt-in versioned profile and typed
  `possession_locate`/`possession_record` tools in a narrow General-owned module
  such as `roster/general/tools/possessions.py`, registered through the
  existing General module. `possession_record` must validate the exact
  server-held owner-report receipt through Switchboard MCP and derive its
  source reference from that result. No new item table, page, or transport lane.
- [ ] 1.3 Implement move, borrow, lend, named-episode return, retire, and
  superseding correction with explicit owner source/time evidence and
  separate observed/recorded times. Enforce one active episode per item and
  replay dependent events before accepting a factual correction. Keep the
  projection derivable from append-only events and preserve historical
  search/export.
- [ ] 1.4 Use one real PostgreSQL row lock or atomic CAS for each typed
  transition, revision check, event append, and projection update. Check
  operation identity under that lock before expected revision so identical
  retries return their original receipt after subsequent revisions.
- [ ] 1.5 Fence `item_update` (including unrelated data/tag writes),
  `item_delete`, `collection_delete` cascade, and `collection_export` against
  the opted-in profile. Generic writers must re-read under the same lock or
  refuse stale input; a reserved-namespace edit must be rejected.

## 2. Optional specialist links and routing after separate review

- [ ] 2.1 Keep receipt and transaction truth in Finance. If a reviewed
  Finance MCP read exists, resolve only an owner-selected reference through
  Switchboard and store minimal status/version; unavailable or revoked
  evidence must not mutate custody or imply purchase ownership.
- [ ] 2.2 Resolve optional people through the existing Relationship/public
  identity seam. Keep unresolved labels and private locations out of public
  graph/catalog payloads. Apply the reviewed Switchboard routing amendment
  only after its separate owner and subsystem checks.

## 3. Future behavior-executing verification

- [ ] 3.1 Extend `tests/tools/test_general_items.py` and
  `roster/general/tests/test_tools.py` for a selected item, unknown and
  source/as-of locate answers, two identical objects, borrow/lend/return,
  overlapping or wrong episode refusal, retirement, and correction with a
  dependent return. Prefer a small parametrized transition matrix over
  duplicate setup.
- [ ] 3.2 Run one real-PostgreSQL contention matrix: two equal-revision
  transitions yield one commit, a winning-operation replay returns its
  original event, and a different payload with the same operation ID fails.
  Race generic `item_update` reading revision 1 against a typed transition
  committing revision 2; both an unrelated JSON key update and a tag-only
  update must preserve revision-2 history or fail stale.
- [ ] 3.2a Add a behavior-executing spoofing case at the General tool seam:
  invented source ID, caller-asserted owner role, a non-owner message,
  mismatched payload digest, unreadable source, and Switchboard validator
  outage all refuse a new transition with item revision, history, episode,
  and projection unchanged. Prove a confirmed exact report succeeds and an
  identical committed replay returns its original receipt without a new write.
- [ ] 3.3 Exercise direct item deletion, collection cascade deletion, and
  concurrent opt-in with real PostgreSQL, plus full-profile export. Cover
  read-model qualification near `tests/api/test_general_stats.py` without
  asserting source text or duplicating the tool tests.
- [ ] 3.4 Extend `roster/finance/tests/test_tools.py` only if Finance gains a
  new bounded receipt read. At the General boundary, prove unavailable
  receipt-link status does not change custody, no `finance.*` query is issued,
  and no location/borrower/source payload enters public graph/catalog output.
- [ ] 3.5 Record `Tests: +a ~b -c` for the implementation; budget roughly six
  to eight focused behavioral cases including the real-PostgreSQL matrix.
  Run the dirty-worktree test planner, its required targeted/collection lanes,
  Ruff, strict OpenSpec, overwrite and repository guards, and terminal hosted
  CI for the exact implementation head.

## 4. Adoption and rollback boundaries

- [ ] 4.1 Update the canonical General manifesto, tool inventory, and
  Switchboard routing specification only after their exact amendments are
  adopted and reconciled with `bu-2jtfw.9`; update owning usage docs with the
  implemented wire shape. Keep Finance, Relationship, and Home ownership
  unchanged.
- [ ] 4.2 Verify disabling the new tools leaves existing profile history and
  export intact. Corrections append and recompute; no rollback claims to
  reverse a physical action. Sync/archive the adopted delta only with the
  completed implementation delivery and after checking other active
  requirement blocks.
