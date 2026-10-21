# Design

[RFC 0038](../../../about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md)
is the single design authority. This file records only what OpenSpec needs to
trace the change.

## Decisions (summary)

1. Switchboard is the control plane: ingress, goal admission, goal ledger,
   goal runner and capability broker, in its own process and container so only
   it holds the signing key (RFC 0038 D1).
2. Domain butlers are capability services and obligation owners; they never
   spawn sessions (D2).
3. Sessions run on a separate session host, sandboxed per invocation, and
   reach only the broker's session listener and their model provider (D3).
4. Every model-reachable tool declares effect class, output trust and
   sensitivity, resource and safety arguments, approval policy, idempotency,
   owner authority and, for any tool that can deliver to or invite a party,
   `recipient_params`; undeclared tools are not grantable (D4).
5. Authority ceilings derive from origin: owner, obligation, third party or
   staffer; caused admissions inherit lineage and exposure under a chain limit
   (D5). Exposure to untrusted output, including tainted stored records, gates
   consequential effects (D6).
6. The broker checks every call transactionally and signs a single-use,
   audience-bound assertion that the owning domain verifies before the handler
   runs (D7).
7. Children are depth 1, created only by `goal.delegate`, with grants that are
   subsets of the lead's minus `administer` (D8, D9).
8. Budgets, state machines, effect ledger, exact-approval execution, Stop and
   epoch-fenced recovery are goal-level (D10 to D14).
9. Model choice is per invocation by role and tier with a finite escalation
   ladder that never creates a new goal (D16).
10. Engineering executors and tool-less model functions are the only model use
    outside goals (D18).
11. Two owner rules bind every goal regardless of origin or exposure:
    communication with anyone other than the owner needs per-event approval or
    an owner-created standing rule, and `transact` is disabled (D4, owner
    decisions 8 and 9).
12. The dashboard presents one mind with specialized regions: goals are the
    unit of activity and domain pages become regions (D19). Its experience
    contract is a separate `dashboard-*` change signed off before it is built.
13. After the switch and the archive of this change, the RFC corpus is
    rewritten and reorganized around the goal architecture as separate work
    (owner decision 10).

## Cutover

No dual-running. The runtime is built dark on main, proven in dev against the
cutover gate (scenarios, adversarial suite, replay evaluation, stop and
recovery drills), then switched on by one release that also deletes the
classify-and-route path and applies the baseline deltas. Rollback redeploys
the previous release.

## Validation strategy

Evaluate whole-goal success, constraint preservation, useful proactive
actions, missed obligations, owner corrections, latency and total cost
including retries and escalations, against recorded outcomes of the current
runtime. Use zero-child work, parallel read-only children, conflicting
cross-domain constraints, scoped child writes, injected third-party content,
cancellation, recovery and delegation-escape attempts. Model tiers are
assigned from this evaluation, not from vendor benchmarks.

## Cross-change collisions

The [complete disposition ledger](collision-dispositions.md) retains the original nineteen authoring-list changes and the fresh entire active-change census. At the recorded base, 45 active changes touch the 79 capabilities this change modifies or removes; the six-requirement/56-scenario dashboard fast-answer change is an additional explicit D18 collision and is treated first. The full affected-change population is 443 requirements and 1,968 scenarios.

Each ledger row requires archive-first, a reviewed re-scope, or an explicit whole-body fold-in before task4.2. A recorded disposition does not mean that its archive, foreign owner transfer, approval or PR has occurred. Same-name MODIFIED blocks must be rebuilt from the actual refreshed baseline; every full scenario body stays authoritative until its successor is concretely preserved and signed where required.

PR4485 cannot introduce its tool-capable model path outside goals. Its complete signed twelve-choice contract is re-scoped through feature-request amendment mode, with genuine independent review and a resolving owner choice before that changed behavior is implemented. No new D18 exception or decision change is proposed.

The dashboard capability successors remain owned by the separate D19 change and its owner signoff. Existing source/custody gates and foreign claims remain binding; D19's historical generic retirement drafts are not whole-clause preservation proof; its current qualified review and authority limits are below.




### Complete correction5 functional composer — proposed, unadopted

All46outcomes (18archive-first/24rescope/4fold-in),19named scope changes,443originalrequirements/1968oldscenarios/4604oldparagraphs,324Generalist bodies, full Generalist81files1083cases79blocks, four complete folds,21outside101REQ498SC,121archive-later complete MODIFIED bodies and12provisional identities remain. The actual future remains443REQ2010SC. Every2010C4case and full C2/C3/C4 repair union has an actual complete C5successor; no case was removed or added. Current source transport has94changed/38unchanged/132coalesced paths and172atomic paths. Counts/hashes are not semantic or execution proof.

