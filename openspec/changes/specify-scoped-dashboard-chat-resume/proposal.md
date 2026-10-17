## Why

The global dock and popover currently select the first server-ordered active Switchboard conversation once per mount. A thread from another task or yesterday can appear without an explanation. The owner needs brief continuity within the same page family, an honest fresh start elsewhere, and a direct way back without losing unsent words.

This is the specification phase of `bu-0ynlk.18`, split from `bu-0ynlk.14` and grounded in `docs/redesigns/2026-09-02-dashboard-chat-pursuit.md` §14. The proposal is not adopted. The closed pursuit release gate `bu-eu1mo`, claim, publication, and successful validators do not supply owner adoption.

## What Changes

- Amend the complete Global Chat Postures requirement with one bounded automatic choice for the dock and popover: the last confirmed local Switchboard submission, less than 30 minutes old, on the same registered scope-preserving route prefix, verified active under existing reads.
- Keep the observation only in the current tab's dashboard memory across global-posture remounts. Reload and other tabs have no local proof and start fresh. No server timestamps, message-content scans, server state, or persistent resume record are added.
- Give explicit history/search/keyboard/palette selection, `/chat/:id`, the bare `/chat` fresh composer, retained unsent identities, unresolved attempts, and New conversation precedence. Asynchronous results and route navigation never replace a settled entry.
- Preserve adopted per-identity browser drafts and the owner's queue Option A. No automatic selection sends, replays, moves draft text, mirrors an in-flight turn, or weakens unknown-outcome recovery.
- Amend complete Recent-Thread Recall (Command Palette) with visible Earlier conversation disclosure, a direct explicit link, quiet status, keyboard access, focus retention, and truthful failure and draft-dismissal paths.

Proposed scope examples: `/health/conditions` and `/health/medications` share static prefix `/health`; `/butlers/health` and `/butlers/finance` do not match. Dynamic prefixes keep the concrete path through the final bound parameter: `/sessions/S1`, `/entities/A`, `/memory/facts/F1`, and `/ingestion/connectors/telegram-bot/E1` each differ from another target of the same family. This follows the existing page and query ownership contracts. Static aggregate routes can share a family; distinct butler, session, record, endpoint, patrol, and investigation targets cannot silently inherit each other's conversation. Query tabs and fragments do not change the same target's scope. Query-owned targets such as `/sessions?selected=S1`, `/ingestion?event=E1`, and `/calendar?calendar=C1` start fresh automatically; the exact selector-key exclusion is in the delta and design. Their values are never retained or compared.

`REQ-dashboard-chat-ui-018` and `-019` backfill IDs on exact-title MODIFIED predecessors that have no IDs. All four predecessor scenarios remain verbatim. Qualified allocation includes current main, active changes, and the complete public-head census, including proposed reservations `-006` through `-017`.

## Impact

Only this five-file OpenSpec package changes under this bead. Future implementation belongs primarily in `ChatDock`, `FloatingChatWidget`, their tests, and a shared selection seam above their remount boundary; existing `ChatPage`, `ChatRecallCommands`, shared turn/context hooks, and draft seams constrain it. Existing GET list and cross-butler identity reads suffice for positive identity/status verification. No API, schema, backend, migration, dependency, or product code is changed here.

## Out of Scope

Server or cross-device resume; shared server read-state, pins, bulk actions, deletion, rename/archive controls, summaries, new chat postures, context-capture policy changes, butler-detail auto-selection policy, cross-posture live-token mirroring, queue implementation, draft-store implementation, or a new persistence policy. The queue amendment PR #4396 and adopted draft change remain owned by their original beads. This amendment does not adopt a sibling proposal or turn its incomplete implementation into permission to drop a guarantee.

## Approval and Completion

Owner adoption of the exact reviewed proposal/design/spec digest or commit is mandatory before implementation. Then the coordinator files implementation from that adopted change, citing both `frontend/src/components/chat/ChatDock.test.tsx` and `frontend/src/components/chat/FloatingChatWidget.test.tsx`, with the preserved draft/recovery prerequisites bound to the actual implementation scope. Specification completion still requires strict OpenSpec, overwrite checks, repository guards, recorded owner adoption, and those implementation beads. No product code lands under `bu-0ynlk.18`.

## Original acceptance criteria (verbatim, completion pending)

1. An OpenSpec change amends dashboard-chat-ui with WHEN/THEN scenarios for the recent same-prefix resume, a stale or other-prefix start-fresh, an explicit selection, draft and queue preservation across the choice, and an accessible disclosure. It passes openspec validate --strict, make check-spec-overwrites and make check-guards. 2. The owner adoption is recorded. 3. Implementation is filed from the adopted change, citing ChatDock.test.tsx and FloatingChatWidget.test.tsx as the seams. 4. No product code lands under this bead.
