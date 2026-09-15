## Why

The dashboard chat-widget classifier (`_build_dashboard_lane_prompt`,
`src/butlers/modules/pipeline.py`) offers three lanes: LANE A (data
statement, `route_to_butler`), LANE B (bug/system report,
`file_bug_report`), LANE C (action request, `route_to_butler`). A genuine
QUESTION — "how much did I spend on groceries this month?", "why did health
flag milk?" — fits none of these: it is not a statement to apply, not a bug,
not an action. Today it either gets misrouted through LANE A/C to a domain
butler that has to improvise an answer path outside its statement/action
contract, or falls into the generic ambiguous/no-tool path and dead-letters
with an uninformative "I wasn't able to figure out how to handle that
message" reply that never distinguishes "I don't understand you" from "I
understood you but have no answer."

This is move 2 of the 2026-09-02 dashboard chat pursuit run 10
(`docs/redesigns/2026-09-02-dashboard-chat-pursuit.md`,
`lens:question-lane-tools`). The 2026-07-28 maturity-pursuit dossier
(`docs/redesigns/2026-07-28-talk-to-butlers-maturity-pursuit.md`) had
concluded against a generic question lane; the 2026-09-02 owner directive
supersedes that decision for this run while keeping its truthfulness
constraints (no silent General routing, no fabricated receipts, Stop honesty)
binding on the new lanes.

Evidence: `lens:question-lane-tools` proposals #1, #4, #6 (dossier move 2).
Baseline `main` @ `e35880fe1`.

## What Changes

- **Two new terminal dashboard classifier tools**, alongside the existing
  `route_to_butler`/`file_bug_report`: `answer_question(scope, question,
  target)` and `cannot_answer(question_summary, scope_checked, reason)`.
  `_build_dashboard_lane_prompt` gains a fourth lane (LANE D — question)
  describing when to call each.
- **`answer_question(scope="domain")`** dispatches through the existing
  `_switchboard_route`/`route.execute` spine (the same tail
  `route_to_butler` uses, now factored into a shared
  `_dispatch_dashboard_target` helper) but injects a new, deterministic
  **read-only** instruction block (`_build_dashboard_answer_block`, sibling
  of `_build_dashboard_confirm_block`) instead of the confirm-loop block: the
  routed session must answer only from its own tools, cite them via
  `conversation_reply`'s new `sources` list, or give an honest decline
  (never fabricate a citation).
- **`answer_question(scope="system")`** dispatches to the Concierge
  system-scope tools when they exist (bu-0ynlk.3); since that bead has not
  landed yet, it currently falls back to the exact same honest-decline
  dead-letter path `cannot_answer` uses.
- **`cannot_answer`** dead-letters the request (`dead_letter_queue`,
  `failure_category='unanswerable'` — a new, distinct reason code added by
  `sw_033`) and replies in-thread naming exactly what was checked. It never
  files a QA bug report and never routes to a domain butler.
- **Lane exclusivity extended**: the existing `route_to_butler`/
  `file_bug_report` mutual-exclusion guard (bu-j5jqv) now also covers the two
  new tools — a second terminal-tool call in the same classification session
  is refused with `dashboard_lane_conflict`, except `file_bug_report`'s
  existing "never suppressed, only surfaced" behavior toward a prior
  `route_to_butler` claim is unchanged.
- **`conversation_reply` gains an optional `sources: list[str] | None`
  parameter.** Omitted (`None`) is unaffected — every existing confirm-loop/
  action-proposal/bug-report reply keeps working exactly as before. An
  explicit empty list (`[]`) or blank source name is rejected with guidance
  text: cite what you consulted, or omit `sources` entirely and give an honest
  decline. A non-empty list persists to a new `dashboard_messages.sources jsonb` column
  (migration `core_213`, nullable, downgrade drops it) and appears in the
  `message_complete` SSE payload.
- **No behavior change to the existing General-fallback text**: the
  dashboard classifier prompt already contains no "route ambiguous input to
  general" instruction as of bu-0ynlk.1 (`_build_dashboard_lane_prompt`'s
  ambiguous-input branch already calls no tool at all rather than
  best-guessing a route) — this proposal only ensures a genuine *question*
  with no identifiable owner never falls into that generic silent path and
  always resolves through `cannot_answer` instead, and adds a regression
  test (grep-level, no "general"/best-guess fallback instruction anywhere in
  the rendered prompt) to keep it that way.
- **Supersession note**: `docs/redesigns/2026-07-28-talk-to-butlers-maturity-pursuit.md`
  gets a dated note recording that the 2026-09-02 owner directive supersedes
  its "no generic question lane" decision for this run.

## Non-Goals

- Concierge / `dashboard_read` system-scope tools themselves (bu-0ynlk.3,
  separate bead) — `answer_question(scope="system")` falls back to
  `cannot_answer`'s dead-letter path until that bead lands.
- The in-process fast lane that lets an answer-lane dashboard turn skip the
  CLI spawn (bu-0ynlk.6). `structured_classify.py`'s schema surface is
  extended so that bead can wire the question lane into the fast path, but
  the fast-lane exclusion for `source == "dashboard"` itself is unchanged —
  dashboard turns (question lane included) still classify via the existing
  CLI path.
- Page-context v2 (bu-0ynlk.4).
- Full citation rendering in the dashboard chat thread — the frontend is out
  of scope for this change; `sources` is persisted and exposed on the SSE
  `message_complete` payload for a later bead (bu-0ynlk.12) to render.

## Impact

- Affected specs: `butler-switchboard` (Dashboard Chat-Widget Classification
  Lanes), `dashboard-conversations` (Conversation Reply Channel, Dashboard
  Message Intent Lanes, Message Data Model, SSE Response Streaming).
- Affected code: `src/butlers/modules/pipeline.py`,
  `src/butlers/core_tools/_switchboard.py`,
  `src/butlers/core_tools/_conversation_reply.py`,
  `src/butlers/api/conversations.py`,
  `src/butlers/api/routers/conversations.py`,
  `roster/switchboard/tools/routing/structured_classify.py`,
  `alembic/versions/core/core_213_dashboard_messages_sources.py`,
  `roster/switchboard/migrations/033_dead_letter_unanswerable_category.py`.
