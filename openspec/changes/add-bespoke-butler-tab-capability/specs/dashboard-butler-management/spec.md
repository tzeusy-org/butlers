## MODIFIED Requirements

### Requirement: Bespoke resident tab per domain butler

Each domain butler SHALL support at most one bespoke resident-mode tab (zero or
one — not zero or more). A butler that does not have a domain-specific surface
MUST NOT invent a bespoke tab. Any bespoke tab MUST conform to the nine rules
below.

The following nine rules govern bespoke tabs:

**Rule 1 — Cardinality.** Each butler MAY have at most one bespoke tab. No
butler shall carry two or more bespoke tabs simultaneously in either mode.

**Rule 2 — Insertion point.** In the tab bar, the bespoke tab MUST appear
immediately after the Memory tab and before any operator-only tabs. In resident
mode this places it at position 8 (Overview, Activity, Logs, Approvals, Spend,
Config, Memory, <Bespoke>). In operator mode it appears at position 11
(Overview, Sessions, Config, Skills, Schedules, Trigger, MCP, State, CRM,
Memory, <Bespoke>).

**Rule 3 — Label.** The bespoke tab label is butler-specific and registered in
the canonical per-butler label table (see Requirement: Per-butler bespoke tab
label registry below). Labels MUST be sentence-case, single-word preferred, and
contain no punctuation. Multi-word labels are permitted only when no single-word
label is accurate (e.g., a hypothetical "Task list" would be acceptable;
"task-list" or "Task List" would not).

**Rule 4 — Discovery mechanism.** Bespoke tab presence is determined by a
hardcoded conditional on the butler name in
`frontend/src/pages/ButlerDetailPage.tsx` and
`frontend/src/pages/butler-detail-tabs.ts`. Discovery MUST NOT be driven by
`butler.toml` fields or runtime API responses. This matches the existing
conditional pattern (`showContactsTab = name === "relationship"`, etc.).

**Rule 5 — Visual contract.** Bespoke tab body content MUST conform to the
Panel grid and KPI quartet rules defined by the sibling resident-tab visual
contract change (bu-iuol4.1). Pages MUST NOT reinvent card layout, spacing
tokens, or KPI quartet shape. All bespoke tab bodies use the same Panel grid
shell as resident base-tab bodies.

**Rule 6 — Loading.** Bespoke tab body components MUST be lazy-loaded via React
`lazy()` and wrapped in `<Suspense fallback={<TabFallback label="..." />}>`.
The `<TabFallback>` component is the shared fallback defined in
`ButlerDetailPage.tsx`. Inline tab body components (non-lazy) are not permitted
for bespoke tabs.

**Rule 7 — Offline/paused fallback.** When the butler is paused or quarantined,
the bespoke tab MUST still render. It MUST display an appropriate empty state:
a centered, muted sentence-case message describing the unavailability (e.g.,
"No data available while this butler is paused."). The empty state MUST NOT use
em-dashes, celebration copy, or title-case headings per voice rules.

**Rule 8 — Mode independence.** Bespoke tabs are visible in both resident mode
and operator mode. They are appended after Memory in both mode tab bars. Deep
links to a bespoke tab key MUST NOT force a mode switch; the bespoke tab is
reachable from either mode.

**Rule 9 — Switchboard opt-out.** The switchboard butler explicitly MUST NOT
carry a resident bespoke tab. Its two existing tabs — Routing Log and Registry —
are operator-oriented surfaces that predate the resident vocabulary and serve
ingress triage, not resident self-service. Those two tabs are preserved unchanged
and are not reclassified as bespoke.

#### Scenario: Bespoke tab appears in resident mode tab list

- **WHEN** a domain butler (e.g., `relationship`) is viewed in resident mode
- **THEN** the tab bar SHALL show: Overview, Activity, Logs, Approvals, Spend,
  Config, Memory, <Bespoke label> — in that order
- **AND** the bespoke tab label (e.g., "Contacts") MUST be sentence-case and
  match the butler's registered bespoke label

#### Scenario: Bespoke tab appears in operator mode tab list

- **WHEN** a domain butler (e.g., `relationship`) is viewed in operator mode
- **THEN** the tab bar SHALL show: Overview, Sessions, Config, Skills, Schedules,
  Trigger, MCP, State, CRM, Memory, Contacts — in that order
- **AND** operator-only tabs (Models, if exposed) appear after the bespoke tab

#### Scenario: Bespoke tab is lazy-loaded

- **WHEN** the bespoke tab is selected for the first time
- **THEN** its body component MUST be loaded on demand via React `lazy()`
- **AND** a `<Suspense fallback={<TabFallback label="..." />}>` MUST wrap the
  component during loading
- **AND** the fallback MUST show the butler-specific label text

#### Scenario: Bespoke tab empty state when butler offline

- **WHEN** the butler status is `paused` or eligibility is `quarantined`
- **AND** the bespoke tab is selected
- **THEN** the bespoke tab body MUST still render
- **AND** it MUST display a centered, muted empty-state message in sentence case
  (e.g., "No data available while this butler is paused.")
- **AND** the message MUST NOT contain em-dashes, title-case headings, or
  celebratory copy

