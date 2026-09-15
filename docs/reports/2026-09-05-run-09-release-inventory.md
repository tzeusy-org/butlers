# Run 09 all-15 release inventory

Evidence snapshot: `2026-09-05T15:15:22+08:00` (`2026-09-05T07:15:22Z`)

Scope: the 15 ranked moves in
[the 2026-09-01 JARVIS pursuit dossier](../redesigns/2026-09-01-jarvis-pursuit.md#ranked-moves-run-09),
their canonical Beads records `bu-7exe4.1` through `bu-7exe4.15`, and current artifacts that
materially overlap those moves. Every Beads, GitHub, worktree, branch, and OpenSpec observation in
this report was refreshed read-only at the timestamp above.

## Decision

**Recommendation: HOLD `bu-xf54r`; do not close the all-15 release gate yet.** [Inferred]

The 15 move packets pass structural lint, but none is semantically complete under the canonical
Dispatch Readiness Packet contract. The release control plane is not safe for an all-at-once
release:

- [Observed] `bd list --parent bu-7exe4 --status open,in_progress,blocked,deferred,closed` returns
  17 direct children. Exactly 15 are the contiguous ranked move IDs `bu-7exe4.1` through
  `bu-7exe4.15`; the other two are evidence-preparation tasks `bu-yl86c` and `bu-n1p0o`.
- [Observed] Move state is 13 open, 1 blocked (`.2`), and 1 closed (`.3`). No canonical move is in
  `bd ready` while `bu-xf54r` remains open.
- [Observed] Scoped `bd lint` over all 15 move IDs, including closed `.3`, returns zero findings,
  and every move has non-empty description, design, and acceptance-criteria fields. That is only
  structural evidence.
- [Observed] A semantic audit of all 15 finds outcome/non-goals, governing intent, surface map,
  behavior/failure coverage, documentation impact, and named verification in every packet. None
  records the required baseline commit or expected net test delta. Semantic result: 0/15 complete.
- [Observed] Global `bd lint` still reports 55 warnings across 53 records, including the run-09
  parent epic (`bu-7exe4`, missing `## Success Criteria`) and the release gate (`bu-xf54r`, missing
  `## Acceptance Criteria`). The move packets pass; the two records controlling their release do
  not.
- [Observed] `bu-xf54r` currently has 20 dependents: the 15 canonical moves and the five nested
  voice children. Its description incorrectly claims that closing it makes “all children enter
  bd ready.” Closure can remove only the `bu-xf54r` edge. `.2`, `.6`, `.11`, `.14`, and nested
  voice work retain other blockers; `.3` is already closed. The gate must not impersonate the
  dependency resolver.
- [Observed] Closing the single gate would still expose several otherwise-unblocked moves at once
  even though their declared surfaces collide with each other or with live foreign work. The graph
  does not encode all of those serializations.
- [Observed] `.2` has a foreign claim and a clean, published worktree at
  `cf0ac589f0777b01484a8a984ffbcee6d0ba2da9`, but
  [PR #3960](https://github.com/tzeusy-org/butlers/pull/3960) is draft, conflicting, and blocked on
  separately authorized fleet deployment evidence. Its stale heartbeat does not transfer
  ownership.
- [Observed] `.7` overlaps a foreign worktree with 15 uncommitted paths and no PR for the current
  slice. That unpublished work must be preserved.

The smallest safe next step is to make every non-closed canonical move packet semantically complete,
repair the parent/gate packet headings, rewrite the gate contract to say closure removes only its own
blocker, and encode or otherwise enforce the missing serialization listed below. Only after all 14
non-closed move packets pass a refreshed semantic audit may the owner release decision be presented
or `bu-xf54r` be closed. Closing that gate is planning/execution release only. It does **not**
authorize runtime access, credentials, device or provider calls, deployment/restart, destructive
action, PR merge, or production change.

## Reading the inventory

- **Packet complete** uses the canonical semantic contract: structured outcome and non-goals;
  governing doctrine/VISION and exact spec intent with baseline commit; owned surfaces and relevant
  trust/schema/runtime/persistence boundaries; happy-path plus relevant failure, concurrency,
  idempotence, retry/replay, compatibility, and rollback semantics; explicit documentation impact;
  and named behavior-executing verification with one gate species per invariant, nearest test seam,
  and expected net test delta. `bd lint` and non-empty fields are necessary structural checks, not
  proof of this state.
- **Runnable now** means `bd ready` currently admits the move and no foreign ownership/artifact
  conflict remains. Current answer for every non-closed move is **no**.
- A heartbeat is called fresh only inside the coordinator's 20-minute TTL. More than 30 minutes
  without progress is stale for recovery inspection, but an existing assignee still wins until an
  authorized recovery pass proves the actor idle and deliberately reclaims it.
- **Release candidate** is a recommendation for a future, repaired graph. It is not a lifecycle
  mutation or authority grant.

## Semantic dispatch-packet audit

The per-row audit below reads the live structured `description`, `design`, and
`acceptance_criteria` fields. “Partial” names the exact missing semantic element rather than
crediting a non-empty field as a packet.

| Move | Outcome + non-goals | Intent + baseline | Surface + boundaries | Behavior/failure matrix | Documentation impact | Verification + delta | Semantic packet |
|---|---|---|---|---|---|---|---|
| `.1` | Present | Intent/specs present; baseline missing | Schema, spawner, routing, API, UI present | Present | Present | Named migration/core/API/UI seams; net delta missing | **No** |
| `.2` | Present | Intent/specs present; baseline missing | Wire, connectors, routing, history, migration present | Present | Present | Named contract, migration, routing, real-Postgres seams; net delta missing | **No** |
| `.3` | Present | Intent/doctrine present; baseline missing | Spend UI; no new trust boundary | Present | Explicit no-delta rationale | Named Spend/UI seams; net delta missing | **No** (historical) |
| `.4` | Present | Intent/spec present; baseline missing | Permissions client and irreversible UI actions; existing auth boundary | Present | Explicit none | Named permissions/UI seams; net delta missing | **No** |
| `.5` | Present | Intent/spec present; baseline missing | Connector status derivation and UI; no new trust boundary | Present | Explicit conformance-only impact | Named ingestion/UI seams; net delta missing | **No** |
| `.6` | Present | Intent/specs/manifesto present; baseline missing | Notification gate, ledger, broker, API/UI, producer migration present | Present | Present | Named notification/broker/API/UI seams; net delta missing | **No** |
| `.7` | Present | Intent/specs present; baseline missing | Public schema, merge authority, event/rebind consumers and dashboard present | Present | Present | Named real-Postgres, contract, consumer, UI seams; net delta missing | **No** |
| `.8` | Present | Intent/conditional spec target present; baseline missing | Shell, RootLayout, router/history state, e2e present | Present | Present | Named shell/e2e/lint seams; net delta missing | **No** |
| `.9` | Present | Intent present; exact active receipt authority omitted and baseline missing | Routing, spawner, dispatch attempts, session/spend UI present | Present | Present | Named routing/spawner/API/UI seams; net delta missing | **No** |
| `.10` | Present | Doctrine/ambiguous spec target present; baseline missing | First-frame HTML, theme hook, CSS, local font assets present | Present | Present | Named DOM, asset, visual, build seams; net delta missing | **No** |
| `.11` | Present | Exact design-language intent present; baseline missing | Tokens, CSS specificity, UI primitives, lint present | Present | Present | Named contrast/focus/lint/component seams; net delta missing | **No** |
| `.12` | Present | Shell intent present; baseline missing | Sidebar, finder, registry, fuzzy matching, tests present | Present | Present | Named registry/sidebar/finder seams; net delta missing | **No** |
| `.13` | Present | Accepted policy/doctrine present; exact child spec and baseline missing | Messenger/Home/provider/presence/runtime trust boundaries present | Parent state machine plus child failure matrices present | Spec/manifesto/RFC/topology child present | Named security/spec/provider/control-plane/e2e reviews; net delta missing | **No** |
| `.14` | Present | Intent/specs present; baseline missing | Closure/continuity storage, routing, Messenger, APIs and chat UI present | Present | Present | Named migration/routing/delivery/spawner/UI seams; net delta missing | **No** |
| `.15` | Present | Intent/spec present; baseline missing | Feedback/engagement schema, broker, ledger, API/MCP/UI present | Present | Present | Named migration/broker/expiry/API/UI seams; net delta missing | **No** |

The shared baseline/test-delta omissions are sufficient to hold every non-closed move. Rows `.9`,
`.10`, `.12`, `.13`, and `.14` have additional exact-authority or active-artifact gaps documented
below. This audit does not mutate their Beads packets.

## Move inventory

All rows share the evidence timestamp at the top of this report.

| # / Bead | Live state and claim | Blockers and dependencies | Active artifact evidence | Packet complete | Runnable now | Recommendation and required owner act |
|---|---|---|---|---|---|---|
| 1 / `bu-7exe4.1` | Open; unassigned; no heartbeat | `bu-xf54r` | No matching PR, branch, or worktree | No | No | **HOLD.** Complete the semantic packet, then serialize before `.9`, PR #3952, and other core migration work. A later explicit close of `bu-xf54r` removes only that gate. |
| 2 / `bu-7exe4.2` | Blocked; foreign assignee `coordinator:321c...`; heartbeat `2026-09-02T19:13:24Z`, stale | `bu-xf54r`; `bu-psarp` | Draft/conflicting [PR #3960](https://github.com/tzeusy-org/butlers/pull/3960), head `cf0ac58`; clean exact remote worktree `bu-lp4bq` | No | No | **HOLD and preserve.** Gate closure is insufficient. Complete the tracked packet; separately authorized production rollout/restart must satisfy `bu-psarp`; the foreign lane must then rebase/renumber and regain exact-head review/CI. Do not reclaim or edit it here. |
| 3 / `bu-7exe4.3` | Closed; historical assignee only; last heartbeat `2026-09-02T17:56:33Z`, not a live claim | Closed review bead `bu-69ufc`; gate remains a historical dependency | [PR #3959](https://github.com/tzeusy-org/butlers/pull/3959) merged at head `11658a85`, merge commit `b5e08ad3` | No (historical) | N/A | **COMPLETE.** No owner release act remains and the gate must not reopen this work. |
| 4 / `bu-7exe4.4` | Open; unassigned; no heartbeat | `bu-xf54r` | No matching PR, branch, or worktree | No | No | **HOLD.** Complete the semantic packet and repair the release control plane before a future explicit close of `bu-xf54r`; that act still does not authorize destructive webhook operations outside the implemented confirmation contract. |
| 5 / `bu-7exe4.5` | Open; unassigned; no heartbeat | `bu-xf54r` | No matching PR, branch, or worktree | No | No | **HOLD.** Complete the semantic packet and repair the release control plane before a future explicit close of `bu-xf54r`. |
| 6 / `bu-7exe4.6` | Open; unassigned; no heartbeat | `bu-xf54r`; `.15` | No matching PR, branch, or worktree | No | No | **HOLD.** Complete the semantic packet; `.15` must land first; and the `core-notify` / notification-gate surface must serialize with `.13` specification work. Gate closure alone will not make this runnable. |
| 7 / `bu-7exe4.7` | Open; unassigned; no heartbeat | `bu-xf54r` | No matching move PR. Foreign `bu-8cdl1.8` is assigned to another coordinator and its worktree has 15 uncommitted paths after merged PR #4005 | No | No | **HOLD and preserve unpublished work.** Complete the packet; resolve/land or explicitly park the foreign entity-graph slice; then encode serialization and refresh before any owner closes `bu-xf54r`. |
| 8 / `bu-7exe4.8` | Open; unassigned; no heartbeat | `bu-xf54r` | Foreign [PR #4015](https://github.com/tzeusy-org/butlers/pull/4015) is open but `CONFLICTING`/`DIRTY`; its successful rollup is stale-base evidence. Clean worktree/remote head `e90f6080` edits `RootLayout.tsx` | No | No | **HOLD.** Complete the packet, let the foreign RootLayout lane settle, refresh `main`, then release only after the overlap is gone or serialized. |
| 9 / `bu-7exe4.9` | Open; unassigned; no heartbeat | `bu-xf54r` | No matching move artifact. Draft [PR #3952](https://github.com/tzeusy-org/butlers/pull/3952), head `6c6591b7`, modifies the same core-spawner attempt-orchestration contract | No | No | **HOLD.** Complete the packet against the active Resolution Receipt contract; serialize after higher-priority `.1` and with PR #3952; coordinate core migration numbering with every live migration PR. |
| 10 / `bu-7exe4.10` | Open; unassigned; no heartbeat | `bu-xf54r` | No matching move artifact. Foreign PRs #4012 and #4015 are both open but `CONFLICTING`/`DIRTY`; they edit `index.css` and `RootLayout.tsx` respectively. PR #4012's `check-integration-5` and fan-in `check` failed; PR #4015's rollup passed on its stale base | No | No | **HOLD, spec first.** Complete the packet; produce and separately approve one exact normative delta resolving the system-font conflict; settle foreign layout/CSS work first. |
| 11 / `bu-7exe4.11` | Open; unassigned; no heartbeat | `bu-xf54r`; `.10` | No matching PR, branch, or worktree | No | No | **HOLD.** Complete the packet; `.10` remains an encoded prerequisite and must land its exact spec/first-frame contract before focus-token implementation. |
| 12 / `bu-7exe4.12` | Open; unassigned; no heartbeat | `bu-xf54r` | No matching move artifact. `bu-ip5c5` is a stale foreign claim on `EntityFinder`, with no PR/worktree found | No | No | **HOLD.** Complete the packet; an authorized recovery/ownership pass must resolve `bu-ip5c5`; and the shortcut/palette requirements need an exact `dashboard-shell` delta before implementation. |
| 13 / `bu-7exe4.13` | Open epic; unassigned; no heartbeat | `bu-xf54r`; nested `.13.1` through `.13.5` sequencing | No PR, branch, worktree, or live voice-egress OpenSpec change found | No | No | **HOLD; spec work first.** Complete the parent packet. A later gate release may authorize only `.13.1` to draft the contract, followed by separate exact-artifact approval. Provider/device/presence/audio/credential/runtime work requires another explicit act and remains forbidden now. |
| 14 / `bu-7exe4.14` | Open; unassigned; no heartbeat | `bu-xf54r`; `.2` | No matching move artifact. Foreign [PR #4012](https://github.com/tzeusy-org/butlers/pull/4012) is `CONFLICTING`/`DIRTY` with failed fan-in; its clean head `5c8531ee` edits conversation API/UI/spec. Two unarchived same-requirement deltas also own reply/outcome semantics | No | No | **HOLD.** Complete the packet by rebuilding against the two active deltas; `.2` must complete and PR #4012 must settle before this lane is shaped against refreshed contracts. |
| 15 / `bu-7exe4.15` | Open; unassigned; no heartbeat | `bu-xf54r` | No matching PR, branch, or worktree | No | No | **HOLD.** Complete the semantic packet and repair the control plane; then run before `.6`, as already encoded. A later explicit gate close authorizes no live messaging or runtime action. |

## Spec authority and overlap

| # / Bead | Current governing authority | Overlap verdict |
|---|---|---|
| 1 / `.1` | [model-failover](../../openspec/specs/model-failover/spec.md), [catalog-token-limits](../../openspec/specs/catalog-token-limits/spec.md), [core-spawner](../../openspec/specs/core-spawner/spec.md); planned delta required | Exact code/schema overlap with `.9`: spawner, model routing, dispatch-attempt storage, Spend/Models. Serialize `.1` first. |
| 2 / `.2` | [connector-telegram-bot](../../openspec/specs/connector-telegram-bot/spec.md), [module-pipeline](../../openspec/specs/module-pipeline/spec.md), [dashboard-conversations](../../openspec/specs/dashboard-conversations/spec.md), plus RFC 0003; PR #3960 carries the delta | Exact overlap with `.14`; current PR also intersects the active `conversation-anchor-provider-resume-ledger` delta. Existing blockers must remain authoritative. |
| 3 / `.3` | [dashboard-spend-dashboard](../../openspec/specs/dashboard-spend-dashboard/spec.md) and the recovery design bar; no new spec delta was required | Historical Spend overlap with `.1`, but the move is merged and closed. Refresh `.1` from current `main`. |
| 4 / `.4` | [dashboard-permissions](../../openspec/specs/dashboard-permissions/spec.md), path-mounted client contract, and recovery design bar; bug-fix packet says no delta | No live exact-file overlap found. |
| 5 / `.5` | [dashboard-ingestion-dispatch-console](../../openspec/specs/dashboard-ingestion-dispatch-console/spec.md) liveness/auth honesty plus design-language state semantics; bug-fix packet says no delta | No live exact-file overlap found. |
| 6 / `.6` | [proactive-insight-engine](../../openspec/specs/proactive-insight-engine/spec.md), [insight-delivery](../../openspec/specs/insight-delivery/spec.md), [core-notify](../../openspec/specs/core-notify/spec.md), plus a Switchboard manifesto amendment | Exact notification/core-notify overlap with `.13`; functional dependency on `.15` is already encoded. |
| 7 / `.7` | [entity-identity](../../openspec/specs/entity-identity/spec.md) and [relationship-facts](../../openspec/specs/relationship-facts/spec.md); planned delta required | Exact memory/relationship/entity-write overlap with foreign `bu-8cdl1.8` dirty worktree. Do not overwrite or clean that worktree. |
| 8 / `.8` | [dashboard-shell](../../openspec/specs/dashboard-shell/spec.md) and the interaction-speed doctrine; packet leaves the need for a delta conditional | Exact `RootLayout.tsx` overlap with open PR #4015. Pin the normative navigation contract before implementation. |
| 9 / `.9` | Active [add-dispatch-intent-fit Resolution Receipt](../../openspec/changes/add-dispatch-intent-fit/specs/dispatch-intent/spec.md#requirement-resolution-receipt) is the exact current contract; its proposal/design explicitly defer receipt persistence and dossier exposure, which `.9` continues. [routing-scorecard](../../openspec/specs/routing-scorecard/spec.md) remains supporting score authority | Exact implementation/storage overlap with `.1`. Draft PR #3952 modifies [core-spawner Logical Session Attempt Orchestration](../../openspec/specs/core-spawner/spec.md#requirement-logical-session-attempt-orchestration), the same attempt contract owned by `.1`/`.9`; reconcile and serialize all three. |
| 10 / `.10` | [dashboard-design-language](../../openspec/specs/dashboard-design-language/spec.md) and [dashboard-shell](../../openspec/specs/dashboard-shell/spec.md); the latter still says the root uses a system font stack | Exact `index.css` overlap with PR #4012 and adjacent RootLayout work in PR #4015. An approved spec amendment must resolve the current font contract first. |
| 11 / `.11` | [dashboard-design-language](../../openspec/specs/dashboard-design-language/spec.md), Interaction Affordances requirement, “Keyboard focus visible” scenario; planned `--focus` / 3:1 delta | Exact `index.css` and primitive overlap with `.10`; dependency already encoded. |
| 12 / `.12` | [dashboard-shell](../../openspec/specs/dashboard-shell/spec.md), Command Palette and Keyboard Shortcuts requirements; uniqueness/count/overflow delta still required | `EntityFinder` ownership overlaps stale foreign bead `bu-ip5c5`; `.8` is adjacent shell work. Resolve ownership and serialize if surfaces remain shared. |
| 13 / `.13` | Messenger doctrine and existing [butler-messenger](../../openspec/specs/butler-messenger/spec.md) / [core-notify](../../openspec/specs/core-notify/spec.md), but no voice-egress contract exists yet; `.13.1` is the spec gate | `core-notify` Channel Validation is also modified by active change `make-routed-approvals-replayable`; `.13.1` already requires overwrite-safe reconciliation. `.6` shares the notification gate. |
| 14 / `.14` | [butler-switchboard](../../openspec/specs/butler-switchboard/spec.md), [butler-messenger](../../openspec/specs/butler-messenger/spec.md), [core-notify](../../openspec/specs/core-notify/spec.md), [core-spawner](../../openspec/specs/core-spawner/spec.md), and [dashboard-conversations](../../openspec/specs/dashboard-conversations/spec.md). Active [durable terminal-action recovery](../../openspec/changes/durable-dashboard-terminal-action-recovery/specs/dashboard-conversations/spec.md#requirement-conversation-reply-channel) and completed-but-unarchived [dashboard question lane](../../openspec/changes/add-dashboard-question-lane/specs/dashboard-conversations/spec.md#requirement-conversation-reply-channel) both modify Conversation Reply Channel/current-turn or message-model semantics | Exact overlap with `.2` and foreign PR #4012. Rebuild `.14` against both same-requirement deltas and serialize it with the still-in-progress durable-recovery implementation before authoring or archiving its closure/continuity delta. |
| 15 / `.15` | [proactive-insight-engine](../../openspec/specs/proactive-insight-engine/spec.md): Verbosity Presets; Adaptive Delivery with Graceful Degradation; Attention Ledger Recording of Delivered/Coalesced/Failed Candidates; Candidate Cleanup. An explicit feedback and expired-outcome contract is planned, not present authority | `.6` consumes the shaped budget and is already blocked by `.15`. No live exact-file foreign artifact was found. |

## Missing serialization before release

The following are observed or conservatively inferred from the live surface maps. They are not
Beads mutations performed by this report.

1. [Observed] `.15` blocks `.6`, `.10` blocks `.11`, `.2` blocks `.14`, and `bu-psarp` blocks `.2`.
   Preserve these edges.
2. [Inferred] Serialize `.1` before `.9`, and serialize both with draft PR #3952. `.1`/`.9` own
   attempt/resolution persistence, spawner/model routing, and Spend/Models surfaces; #3952 modifies
   the same core-spawner Logical Session Attempt Orchestration requirement. `.9` must build on the
   active `add-dispatch-intent-fit` Resolution Receipt contract rather than invent another receipt.
3. [Observed] Hold `.7` until foreign `bu-8cdl1.8`'s 15-path dirty worktree is published, landed, or
   explicitly parked by its owner. No cleanup, reset, or takeover is authorized.
4. [Observed] Hold `.8` until PR #4015 settles; hold `.10` until PRs #4012/#4015 settle and its font
   contract is approved; hold `.14` until `.2`, PR #4012, and the active same-requirement
   `dashboard-conversations` deltas settle or are rebuilt into one current contract.
5. [Inferred] Serialize `.6`'s `core-notify` work with `.13.1`'s contract amendment. The active
   `make-routed-approvals-replayable` change already modifies `core-notify` Channel Validation.
6. [Observed] Resolve the stale foreign `bu-ip5c5` claim before assigning `.12`; absence of a PR or
   worktree is not authority to take it over.
7. [Observed] Any move adding a core migration must refresh the global revision chain immediately
   before publishing. The full open-PR revision set is listed below; omission of a lower-numbered
   stale branch still creates a rebase/renumber collision risk.

### Open core-migration PR set

| PR | Live state | Head | Core revision |
|---|---|---|---|
| [#3960](https://github.com/tzeusy-org/butlers/pull/3960) | Open draft; `CONFLICTING`/`DIRTY` | `cf0ac58` | `core_209_conversation_identity_split.py` |
| [#4006](https://github.com/tzeusy-org/butlers/pull/4006) | Open non-draft; mergeable but blocked with failed `guards`/`check` | `de7ff47` | `core_215_qa_repo_org_rename.py` |
| [#4012](https://github.com/tzeusy-org/butlers/pull/4012) | Open non-draft; `CONFLICTING`/`DIRTY`, failed fan-in `check` | `5c8531e` | `core_218_dashboard_messages_search_index.py` |
| [#4016](https://github.com/tzeusy-org/butlers/pull/4016) | Open non-draft; `MERGEABLE`/`CLEAN`, green `guards`/`check` | `b520011` | `core_220_sessions_friction.py` |

## Owner-questionnaire handoff

Do not present “close `bu-xf54r` now” as the recommendation from this snapshot. Present one bounded
decision only after every non-closed canonical move has a semantically complete packet and the
graph/control-plane repairs above are independently verified:

> **Release run 09 against the refreshed all-15 graph?**
>
> Recommended option: close `bu-xf54r` only after all 14 non-closed canonical move packets pass the
> full semantic Dispatch Readiness Packet audit, the release gate and parent epic pass `bd lint`, the
> gate text truthfully says closure removes only this gate while all other dependencies still govern
> readiness, all missing serialization is encoded or otherwise enforceable, and every foreign
> artifact named in this report is settled or explicitly preserved behind a blocker. Until every
> condition holds, neither present nor execute the close. A later close releases only the current
> planning/execution packets. It does not authorize runtime access, credentials, provider or device
> calls, deployment/restart, destructive action, PR merge, or production changes.

For `.13`, the release can authorize only `.13.1` specification work. Exact-artifact owner approval
is a later act. Runtime/provider evidence is later still. For `.2`, the production rollout/restart
authorization required by `bu-psarp` is independent of the run-09 release decision.

## Verification receipts

Final verification ran after rebasing onto clean base
`b087a7c1e61ff80aba07c396d92cd204128c40f6`, which matched `origin/main`.

| Check | Result |
|---|---|
| Canonical count | 15 ranked dossier entries map bijectively to contiguous IDs `.1` through `.15`; 17 direct epic children minus the two named prep tasks equals 15 moves |
| Current readiness | `bd list --parent bu-7exe4 --ready ...` returned `[]` |
| Move packet lint | `bd lint bu-7exe4.1 ... bu-7exe4.15 --status all --json --readonly`: 0 findings |
| Global lint context | `bd lint --json --readonly`: 55 warnings / 53 records; includes parent epic and release gate as described above |
| Dependency cycles | `bd dep cycles --json`: `[]` |
| Strict OpenSpec | `openspec validate --all --strict`: 292 passed, 0 failed |
| Spec overwrite guard | `make check-spec-overwrites`: pass; no unfrozen baseline losses across 67 modified requirements with debt; tightening opportunities only |
| PR/worktree identity | PR #3960 head equals clean worktree/remote `cf0ac58`; PR #4012 equals clean worktree/remote `5c8531ee` but is conflicting with failed fan-in; PR #4015 equals clean worktree/remote `e90f6080` but is conflicting; foreign `bu-8cdl1.8` worktree has 15 uncommitted paths |
| Link and row hygiene | Every local Markdown link resolves; mechanical assertions found exactly 15 rows in each inventory table |
| Diff/privacy hygiene | `git diff --check`, em-dash guard, and session-link guard pass; no generated frontend-copy drift |
| Independent report review | The prior author-local PASS assertion had no durable reviewer receipt and is withdrawn. PR #4019 review on head `41d1f346` recorded `corrections-required` in five GitHub threads and review bead `bu-p2gw3`; exact-head re-review is pending after this correction |

## Falsification notes

- The count would be false if every direct child of the epic were called a move. Two direct children
  are preparation tasks, so the dossier ranking plus the contiguous `.1` through `.15` identity is
  the canonical-set proof.
- The gate description's claim that closure puts all children in `bd ready` is false. `bu-xf54r`
  has 20 dependents, several retain independent blockers, nested voice tasks retain sequencing, and
  `.3` is closed. Gate closure removes one edge; only the dependency resolver determines readiness.
- “Packet complete” would be false if inferred from `bd lint` alone. This report also checked each
  move against the full semantic readiness contract. Structural lint passes 15/15, but missing
  baseline commits and expected test deltas make semantic completeness 0/15.
- PR #3960's green historical check rollup is not current merge evidence. GitHub reports the PR
  `CONFLICTING`/`DIRTY`, so its earlier checks are frozen evidence for an old base relationship.
- A stale heartbeat is not proof that work is disposable. `.2` has a clean published artifact and
  `bu-8cdl1.8` has dirty unpublished work; both are held and preserved.
- Strict OpenSpec success does not create missing future contracts. In particular, there is no live
  voice-egress changeset, and several moves explicitly require future deltas.
- The overwrite guard is textual deletion protection, not contradiction detection. Each future
  delta must still be rebuilt against the then-current baseline and active same-requirement changes.
- An author handoff claiming an independent PASS is not an auditable review receipt. This report
  records only the durable `corrections-required` review on head `41d1f346`; a later PASS belongs to
  the independent reviewer of the corrected exact head, not to this author correction.

---

## Conclusion

**Real direction**: Preserve earned operational truth while turning the run-09 findings into
spec-bound, non-overlapping execution lanes.

**Work on next**: Complete all 14 non-closed move packets semantically; repair the release-control
packets; encode the missing serialization; settle or protect foreign artifacts; then refresh and
present the single owner release decision.

**Stop pretending**: A structurally complete 15-row inventory is not an all-at-once runnable graph,
and a green historical PR rollup is not current release evidence for a conflicting branch.
