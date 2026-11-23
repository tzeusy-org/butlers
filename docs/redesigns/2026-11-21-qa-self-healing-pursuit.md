# Run 25: QA that heals instead of tripping

**Scope:** a scoped pursuit on the QA ecosystem, asked for by the owner on 2026-11-21 after
repeated `circuit breaker tripped after 5 consecutive failures` notices and no visible
self-healing. It pairs a live dev-stack debugging pass with seven read-only lens agents. It is
planning only: no runtime code, spec, credential or deployment changed.

**North star:** five-second fleet verification with earned calm: nothing fabricated, failure never
impersonates health, and every consequential claim opens its evidence. For QA that means a
remediation loop the owner can trust without watching it. [Mandate](../../about/heart-and-soul/vision.md);
[cumulative ledger](pursuit-ledger.md).

## What is actually happening

QA's discovery works and its remediation has been dead for 81 days. The breaker is the visible
symptom of a loop that cannot succeed.

- **The last QA pull request was #3957 on 2026-09-01.** None has been opened since.
- **Every successful investigation is recorded as a failure.** After the credential was removed
  from investigation agents (#4595), a committed fix is published only when a trusted publisher is
  injected (`src/butlers/core/qa/dispatch.py:1385`). No production caller injects one
  (`src/butlers/modules/qa/__init__.py:1320`, `:1834`), so the attempt ends `failed` with
  `publication_unavailable` (`dispatch.py:1429-1466`). `failed` counts toward the breaker
  (`src/butlers/core/healing/constants.py:3`), which halts dispatch after five.
- **A manual reset buys five discarded investigations.** Observed on 2026-11-21: the breaker was
  reset at 12:50 UTC; the 12:54 and 13:04 patrols each dispatched two investigations (the
  concurrency cap); all four model sessions succeeded and all four attempts closed `failed`. One
  more failure re-trips it. While tripped, each ten-minute patrol re-triages about 17 findings as
  novel and writes one breaker event per finding.
- **The manifesto promises a recovery that cannot occur.** `roster/qa/MANIFESTO.md:197` says the
  breaker resets on the next success, but no dispatch is admitted while it is tripped.
- **The planned repairs are stalled.** Publisher activation waits on `bu-mc01y5.7` (blocked and
  unowned since 2026-11-04, waiting for an isolation facility that does not exist). The breaker
  symptom itself is `bu-mc01y5.12` (open since 2026-10-17); its fix, #4749, was closed stale on
  2026-11-03. The adopted remediation epic `bu-6a9xxw` (run 19) has shipped nothing: its seven
  children are serial and the first waits on `bu-mc01y5.7`.

Two corrections to the obvious fixes came out of the lenses:

- **Reopening #4749 alone would make things worse.** Excluding `publication_unavailable` from the
  breaker count removes the only spend limiter. By configuration (two concurrent investigations,
  ten-minute patrols, a 60-minute cooldown per fingerprint) QA would then re-diagnose the same
  findings indefinitely with nothing published.
- **An activated publisher would not be enough.** The investigator launches through another
  butler's spawner, inherits that butler's persona, memory context and a shared prompt that
  forbids reading source or running shell commands, resolves its model from a tier shared with
  cheap discretion calls, and has no evidence it can read. Today's sessions ran 30 to 85 seconds.

**Limits of this run.** The dev database and dashboard API were not reachable from the audit host,
and the daemon pod had been restarted minutes earlier, so breaker frequency over weeks was not
measured; the live evidence is one reset cycle plus source, beads and pull-request history. The
containment finding below is from source and chart reading and was not exercised.

## Tier board

Run 19's board is the baseline. No code shipped between the runs, so no tier improved; two rows
are new and two are lower because this run read deeper, not because the system regressed.

| Surface | Run 19 | Run 25 | Evidence |
|---|---|---|---|
| Deterministic discovery | Functional | Weak (lower: deeper read) | One defect becomes about 16 fingerprints per window because the log scanner hashes the rendered traceback; warning-level owner-facing failures are excluded |
| Runtime ownership and lineage | Broken | Broken | Launch still resolves through another butler's spawner; three identities in one launch |
| Investigation evidence and outcomes | Broken | Broken | Borrowed persona and prohibitions; no readable evidence; no executed verification |
| Durable admission and feedback | Weak | Weak | A tripped breaker drops one-shot findings; cap-skipped findings are not queued despite the log line |
| Publication capability | Unverified | Broken (lower: deeper read) | No publisher is injected, no concrete provider exists, and follow-ups cannot publish even with one |
| Recovery and cost assurance | Weak | Weak | Merge is still the end of tracking; a breaker-halted patrol is stored as `suppressed` |
| Investigator containment | not tiered | Broken (new) | The isolation the publication hold protects is not enforced at runtime; details are held in the bead, not in this public file |
| QA operator surface | not tiered | Functional (new) | Accurate about the symptom, silent about the cause; Reset promises a remedy that cannot work |

## Themes

1. **A gate with its cost and without its benefit.** Publication is off to protect an isolation
   boundary that the runtime does not enforce. The system pays the whole price (no fixes) and
   gets none of the protection. Containment must be made real regardless of how fixes are
   published.
2. **Success recorded as failure.** A committed fix becomes `failed`, the dossier calls its
   publication "unresolved" when the code knows nothing was pushed, the KPI strip calls a
   deliberate gate a crash, and the breaker reports a streak with no cause. Each is failure or
   uncertainty impersonating something else.
3. **Identity that does not identify.** Fingerprints change with dates in tracebacks and with
   every deploy, so cooldowns, dismissals, holds and joins cannot hold. Anonymization rewrites
   module paths and exception classes as hostnames, which is why titles read
   `unknown in [REDACTED-HOST]`.
4. **Work thrown away.** The branch is deleted and the worktree removed after every run; only a
   truncated diff snapshot survives, with no way out of the system.
5. **Nothing watches the watcher.** QA reports itself healthy while shipping nothing, the only
   owner alert covers an overdue patrol, and QA can neither investigate itself nor see that its
   prerequisite work items are stalled.
6. **Serialization as a blocker.** The honest-outcome work of run 19 sits behind an isolation
   facility for ordering reasons, not technical ones. Most of what would stop the breaker storm
   needs no credential and no publisher.

## Ranked moves

Trust and correctness defects lead. Moves 1 to 9 need neither a publisher nor `bu-mc01y5.7`.
Every move is a held bead under epic `bu-ejow5p` with a full readiness packet in its structured
fields.

1. **Evict the QA write token from investigator-reachable storage (`bu-ejow5p.1`, S).** Use a
   read-only token for pull-request polling, refuse to resolve the write key in the daemon, and
   state plainly that investigator containment is not yet enforced. Includes an owner rotation.
2. **Proposal-ready rung (`bu-ejow5p.2`, M).** With no publisher, investigate each fingerprint
   once, seal and retain the validated patch, record a `publication_unavailable` refusal receipt,
   show the case as provably unpublished, and hold the fingerprint. The breaker change and the
   hold ship together. Supersedes `bu-mc01y5.12` and #4749; pre-empts part of `bu-6a9xxw.2`.
3. **Breaker v2 (`bu-ejow5p.3`, M).** Count execution failures only; evaluate once per patrol
   instead of once per finding; queue findings instead of dropping them; carry a typed cause to
   the API, briefing and reset dialog; probe back to health with backoff instead of waiting for a
   manual reset; persist a halted patrol as halted.
4. **Canonical finding identity (`bu-ejow5p.4`, M).** Fingerprint the exception class, the
   innermost repository frame and a normalized message, never the rendered traceback; let typed
   code identifiers bypass hostname scrubbing; stop raw exception text bypassing anonymization.
5. **Engineering-executor launch profile and model ladder (`bu-ejow5p.5`, M).** One small
   core-owned system prompt with no persona, memory or situational context (RFC 0038 D18), and
   an exact-tier ladder on workspace-capable runtimes in place of the shared specialty tier.
6. **Honest QA surface (`bu-ejow5p.6`, M).** Publication-off as a named standing condition
   rather than a failure streak, a funnel from findings to recovered, days since the last fix,
   null instead of a calm 0% for an empty sample, and a recorded reason on every reset.
7. **Owner-as-publisher Fix Inbox (`bu-ejow5p.7`, M).** Export a retained patch for review, let
   the owner publish it under their own identity, and bind that pull request back to the attempt.
   No credential reaches an agent.
8. **Heal the healer (`bu-ejow5p.8`, M).** Deterministic remediation-loop liveness conditions
   with owner attention (publisher absent, no fix in N days, breaker open), an honest staffer
   status, and a prerequisite-stall watchdog that lives outside the runtime.
9. **Tracked-defect linkage (`bu-ejow5p.9`, M).** A finding linked to a work item or pull request
   is not re-investigated until its fix deploys; a recurrence after that is a regression. Both
   post-reset investigations on 2026-11-21 went to a defect already filed as `bu-2cgabc`.
10. **QA-to-fleet handoff (`bu-ejow5p.10`, L).** QA keeps a deduplicated outbox of structure-only
    packets; a read-only transport exposes it to the host; a deterministic coordinator-side intake
    turns packets into dispatch-ready work items; the existing fleet fixes them; QA closes the
    loop from a commit trailer on main plus the deployments ledger. No runtime-to-tracker write
    path is added, in line with the standing deny.
11. **Per-attempt Kubernetes Job investigator with an isolation verifier (`bu-ejow5p.11`, L).**
    One credential-free pod per investigation gives a distinct identity and private process,
    mount and network namespaces from the platform, replacing the one-host facility that stalled
    `bu-mc01y5.7`. A probe built from the production template proves it with inverse controls.
12. **Dedicated publisher workload (`bu-ejow5p.12`, L).** A separate publisher with a real
    compare-and-swap provider, a follow-up operation identity and a restore fence rebuilt from
    the bound repository. Deferred until the remediation model is chosen.
13. **Dispatcher-assembled evidence bundle (`bu-ejow5p.13`, M).** Evidence the dispatcher
    observed and issued identifiers for, with notes validated against it, replacing agent-written
    evidence lines shown as evidence.
14. **Dispatcher-executed red/green receipt (`bu-ejow5p.14`, L).** Trusted code shows the
    reproducer fails on base and passes on head, on a prebuilt test environment.
15. **Per-failure-class trust ladder (`bu-ejow5p.15`, L).** Durable rung state per class,
    automatic demotion on any bad outcome, promotion only by owner step-up on evidence the agent
    cannot forge, and an RFC draft for steward rungs with restorative actions first.

**Recommended order.** Moves 1 to 5 and 8 first: they stop the waste and the false signals this
month. Then 6, 7 and 9 so retained fixes reach the owner. Then the strategic pair: handoff
(move 10) as the fastest route to a merged fix, and containment (move 11) because it is needed
whichever way fixes are published. Moves 12 to 15 follow the owner's choice of model.

## Owner decisions

The gate `bu-l34qcx` lists these in full. The owner released it on 2026-11-22, adopting every
recommended option (auto-merge and auto-deploy rungs stay unadopted; move 15 drafts the RFC only).
Per-move resolutions are in each child bead's notes; the token rotation is owner step `bu-ejow5p.16`.

1. Rotate the QA write token and choose the polling arrangement (move 1).
2. Choose the remediation model: fleet handoff, isolated publisher, or both with handoff first
   (recommended).
3. Accept one pod per attempt as the isolation facility for the publication-authority
   requirements, or keep the one-host plan.
4. Amend the publication-unavailable scenario so a committed investigation is an unpublished
   proposal and an execution success.
5. Act as interim publisher for retained patches, or not.
6. Decide whether auto-merge, auto-deploy or auto-rollback rungs are ever acceptable; today's
   doctrine keeps the merge seat human and forbids self-granted promotion.
7. Let moves 2, 3 and 6 land ahead of run 19's serial lane, re-scoping `bu-6a9xxw.2`.

## Dedupe and dropped

| Existing owner | Treatment |
|---|---|
| `bu-mc01y5.12`, #4749 | Superseded by move 2; do not reopen the count-exclusion alone |
| `bu-mc01y5.7` | Move 11 re-scopes its isolation half; move 12 splits out its restore fence |
| `bu-6a9xxw.1` to `.7` (run 19 A to G) | Not re-filed; moves 2, 3, 5, 9 and 10 are deltas or pull-forwards and name the overlap in their packets |
| `bu-mc01y5.4`, `.2`, `.31` | Move 12 consumes them; no second verifier or provider mapping |
| `bu-2cgabc` | The live defect QA rediscovered 16 ways; evidence for moves 4 and 9, not re-filed |
| `bu-fvw4ap`, `bu-0j2juv` | Move 8 consumes the condition producer and notes the unscraped metrics; no duplicate |

Dropped from the ranked list, so nothing vanishes silently:

- Condition-ledger coverage for stored-function drift, masked provider-auth failures and MCP
  reachability: deferred until move 4 lands, because new sources amplify noise while identity is
  broken.
- Impact-ranked triage and sustained-warning admission: deferred for the same reason. The live
  example (a reminder projection failing 97 times in 25 minutes at warning level) is recorded in
  move 4's evidence.
- Re-planning run 19's programs as a standalone work item: it is decision 2 on the gate.
- An in-pod distinct-UID stopgap for investigators: rejected in favour of move 11, since it still
  shares the pod network namespace.
- Sidecar and hosted-sandbox isolation: rejected against the publication-authority requirements.

## Run receipts

- Seven lens agents in three batches of at most three (3, 2, 2); the owner waived the hourly
  stagger mid-run. Lenses: isolation unblock, value without publication, discovery quality, fleet
  handoff, loop closure and autonomy, investigation runtime (planning tier) and the QA dashboard
  surface (audit tier). All seven returned structured output; synthesis was done by the
  orchestrator from the on-disk harvest.
- Beads: gate `bu-l34qcx` and epic `bu-ejow5p` are owner-assigned; 15 children each depend on the
  gate, with 11 ordering edges between them. `bd lint` over all 17 records reported no warnings.
  No child is ready.
- No tracked data JSON was created. Per-agent output stays in the gitignored local harvest; the
  beads' structured fields hold the packets. The owner's artifact report is private:
  <https://claude.ai/artifact/3SDDwtg9Y31FHnwpgT1iGK>. It has no structured attachment.
- Source anchors were read at `664406349`. Re-verify them before implementation.
