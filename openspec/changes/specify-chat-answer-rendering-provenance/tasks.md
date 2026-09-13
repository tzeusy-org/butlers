## 1. Authority and Active-Change Reconciliation

- [ ] 1.1 Obtain explicit owner sign-off on the exact proposal, design, tasks, and three delta specs. Do not begin any later task from a draft merge, Bead dependency update, or prior dashboard-chat release approval; sign-off authorizes planning, not implementation, migration, deployment, or release.
- [ ] 1.2 Hand the adopted change to project direction, update implementation reference `bu-0ynlk.12` to this exact adopted artifact, and preserve every existing non-goal and authority gate.
- [ ] 1.3 Refresh all active `dashboard-chat-ui`, `dashboard-conversations`, and `dashboard-design-language` deltas. If another change has archived or now modifies a same-named requirement, rebuild this change from the refreshed baseline and independently review the resulting whole requirement before implementation.
- [ ] 1.4 Reconcile `durable-dashboard-terminal-action-recovery` without adding a second `MODIFIED` block for `Conversation Reply Channel` or `Conversation Pydantic Response Models`; preserve its action, Stop, receipt, read-recovery, and compatibility clauses unchanged.

## 2. Add the Canonical Message Contract

- [ ] 2.1 Add the server-loaded citation-route contract manifest with exact static/dynamic patterns, parameter constraints, query and fragment policy, and a parity gate proving every eligible internal route resolves through `SHELL_CAPABILITIES`.
- [ ] 2.2 Add canonical citation input/output models, plain-text labels, deterministic item and payload budgets, stable deduplication, HTTPS and internal-route validation, and content-blind rejection reason/count logging. Preserve whole-call rejection for empty, blank-label, and over-budget inputs; keep `sources` as the only MCP evidence argument and do not add a parallel `citations` argument.
- [ ] 2.3 Extend `register_conversation_reply_tool` and `conversation_reply_create` so legacy strings normalize to unlinked citations, structured entries normalize server-side, an explicit all-invalid set inserts no reply, and the server-held `ToolContext.butler_name` is the only message-author input.
- [ ] 2.4 Add an additive core migration for nullable `dashboard_messages.citations` and message `routed_butler`, backfill valid legacy source strings as unlinked citations, leave legacy authors null, retain `sources` for rollback, and prove upgrade/downgrade plus malformed-legacy behavior against real PostgreSQL.
- [ ] 2.5 Extend message create/find/list queries, `ConversationMessage`, and frontend API types with canonical citations and nullable message author. Preserve null for generic API-created error rows and every path without a registered butler author.
- [ ] 2.6 Emit persisted citations and message `routed_butler` on `message_complete`, retain the bounded string-only `sources` response projection, and make stored-message reload/reconnect authoritative over display-only token and phase events.

## 3. Render Safe, Truthful Answers

- [ ] 3.1 Replace `SimpleMarkdown` with a focused answer boundary using direct `react-markdown` and `remark-gfm` dependencies, raw HTML disabled, an explicit allowed-element set, no images, safe URL transformation, and a synthetic render-only close for a half-open fenced code block.
- [ ] 3.2 Render headings, lists, emphasis, blockquotes, inline/fenced code, and tables using Dispatch type roles. Keep narrative prose in Voice, interface structure in Body/Title, and code, timestamps, identifiers, and numeric table cells in Mono with tabular numerals.
- [ ] 3.3 Render internal citations through router navigation and external citations as visibly external HTTPS links with `rel="noopener noreferrer"`; render legacy unlinked citations as text and never label any citation as verified evidence.
- [ ] 3.4 Render per-message attribution from message `routed_butler` using `ButlerMark`, name, relative time, and an available session link. For null authors render no butler identity and do not infer from conversation routing, phases, sessions, or model text.
- [ ] 3.5 Consolidate model, token, duration, and cost detail behind one native keyboard-operable disclosure that is closed by default while primary attribution and session navigation remain visible; retain the existing tool-call disclosure as a separate diagnostic surface.
- [ ] 3.6 Contain tables and long code in named, focusable, keyboard-scrollable regions; verify the message and page have no horizontal overflow at 360 CSS pixels and every citation/disclosure target meets the active coarse-pointer floor.

## 4. Extend Existing Verification Seams

- [ ] 4.1 Extend `frontend/src/components/chat/MessageThread.test.tsx` for allowed markdown, raw HTML and unsafe URL suppression, half-open fence completion, Voice/Body/Mono roles, local table/code overflow, internal router navigation, external-link safety, unlinked sources, author/null-author rendering, and disclosure keyboard behavior. Add a focused answer-component test file only if these behaviors cannot remain clear at the existing seam.
- [ ] 4.2 Extend `tests/core_tools/test_conversation_reply.py` for legacy/structured input normalization, plain-text labels, budgets, mixed invalid-target filtering, empty/blank/all-invalid rejection, content-blind diagnostics, and non-overrideable server-held author.
- [ ] 4.3 Extend `tests/integration/test_conversation_reply_db.py` and the existing dashboard-message migration test for canonical persistence, legacy backfill, nullable authors, downgrade behavior, and no partial message write on rejection.
- [ ] 4.4 Extend `tests/api/test_conversations.py` for message-list and `message_complete` projection, stored/SSE/reconnect equivalence, compatibility `sources`, and non-session-authored null attribution.
- [ ] 4.5 Keep one gate species per invariant, reuse existing factories, run the dirty-worktree planner before widening scope, and include the measured `Tests: +a ~b -c` delta plus any adds-only reason in the implementation PR body.

## 5. Compatibility, Security, and Merge Readiness

- [ ] 5.1 Prove every repository consumer reads canonical `citations` and nullable message `routed_butler`; after one separately authorized successful deployment and rollback observation window, remove the response `sources` projection and dual-read branches under the same adopted change.
- [ ] 5.2 Prefer code rollback against the additive schema. Remove the retained `sources` column only after consumer and rollback evidence permits it; require explicit acceptance of structured-target loss before any SQL downgrade.
- [ ] 5.3 Run targeted frontend and Python tests, frontend lint, `npm run knip`, frontend build/typecheck and test, `make lint`, migration and real-Postgres lanes, `make check-spec-overwrites`, `make check-guards`, and `git diff --check`. Use terminal hosted merge-queue CI as the broad full-tree evidence.
- [ ] 5.4 Obtain independent exact-head security, API, and UX review. Security must cover markdown/URL safety, citation-route authority, content-blind diagnostics, and author spoofing; API must cover schema, replay, compatibility, migration, and active-delta overlap; UX must cover trust language, semantic typography, keyboard/focus behavior, disclosure hierarchy, and 360 CSS-pixel containment.
