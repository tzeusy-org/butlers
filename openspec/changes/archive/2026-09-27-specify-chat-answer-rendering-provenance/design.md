## Context

See `proposal.md` for motivation and scope. This design is based on `main` at
`d8b1924635a04f3159dffa5e3e47e83633e79693`.

[Observed] `dashboard-chat-ui` requirement `Message Thread Display`, scenario
`Assistant message rendering`, limits the current renderer to fenced code and
newline-preserving paragraphs. `MessageThread.tsx` uses that `SimpleMarkdown` path for both
stored and in-progress assistant content.

[Observed] `dashboard-conversations` requirement `Conversation Reply Channel`, scenarios
`conversation_reply accepts an optional sources list for an answer-lane reply`,
`conversation_reply is unaffected when sources is omitted`, and `conversation_reply rejects
empty or blank source names`, defines `sources: list[str]`. `Message Data Model` stores those
strings, and `SSE Response Streaming`, scenario `SSE stream for new conversation`, emits them.
The messages-list response model does not currently project them. A source string is useful
grounding context, but it cannot encode a safe destination or prove that the named source
supports the answer.

[Observed] `dashboard-conversations` requirement `Conversation Data Model`, scenario `Sticky
routed_butler stamping`, records only the first successful classification route on the
conversation. A later assistant reply can have a different or unknown author, so this sticky
thread field is not message attribution.

[Observed] these active changes touch adjacent contracts:

- `durable-dashboard-terminal-action-recovery` modifies `Conversation Reply Channel`,
  `Conversation Pydantic Response Models`, `SSE Client Integration`, and conversation-query
  behavior. This change avoids competing `MODIFIED` blocks for those exact requirement names.
- `reconcile-dashboard-conversation-contracts` modifies `Dashboard Ingestion Envelope
  Construction`; `conversation-anchor-provider-resume-ledger` modifies `Conversation Data
  Model`; `amend-dispatch-viewport-modality-contract` adds the device-band and 44 by 44 CSS-pixel
  interaction floor. Their clauses are cited or preserved, not copied into this scope.
- PR #3960 currently includes the first two conversation changes above while separating
  conversation identity from provider reply targets. It confirms that sticky conversation
  identity and current-turn authorship are distinct concerns.
- Archived `add-dashboard-question-lane` deliberately deferred rendering and established
  optional `sources` as the existing evidence input for `bu-0ynlk.12`.

## Goals / Non-Goals

**Goals:**

- Give stored and streaming answers the same safe markdown behavior.
- Make citations useful for navigation without representing model assertions as verified facts.
- Make the server the authority for accepted citation targets and message authors.
- Preserve legacy `sources` callers and rollback safety through an explicit, bounded bridge.
- Make the experience readable, keyboard-operable, and contained at a 360 CSS-pixel viewport.
- Leave an implementer with exact schemas, callers, migration order, test seams, and failure
  behavior.

**Non-Goals:**

- Changing question routing, answer generation, tool eligibility, or the requirement that an
  ungrounded answer declines honestly.
- Fact-evidence identity, attestations, citation content fetching, link availability checking, or
  an assertion that a citation proves an answer.
- Conversation-level identity, thread anchors, owner identity, action proposals, approvals, undo,
  or terminal-action recovery.
- Implementing any renderer, API, schema, migration, deployment, or adoption in this proposal.

## Surface and Trust Map

| Surface or value | Producer | Trust status | Consumer | Required handling |
| --- | --- | --- | --- | --- |
| Assistant markdown `content` | Ephemeral LLM session through `conversation_reply` | Untrusted display content | Stored and streaming answer renderer | Parse as markdown with raw HTML disabled and an explicit element/URL policy |
| `sources` input | Ephemeral LLM session | Untrusted evidence claim and target proposal | Server normalization at `conversation_reply` | Validate budgets, target shape, scheme, and internal route eligibility before persistence |
| Canonical `citations` | Server normalization | Safe navigation shape, not verified evidence | Message read API, SSE, frontend | Persist and project unchanged; never add a `verified` presentation |
| Message `routed_butler` | Registered server `ToolContext.butler_name` | Trusted message-author identity | Message row, read API, SSE, attribution line | Caller cannot supply or override; nullable when server authority is absent |
| Conversation `routed_butler` | Switchboard sticky classification route | Trusted thread routing history, not authorship | Follow-up routing and conversation summary | Never backfill or infer message author from it |
| `phase.target` | Live dashboard routing observation | Display-only current-turn progress | In-progress status | Never persist or reuse as message author |
| Inline markdown URL | LLM-authored text | Untrusted | Markdown renderer | Only absolute HTTPS becomes an external link; all other inline targets render as text |
| Internal citation target | LLM proposal accepted by server contract manifest | Route-shape validated only | Client router | Navigate in SPA; destination handles later not-found or unavailable state |
| External citation target | LLM proposal with validated HTTPS syntax | Scheme-shape validated only | Browser | Use safe external-link attributes and make externality apparent |