#### Scenario: Deep link to bespoke tab does not force mode switch

- **WHEN** a user navigates to `/butlers/relationship?tab=contacts`
- **AND** the stored mode is either `resident` or `operator`
- **THEN** the bespoke `contacts` tab MUST be selected in the current mode
  without switching to the other mode

#### Scenario: Switchboard has no resident bespoke tab

- **WHEN** the butler name is `switchboard`
- **THEN** the tab bar in resident mode MUST contain only the seven resident
  base tabs plus Routing Log and Registry — no additional bespoke tab
- **AND** the tab bar in operator mode MUST contain the ten operator base tabs
  plus Routing Log and Registry — no additional bespoke tab
- **AND** Routing Log and Registry MUST remain unchanged in label, position, and
  visibility

#### Scenario: Single bespoke tab per butler

- **WHEN** any domain butler is rendered
- **THEN** at most one tab beyond Memory SHALL be present that is classified as
  a bespoke tab for that butler
- **AND** no butler SHALL render two or more bespoke tabs simultaneously

## ADDED Requirements

### Requirement: Per-butler bespoke tab label registry

Each domain butler that carries a bespoke tab SHALL use the label registered in
the table below. The labels in this table are normative; any implementation that
uses a different label for a listed butler is non-conformant. Switchboard is
explicitly absent — it carries no resident bespoke tab (Rule 9).

| Butler       | Bespoke tab label | Justification                                                                   |
|-------------|-------------------|---------------------------------------------------------------------------------|
| chronicler  | Timelines         | Core identity: "retrospective time butler" that projects events and episodes.   |
| education   | Reviews           | Spaced-repetition review sessions are the primary user action; Anki integration is explicitly rejected by the manifesto ("We do not connect to Coursera, Anki, Canvas…"), so "Decks" is ruled out. |
| finance     | Finances          | Direct mapping to the butler's domain: financial clarity over inbox noise.      |
| general     | Collections       | The manifesto's organizing metaphor: "Collections let you group related things together." |
| health      | Measurements      | Health butler leads with measurement tracking; the existing "Health" label is generic and collides with the butler name — "Measurements" is the primary tracking surface. |
| home        | Devices           | Device orchestration and monitoring is the bespoke surface: "Monitor device health." |
| lifestyle   | Taste             | Manifesto central concept: "Taste is autobiography" — the butler is the keeper of your taste. |
| messenger   | Conversations     | Delivery health surface showing per-conversation send/receive outcomes; NOT a user-facing chat UI. |
| qa          | Investigations    | Primary operator surface: active and historical investigation dispatch records. |
| relationship| Contacts          | Contact management is the primary bespoke surface: "A living database of the people in your life." |
| travel      | Trips             | Trip-centric organization: "See your complete trip timeline" is the core value proposition. |

Labels are sentence-case. No em-dashes. No exclamation marks. No title-case.
Switchboard is absent from this table because it carries no resident bespoke tab.

#### Scenario: Each butler renders its registered bespoke tab label

- **GIVEN** the per-butler bespoke tab label registry above
- **WHEN** a domain butler from the registry is viewed in resident mode or
  operator mode
- **THEN** the bespoke tab trigger SHALL display exactly the label registered
  for that butler (e.g., `Timelines` for chronicler, `Investigations` for qa)
- **AND** the label MUST be sentence-case and contain no punctuation
- **AND** no butler in the table SHALL use a label that differs from the one
  registered here

#### Scenario: Switchboard does not render a bespoke tab from the registry

- **WHEN** the butler name is `switchboard`
- **THEN** the tab bar SHALL NOT contain any label from the per-butler registry
- **AND** the only tabs beyond the base set are the existing operator-oriented
  tabs: Routing Log and Registry

#### Scenario: New butlers (general, lifestyle, messenger, qa) include bespoke tabs

- **WHEN** any of `general`, `lifestyle`, `messenger`, or `qa` is viewed
- **THEN** the bespoke tab SHALL appear at position 8 in resident mode
  (immediately after Memory, before any operator-only tabs)
- **AND** the labels SHALL be exactly: `Collections` (general), `Taste`
  (lifestyle), `Conversations` (messenger), `Investigations` (qa)
- **AND** the health butler bespoke tab SHALL be relabeled from `Health` to
  `Measurements` to match the registry

## Source References

- Gate B decision (bu-41p8z): operator/resident mode B2 toggle.
- `redesign-detail-page-tab-vocabulary`: settled parent change; defines the
  resident and operator base tab sets and the conditional-tab rule that gates
  this work.
- `redesign-butler-detail-no-hero`: settled no-hero rule; bespoke tab body
  content MUST NOT add a page-level hero tier.
- Sibling change bu-iuol4.1: resident-tab visual contract (Panel grid + KPI
  quartet rules) that all bespoke tab bodies must conform to.
- Non-Negotiable 2 (design-language.md): "The `Page` is a primitive." Bespoke
  tab bodies must not reinvent the Page shell chrome or introduce their own
  card layout primitives.
- Non-Negotiable 6 (design-language.md): "No em-dashes in prose." Bespoke tab
  empty-state copy must comply.
