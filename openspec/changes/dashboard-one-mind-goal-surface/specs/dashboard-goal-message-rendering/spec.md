## Purpose

This capability specifies the future D19 dashboard goal message rendering owner experience. It preserves the complete source-bound functional contracts below, remains subject to RFC 0038 and owner-controlled M6 integration, and conveys no current runtime or capability authority.

## ADDED Requirements

### Requirement: Goal reply citation normalization and trust

The server SHALL normalize the existing `conversation_reply(..., sources=...)` input into one canonical `citations` representation before persistence. A canonical citation SHALL contain a non-empty plain-text `label`, a nullable `target`, and exactly one `kind` of `internal`, `external`, or `unlinked`. Citation labels SHALL NOT be parsed as markdown. A structurally valid citation establishes only a safe navigation target, never the truth, completeness, availability, or evidentiary quality of the model's claim. Research audit questions and citation labels SHALL remain plain untrusted data. A broker ledger reference or canonical target establishes safe navigation only; this D19 display SHALL not adopt pending broker citation handles, live exposed-call permission, third-party cached ceilings, answer-text retention or a notify/Drive Docs link exception.

ID: REQ-dashboard-goal-message-rendering-001
Source: REQ-dashboard-conversations-010; RFC 0038 D19; design.md; bu-6qaju2.4 original audit contract and D19 owner note (2026-10-15); bu-0rbswe read-model contract; design.md
Scope: post-v1

#### Scenario: Structured internal source is accepted by server authority

- **WHEN** the goal reply publisher receives a `sources` entry with a non-empty label and an internal target matching a server-held citation-eligible shell route pattern
- **THEN** the server normalizes it to `{label, target, kind: "internal"}` and persists it in `citations`
- **AND** the client cannot expand the accepted route set by changing its own route registry or request payload
- **AND** target validation does not assert that the referenced resource exists or proves the answer text

#### Scenario: Structured external source is accepted safely

- **WHEN** the goal reply publisher receives a `sources` entry with a non-empty label and an absolute HTTPS target with no embedded credentials
- **THEN** the server normalizes it to `{label, target, kind: "external"}` and persists it in `citations`
- **AND** HTTP, script, data, file, protocol-relative, credential-bearing, or malformed targets are invalid
- **AND** acceptance does not assert that the remote destination is reachable or trustworthy

#### Scenario: Legacy source string remains compatible but unlinked

- **WHEN** the goal reply publisher receives an existing non-empty string entry in `sources`
- **THEN** the server normalizes it to `{label: <trimmed string>, target: null, kind: "unlinked"}`
- **AND** the string cannot become a link by resembling a path or URL
- **AND** the reply success shape remains compatible with the existing tool contract

#### Scenario: Invalid structured targets are removed content-blindly

- **WHEN** a non-empty `sources` input contains at least one valid entry and one structured entry with a malformed, unsafe, or non-allowlisted target
- **THEN** only the valid normalized entries are persisted in `citations`
- **AND** the server emits a content-blind warning with reason code and rejected count, without labels, targets, answer text, request arguments, session IDs, or sensitive payloads
- **AND** the tool success response does not echo the rejected entry

#### Scenario: Empty blank or over-budget sources are rejected

- **WHEN** `sources` is explicitly empty, contains any blank label, exceeds an item or total payload budget, or leaves no usable entry after structured-target validation
- **THEN** no assistant message is inserted
- **AND** the tool returns a structured error directing the caller to supply usable sources or omit `sources` and give an honest decline
- **AND** no rejected label or target is copied into logs, telemetry, or the error response

#### Scenario: Omitted sources preserve refusal and non-answer semantics

- **WHEN** the goal reply publisher is called without `sources` for a confirm loop, action proposal, bug report, or honest answer decline
- **THEN** the message persists with `citations = null` and `sources = null`
- **AND** omission is not relabeled as grounded, invalid, or verified

#### Scenario: Research evidence is data and navigation only

- **WHEN** a research ledger question or citation contains markup, instructions or a source target
- **THEN** question/labels are rendered as text, only actual server-normalized eligible targets become audit navigation, rejected targets remain inert and no returned instruction, citation or digest satisfies approval, authorization or source-truth checks.
- **AND** ordinary owner audit navigation does not broaden any delivery surface’s stricter source/link contract or settle pending D20 choices.


### Requirement: Goal thread safe message display

The active conversation SHALL render as a scrollable message thread with user and assistant messages differentiated by alignment and styling. Assistant answers SHALL use one safe rendering path for stored and streaming content, and SHALL present citations and authorship without implying trust the server cannot establish.
Public voice SHALL be one assistant. Any region mark/name in the clauses below is secondary server-provenance evidence in a visible provenance line separate from the one Assistant speaker label. It is not hidden in the closed details disclosure and never becomes a per-region public speaker identity. Unknown historical provenance SHALL remain unknown; nullable historical session links remain historical rather than forged invocation links.

ID: REQ-dashboard-goal-message-rendering-002
Source: REQ-dashboard-chat-ui-004; RFC 0038 D19; design.md
Scope: post-v1

#### Scenario: User message rendering

- **WHEN** a user message is displayed in the thread
- **THEN** it renders right-aligned with `bg-primary text-primary-foreground` styling
- **AND** it shows the message content and a relative timestamp below

#### Scenario: Assistant message rendering

