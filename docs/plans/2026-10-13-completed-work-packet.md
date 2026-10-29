Reviewed commit: 2c4c54c685265e126f299b0295dff35cc89024e8

# Completed-work audit, 9–13 October 2026

## Outcome

**Four confirmed findings: one P1 and three P2.** Calendar response execution has an unresolved-attempt boundary gap across enabled butlers, and two related ownership/recovery defects. The new condensation guard can admit executable additions that suppress existing tests without survivor evidence. No P0 was found.

[Observed] Delivery discipline was strong: all **42 merged PRs** in the window have a successful GitHub `merge_group` run at their actual merge SHA, including successful `check`, `guards`, and `frontend` contexts. The audit nevertheless reproduced correctness gaps that those tests do not cover. This is a review of completed work, not a claim that the whole repository is correct.

[Observed] **101 beads closed**, including 59 explicitly review-titled records, 2 epics, 2 deployment-related closures, and other implementation, preparation, adoption, reconciliation, and documentation records. Review count is title-classified and includes reviews of proposals; it is not a count of independently shipped features. Seven broader beads named in merged commits remain open or blocked. Their source prerequisites must not be counted as completed parent outcomes.

## Scope and method

- Window: **2026-10-09 01:42 UTC through 2026-10-13 01:42 UTC**, or **9 October 09:42 through 13 October 09:42 Singapore time**. This fixes “past four days” to a 96-hour interval at audit start.
- Git range: `8c81a494cd3126fbf6459f1f00182d7252745bdd..2c4c54c685265e126f299b0295dff35cc89024e8`. GitHub merge timestamps were checked separately; the selected 42 commits and 42 merged PRs agree. Net range: 299 files, 32,556 insertions, 7,325 deletions.
- Live Dolt-backed `bd list --all --closed-after ... --limit 0 --json` supplied the closure inventory. The gitignored export mirror was not used as authority. Relevant parent states and deployment comments were read separately.
- Applied th-projects/project-review, th-engineering/engineering-bar, and the repository's craft-and-care and heart-and-soul guidance. Three isolated review lanes covered Calendar/approvals, CI/tooling, and runtime/data/connectors; the lead reconciled coverage, tracker state, hosted checks and findings. The Calendar P1 also received a separate adversarial challenge.
- Every PR received a scoped diff/contract review. Large supporting test and workflow bodies were risk-sampled, not exhaustively re-executed. Every closed record is included in the inventory; historical private review packets were not all reauthenticated. The two completed epics' current closure account was inspected, including its 14-row disposition map, rather than inferred from child counts.
- The initial audit changed no product source, tracker state, hooks, deployment or provider state. After the owner requested autonomous follow-through, four scoped follow-up beads were created and dedicated agents dispatched as recorded below. Existing unrelated untracked root files were preserved.

## Normative baseline

The binding order was doctrine and RFCs, then capability specs, topology, and engineering standards; issue and implementation claims were checked against those sources. Relevant norms include MCP-only inter-butler communication, isolated owning schemas, deterministic infrastructure, fail-closed authority, same-change contract documentation, genuine behavior evidence and explicit incomplete outcomes.

The shape scanner assessed the repository **SHAPED**, not Mature: all five pillars and their navigators exist. Conservative per-pillar ratings are doctrine **structured**, design contracts **structured**, capability specs **structured**, topology **structured**, and engineering standards **structured**. These are baseline structure judgments, not semantic certification. The scan's textual heuristics found source references in 158/398 active specs, scenarios in 398/398, doctrine references in 19/37 RFCs and topology cross-links in 8/8 documents. Some spec scaffold markers remain. This inherited traceability debt is not attributed to the four-day window.

Primary standards: [vision](../../about/heart-and-soul/vision.md), [development](../../about/heart-and-soul/development.md), [engineering bar](../../about/craft-and-care/engineering-bar.md), [Calendar requirements](../../openspec/specs/module-calendar/spec.md), [dashboard API](../../openspec/specs/dashboard-api/spec.md), and [testing requirements](../../openspec/specs/testing/spec.md).

## Confirmed findings

### F1 — P1: one Calendar owner can bypass another owner's unresolved response attempt

**[Observed, Confirmed, high confidence]** PR #4464; closed bead `bu-q7vx1q.18`.

`src/butlers/modules/calendar_response.py:500–508` checks unresolved command rows through the current butler's pool. Both Messenger and Relationship enable Calendar responses. Calendar discovery mirrors accessible account calendars into separate schemas; the submitted entry UUID selects its local owner, not an exclusive owner of the physical invitation.