The server validation label means only that a target is structurally safe and supported by the
dashboard navigation contract. It does not mean the source was consulted, is reachable, contains
the claim, or is true.

## Schemas and Callers

### Input and canonical output

Keep one tool parameter:

```text
conversation_reply(
  conversation_id: str,
  message: str,
  sources: list[str | CitationSourceInput] | None = None,
)

CitationSourceInput = {
  label: str,
  target: str,
}

Citation = {
  label: str,
  target: str | null,
  kind: "internal" | "external" | "unlinked",
}
```

There is no `citations` tool argument. `sources` remains the single evidence-input name so the
question-lane contract and existing callers do not fork. A string source is normalized to an
`unlinked` citation and cannot acquire link semantics by resembling a path or URL. A structured
source becomes `internal` only after route-manifest validation or `external` only after HTTPS
validation.

Budgets are deterministic: at most 20 entries; a trimmed plain-text label is 1 to 200 Unicode scalar values;
a target is at most 2048 characters; the normalized JSON payload is at most 32 KiB. Stable-order
deduplication keeps the first identical `(kind, target, label)` tuple. A duplicate is not an error.
Blank labels, control characters, unsafe schemes, embedded credentials, non-absolute external
URLs, invalid percent encoding, path traversal, non-allowlisted internal routes, and any item or
payload over budget are invalid. An empty list, any blank label, or any item/total budget breach
rejects the entire call to preserve the existing fail-fast `sources` contract. A malformed,
unsafe, or non-allowlisted structured target is dropped per entry only when at least one usable
entry remains.

### Persisted message

The additive migration appends:

```text
dashboard_messages.citations       JSONB NULL
dashboard_messages.routed_butler   TEXT NULL
```

The existing `dashboard_messages.sources JSONB NULL` remains during the compatibility and
code-rollback window. New writes store the normalized `Citation[]` in `citations`, store the
server-held author in message `routed_butler`, and dual-write only citation labels to `sources`.
Backfill converts each valid legacy source string to `{label, target: null, kind: "unlinked"}`.
It does not backfill message authors. Invalid legacy JSON leaves `citations = null` and is counted
content-blindly rather than repaired by guesswork.

### Read and stream callers

`ConversationMessage`, its frontend `Message` mirror, the message list query, and
`message_find_reply_since` carry `citations` and nullable `routed_butler`. The
`message_complete` event copies both from the persisted row. The stored-row projection is the
authority after reconnect, reload, late reply, or a mismatch with display-only token events.

During the bounded compatibility window, the messages list and `message_complete` also expose
`sources: string[]` for verified repository consumers. Both `citations` and `sources` serialize
database NULL as `[]` on the wire. The legacy projection is derived from canonical citation labels
on new writes; it is never combined back into `citations` by canonical readers. No endpoint returns
rejected source content.

### Write boundary and author source

`register_conversation_reply_tool` already closes over `ToolContext.butler_name`. The eventual
implementation passes that value as a required server-only `routed_butler` argument to
`conversation_reply_create`, which persists it atomically with the assistant row. The MCP schema
does not expose an author field. Calls to lower-level generic `message_create` outside this
registered tool leave message `routed_butler` null unless another future contract establishes an
equally trusted server authority.

## Decisions

### 1. Use an AST markdown renderer with an explicit allowlist

