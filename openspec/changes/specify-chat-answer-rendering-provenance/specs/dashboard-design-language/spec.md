## MODIFIED Requirements

### Requirement: Type System
Pages SHALL use only the three type families — no page invents a fourth: **Inter Tight** (everything UI —
display, body, labels, interface numbers), **Source Serif 4** (the system's *voice* — LLM-written
assistant answers and elaborations, empty-state lines, "why this shape" prose), and **JetBrains Mono** (times, IDs,
deltas, KPI numbers, eyebrows, code, file paths). The serif/sans split is meaningful: sans is the
system speaking in data, serif is the system speaking in sentences. Forbidden primary faces:
Inter (non-Tight), Roboto, Arial, Helvetica, Fraunces, `system-ui`.

The type scale SHALL be:

| Role        | Family   | Size  | Weight | Tracking | Leading |
|-------------|----------|-------|--------|----------|---------|
| Display     | sans     | 44px  | 500    | -0.025em | 1.08    |
| Title       | sans     | 24px  | 500    | -0.015em | 1.2     |
| Body        | sans     | 14px  | 400    | normal   | 1.5     |
| Body small  | sans     | 13px  | 400    | normal   | 1.5     |
| Voice       | serif    | 16px  | 400    | normal   | 1.6     |
| Eyebrow     | mono     | 10px  | 400    | 0.14em   | 1.0     |
| Mono inline | mono     | 11px  | 400    | normal   | 1.4     |

Display weight is 500, never 700 — bold display is loud; tight tracking does the work weight
would do. An assistant answer uses Voice for narrative prose, Body for interface labels and
structural metadata, and Mono for code, identifiers, timestamps, and tabular numeric cells.

ID: REQ-dashboard-design-language-001
Source: dashboard-design-language § Type System and Voice Surface; dashboard-chat-ui § Message Thread Display; design.md Decision 5
Scope: v1-mandatory

#### Scenario: Display headlines are medium weight
- **WHEN** a diff adds a display headline
- **THEN** it uses weight 500 (no `font-bold` / `font-weight: 700` on display text)

#### Scenario: No fourth family
- **WHEN** a page sets a font family
- **THEN** it resolves to Inter Tight, Source Serif 4, or JetBrains Mono

#### Scenario: Assistant answer type roles remain semantic

- **WHEN** an assistant answer renders narrative prose, headings or labels, code, timestamps, or numeric table cells
- **THEN** narrative prose uses Voice while headings, labels, controls, and structural metadata use the applicable sans Body or Title role
- **AND** code, identifiers, timestamps, and numeric table cells use Mono
- **AND** numeric cells retain tabular numerals

### Requirement: Voice Surface
The **Voice** — a headline plus serif paragraph, or assistant-answer narrative prose — SHALL be a distinct surface type reserved for places
the system is literally speaking in sentences: assistant answers, the Overview briefing, empty states ("Nothing
waiting."), and "why this shape" glosses. Voice is serif italic for empty states and serif roman
for briefings and assistant-answer prose. It is never decorative: adding a serif paragraph because a page feels empty is a
violation. Controls, citations, attribution, timestamps, status, and other interface metadata remain in their applicable Body or Mono role.

ID: REQ-dashboard-design-language-002
Source: dashboard-design-language § Voice Surface; dashboard-chat-ui § Message Thread Display; design.md Decision 5
Scope: v1-mandatory

#### Scenario: Voice reserved for sentences
- **WHEN** a serif paragraph appears on a surface
- **THEN** it is assistant-answer narrative, a briefing, an empty state, or an explanatory gloss — not filler for visual balance
- **AND** interactive controls, citation labels, attribution, and operational metadata do not inherit Voice styling merely because they are adjacent to narrative prose