After an approved Messenger response becomes uncertain, an approved Relationship response to its copy of the same calendar/event/self can dispatch another provider write while Messenger's unresolved command remains. The production coordinator reproduction produced:

```text
messenger uncertain provider writes= 1
relationship applied provider writes= 1
Same physical target key: True
First fence still unresolved: True
```

This contradicts REQ-module-calendar-029's requirement that a replacement request cannot bypass unresolved target evidence. Both attempts are approved; the defect is the physical target's uncertainty fence, not an unapproved-write claim.

**Supporting evidence:** local fence query above; `calendar.py:5997–6019` discovery and `_target`'s null-owner admission; both roster configurations; `calendar_workspace.py:2660–2673` local-UUID owner lookup. The lead independently reran the reproduction and a separate reviewer confirmed the actual configured path.

**Contradictory evidence checked:** the common advisory lock serializes claims but does not make the other schema's rows visible. ETag/If-Match prevents stale overwrites, not a second attempt after a connection failure with no provider effect, or after a later preparation reads a fresh version. Existing migrated tests cover same-schema fencing. The software SQL carrier proves the control-flow gap, not a real concurrent PostgreSQL race or duplicate actual provider effect.

**Remedy:** enforce a single authoritative owner/fence for each physical target across all enabled callers, or reject nonauthoritative owners before admission. Respect the existing data boundary; do not fix this with an undocumented cross-schema shortcut. Extend the existing migrated contract tests to two enabled owners and prove zero second provider call while the first outcome remains unresolved. Any new shared authority mechanism needs its governing design contract settled before implementation.

### F2 — P2: invitation capability can belong to a different schema than its entry ID

**[Observed, Confirmed, high confidence]** PRs #4455/#4464; `bu-q7vx1q.17` and `.18`.

`calendar_workspace.py:1629` returns the globally deduplicated instance ID, while lines 1636–1638 choose its advertised owner and response capability from a separately canonicalized source. Event deduplication chooses the freshest instance; source canonicalization ranks operational capability, health and cursor freshness. Those selectors can choose different schemas.

The reproduction using both production selectors returns a Finance instance with Messenger as its advertised response-enabled owner. POST `/respond` looks up the actual UUID and dispatches to Finance, where the response tool is unavailable. A Messenger/Relationship mismatch can also suppress the dossier link because `CalendarResponsePanel.tsx:161–166` compares the advertised owner against the receipt's actual owner.

**Remedy:** derive capability and owner from the exact writable physical instance, or deliberately resolve to the canonical owner's corresponding instance before advertising a write. Add the cross-schema freshness/owner-disagreement case to the existing API tests. This is a current-contract correction, not new feature scope. The selector reproduction was independently rerun; the browser failure was traced, not newly driven live.

### F3 — P2: checking a pending undo replays the original response request

**[Observed, Confirmed, high confidence]** PR #4464; `bu-q7vx1q.18`.

`CalendarResponsePanel.tsx:96–103` stores the inverse receipt without updating the entry's retained request. Lines 150–155 use that retained original request for “Check response outcome.” An original response can apply, Undo can reserve and approve an inverse, and dispatch can then be temporarily unavailable. The API legitimately returns `approved` for that inverse. Checking it resubmits the original request and replaces the inverse receipt with the original applied result. If the invitation row disappeared, the check control is absent altogether.

The reachable backend chain is `_dispatch_approved_action_outcome`'s unreachable result (`approvals.py:1400–1415`), `approve_owning_action` retaining approved status (`3190–3198`), response projection (`calendar_workspace.py:2598–2601,2628–2630`), and the undo wrapper (`3768–3771`). The durable inverse and generic dossier remain available; this is broken mounted recovery, not lost database evidence.

**Remedy:** retain and query the inverse's exact identity independently of the invitation row; never use the original request as its status check. Add mounted coverage for applied original → approved inverse → check, including row removal. The source path was traced and reopened by the lead; no new browser execution is claimed.

### F4 — P2: condensation admission misses executable additions that skip existing assertions

**[Observed, Confirmed, high confidence]** Introduced in #4466, retained through the subsequent compiler/observer fixes; finite completed prerequisite `bu-ly3lv5.11.1`.

`check_condensation_ledger.py:889` accepts module/class context when old statements remain an ordered subsequence. Its ancestor-conftest scan at line 891 iterates the baseline-only paths populated at line 832. Two committed, clean-tree changes therefore evade loss detection:

1. Append `test_a.pytestmark = pytest.mark.skip(reason="unavailable")` to an existing test module.
2. Add a previously absent ancestor `tests/conftest.py` with an autouse fixture calling `pytest.skip`.