The implementation uses direct dependencies on `react-markdown` and `remark-gfm`. Raw HTML is
disabled, only the specified answer elements are rendered, images are disabled, and URL handling
allows only absolute HTTPS inline links. Custom renderers own code blocks, tables, and links.
`rehype-raw` is not enabled. This avoids a hand-written parser while keeping the accepted syntax
and URL surface reviewable.

For streaming content, a small preprocessor appends a synthetic closing fence only when the
current text contains a half-open line-start fenced code block. The synthetic delimiter is never
stored or copied. All other incomplete constructs go through the same parser and element policy.
On completion the raw persisted text is rendered without the synthetic closure.

Alternative rejected: expand `SimpleMarkdown` with regular expressions. Nested lists, escaping,
GFM tables, and half-open syntax make that approach a parser with an unreviewed security surface.

Alternative rejected: enable raw HTML and sanitize afterward. Raw HTML is not a required
capability, so removing it before an HTML tree exists is the smaller trust surface.

### 2. Evolve `sources`; do not introduce a parallel evidence input

`sources` remains the only caller-facing argument and accepts the legacy string or new structured
input. `citations` is the sole forward persisted and response representation. The temporary string
projection exists only for mixed-version repository consumers.

Alternative rejected: add `citations` beside unchanged `sources` indefinitely. Two evidence
inputs would diverge on ordering, validation, and trust and would leave every caller to choose.

Alternative rejected: interpret legacy source strings as URLs. Existing values include tool names
and record identifiers, so doing so would manufacture link trust that the caller never supplied.

### 3. Put citation-route authority in a server-loaded contract manifest

Add one repository-owned, language-neutral citation route manifest under the API contract layer.
Each entry names a citation-eligible shell path pattern and parameter constraints. The server loads
that manifest and validates internal targets; the frontend consumes or parity-checks the same
entries against `SHELL_CAPABILITIES`. A contract test fails when a citation route has no matching
shell capability or its parameter shape drifts.

Internal targets must start with `/`, resolve to exactly one manifest pattern, contain no origin,
userinfo, path traversal, encoded separator, or unapproved query key, and normalize to the same
path after parsing. Fragments are permitted only for a manifest entry that explicitly declares a
fragment contract, such as the existing chat message anchor. Query parameters are permitted only
when the manifest entry declares their exact names. The browser registry cannot widen this set at
runtime.

Alternative rejected: validate only in `CitationRow` using `resolveShellCapability`. Model output
would reach persistence unvalidated, old clients could bypass the check, and the browser would be
treated as a security boundary.

Alternative rejected: make the TypeScript shell registry the server's runtime input. Python must
not parse or execute a frontend module to decide whether untrusted model output is safe.

### 4. Attribute at the message write boundary

The registered tool's server-held butler name is the only available authority that describes who
authored that reply. It is persisted on the same row in the same write as `content` and citations.
Session linkage remains nullable and orthogonal: it helps navigation and accounting, but an absent
session ID does not erase a known author, and a later session lookup does not manufacture one.

Alternative rejected: copy `dashboard_conversations.routed_butler`. That field is sticky routing
history and can be null, stale for the current turn, or a different concept from the process that
wrote the message.

Alternative rejected: accept `routed_butler` in `sources`, message text, MCP arguments, request
headers, or browser state. Every option is caller-asserted and forgeable.

### 5. Treat answer prose as Voice and controls as interface

Assistant narrative uses the existing Source Serif 4 Voice role because this is the system
speaking in sentences. Headings, citation labels, buttons, and attribution remain sans Body or
Title; code, timestamps, identifiers, and table numerals use Mono, with tabular numerals for
numeric cells. This extends, rather than bypasses, `Type System` and `Voice Surface`.

The attribution line shows `ButlerMark`, name, relative time, and an available `Session ->` link.
One native button/disclosure, closed by default, contains model, token, duration, and cost detail.
The existing tool-call disclosure remains separate. Wide table and code regions are named,
focusable, and horizontally scrollable without
widening the message or page at 360 CSS pixels. Citation links and the disclosure meet the active
viewport contract's coarse-pointer target floor and visible-focus rule.

### 6. Use an additive compatibility window and code-first rollback

The implementation has three phases:

1. Expand: add nullable columns, backfill legacy source strings as unlinked citations, accept both
   source item forms, dual-write, and emit both canonical and legacy projections.
