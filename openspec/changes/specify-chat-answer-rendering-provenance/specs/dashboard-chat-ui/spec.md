## MODIFIED Requirements

### Requirement: Message Thread Display

The active conversation SHALL render as a scrollable message thread with user and assistant messages differentiated by alignment and styling. Assistant answers SHALL use one safe rendering path for stored and streaming content, and SHALL present citations and authorship without implying trust the server cannot establish.

ID: REQ-dashboard-chat-ui-004
Source: heart-and-soul/vision.md § What Butlers Is Not (Not an experiment); dashboard-design-language § Type System, Voice Surface, Butler Letter-Mark, and Interaction Affordances; design.md Decisions 1-4
Scope: v1-mandatory

#### Scenario: User message rendering

- **WHEN** a user message is displayed in the thread
- **THEN** it renders right-aligned with `bg-primary text-primary-foreground` styling
- **AND** it shows the message content and a relative timestamp below

#### Scenario: Assistant message rendering

- **WHEN** an assistant message is displayed in the thread
- **THEN** it renders left-aligned with `bg-muted` styling
- **AND** sanitized markdown renders headings, lists, inline code, emphasis, blockquotes, fenced code, and tables while raw HTML and executable markup render no active element
- **AND** answer prose uses the Voice type role, structural labels use Body, and code plus tabular numeric cells use Mono with tabular numerals
- **AND** butler, time, session, model, token, duration, and cost metadata follow the attribution and disclosure scenarios below

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

#### Scenario: Per-message butler attribution

- **WHEN** an assistant message carries a non-null server-derived `routed_butler`
- **THEN** its attribution line renders the existing `ButlerMark`, the butler name, and the relative message time
- **AND** a non-null `session_id` adds a keyboard-focusable `Session ->` navigation link beside that attribution
- **AND** the line describes only that message and does not infer authorship from the conversation's sticky route or a live phase event

#### Scenario: Unknown message author is not guessed

- **WHEN** an assistant message has `routed_butler = null`, including a legacy or non-session-authored row
- **THEN** the thread renders no butler mark or butler name for that message
- **AND** it does not substitute the conversation owner, sticky `routed_butler`, current route phase, model name, or session lookup as the author
- **AND** any available timestamp, error, and session navigation remain usable without an attribution claim

#### Scenario: Secondary details are disclosed accessibly

- **WHEN** an assistant message has model, token, duration, or cost details
- **THEN** one disclosure control contains those secondary details and is closed by default
- **AND** the control exposes its expanded state, has an accessible name, and toggles by keyboard
- **AND** the primary butler, time, and available session navigation remain visible without opening it

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