In a disposable repository the original `assert False` executes and fails. Each addition changes actual pytest execution to `1 skipped`, exit 0, while `verify(root, base, [])` reports `PASS`, `losses: 0`, and `ledger_clusters: 0`. The lead independently repeated this behavioral reproduction.

This violates the preservation requirement in REQ-testing-053 and the documented prohibition on treating unchanged names or additive structure as preservation. No evidence was found that these particular bypasses were used in the audited production changes.

**Contradictory evidence checked:** a simpler top-level rebinding example could be caught by duplicate-name validation and was discarded as the primary example. The two reported forms do not duplicate a binding. Fresh current inventory and upper-bound test budgets do not prove historical execution preservation, and ordinary skip admission does not restore that missing guarantee.

**Remedy:** include old and current ancestor-conftest paths; conservatively account for executable context additions unless their effect on retained tests is actually proven. Extend the existing loss-scanner tests with these two committed cases and harmless-addition controls. Keep this within the existing guard species.

## Completion accounting

The [inventory](2026-10-13-completed-work-inventory.md) lists every merged PR, its exact successful merge-group run, and every closed bead. The [review details](2026-10-13-completed-work-details.md) preserve per-domain coverage and limitations; [reproductions](2026-10-13-completed-work-reproductions.md) retain the executable synthetic controls.

The following source-linked parent outcomes remain incomplete in live Beads, appropriately limiting what their merged PRs prove:

| Bead | State at audit | Remaining outcome class |
| --- | --- | --- |
| bu-ly3lv5.6 | blocked | Whole inventory/shard performance and delivery acceptance beyond the merged source |
| bu-ly3lv5.10 | in_progress | Fleet hook activation, historical timing and broader first-red workflow |
| bu-ly3lv5.11.3 | blocked | Actual first three condensation clusters and survivor/runtime evidence |
| bu-ss4o2u | in_progress | Contacts runtime/SQL and original operational outcome |
| bu-yq0rz9 | blocked | Dev heartbeat impact and full operational acceptance |
| bu-0kf2fd | blocked | Kubernetes cutover contract/operational proof beyond proposal publication |
| bu-ihitkl | in_progress | Non-Python resource observation implementation and runtime qualification |

The completed `bu-h40h2b.5` epic has a retained 14-row closure account: S1–S6 and S8–S13 implemented, S7 superseded by an adopted delivered contract, S14 audit-only. Its executable-actions sub-epic references earlier PRs #4186/#4187/#4362. Those are retrospective closures, not new changes in this window. The inspected account and current source links support the disposition structure; this pass did not independently replay every historical packet or all 14 old behaviors. The two gen-1 reconciliation records explicitly close by supersession, not by erasing their old unresolved evidence.

`bu-psarp` and `bu-7exe4.2` record a dev rollout on 11 October. PR #3960 independently shows merged on 8 October, outside this window. Notes explicitly authorize the sole dev fleet and supersede the older production wording; rollback ordering is recorded. Closure assertions include Helm revision 9, the deployed image and preserved anchor/message counts. The issue bodies and retrieved comments do not link a per-writer immutable image/process/health receipt sufficient to independently revalidate the historical rollout. **[Unknown]** This audit does not establish failed rollout or missing authorization. Recover the existing receipt before relying on the historical fleet-wide operational attestation; do not redeploy merely to recreate audit evidence.

## Verification and limits

[Observed] Fresh focused tests: **100 passed** (85 Calendar/approval/notification, 15 Contacts/liveness/Prometheus/Gmail). Three synthetic controls reproduce F1, F2 and F4 using production code; F1/F2/F4 were independently repeated by the lead. F3 is a reachable static control-flow finding. These are scoped results, not a full-suite verdict.

[Observed] Hosted evidence: all 42 actual merge SHAs have successful merge-group runs and all three named required contexts. This pass read GitHub run/job metadata, not every historical JUnit, artifact digest, review transcript, or provenance bundle. Existing historical closure claims remain historical claims except where independently verified here.

[Observed] `check_archived_requirements_landed.py --strict` exits 1 with 707 requirement findings across 569 entries. The ratcheted invocation exits 0 with 579 frozen entries outstanding. This is inherited debt, explicitly disclosed by related closure notes, not evidence that a new change introduced 707 regressions or that all archives fully match current specs.

[Unknown] One investigator's targeted CI-tooling pytest command lost its terminal receipt. Its result is excluded from the 100 passing tests; process exit without a retained verdict is not a pass. No broad local Docker lane, new PostgreSQL concurrency run, browser session, live provider mutation or cluster operation was used as audit evidence.