2. Migrate: update all repository API types, SSE parsing, stored-message reads, and renderers to
   consume `citations` and message `routed_butler`; prove no repository consumer reads response
   `sources`.
3. Contract: remove the response `sources` alias and dual-read branches after one successful
   deployment plus its rollback observation window. Retain the old DB column until the same proof
   permits its removal.

Rollback prefers old runtime code against the expanded schema. Old code ignores the nullable new
columns and continues reading `sources`. A SQL downgrade happens only after runtime rollback and
explicit acceptance that structured targets written since upgrade will be lost; it drops
`citations` and message `routed_butler` but preserves the pre-existing `sources` column. Re-upgrade
can recover labels as unlinked citations, not the lost targets. No automatic downgrade rewrites a
target into apparently validated provenance.

### 7. Make partial rejection and replay deterministic

Structured-target normalization is per entry. A mixed valid/invalid-target list persists only valid
citations and emits a content-blind reason-code/count warning. An empty list, any blank label, any
budget breach, or a list with zero usable entries rejects the tool call and inserts no message,
preserving the existing empty/blank-source refusal. Omitting `sources`
persists an ordinary unattributed-to-evidence reply with null citations, including an honest
decline.

Late SSE replies, disconnects, and replay do not rerun normalization in the browser. The persisted
row is read once and projected unchanged. A valid internal route that later points to a missing
resource navigates to the destination's existing not-found state; an external destination that is
offline remains an external link. Neither case mutates the stored citation or changes its trust
label.

## Behavior Matrix

| Case | Persisted result | SSE/list result | UI result | Trust or recovery rule |
| --- | --- | --- | --- | --- |
| Stored complete markdown | Exact content | Same exact content | Full allowed markdown | One sanitized renderer |
| Streaming half-open fence | No synthetic text persisted | Tokens remain display-only | Temporary code block, then clean completed render | Persisted `message_complete` wins |
| Raw `<script>` or event-handler HTML | Text content only | Content unchanged | No active element or handler | Raw HTML disabled |
| Inline absolute HTTPS link | Content only | Content unchanged | Safe external link | Link is not a citation or verified evidence |
| Inline relative, script, data, file, or malformed link | Content only | Content unchanged | Label/text only | No client-created internal trust |
| Valid internal structured source | Canonical `internal` citation | Same citation | Router navigation, no full reload | Server manifest is authority |
| Valid external structured source | Canonical `external` citation | Same citation | Safe external link | Scheme safety only |
| Legacy string source | Canonical `unlinked` citation plus compatibility label | Same values | Text-only source | No target inferred |
| Mixed valid and invalid structured targets | Valid entries only | Valid entries only | Valid citations only | Content-blind reject count/reasons logged |
| Empty, any blank, over-budget, or all-invalid explicit sources | No message | Structured tool error, no SSE completion | No grounded answer appears | Existing refusal behavior preserved |
| Sources omitted | Null citations | `citations: []` and compatibility `sources: []` | No citation row | Not treated as grounded or invalid |
| Registered butler writes reply | Message author persisted from server context | Same nullable author | ButlerMark and name | Caller cannot override |
| Legacy or API-authored assistant row | Author remains null | Null author | No butler attribution | Conversation route is not substituted |
| Destination later unavailable | Citation unchanged | Citation unchanged | Existing destination error/not-found | Validation never promised availability |
| Reload or reconnect | Stored row authoritative | List equals completed message | Same answer, citations, author | No reconstruction from phase/model text |
| 360 CSS-pixel table | Content/citation unchanged | Unchanged | Local horizontal scroll only | Page and message do not overflow |

## Test Inventory and Proposed Delta

No test is added for this draft prose. The future implementation extends existing behavior seams:

