## Why

Assistant replies currently expose markdown text, optional `sources` strings, and
conversation-level routing, but those contracts do not establish safe rich rendering,
navigable citations, or the author of each individual reply. The result is a trust gap:
model-authored text can look like provenance, a thread-level route can be mistaken for a
message author, and the limited `SimpleMarkdown` surface cannot present ordinary answer
structure accessibly.

This proposal is the specification prerequisite for implementation Bead `bu-0ynlk.12`.
It is based on `main` at `d8b1924635a04f3159dffa5e3e47e83633e79693` and remains
**proposed** until the owner signs off on this exact change. Drafting or merging these
artifacts does not authorize implementation, migration, release, deployment, or adoption.

## What Changes

- Expand assistant-answer rendering from fenced code and newline-preserving paragraphs to
  sanitized markdown headings, lists, inline code, emphasis, blockquotes, fenced code, and
  tables. Raw HTML is never rendered, incomplete streaming syntax remains safe, and wide
  tables scroll within the answer instead of widening the page.
- Add one canonical structured citation projection to assistant messages. Internal targets
  must match a server-held mirror of citation-eligible shell capability routes; external
  targets must be absolute HTTPS URLs. Invalid entries are removed before persistence, and
  an explicit source set that leaves no usable entries is rejected rather than presented as
  grounded.
- Keep the existing `conversation_reply(..., sources=...)` parameter as the only evidence
  input. During a bounded compatibility window, its existing non-empty string entries are
  accepted and normalized into visibly unlinked legacy citations; structured entries can
  produce internal or external links. No parallel `citations` tool argument is introduced.
- Persist the server-normalized citation objects as `citations` and expose that same shape
  through stored message reads and `message_complete`. A temporary `sources` wire projection
  remains only for verified repository consumers during the migration window and has a
  required same-repo removal task.
- Persist nullable `routed_butler` on every assistant message created through
  `conversation_reply`, deriving it from the server's registered `ToolContext.butler_name`,
  never from model text, request data, conversation-level sticky routing, or the browser.
  Legacy and non-session-authored messages remain explicitly unattributed.
- Render attributed assistant messages with the existing `ButlerMark`, butler name, relative
  time, and session navigation. Model, token, duration, and cost details stay behind one
  keyboard-operable disclosure that is closed by default; the existing tool-call disclosure
  remains a separate diagnostic surface.
- Define additive migration, staged compatibility, downgrade, replay, logging, and test
  obligations without implementing them in this change.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-chat-ui`: replace the limited assistant renderer with a safe, streaming-stable,
  accessible answer and provenance presentation.
- `dashboard-conversations`: define canonical citation persistence and projection plus
  truthful server-derived per-message butler attribution.
- `dashboard-design-language`: recognize assistant answer prose as a Voice surface while
  retaining Body and Mono roles for structure, metadata, code, and tabular numerals.

## Impact

An eventual implementation would affect
`frontend/src/components/chat/MessageThread.tsx`, a focused answer component boundary,
`frontend/src/lib/shell-capability.ts`, frontend API types, the dashboard conversation API and
core `conversation_reply` path, and an additive `public.dashboard_messages` migration. The
server remains the validation authority; the frontend route registry is a parity-checked
consumer, not a security boundary.

Relevant live authority is:

- `dashboard-chat-ui` requirement `Message Thread Display`, especially scenario `Assistant
  message rendering`.
- `dashboard-conversations` requirements `Message Data Model`, `Conversation Messages List`,
  `SSE Response Streaming`, `Conversation Pydantic Response Models`, and `Conversation Reply
  Channel`.
- `dashboard-design-language` requirements `Type System`, `Tabular Numerals`, `Butler
  Letter-Mark`, `Voice Surface`, and `Interaction Affordances`.
- Active change `durable-dashboard-terminal-action-recovery`, which already modifies
  `Conversation Reply Channel` and `Conversation Pydantic Response Models`. This proposal
  does not create competing `MODIFIED` blocks for those requirement names.
- Active changes `conversation-anchor-provider-resume-ledger`,
  `reconcile-dashboard-conversation-contracts`, and
  `amend-dispatch-viewport-modality-contract`, whose unrelated conversation identity,
  envelope, and viewport clauses remain intact.

Out of scope: renderer or API implementation, migrations, routing or answer generation,
approval actions or undo, fact-evidence identity, owner identity, the conversation thread
spine, unrelated chat features, deployment, and owner adoption.

## Feature Funnel Summary

Size: large. Baseline: `d8b1924635a04f3159dffa5e3e47e83633e79693`.

- Gate 0, baseline: all five project pillars are present. The capability specs are binding;
  active deltas are reconciled above.
- Gate 1, motif: the owner needs structurally rich answers that remain safe and say what can be
  followed and who actually wrote each message, without turning model text into proof.
- Gate 2, doctrine: aligned with `heart-and-soul/vision.md` domain specialization and reliability,
  plus `heart-and-soul/security.md` session sandboxing and server-enforced trust boundaries.
- Gate 3, topology: the contract crosses the registered butler MCP tool, shared dashboard-message
  persistence, dashboard API/SSE, and the React conversation surface; it creates no new process or
  cross-butler path.
- Gate 4, design: `design.md` fixes the data, trust, rendering, interaction, compatibility, and
  rollback decisions; the dashboard design-language deltas make answer typography explicit.
- Gate 5, specification: three existing capabilities are modified through 43 testable scenarios,
  with no new capability or hidden scope.
- Gate 6, engineering bar: `tasks.md` binds migration, content-blind diagnostics, existing-test
  extension, overwrite/body checks, guards, hosted CI, and independent exact-head review.

Open questions: none. Sign-off: pending exact owner adoption. Recommended handoff after adoption:
project direction updates and allocates `bu-0ynlk.12`; no implementation is released by this draft.