The first general repository scan included local proof snapshots and was stopped; the replacement scan ran in the clean report worktree. Artifact-inflated counts were discarded. Neither scanner is correctness evidence.

## Scoped scorecard and verdict

| Area | Score / 5 | Confidence | Basis |
| --- | --- | --- | --- |
| Doctrine and scope alignment | 4 | medium | Narrow prerequisites and future proposal boundaries are explicit; no new doctrine breach confirmed |
| Correctness and data boundaries | 2 | high | Reproduced cross-owner write fence plus ownership/recovery defects |
| Testing and preservation | 3 | high | Real focused/hosted evidence, but missing multi-owner cases and executable-addition guard coverage |
| Release and closure discipline | 4 | high for source; medium for operations | All 42 exact merge SHAs have named green gates; deployment receipt gap remains |
| Documentation and traceability | 3 | medium | Same-change contracts and explicit supersession, with inherited archive/source-link debt |
| Tooling and maintainability | 3 | medium | Extensive proof machinery and explicit boundaries; one reproduced fail-open path |

**Verdict: Functional but accumulating debt** for this window. The scoped mean is 3.17/5; the reproduced external-write boundary takes priority over the mean. Dependency/security/performance scores for the entire repository are not inferred from this audit. Important strengths to preserve are task-bound approval execution, durable uncertainty markers, truthful degraded states, explicit parent incompleteness, and hosted checks on the actual landing tree.

## Planning handoff

The owner subsequently authorized autonomous follow-through. The audit findings remain pinned to the reviewed commit; later correction delivery must be verified separately. Address F1 before treating cross-owner Calendar response safety as complete. Resolve its authority placement under existing doctrine; do not invent cross-schema access. F2 and F3 are cohesive Calendar corrections against existing contracts and can reuse the current owners/tests. F4 belongs to the existing condensation guard and accepted testing requirement. A deduplication pass checked open/recently closed beads, concrete description terms and active PR file surfaces. Open #4394 owns Calendar context/read-model work, #4484 owns observer decoding and #4478 owns a real condensation candidate; the dispatched corrections avoid those surfaces where possible.

Keep the seven broader incomplete outcomes open until their original evidence is earned. Treat deployment receipt retrieval as bounded evidence work; do not count it as a demonstrated runtime defect. Do not launch a repository-wide spec rewrite, new guard species, blanket test removal, hook installation or deployment from this audit.

## Veracity ledger

| Challenged claim | Disposition | Evidence |
| --- | --- | --- |
| Green CI proves all completed behavior correct | Incorrect | F1/F2/F4 reproduce against the green merged tree |
| A shared advisory lock closes the cross-owner response fence | Incorrect | It serializes claims while each query still reads only its own schema |
| Dev rollout violates a production-only authorization | Incorrect | Explicit October 8 owner note says no production fleet exists and authorizes dev rollout |
| Deployment definitely failed | Unverifiable; excluded from findings | No historical per-writer receipt was located in issue body/comments; absence there is not failure proof |
| Any top-level test rebinding bypasses every guard | Overstated; replaced | Duplicate-name scanner can catch direct rebinding; confirmed forms use attribute mutation/new conftest |
| All 101 closed beads represent shipped features | Incorrect | Review, preparation, supersession and retrospective closure records are present |
| The shape scanner proves semantic spec drift | Incorrect | Source-link heuristics are structural, and strict archive results are separately scoped |

No other P0/P1 claim survives. Every retained P1 includes both supporting evidence and the contradictory mechanisms checked.

## Authorized autonomous follow-through

Following the owner's subsequent instruction, these structured beads were created, linted and assigned. This records dispatch, not completion.

| Bead | Outcome | Initial dispatch |
| --- | --- | --- |
| `bu-ed7nzt` | Resolve F1 physical-target response authority under existing doctrine; prepare concrete safe correction or the smallest necessary amendment | Dedicated authority-analysis lane |
| `bu-cxukkp` | Correct F2/F3 physical-owner capability and exact inverse recovery as one cohesive Calendar outcome | Dedicated implementation worktree/lane |
| `bu-36bqku` | Correct F4 executable-addition suppression admission within REQ-testing-053 | Dedicated implementation worktree/lane |
| `bu-cc098y` | Recover historical per-writer rollout evidence without new operations | Queued for the authority lane after its current task |

The first three have isolated worktrees and active owners. The existing seven broader source-linked outcomes remain with their existing owners; this audit does not duplicate or relax their unfinished requirements. Implementation and review are separate ownership steps. The audit report remains an immutable account of the findings at its reviewed SHA.