| Invariant | Nearest existing gate | Proposed extension |
| --- | --- | --- |
| Markdown features, raw HTML, half-open fence, table containment, typography | `frontend/src/components/chat/MessageThread.test.tsx` | Extend the existing assistant-message suite, using behavior and DOM semantics rather than source scans |
| Citation router navigation and external-link attributes | `frontend/src/lib/shell-capability.test.ts` plus `MessageThread.test.tsx` | Extend shell resolution for the shared manifest and exercise `CitationRow` through the thread |
| `sources` input compatibility, invalid filtering, server-derived author | `tests/core_tools/test_conversation_reply.py` | Extend the registered-tool boundary; do not add a second source-text guard |
| Persisted citations, author, backfill, downgrade | `tests/integration/test_conversation_reply_db.py` and `tests/migrations/test_dashboard_messages_sources_migration.py` | Extend real-Postgres write/read and migration round-trip coverage |
| Message list response projection | `tests/api/test_conversations.py` | Extend existing response-model/list cases |
| `message_complete` equality and reconnect | `tests/api/test_conversations.py` | Extend existing sources and persisted-reply streaming cases |
| Contract manifest parity | `frontend/src/lib/shell-capability.test.ts` or one existing contract-gate home selected at implementation | One parity gate only; no duplicate frontend and Python source-grep gates |

Proposed implementation delta: extend existing tests first and add only a focused behavior file if
the new answer component cannot be exercised clearly through `MessageThread.test.tsx`. Draft delta:
`Tests: +0 ~0 -0`.

## Risks / Trade-offs

- [A valid route can still reference a missing resource] → Define validation as route-shape safety,
  keep the destination's explicit not-found behavior, and never use verified language.
- [A broad HTTPS policy can link to a hostile site] → Mark externality, require HTTPS without
  embedded credentials, use safe browser attributes, and keep it distinct from internal routes.
- [Mixed-version consumers can mistake labels for structured citations] → Canonical clients read
  only `citations`; `sources` stays a string-only one-way compatibility projection with a removal
  gate.
- [The route manifest can drift from the shell] → One shared contract artifact plus one parity gate
  blocks a server-accepted route that the client cannot navigate.
- [Backfill could falsely assign authors] → Never backfill message `routed_butler`; null is the
  truthful legacy state.
- [Streaming preprocessing can alter copied content] → Synthetic fence text exists only in the
  render input; persisted and copied content remains the original string and reconciles on
  completion.
- [Markdown dependencies expand the frontend supply chain] → Admit only direct maintained parser
  dependencies needed for an AST/GFM path, pin through the lockfile, and keep raw HTML support
  disabled.

## Migration Plan

1. Obtain owner sign-off on the exact proposal, design, tasks, and three delta specs. Until then,
   `bu-0ynlk.12` remains blocked and no implementation task is released.
2. Reconcile the active `durable-dashboard-terminal-action-recovery` requirement bodies before
   implementation. If it archives first, rebuild affected conversation deltas against the
   refreshed baseline before continuing.
3. Land the additive schema and server normalization/attribution path, including migration and
   real-Postgres evidence, without changing the frontend renderer yet.
4. Land canonical API/SSE projection and update every repository consumer to tolerate and prefer
   `citations` plus nullable message `routed_butler`.
5. Land the safe renderer, citation navigation, attribution, disclosure, and narrow-viewport
   behavior behind the canonical fields.
6. Run the implementation verification in `tasks.md`, deploy only under separately granted
   authority, and observe one rollback window.
7. Remove the legacy response projection and then the retained DB column only after consumer and
   rollback evidence says it is safe.

Draft rollback has no runtime effect: revert this change. Implementation rollback follows Decision
6 and never requires fabricated author or target backfill.

## Independent Review Target

Review the exact commit containing:

- `proposal.md`
- `design.md`
- `tasks.md`
- `specs/dashboard-chat-ui/spec.md`
- `specs/dashboard-conversations/spec.md`
- `specs/dashboard-design-language/spec.md`

Security review concentrates on markdown/URL safety, citation authority, content-blind rejection,
and server-derived authorship. API review concentrates on one canonical shape, additive migration,
stored/SSE/list equivalence, mixed-version compatibility, and active-change overlap. UX review
concentrates on readable semantic typography, keyboard/focus behavior, attribution honesty,
disclosure hierarchy, and 360 CSS-pixel containment.

## Owner Decisions

One owner decision remains: adopt or reject this exact proposed behavior and compatibility policy.
There are no unresolved implementation alternatives hidden for a worker to choose. Approval of the
spec authorizes later planning only; it does not authorize implementation, migration, deployment,
release, or broader chat changes.
