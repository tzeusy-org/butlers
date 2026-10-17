## Why

The dashboard chat composer stops the owner while the system works. During an active turn the textarea and Send are disabled, so a follow-up thought has to be held in the owner's head until the reply finishes. That is the mental labor the system exists to absorb (`about/heart-and-soul/vision.md` § What Success Looks Like). The surrounding chrome also contradicts the Dispatch language it sits inside. The popover trigger is a filled, shadowed circular button with a red badge. The popover is a shadowed card. Pending activity animates three bouncing dots, although the design language bans `animate-bounce` and prescribes a status pill for the system reporting on its own process. An empty thread says only "No messages yet. Start the conversation below." It does not say what the four dashboard lanes do: question, statement or correction, action request, bug report.

This change amends `dashboard-chat-ui` for the narrowed `bu-0ynlk.14` outcome. It is frontend-only. It adds:

- a composer that never blocks typing;
- one queued follow-up turn;
- bounded route-aware starting prompts on three route families;
- an empty thread that teaches the lanes;
- the remaining Dispatch chrome corrections.

It is proposed future behavior only. The owner answered the queued-text retention question on 2026-10-09 (Option A, recorded in `design.md` § Owner decision). Implementation stays blocked until the owner adopts this exact artifact.

## What Changes

- **Supersede the disabled composer.** No load, ownership, or turn state disables the textarea. Turn state gates only Send. Send and Stop render side by side while a turn is active, and Stop keeps every existing server-cancel clause. This replaces the baseline scenario "Input disabled during streaming".
- **Add a single-slot queued turn.** Pressing Enter during an active turn creates one queued item, scoped to `{butler, conversation | new}`. The item holds the trimmed text, one immutable client `message_id`, and the context-chip choice captured at that moment. A second Enter never replaces or sends the queued item.
  - **Auto-send.** The queued item auto-sends exactly once, and only after the prior turn's committed `message_complete`.
  - **Demotion.** Every other outcome returns the queued text to the composer as ordinary editable text and never sends it: Stop, error, `done` without `message_complete`, unknown outcome, conversation switch, or unmount.
- **Supersede the bouncing dots.** Pending activity renders as a Process Status Pill (one `StateDot` plus the truthful phase label). The bounce animation is gone. This replaces the baseline Typing Indicator dots scenario.
- **Add route-aware starting prompts.** These are reviewed static strings, available only on `/spend`, `/qa`, and `/entities/:entityId`.
  - **Inputs.** They are computed only from a safe projection: route pattern, registered resource kind, coarse window class.
  - **When they show.** They render only when the thread and composer are both empty.
  - **What activation does.** It fills and focuses the composer. It never sends, changes lane, or attaches hidden context.
- **Replace the empty-thread copy.** The new copy is a page-level empty state that names the four lanes and claims no capability that is not on main.
- **Correct the remaining chrome.**
  - **Trigger.** Below `xl`, the popover trigger becomes a hairline mono pill. While a reply is unread it carries one `StateDot` and the label "new reply".
  - **Popover.** The popover loses card shadow and fill.

### Relationship to the adopted draft contract

The owner adopted `persist-safe-dashboard-unsent-drafts` on 2026-09-12 (PR #4056 at `d2dc32ff`, recorded in `bu-2jtfw.15` as `[owner-adoption bu-qqb2kw]`). That contract already governs browser-local chat drafts per `{butler, conversation | new}`: transactional IndexedDB, 24-hour sliding expiry, a 64 KiB limit, compare-and-swap cross-tab ordering, and clear-on-proven-acceptance (REQ-dashboard-chat-ui-004 and -005). `bu-2jtfw.15` owns implementing it.

This change does not re-specify, fork, or weaken that contract. Earlier `bu-0ynlk.14` drafts proposed a local key with timestamp last-write-wins; that conflicts with the adopted contract and is withdrawn. The one narrower retention question, whether a queued, not-yet-dispatched message belongs to that same draft record, was answered by the owner on 2026-10-09: it does (Option A, `design.md` § Owner decision).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-chat-ui`:
  - modify `Message Input Area` and `Typing Indicator`;
  - add the queued turn, queued-text retention, route-aware starting prompts, lane-teaching empty thread, and chat trigger/popover chrome requirements.

## Impact

- **Code.** Future implementation is confined to `frontend/`:
  - the components: `MessageInput`, a focused queue helper under `components/chat/composer/`, `SuggestedPrompts`, the `MessageThread` empty and pending slots, the `TypingIndicator` replacement, and the `FloatingChatWidget` trigger and popover;
  - the integration points: an optional suggestion projection on `ShellCapability` in `frontend/src/lib/shell-capability.ts`, and the turn integration in `useConversationTurn`;
  - the consumers: `ChatDock`, `ChatPage`, `FloatingChatWidget`, and `ChatPanel`, which use the shared behavior rather than reimplementing it.
- **Out of scope.** No backend request model, endpoint, migration, core tool, conversation-summary field, `last_seen_at`, pin field, lane override, rename/archive action, scoped-resume rule, or cross-channel thread behavior.
- **Rollback.** Revert the frontend components. No server or data downgrade exists. Under the decided retention (Option A), the queued text is ordinary draft text in the adopted store, so reverting leaves it restorable as a draft.
- **Tests.** No test files change in this spec-only change (`Tests: +0 ~0 -0`). The implementation estimate is in `tasks.md`.

## Out of Scope

Each item cut from the original `bu-0ynlk.14` bundle has a named owner:

| Cut item | Owner |
| --- | --- |
| Browser-local draft persistence (store, expiry, cross-tab ordering) | `bu-2jtfw.15` under the adopted `persist-safe-dashboard-unsent-drafts` |
| Rolling thread summary replacing the 4000-character envelope truncation | `bu-0ynlk.16` (spec-first, owner-gated) |
| Server-side read state replacing browser-only unread badges | `bu-0ynlk.17` (spec-first, after `bu-0ynlk.15` schema) |
| Scoped resume instead of unconditional most-recent resume | `bu-0ynlk.18` (spec-first) |
| Rename and archive wired to the existing PATCH endpoint; pinning | `bu-0ynlk.19` (rename/archive); pinning in `bu-0ynlk.15` thread spine |
| Slash commands and explicit lane overrides (`/ask`, `/fact`, `/bug`, `/new`) | `bu-0ynlk.20` (spec/API-first, server-validated lane override) |

Also out of scope:

- multi-item queues;
- queued-item reordering;
- image or attachment input;
- cross-posture live-stream or queue mirroring (`Global Chat Postures` keeps each instance's turn state independent);
- action-proposal suggestions before `bu-0ynlk.8` lands;
- any LLM-generated suggestion;
- changes to `durable-dashboard-terminal-action-recovery`, `persist-safe-dashboard-unsent-drafts`, `bu-0ynlk.13` accessibility behavior, or `bu-p5umi2.1` ownership gating;
- implementation, activation, baseline-spec sync, archive, or merge.
