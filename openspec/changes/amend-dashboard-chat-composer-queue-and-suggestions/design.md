## Context

Baseline: `main` at `795d5d158` (2026-10-08). Governing bead: `bu-0ynlk.14`, narrowed by its 2026-09 scope correction. Dossier: `docs/redesigns/2026-09-02-dashboard-chat-pursuit.md` § 14. The companion `-data.json` was pruned from the tree by #4245; the dossier text and the bead's scope correction are the surviving sources.

Size: **Medium**. The change spans several frontend components and alters observable behavior. It crosses no backend, trust, or persistence boundary of its own.

### Scope verification against current main

The scope correction was written on 2026-09-21. Re-checked on 2026-10-08:

| Claim in the scope correction | Current main | Effect on this change |
| --- | --- | --- |
| `bu-0ynlk.12` must land before `MessageThread` is touched | Closed; PR #4205 merged the answer renderer, citations, and attribution | Serialization satisfied. Build on the landed `MessageThread`. |
| `bu-0ynlk.13` supplies a truthful pending label | Shipped: `pendingActivityStatus()` and `chat-activity-status` live region in `MessageThread.tsx` | Only the decorative dots remain. The pill reuses the existing label text. |
| This bead owns per-`{butler, conversation}` local draft persistence with timestamp last-write-wins | **Stale.** The owner adopted `persist-safe-dashboard-unsent-drafts` on 2026-09-12 (PR #4056 at `d2dc32ff`). It forbids timestamp last-write-wins and is owned by `bu-2jtfw.15` (unimplemented) | Drafts removed from this change. The narrower queued-text retention question was answered by the owner on 2026-10-09 (Owner decision below). |
| Composer is disabled only during streaming | **Incomplete.** All four postures also pass `disabled={isLoadingConversations}` or `isLoadingDetail` | The amended Message Input Area forbids any state from disabling typing. |
| Composer text is per conversation | **Incomplete.** `ChatDock`, `ChatPanel`, `FloatingChatWidget`, and `ChatPage` each hold one `inputValue` per posture. Text typed for conversation A stays visible after switching to B | The queue needs per-identity composer text (Decision 2). |
| Suggestion projection carries a registered resource kind and coarse window | `/qa` publishes kind `qa_overview`. `/spend` publishes kind `spend_window` with an exact `YYYY-MM-DD..YYYY-MM-DD` label. `/entities/:entityId` publishes no resource kind, only `entity_ref` (a canonical name, which is forbidden input) | Window class is derived from the page's preset, never the exact dates. Entity suggestions use the route pattern alone. |
| Design language bans bouncing dots | `animate-bounce` is banned by the Motion Vocabulary spec, but `frontend/eslint.config.js` only bans `animate-pulse` | Removing the dots leaves no offender. The implementation adds the specified `animate-bounce` selector. |
| `.15` depends on `.14` for summary and read-state columns | Still true in `bu-0ynlk.15` | Corrected in beads: dependency removed, cut items re-homed (`proposal.md` § Out of Scope). |

Two open governance items touch this area without blocking it:

- `bu-lsxqb0.1` (owner decision on dashboard identity: read-mostly surface versus control plane with global chat). This change neither enlarges chat's role nor adds a surface. It reduces friction in an existing secondary input channel. Either answer leaves it valid.
- `bu-p5umi2.1` (block sends until conversation ownership resolves). It gates **Send**, not typing, so it fits the amended Message Input Area unchanged.

Pre-existing spec-integrity defect found while allocating IDs: `REQ-dashboard-chat-ui-004` is used both by baseline `Message Thread Display` and by `persist-safe-dashboard-unsent-drafts`' first ADDED requirement. This change takes the next free numbers (`-006` to `-013`) and does not touch either block. The collision is filed separately as `bu-bjaxxd`.

## Funnel summary

- **Motif.** The interface must keep up with an owner who already knows what they want to say next, and must say plainly what it is doing and what each input lane does.
- **Doctrine gate. Aligned.**
  - `vision.md` § What Success Looks Like: the system absorbs mental labor, so holding a follow-up in one's head is a defect.
  - `design-language.md` § What the Dashboard Is Not ("Not a chat app"): no new surface; chat stays a secondary operator lane.
  - Design-language spec, Motion Vocabulary (`animate-bounce` forbidden).
  - Process Status Pill (the system reporting on its own process).
  - Status Indicators (`StateDot`).
  - Anti-Pattern Prohibitions (no shadows on cards).
  - Interface Copy (page-level empty-state form, no future tense, no first person).
  - Button Forms (pill and commit forms; one commit button per surface).
  - Viewport and Modality Contract (44×44px coarse-pointer floor).
- **Topology.** `frontend/` only:
  - `components/chat/` (composer, thread slots, trigger, popover);
  - `lib/shell-capability.ts` (optional suggestion projection);
  - `hooks/use-conversation-turn.ts` (queue integration point).
  - No API, schema, core tool, or runtime surface.
- **Engineering bar.** `about/craft-and-care/engineering-bar.md`: the spec is updated in the same change as behavior; repository conventions are preferred. Verification ladder in `tasks.md`.

## Goals / Non-Goals

**Goals:**

- typing is never blocked;
- one queued follow-up with exactly-once, outcome-gated auto-send;
- truthful pending state in the Dispatch vocabulary;
- bounded, reviewed starting prompts on three route families;
- an empty thread that teaches the four lanes;
- Dispatch-conformant trigger and popover.

**Non-Goals:** everything in `proposal.md` § Out of Scope. In particular this change persists nothing to browser storage itself; persistence is the adopted draft contract's job.

## Decisions

### 1. The queued turn is a per-instance, per-identity, single-slot state machine

The queue lives beside each mounted `useConversationTurn` instance, behind a shared composer helper. `Global Chat Postures` already makes turn state per instance, so cross-posture mirroring stays out of scope. Identity is `{butler_name, conversation_id | "new"}`. On `conversation_created`, a `new` identity holding a queued item migrates to the created id.

```text
           Enter during active turn (slot empty)
  EMPTY ──────────────────────────────────────────▶ QUEUED{text, message_id, context}
    ▲                                                   │
    │ committed message_complete of prior turn          │ Stop activated, error, done without
    │   → dispatch once with the same message_id        │ message_complete, INGEST_IN_PROGRESS,
    │   (becomes an ordinary submission)                │ TURN_OUTCOME_UNKNOWN, interrupted watch,
    │                                                   │ conversation switch, unmount, Edit
    └──────────── DISPATCHED ◀──────────────────────────┤
                                                        ▼
                                          DEMOTED → text returned to that identity's composer,
                                                    message_id discarded (never used)
```

- Activating Stop demotes the queued item immediately, whatever the cancel outcome. The owner has said "stop", and an auto-send after that would surprise them. This also covers the cancel-failure and still-settling cases with one rule.
- A demoted `message_id` is discarded. It was never sent, so no server attempt exists under it. A later send allocates a fresh id. A dispatched queued item that then fails keeps its id for the existing retry path. There is no automatic replay.
- Rejected alternative: a multi-item queue. It needs reordering, per-item cancel, and partial-failure semantics, and the dossier's motif is "don't make me wait", not "batch my thoughts".

### 2. Composer text becomes per identity, in memory, within each posture instance

Today each posture holds one `inputValue`, so text follows the owner across conversation switches. The queue cannot demote correctly without knowing whose text it is. This change moves composer text behind the same `{butler, conversation | new}` identity, in memory only, for the life of the posture instance.

- Ownership boundary: the adopted draft contract (`bu-2jtfw.15`) later backs that identity seam with IndexedDB. Whichever lands second adapts to the other's seam; neither duplicates storage logic. A note on `bu-2jtfw.15` records this.
- This is the in-memory degraded mode that `dashboard-shell` REQ-dashboard-shell-006 already requires, so it is not a competing contract.

### 3. Page context is captured when the owner queues, not when the queue dispatches

The owner commits intent at Enter. If they then navigate, sending the new page's context would ground the question on a page they did not ask about.

- The queued item snapshots the context-chip inclusion choice and the context value at queue time, in memory only.
- The queued row names the attached context label so nothing is hidden.
- A demoted item drops its snapshot. The text then follows the adopted draft rule: current context governs the eventual send.

### 4. Editing a queued item is one explicit action that never discards text

The queued row offers one pill action, `Edit`. It demotes the item into the composer:

- **Composer empty:** the composer receives the queued text.
- **Composer holds text:** the queued text is placed first, then a blank line, then the existing text, with the caret at the end of the queued text.

Both parts are owner-authored and visible, so nothing is lost or silently merged out of view. The same placement rule applies to every other demotion path.

### 5. Suggestions come from a closed, safe projection on the shell capability

`ShellCapability` gains an optional `chatSuggestions(projection) => readonly string[]`. Only `/spend`, `/qa`, and `/entities/:entityId` define it in this change.

- **Projection.** Exactly `{ routePattern, resourceKind | null, windowClass: "today" | "week" | "month" | "custom" | null }`, built in one place from the capability path, the registered `visible_resource.kind`, and the page's window preset.
- **Excluded inputs.** Raw ids, labels, `entity_ref`, query parameters, `visible_summary`, query-cache rows, and third-party content are not reachable from the factory's input type. A test asserts this.
- **Bounds.** At most three strings per route, sentence case, ending in a question mark. Question-oriented until `bu-0ynlk.8` lands action proposals.

Proposed initial copy (reviewed static strings; owner may edit at adoption):

| Route | Suggestions |
| --- | --- |
| `/spend` | "What drove spend in this window?" · "Which butler cost the most in this window?" · "How does this window compare with the previous one?" |
| `/qa` | "What is QA investigating now?" · "Which escalations need the owner?" · "What did the last patrol find?" |
| `/entities/:entityId` | "What changed on this record recently?" · "Which open commitments involve this entity?" · "When was the last interaction?" |

Activation fills the composer and moves focus to its end. The visible context chip, which the owner can remove, is the only grounding; "this window" and "this record" resolve through it. If the owner removed the chip, the question is ambiguous in the open, not silently grounded.

### 6. The empty thread uses the page-level empty-state form

- **Title:** "No messages yet."
- **One sentence of context:** "Ask a question, correct a record, request a change for approval, or report a bug."
- **No action button.** The composer is the action.
- **Suggestions,** where defined, render beneath it as pill buttons in a list named "Suggested questions".

Each clause maps to a classifier lane on main (`pipeline.py` lanes D, A, C, B). "For approval" is truthful because lane C carries the propose-don't-apply contract (`bu-0ynlk.1`). The copy claims nothing about proposal cards (`bu-0ynlk.8`). When history is unavailable (`suppressEmptyState`), neither copy nor suggestions render.

### 7. Chrome uses existing primitives only

- **Trigger (below `xl`).** A pill-form button: hairline 1px `--border`, 3px radius, mono 11px, no fill, no shadow. Label "Chat". It keeps the 44×44px coarse-pointer hit area through padding or hit slop rather than a larger visual.
  - **Unread state.** The label becomes "Chat · new reply" with one `StateDot` in the `ok` state. The text carries the meaning; the dot is a redundant marker.
  - **Why not red.** The red badge is retired because `error` is the wrong semantic: an unread reply is not a fault.
  - **Kept as-is.** The accessible name and the `.13` shell announcement are unchanged.
- **Popover.** Page background and a 1px hairline border; no `shadow-*`, no `bg-card` fill.
- **Pending activity.** The Process Status Pill shape: one `StateDot` in `degraded` (amber, the `composing…` colour) plus the existing truthful mono label. The briefing pill is clickable to refresh; this pill is deliberately **non-interactive**, because Stop is the control for an active turn. No element animates.

### 8. "Re-enable the input" elsewhere in the capability means Send availability

`SSE Client Integration` (baseline, and the active `durable-dashboard-terminal-action-recovery` MODIFIED block) says `done` and confirmed cancellation "re-enable the input". This change does not modify that requirement, to avoid a second active restatement. The amended Message Input Area states that such clauses restore Send availability, because the textarea is never disabled. Archive-time reconciliation of the wording is a task.

## Owner decision: retention of queued, not-yet-dispatched text

**Decided 2026-10-09: Option A.** Queued, not-yet-dispatched text is kept in the conversation's adopted browser-local draft record, restored after reload marked "Not sent", and never sent automatically. REQ-dashboard-chat-ui-010 encodes this choice. The question, options, and reasoning below are kept as the decision record.

**Question.** When the owner queues a follow-up and the page then reloads, closes, or crashes before that queued message is dispatched, should the queued text survive in this browser?

**Background.** Ordinary unsent composer text is already retained under the adopted browser-local draft contract (`persist-safe-dashboard-unsent-drafts`, adopted 2026-09-12). Retention is per butler and conversation, in this browser profile's IndexedDB, for 24 hours after the last edit, capped at 64 KiB, and cleared on proven send acceptance or Discard. That contract does not cover queued text. Queueing clears the editor, and an empty editor tombstones the draft.

| | Option A: retain as draft (chosen) | Option B: memory only (not chosen) |
| --- | --- | --- |
| Behavior | While queued, the text is kept in the same conversation's draft record. After reload it reappears as ordinary editable text marked "Not sent", never auto-sent. The auto-send intent and its `message_id` are never persisted. | The queued text lives only in the open tab. Reload, tab close, or crash before dispatch loses it with no trace. |
| Retention | Same as the adopted draft contract: same browser profile, 24-hour sliding expiry, 64 KiB, cleared on dispatch-then-acceptance or Discard. | None beyond the tab's lifetime. |
| Privacy consequence | No new data class or storage location: the queued text is owner-typed text of the kind the owner already agreed to keep. Anyone or any script with access to this browser profile and the dashboard origin can read it until it is sent, discarded, or expires. That is the same residual already accepted for drafts. | Strictly less retained text. Nothing extra is readable from the browser profile. |
| Failure the owner experiences | None new. The worst case is a stale "Not sent" draft that expires after 24 hours. | The owner pressed Enter and saw "1 message queued", so they believe the words are on their way. After a reload they are gone, and nothing tells the owner to retype them. |
| Spec effect | Keep REQ-dashboard-chat-ui-010 as written. | Replace REQ-dashboard-chat-ui-010's scenarios with one: "Queued text is not retained across reload". Leave the rest of the change unchanged. |

**Recommendation (accepted): Option A.** With a single owner who owns the data, the meaningful threat is losing the owner's words, not exposing them to themselves. Option A adds no exposure beyond what the owner already accepted on 2026-09-12. Option B creates the most damaging kind of loss: words the owner believes were sent. Option A's effect depends on `bu-2jtfw.15` implementing the draft store. Until then, queued text is held in memory only.

## UX walkthrough (design bar)

- **Entry.** Unchanged: shortcut and focus from `.13`. Suggestions save typing on the three highest-intent pages.
- **First glance.** An empty thread now states what the lanes are, in one line.
- **Pace.** No dead wait: typing continues during a turn, and the queued row acknowledges Enter immediately. Pending state is a truthful label, not motion.
- **Repetition.** A second Enter cannot replace or double-send. The helper line explains the one-item limit, and auto-send is exactly-once.
- **Defaults.** Context is captured at the moment of intent. Suggestions never overwrite typed text.
- **Recovery.** `Edit` and every demotion path return text visibly and never discard it. Stop never leaves a surprise send behind. Dispatch failure reuses the existing retry and identity.
- **Habit.** Keyboard-only flow: Enter queues, Tab reaches `Edit`, suggestions are Tab-reachable pills. No new shortcut is needed, because Enter is the shortcut.

## Risks / Trade-offs

- **The owner forgets a queued item exists.** Mitigation: the persistent queued row above the composer, plus the announcement on enqueue and on demotion.
- **Context captured at queue time may surprise an owner who expected current-page grounding.** Mitigation: the queued row names the attached context label, and `Edit` demotes it to current-context semantics.
- **Per-identity in-memory composer text could overlap `bu-2jtfw.15`'s chat-draft slice.** Mitigation: Decision 2 assigns the seam here and storage there, with a note on `bu-2jtfw.15`.
- **Static suggestions can go stale as pages change.** Mitigation: they are bounded to three routes, kept in the capability manifest beside the route, and covered by a manifest test.

## Migration Plan

Frontend-only. Rollback means reverting the components. No server state, migration, or browser storage key is introduced by this change.