C15 removes recipient/message resolution as retry authority. Automatic enqueue requires affirmative proven pre-send/not-attempted evidence, complete recipient/message and the existing bounded policy. A confirmed external receipt survives bookkeeping faults with no resend. Begun/possibly begun transport without confirmation stays explicit uncertain and runtime-attention terminal under the existing claim/token/epoch/lease fence, with only the existing authenticated owner-confirmed one-successor reissue path. Closed safe failure reasons, quiet-hours deliver_at, the single deferral path, supersede dedupe and confirmed-only debounce remain.

C16 preserves the mandatory fixed private-result/public-audit transaction and constrained NOLOGIN audit writer. Projection rejection rolls the enclosing attempted result/timestamp/failure epoch back. Only genuine post-commit best-effort attention/independent telemetry may fail without erasing a committed result. Uncommitted/unavailable attempt evidence creates no new checked_at/failing_since or24hour fail clock. Reader/API and due checks retain only the last actual committed result, immediate no-result policy, or truthful degraded authority read. Cleanup, closed stage/code vocabulary,512character sanitization, seven-day committed-pass/24-hour committed-fail cadence, privilege boundaries and real-PG obligations survive in the complete cases.

All prior F01–F09/C01–C14 repairs remain binding, including closed D15 prompts, trusted/exposed Memory partitions and scoped apply, fixed authenticated QA relay/truthful attribution, admitted effect-free readiness, absolute initial+at-most-one pre-capability replacement, exact Education obligation grants and same bound map150cases, native deny-all, operation-bound DB-free host, existing custody/control provenance transaction owner, complete5+6coverage, retained TOML orphan cadence, original fullspan ingress/WhatsApp, current goal/invocation fleet facts, and core-ingest Telegram lifecycle ownership. The RFC0036 vision-proof controls follow the `models-exact-path-vision-proof` row above: a D18 tool-less model function through the runtime adapter's tool-less mode, conditional on owner adoption of the RFC0036 Amendment 1 draft (bu-o7lumy); the exact tuple, custody, private comparison, redaction, cleanup and zero-retry rules stay mandatory either way. Full two-RFC proposals remain exact C4 candidates, unadopted. No eligibility/custodyB/private/PG/M6 release.

PR4485 remains first: no new goal-external tool-capable D18fast-answer exception. Operative6REQ56SC and separate held4file8REQ65SC/canonical8/65candidate remain distinct with all12SIGNEDhistory choices,12unsigned treatments,oneexactUNSENTquestion,sevencompound obligations/sixcitationcases. Four native append proposals remain unchanged/unapplied. Inherited outside-onlyO01–O03 retain complete bodies/treatments/existing-holder limits and are not introduced by this composer.

Latest full native query2026-10-15T00:31:53.689715+00:00–2026-10-15T00:31:55.955834+00:00 is8934ALL136READY320BLOCKED31IP58OPEN/allfiveexit0, with210full relevant holders/204rowrefs and58unchangedheads/1354complete paginatedpublic files. Foreign authority and source allocations remain held. Existing PR4524/4525/4526 ordinary green contexts do not clear protected failed runs or establish merge/delivery. OriginalAC1–3 remainunmet;AC4source-onlyTests:+0~0-0 preserved. Allstrict/guards/productgates remainUNRUN; inherited trace NONPASS. Fresh independent whole review and exact ROOT/holder source allocation/adoption precede any repository action.


## Spec format note

The two new capability specs keep `## Purpose` and `## Source References`
headings, which `spec-trace-check.py --authoring` reports as non-delta H2s.
That matches this repository's convention for new capabilities in active
changes (for example `specify-improvement-proposal-spine` and
`add-connector-oauth-scope-surface`); `openspec validate --strict` accepts
them, and archiving carries them into the baseline spec.

The checker's other reports on this change are inherited, not introduced:
MODIFIED blocks copy baseline requirements that have no `ID:` line or that
use multi-WHEN scenarios, a MODIFIED block repeats its baseline ID, and
REMOVED blocks carry `Reason`/`Migration` rather than `ID`/`Source`/`Scope`.
Backfilling IDs across the baseline is left to the post-switch RFC and spec
reorganization.