- **WHEN** an assistant message is displayed in the thread
- **THEN** it renders left-aligned with `bg-muted` styling
- **AND** sanitized markdown renders headings, lists, inline code, emphasis, blockquotes, fenced code, and tables while raw HTML and executable markup render no active element
- **AND** answer prose uses the Voice type role, structural labels use Body, and code plus tabular numeric cells use Mono with tabular numerals
- **AND** region, time, invocation, model, token, duration, and cost metadata follow the attribution and disclosure scenarios below

#### Scenario: Streaming markdown remains safe and stable

- **WHEN** an assistant answer is rendered before the stream has completed, including an unterminated fenced code block or other half-open markdown construct
- **THEN** the partial content uses the same sanitized renderer as the stored answer and cannot create raw HTML, an executable element, or an unsafe link
- **AND** an unterminated fence renders as code without breaking the surrounding layout
- **AND** when later tokens close the construct, the answer re-renders into its completed structure without duplicated or lost text
- **AND** `message_complete` renders the persisted content byte-for-byte as renderer input, with no duplicated, lost, or synthetic fence text; rendered semantics remain governed by the allowed-markdown clauses above

#### Scenario: Internal citation navigation

- **WHEN** an assistant message carries a citation whose `kind` is `internal`
- **THEN** the citation renders as a labeled, keyboard-focusable link and activates through the dashboard router
- **AND** activation performs in-SPA navigation to the server-validated target without assigning `window.location` or causing a full page reload
- **AND** focus is visible according to the Interaction Affordances contract

#### Scenario: External citation navigation

- **WHEN** an assistant message carries a citation whose `kind` is `external`
- **THEN** the citation renders as a labeled HTTPS link with `rel="noopener noreferrer"`
- **AND** the external destination is visually and accessibly distinguishable from an internal dashboard destination
- **AND** the citation does not claim that the destination's content or availability was verified

#### Scenario: Unlinked legacy citation remains honest

- **WHEN** an assistant message carries a citation whose `kind` is `unlinked` or whose once-valid destination is unavailable when followed
- **THEN** an unlinked citation renders as text rather than as a fabricated destination
- **AND** a destination that later returns a not-found or unavailable state uses that destination surface's existing recovery behavior
- **AND** neither state is labeled as verified evidence

#### Scenario: Per-message region attribution

- **WHEN** an assistant message carries a non-null server-derived `author_region`
- **THEN** the primary speaker remains Assistant with the relative message time; a separate always-visible provenance line renders the existing RegionMark and the server-derived region name
- **AND** a non-null `invocation_id` adds an always-visible keyboard-focusable Invocation navigation door beside the message provenance/time, independently of the closed details disclosure
- **AND** the line describes only that message and does not infer authorship from the conversation's sticky route or a live phase event

#### Scenario: Unknown message author is not guessed

- **WHEN** an assistant message has `author_region = null`, including a legacy or non-invocation-authored row
- **THEN** the thread retains its one Assistant speaker and renders no region mark, region name or empty inferred provenance line for that message
- **AND** it does not substitute the conversation owner, sticky `author_region`, current route phase, model name, or invocation lookup as the author
- **AND** the relative timestamp, available error and an actual stored invocation navigation door remain visible/usable without an attribution claim, independently of the closed details disclosure

#### Scenario: Secondary details are disclosed accessibly

- **WHEN** an assistant message has model, token, duration, or cost details
- **THEN** one disclosure control contains those secondary details and is closed by default
- **AND** the control exposes its expanded state, has an accessible name, and toggles by keyboard
- **AND** the single Assistant speaker, relative time, actual visible secondary region provenance (when non-null), and stored invocation navigation remain visible when the disclosure is closed, opened or toggled

#### Scenario: Narrow viewport contains wide answers

- **WHEN** an answer containing a table or long code line is displayed at a 360 CSS-pixel viewport
- **THEN** the message and page do not acquire horizontal overflow
- **AND** each wide table or code block scrolls within its own named region
- **AND** the overflow region is keyboard focusable and keyboard scrollable
- **AND** citation and disclosure controls retain at least the active Viewport and Modality Contract's coarse-pointer target floor

#### Scenario: Auto-scroll to latest message

- **WHEN** a new message is added to the thread (user or assistant)
- **THEN** the thread scrolls to the bottom to show the latest message
- **AND** if the user has manually scrolled up (more than 100px from bottom), auto-scroll is suppressed until they scroll back to the bottom

#### Scenario: Tool call visibility

- **WHEN** an assistant message includes `tool_calls`
- **THEN** a collapsible "Tool calls" section renders below the message content
- **AND** each tool call shows the tool name and a truncated argument summary
- **AND** clicking a tool call expands to show full arguments and result as formatted JSON

#### Scenario: Error message rendering

- **WHEN** an assistant message has a non-null `error` field
- **THEN** the message renders with a `destructive` border-left accent
- **AND** the error text is shown below any partial content in `text-destructive text-sm`

## Source References

- Non-Negotiable Rules 1, 3, 4 and 6 in about/heart-and-soul/vision.md.
- RFC 0038 D19 and owner decision 11; further exact decisions and current source boundaries remain the per-requirement Source fields.
- Requirement-level source: REQ-dashboard-conversations-010; RFC 0038 D19; design.md
- Requirement-level source: REQ-dashboard-chat-ui-004; RFC 0038 D19; design.md
- Amendment source: bu-6qaju2.4 original audit contract and D19 owner note (2026-10-15); bu-0rbswe read-model contract; design.md
