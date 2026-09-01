# Dashboard Chat Pursuit — Run 10 (2026-09-02)

Focused relentless-JARVIS run on a single surface: the dashboard "chat with butler" experience
(floating widget, ChatPanel, the Switchboard dashboard lane behind them). Owner directive:
*"Heavily flesh out a professional, well-made, accessible chat interface on the dashboard, which I
can use to interact with butlers — with context (knowing the contents of the page I'm on), able to
answer questions (by this chat-butler having access to reasonable tools). Figure out numerous use
cases of chatting with an agent on the dashboard itself and make the UI/UX a smooth well-designed
experience."*

- **Run:** wf_ae184fd2-da6 — 9 agents in 3 hourly batches of 3 (throttle honored), synthesis inline.
- **Yield:** 97 findings · 66 proposals · 93 candidate findings dropped by agents as already-known.
- **Data:** [`2026-09-02-dashboard-chat-pursuit-data.json`](2026-09-02-dashboard-chat-pursuit-data.json)
  — full per-lens structured output, keyed by lens name. Access pattern:
  `jq '.["lens:use-cases"]' docs/redesigns/2026-09-02-dashboard-chat-pursuit-data.json`
  (lenses: `qc:current-surface`, `lens:page-context`, `lens:use-cases`, `lens:question-lane-tools`,
  `ux:conversation-surface`, `ux:a11y-keyboard`, `lens:actions-from-chat`,
  `lens:continuity-memory`, `ux:speed-streaming`; each has `.findings[]`, `.proposals[]`,
  `.jarvis_gap`, `.dropped_as_known[]`).

## Product boundary shift (supersession)

The 2026-07-28 talk-to-butlers dossier recorded **"no generic question lane"** as a deliberate
product decision. The owner's 2026-09-02 directive **supersedes that boundary**: the question lane
is now in scope, designed through the existing Switchboard dispatch spine. The dossier's
truthfulness constraints **survive unchanged** and bind every move below: no silent routing of
uncertainty to General, no fabricated receipts, Stop honesty. This supersession should be recorded
in `docs/redesigns/2026-07-28-talk-to-butlers-maturity-pursuit.md` when move 2 lands.

## North star

The owner opens any dashboard page, presses one key, and talks to the household in plain language
about **what is on screen** — asks questions and gets fast, cited, truthful answers; states facts
and sees them recorded reversibly; requests actions and consents *before* anything runs; and the
thread itself is a durable, addressable, searchable object that follows the owner across surfaces.
Earned calm: the surface never pretends (no fake streaming, no fake undo, no guessed attribution,
no silently dropped context).

## Tier board

The 2026-07-28 baseline verdict for this surface was **"functional, not mature"** — reliable
plumbing (durable turns, honest Stop, truthful receipts), deliberately narrow product. This run
decomposes that single verdict; movement is reported against it, not against a per-facet baseline
(none existed).

| Facet | Tier | Note |
| --- | --- | --- |
| Turn plumbing & Stop honesty (QC) | **functional** | The 07-28 reliability work held up under QC; defects found are gaps, not regressions |
| Conversation surface (thread/composer/chrome) | **functional** | Renders and works; contradicts the Dispatch language it sits inside; markdown/citations absent |
| Accessibility & keyboard | **functional** | Semantics mostly present; streamed replies silent to SR; no direct shortcut; focus-trap gap |
| Speed & streaming | **weak** | Streaming is simulated (one token event after a 500 ms DB poll); two cold CLI spawns per turn |
| Question lane / tools / actions / context / continuity (5 ideation lenses) | *not tiered* | Greenfield capability lenses — the gap analysis below is their output |

## Systemic themes

**1. Questions become writes — the read/ask lane is structurally absent.** The dashboard
classifier is a forced binary between "route it as a statement" and "file a bug"
(`src/butlers/modules/pipeline.py:562-589`), ambiguity is *normatively instructed* to resolve into
a best-guess route to General (`:576-579`), and the block injected into the routed butler is a
mutation contract — "apply it, then confirm" (`src/butlers/core_tools/_switchboard.py:90-107`).
Ask "how much did we spend this week?" and the system's honest options are to write something or
file a bug about it.

**2. "Context-aware" is one JSON line.** `page_context` is `{route, query_params, entity_ref}`;
exactly 1 of ~60 routes enriches it (`EntityDetailPage.tsx:2378`), ChatPanel sends none at all,
pinned/sticky follow-up turns bypass injection entirely (`pipeline.py:2394-2447`), and the payload
is never persisted — not auditable, not replayable, invisible and unremovable to the owner. The
butler is told where the owner is standing, never what they are looking at.

**3. Nobody holds the tools to answer.** Spend, sessions, fleet health, timeline, approvals,
search — every cross-butler read model exists only inside FastAPI
(`src/butlers/api/read_models/*`); no butler has an MCP surface over any of it, and system
operations (pause/resume, trigger, spend ceilings, model routing) have zero MCP tools. Even
`delegate_ask` is staffer-gated away from Switchboard (`_delegation.py:262`).

**4. Consent is theatre.** The routed-butler contract mandates applying the change *before*
asking for confirmation — directly contradicting the security doctrine that the gate pauses
execution and requests confirmation before proceeding (`about/heart-and-soul/security.md:103-105`).
The thread has zero action affordances; a gated tool parked mid-turn produces an approval the
conversation never mentions.

**5. Streaming is simulated and the lane is slow by construction.** The backend polls the DB every
500 ms (`routers/conversations.py:165`) and emits the entire reply as one `token` event (`:798`);
the dashboard is excluded from the fast structured-classify lane (`pipeline.py:2954-2958`), so
every turn pays two sequential cold CLI spawns against a 300 s SSE timeout. No reconnect: a tab
sleep reports failure for a turn that succeeded.

**6. The thread has no memory, identity, or spine.** Every dashboard turn forks a shadow duplicate
conversation row on the target butler (`_routing.py:1014-1041`); replies persist
`session_id=NULL` always (`api/conversations.py:700-736`), killing the spec-mandated session
drill-down; thread memory is a 4000-char last-5-exchanges preamble
(`conversation_envelope.py:19-59`); no tool anywhere can read past conversations; the dashboard
operator resolves to no entity; read state is one browser's localStorage.

**7. The system's voice contradicts its own design language.** Round drop-shadowed FAB, bouncing
dots, code-fence-only markdown, no citation field on messages, seven-signal meta rows, no SR
announcement of replies, no direct keyboard shortcut — and ~250 lines of send/stream/stop logic
duplicated verbatim between `ChatPanel.tsx` and `FloatingChatWidget.tsx`.

## Ranked moves

Trust/correctness defects lead (moves 1–2, 5); feature/capability moves are the center of gravity
(2–4, 6–10, 15). Each move cites its full proposal(s) in the data file as `[lens #rank]`.

### 1. Propose-then-act: kill the confirm-after-write theatre `[actions-from-chat #3]`
Split the injected dashboard block by intent: statements keep apply-then-confirm; **action
requests must park through the approval gate first** and reach the thread as a proposal, never as
a done deed. Add an ACTION lane to the classifier with an in-thread clarifying question as the
ambiguity fallback. Aligns the runtime with `security.md:103-105`.
*IP:* `_switchboard.py:85-108`, `pipeline.py:560-586`, roster CLAUDE.md interactive-mode sections.

### 2. The question lane, with a truthful decline `[question-lane-tools #1, #4, #6]`
Extend the classifier from two lanes to four terminal tools: `route_to_butler`, `file_bug_report`,
**`answer_question(scope, question, target)`**, **`cannot_answer(reason, what_i_checked)`**.
`answer_question` injects a read-only sibling of the confirm block (answer from your own tools,
cite them, never write); `cannot_answer` dead-letters durably and replies naming what was checked.
**Delete the route-to-General fallback** (`pipeline.py:576-580`). Enforce provenance at the schema
level: a `sources` list on `conversation_reply`, required for answer-lane replies — cite or
decline. This is the owner's headline ask made expressible, on the proven dispatch/Stop spine.
*IP:* `pipeline.py:517-634`, `_switchboard.py` (new tools + `_build_dashboard_answer_block`),
`structured_classify.py`, `_conversation_reply.py:59`.

### 3. Concierge staffer + `dashboard_read` module + RFC 0012 `[question-lane-tools #2, #3]`
A new `roster/concierge/` staffer owns the system plane, backed by a new
`src/butlers/modules/dashboard_read/` module (~18 read tools mirroring the FastAPI read models:
fleet status, sessions, spend, search, knowledge, timeline, approvals-read, QA, audit …), every
result carrying a `source` envelope. Doctrinal unlock: RFC 0012 extends the RFC 0010 exception
class to fleet operational telemetry via column-allowlisted read-only UNION views
(`concierge.v_fleet_sessions`/`v_fleet_spend`) — no prompt/result text crosses schemas. Domain
questions stay with domain butlers; Concierge answers only "the fleet" questions.
*IP:* `roster/concierge/`, `modules/dashboard_read/`, `about/legends-and-lore/rfcs/0012-*.md`,
`read_models/*_v1.py` as DTO contracts.

### 4. Page-context v2: typed, visible, removable, redacted `[question-lane-tools #7; conversation-surface #3; page-context #1–#8]`
Extend `PageContext` with `visible_resource {kind, id, filters, window}` + `visible_summary`, a
per-route registry so every path gets useful context for free, and `usePageSubject()` for pages
with real state. Surface it as a **removable context chip** above the composer showing exactly
what will be sent (pre-send), with per-message opt-out. Persist the snapshot with the message;
carry capture-time staleness; fence third-party data; **ref-only/none contextPolicy with
server-side redaction for /secrets, /settings/permissions, /settings/models** — page context is
the one path shipping arbitrary dashboard state into an LLM prompt. Fix the bypasses: ChatPanel
sends context; sticky/pinned turns keep it.
*IP:* `lib/page-context.tsx`, new `page-context-registry.ts`, `models/conversation.py:17-30`,
`pipeline.py:591-597` + `:2394-2447`, `_switchboard.py:88-89`, new `ContextChip.tsx`.

### 5. Thread integrity: one row, a session link, inspectable work `[continuity-memory #1, #2; actions-from-chat #6]`
Stop the shadow-anchor fork (`_routing.py:1014-1041` resolves the existing conversation instead of
creating a ghost; cleanup migration folds ghosts back). Stamp `session_id` and `tool_calls` on
replies (`_conversation_reply.py` → `conversation_reply_create`) — revives ~120 lines of
already-shipped drill-down UI and makes two normative spec scenarios satisfiable
(`dashboard-chat-ui/spec.md:80-86, 282-291`). Add the reverse "Asked in chat →" link on session
detail.

### 6. Read fast lane + lane-aware budgets `[question-lane-tools #5, #8; speed-streaming #1]`
Answer-lane turns run in-process (structured classify + read tools + one phrasing call), with a
`register_inprocess_runtime` handle so Stop still addresses them — target p95 < 3 s vs two cold
spawns. Pre-resolve ownership via the existing `resolve_target_via_catalog` scorer (no-hit is the
deterministic `cannot_answer` trigger); make `_SESSION_TIMEOUT_S` lane-aware (~45 s answers /
300 s mutations).
*IP:* `pipeline.py:2954-2998`, `dashboard_turns.py`, `structured_classify.py:81-140`,
`delegation_ledger.py:565-605`, `routers/conversations.py:168`.

### 7. Real streaming, phase truth, reconnect `[speed-streaming #2–#6]`
Replace poll-for-finished-row with live forwarding (LISTEN/NOTIFY per request_id) so tokens stream
as they arrive; emit phase events (classifying → routed to X → thinking (tool: Y) → writing) into
`pendingActivityStatus`; recover from network blips by refetching messages before declaring
failure; throttle auto-scroll ahead of real tokens.
*IP:* `routers/conversations.py:398-812`, `MessageThread.tsx:275-316`, `sse-utils.ts:45-81`.

### 8. Actions from chat: proposal cards on the approval spine `[actions-from-chat #1, #2, #4, #5, #7]`
`conversation_propose_action(action_id)` — a butler can only surface an approval the gate already
parked, rendered as an in-thread card (dossier: why/evidence/blast-radius/reversibility) whose
Confirm calls the *same* `useApprovalDecisionMutations()` as /approvals. A `chat_eligibility`
taxonomy in `command_contracts.py` + butler.toml (gated / page-authority / self-scoped /
**never-chat** for secrets & policy). Honest receipts via the turn ledger (`action_confirm` claim
kind → executed / queued-not-run / failed / half-happened are all representable); chat-origin
provenance server-verified into the audit log; reversibility-gated undo (no undo affordance on
irreversible actions — typed second confirm instead).

### 9. `conversation_recall` + message-level search `[continuity-memory #4; conversation-surface #8]`
A core tool over the conversation store (`tsvector` + trigram indexes, owner-scoped across
butlers) answering "what did I ask you last week about X" — the same primitive backs the widget's
search box (snippets, highlighting, jump-to-message, butler/date/lane filters on /chat).

### 10. Chat-to-graph provenance + a resolved owner `[continuity-memory #5, #8]`
`"message"`/`"conversation"` evidence kinds so facts learned in chat cite the utterance; explicit
echo-back confirm-loop line ("Recorded: … — say 'no' to undo"); resolve `dashboard:operator` to
the owner entity so dashboard speech anchors to a person, not a surface, and "you told me" is
honest.

### 11. Postures + addressability: docked rail and /chat `[conversation-surface #2; continuity-memory #3]`
Three postures — docked side rail (default ≥ xl, a Shell sibling column), full-page `/chat` +
`/chat/:conversationId` routes with `#m-{id}` anchors and palette recall, popover below md.
Extract the duplicated ~250-line state machine into one `use-conversation-turn` hook. A
conversation you can link to can be handed off, cited, and reopened.

### 12. Answers that render: markdown, citations, attribution `[conversation-surface #1, #5]`
Replace the code-fence-only renderer with a safe full-markdown pipeline; a server-validated
citation contract whose entries deep-link into dashboard routes through the shell-capability
allowlist (in-SPA navigation, never a full reload); persist `routed_butler` per message and render
a Dispatch attribution line (ButlerMark + name + Session →, detail behind one disclosure) — never
guessed.

### 13. Accessibility batch `[a11y-keyboard #1–#6; conversation-surface #7]`
An `aria-live` region so streamed and committed replies are actually announced; a direct global
shortcut to open/focus chat (initial focus → composer); real focus containment for the popover;
reduced-motion guards on auto-scroll; 44px targets in the header; keyboard-viewport-aware height
(`dvh`/`visualViewport`) on mobile; unread state fed to the shell announcer.

### 14. Composer, suggestions, thread management `[conversation-surface #4, #6, #7; continuity-memory #7, #9]`
Never-blocked composer with per-conversation draft persistence and slash commands (`/ask`,
`/fact`, `/bug`, `/new`) that make the lanes explicit and pre-empt misclassification; per-page
suggested prompts declared on `ShellCapability`; empty state that teaches the lanes; Dispatch
conformance for the chrome (no FAB fill/shadow, process-status pill instead of bouncing dots);
rolling thread summary replacing the silent 4000-char truncation; rename/pin/archive wired to the
already-shipped PATCH endpoint; scoped resume (recent + same route prefix) instead of
unconditional; server-side read state so every surface agrees on what the owner has seen.

### 15. The owner thread spine `[continuity-memory #6]`
`public.conversation_threads` + channel bindings: one durable owner-level thread across dashboard
and Telegram, with "continue on phone" handoff (notify() carrying the `/chat/{id}` deep link) and
its inverse. Highest cost in the list; deliberately sequenced after the cheap integrity repairs
it builds on (moves 5, 9, 10).

## Dropped as known

Agents dropped 93 candidate findings against the ledger (per-lens lists in
`dropped_as_known` in the data file). Excluded work: bu-s3qvp, bu-27dxl.9, bu-d2ft5, bu-4ewbl,
bu-7exe4.14/.2/.13, bu-6jv4m.1; landed: bu-p6ey8.3/.4, bu-o0ab2. Notables the run confirmed as
already-tracked rather than re-filing: dashboard turn durability/cancellation (bu-7exe4.14),
page-context capture itself (bu-p6ey8.4, shipped), Telegram-side conversation continuity work.
