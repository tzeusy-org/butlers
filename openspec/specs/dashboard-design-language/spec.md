# Dashboard Design Language (Dispatch)

## Purpose

Defines **Dispatch**, the binding visual, interaction, and copywriting language for every
Butlers dashboard surface. The thesis: *a butler announcing, not a chatbot reporting*. When a
question arises that this spec does not cover, ask: would a discreet, competent butler do this?
They wouldn't gradient-mesh anything, they wouldn't exclaim, they wouldn't decorate a quiet day
with an empty-state illustration. They'd stand in the doorway and tell you what's true, in order
of importance, then leave.

This spec is the canonical home of the design language (graduated from the former
`pr/overview/DESIGN_LANGUAGE.md` and its duplicates). It is the WHAT layer; the WHY —
principles, settled owner decisions, and drift analysis — is the doctrine document
`about/heart-and-soul/design-language.md`, which explicitly delegates tokens, components, and
pages to capability specs like this one. Two settled doctrine decisions bind this spec's
interpretation: **dark-primary with light fallback** (dark is the designed-first mode; when a
trade-off is forced, the dark experience wins) and the **light-mode WCAG AA accessibility
floor** (AA contrast minimums for the light fallback; AAA is desirable but never at the cost of
dark-mode palette coherence).

Exact token values are implemented in `frontend/src/index.css`, which is normative for values;
the tables below are the reference snapshot and the *roles and usage rules* they carry are
binding. Component primitives (Eyebrow, KpiStrip, AttentionList, Voice, ButlerMark, StateDot,
Section) live under `frontend/src/`. Portable execution material (JSX patterns, page recipes,
paste-ready tokens, review checklist) lives in the `butlers-redesign-prompt` skill's
`references/dispatch-kit/`.

For design review, this spec is the *project design system* in the sense used by the
`th-design` skill's design-bar: where this spec speaks, it overrides generic design biases;
where it is silent, design-bar's defaults apply.

The North Star test for every page: **if the system were a person handing me a sheet of paper,
would I trust the typography of that sheet?** Newspapers from 1965 pass. Bank statements from a
private bank pass. Default Bootstrap dashboards and gradient hero sections do not.
## Requirements
### Requirement: Composure Doctrine
Every dashboard surface SHALL obey five inviolable rules: (1) **Composure is the brand** — the
page reads calm even when the system is broken; color and motion appear only when state demands.
(2) **Type is the system** — hierarchy comes from type and rules, not shadows or fills.
(3) **Surfaces, not cards** — one elevation; structure is rules and rhythm. (4) **Every element
earns its place against state** — if nothing is happening, the section drops its borders or
shows a single serif-italic line; nothing is decorated. (5) **One affordance per signal** —
status is exactly one of: dot, sliver, numeral, color. Never a word like "active"; never two of
the four together.

#### Scenario: Quiet state removes chrome
- **WHEN** a section has no active content (no items, no alerts, no pending work)
- **THEN** the section renders either without borders or as a single serif-italic sentence
- **AND** no illustration, mock content, or decorative filler is added

#### Scenario: Status rendered as a single affordance
- **WHEN** any element communicates status
- **THEN** it uses exactly one of dot, sliver, numeral, or color
- **AND** it does not pair two of those affordances, and does not label the state with a word like "active"

### Requirement: Surface Palette
Surfaces SHALL use only the canonical surface tokens, with dark mode as the canonical theme and
a paper-warm light variant (faint oklch hue-85 cast — paper, not screen; never true neutral):

| Token            | Dark                       | Light                       | Use |
|------------------|----------------------------|-----------------------------|-----|
| `--bg`           | `oklch(0.145 0 0)`         | `oklch(0.985 0.003 85)`     | Page |
| `--bg-elev`      | `oklch(0.205 0 0)`         | `oklch(1 0 0)`              | Code blocks, tooltips |
| `--bg-deep`      | `oklch(0.115 0 0)`         | `oklch(0.965 0.005 85)`     | Sidebar, sticky bars |
| `--fg`           | `oklch(0.985 0 0)`         | `oklch(0.18 0 0)`           | Primary text |
| `--mfg`          | `oklch(0.708 0 0)`         | `oklch(0.46 0 0)`           | Muted text, eyebrows |
| `--dim`          | `oklch(0.55 0 0)`          | `oklch(0.62 0 0)`           | Tertiary text, deltas |
| `--border`       | `oklch(1 0 0 / 0.10)`      | `oklch(0.922 0 0)` (opaque, resolves ≈ 10% black) | Hairline rules |
| `--border-soft`  | `oklch(1 0 0 / 0.06)`      | `oklch(0 0 0 / 0.05)`       | List separators |
| `--border-strong`| `oklch(1 0 0 / 0.18)`      | `oklch(0 0 0 / 0.20)`       | Buttons, link underlines |

#### Scenario: No invented colors
- **WHEN** a diff introduces a color reference (`oklch(`, `#`, `rgb(`, `hsl(`)
- **THEN** the reference resolves to an existing token defined in `frontend/src/index.css`
- **AND** raw color literals appear only in the token definition file itself

### Requirement: State Color Discipline
Exactly three state colors exist — red, amber, green — and they SHALL appear only when state
demands, never as decoration:

| Role     | Dark                       | Light                      | Used for |
|----------|----------------------------|----------------------------|----------|
| `--red`  | `oklch(0.685 0.250 29.2)`  | `oklch(0.627 0.257 29.2)`  | High severity, error, blockers, reauth |
| `--amber`| `oklch(0.810 0.185 84.0)`  | `oklch(0.769 0.189 84.0)`  | Medium severity, degraded |
| `--green`| `oklch(0.790 0.195 148)`   | `oklch(0.50 0.140 152)`    | Healthy, positive delta |

(Values align with the `--severity-*` scale in `frontend/src/index.css`, which is normative.)

A page may show all three only if all three states are actually present. State colors are never
used as brand accents or hover colors, and never on background fills — foreground or border
only — with **one permitted exception**, the attention tint (settled in doctrine,
`about/heart-and-soul/design-language.md` § Attention-tint exception): a row or panel that
*demands human attention right now* may carry a 4–7% alpha background tint in red (critical) or
4–6% in amber (warning), paired with a 2px left rail in the same color; tint + rail travel
together as a single signal unit, routine states get neither, and a row already carrying a
`Sev` glyph or other affordance does not also receive the tint (one affordance per signal).
Implemented by `.attention-row` with `data-tone`.

#### Scenario: State color on foreground or border only
- **WHEN** a diff tints an element with a state color
- **THEN** the tint applies to text, glyph, or border — never a background fill (no `bg-red-500/10`-style patterns)
- **UNLESS** the element is a demands-attention-now row using the `.attention-row` tint+rail pattern within its alpha bounds

#### Scenario: Attention tint reserved for live attention states
- **WHEN** a row renders the attention tint+rail
- **THEN** the row's state is one that demands a human decision or repair now (open approval, reauth required, model error, spend near ceiling, recent webhook failure)
- **AND** the row carries no second status affordance

#### Scenario: State color only when state is present
- **WHEN** a page renders red, amber, or green
- **THEN** each rendered state color corresponds to a live state of that severity on the page

### Requirement: Butler Category Hues
Each butler SHALL have one assigned identity hue from `--category-1..12` (defined in
`frontend/src/index.css`), and the hue SHALL appear **only on the butler's letter-mark** — the
colored squircle with the butler's initial — never on backgrounds, borders, buttons, headers, or
anywhere else. The mapping is generated from `KNOWN_BUTLERS` and `CATEGORY_VARS` in
`frontend/src/components/ui/ButlerMark.tsx` — that module is the single source of truth; this
table MUST be regenerated by hand from it whenever the roster or ramp changes. Canonical mapping
(bu-86c4c.6 — extended from 8 to 12 slots so the full 11-butler roster gets distinct hues; slot 12
is unused headroom for the next butler added to the roster):

| Butler         | Token           |
|----------------|-----------------|
| chronicler     | `--category-1`  |
| education      | `--category-2`  |
| finance        | `--category-3`  |
| general        | `--category-4`  |
| health         | `--category-5`  |
| home           | `--category-6`  |
| lifestyle      | `--category-7`  |
| messenger      | `--category-8`  |
| qa             | `--category-9`  |
| relationship   | `--category-10` |
| travel         | `--category-11` |
| _(unassigned)_ | `--category-12` |

#### Scenario: Category hue confined to letter-marks
- **WHEN** a diff references `var(--category-`
- **THEN** every match is inside a `ButlerMark` component or its style block

### Requirement: Non-butler Categorical and Decorative Hue Ramp
The dashboard SHALL provide `--categorical-1..12` as a separate, theme-aware
token ramp for discrete local categories and decorative data differentiation.
This ramp is not a Butler identity system and is not an operational state
system.

#### Scenario: Labeled non-status categories
- **WHEN** a surface distinguishes a local taxonomy, syntax value type, chart series, or legend item
- **THEN** it uses the `--categorical-*` ramp rather than `--category-*` or state colors
- **AND** the item retains a text label, icon, stable position, or direct data label so color is never the only signal
- **AND** a foreground use clears the WCAG AA text-contrast floor in both supported themes

#### Scenario: Categorical colors do not become chrome
- **WHEN** a diff uses a `--categorical-*` token
- **THEN** it appears only on the category mark, label, border, or data visualization it differentiates
- **AND** it does not become a page background, generic hover color, button treatment, or status indicator

### Requirement: Semantic Visual Role Matrix
Every visual color request SHALL resolve through exactly one semantic role:

| Role | Resolver | Token family | Required signal |
|------|----------|--------------|-----------------|
| Butler identity | `ButlerMark` (private) | `--category-1..12` | letter-mark only |
| Operational state | `StateDot` / `stateColorVar` | `--red`, `--amber`, `--green`, neutral | state affordance |
| Local category | `categoricalHueVar` / `categoricalColor` | `--categorical-1..12` | label, icon, position, or legend |
| Chart series | `chartSeriesColor` / `chartColor` | `--chart-1..5` | series label or legend |
| Owner custom color | `ownerCustomColor` | owner-provided value | owner label or legend |

Identity resolvers SHALL NOT be exported for general consumers. A local
category, chart series, or state SHALL never request a Butler identity token.
The registry in `frontend/src/lib/visual-token-roles.ts` is the executable
source for this table; the table and registry MUST be checked for parity.

#### Scenario: Every categorical and chart use is labeled
- **WHEN** a surface renders a local category or chart series
- **THEN** it provides a text label, icon, stable position, direct data label, or legend
- **AND** color is not the sole carrier of meaning

### Requirement: Type System
Pages SHALL use only the three type families — no page invents a fourth: **Inter Tight** (everything UI —
display, body, labels, interface numbers), **Source Serif 4** (the system's *voice* — LLM-written
elaborations, empty-state lines, "why this shape" prose), and **JetBrains Mono** (times, IDs,
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
would do.

#### Scenario: Display headlines are medium weight
- **WHEN** a diff adds a display headline
- **THEN** it uses weight 500 (no `font-bold` / `font-weight: 700` on display text)

#### Scenario: No fourth family
- **WHEN** a page sets a font family
- **THEN** it resolves to Inter Tight, Source Serif 4, or JetBrains Mono

### Requirement: Tabular Numerals
Every numeric value — costs, counts, deltas, KPI mega-numbers, timestamps, badge digits — SHALL
render with `font-variant-numeric: tabular-nums`. This is what makes lists of numbers scannable
without alignment hacks. Numeric facts (IDs, timestamps, deltas) additionally use the mono family.

#### Scenario: Numbers are tabular
- **WHEN** a component renders a numeric value
- **THEN** the value has `font-variant-numeric: tabular-nums`
- **AND** numeric facts (not measures) use the mono family

### Requirement: Eyebrow Section Titles
Sections SHALL be titled with eyebrows — `10px / mono / uppercase / 0.14em letter-spacing /
muted color` — in lieu of headings. Eyebrows establish rhythm without shouting; section titles do
not use large sans headings.

#### Scenario: Section titled by eyebrow
- **WHEN** a page introduces a titled section
- **THEN** the title renders as a 10px mono uppercase eyebrow with 0.14em tracking in the muted color

### Requirement: Page Shell and Layout
Pages SHALL use the canonical shell: a fixed 56px full-height icon-rail sidebar; a main column
of max-width 1280px centered with `48px 56px` page padding; 56px gutters between major columns.
Pages structured as "what's happening" + "what to look at" use the two-column editorial grid
(`grid-template-columns: 1.4fr 1fr; gap: 56px`) — the left column is the narrative (display
headline, voice paragraph, attention list, KPI strip), the right column the index (quiet lists
with eyebrow titles). Reading widths: display headlines `max-width: 14ch` (forcing the dramatic
line break), voice paragraphs `max-width: 50ch`, lists full column width.

#### Scenario: New page adopts the shell
- **WHEN** a new dashboard page is added
- **THEN** it uses the two-column editorial shell or a single 1280px-max readable column with the same gutter

### Requirement: Density and Spacing
Surfaces SHALL be information-dense without claustrophobia: rule-separated rows (CSS grid of
`time / mark / content / meta` split by hairlines), never cards. Vertical row padding is 8–18px
scaled to importance — attention rows 18px (read), index rows 10px (scanned); `padding: 24px` on
a list item is card thinking and forbidden. All spacing uses multiples of 4px exclusively
(common: 4, 8, 12, 14, 16, 18, 24, 32, 36, 48, 56); no magic numbers.

#### Scenario: Spacing on the 4px scale
- **WHEN** a diff introduces padding, margin, or gap values
- **THEN** every value is a multiple of 4px

### Requirement: List Primitive
Lists SHALL use the rule-separated grid row as the canonical primitive:

```
display: grid;
grid-template-columns: <mark> 1fr <meta>;
gap: 10–18px;
padding: <vertical> 0;
border-bottom: 1px solid var(--border);
```

Canonical variants: **Attention list** (24px sev-glyph / 1fr title+serif-detail / auto action),
**Butler index** (8px status-dot / 1fr name / auto sessions / auto cost), **Next list** (50px
mono-time / 1fr label / auto kind tag), **Sidebar item** (20px icon / 1fr label / auto badge).

#### Scenario: Multi-item lists are rule-separated grids
- **WHEN** a page renders a list of more than one item
- **THEN** rows are separated by hairline rules (not wrapped in cards) using the grid primitive

### Requirement: KPI Strip
KPI strips SHALL be a four-column grid divided by hairline borders, each cell stacking
mono-eyebrow (10px, muted, uppercase), mega-number (32px, sans 500, tracking -0.03em,
tabular-nums), and mono-delta (10px, muted). No background fills, no card chrome.

#### Scenario: KPI cell anatomy
- **WHEN** a KPI strip renders
- **THEN** each cell is eyebrow + mega-number + delta with hairline dividers and no background fill

### Requirement: Button Forms
Buttons SHALL take one of exactly three forms: (1) **Action arrow `→`** in a list row — an underlined word
ending in →, no button chrome — the universal "go look at this" signal; (2) **Pill button** —
`4px 10px / 1px border / 3px radius / mono 11px`, used for filters, scenario picks, theme
toggles; active state is inverted bg/fg, never colored; (3) **Commit button** — same shape as
the pill with `--fg` background and `--bg` text, reserved for committing actions (`Approve`,
`Re-authorize`, `Send`) and used **at most once per surface**. The system has no rounded
gradient CTA anywhere.

#### Scenario: One commit button per surface
- **WHEN** a surface offers committing actions
- **THEN** at most one commit-style button renders on that surface

### Requirement: Kind Tags
Tags and chips SHALL be mono uppercase in the muted color with no background — they label a
kind, they do not celebrate one.

#### Scenario: Tag renders without chrome
- **WHEN** a kind tag renders (e.g. `approval`)
- **THEN** it is mono, uppercase, muted, and has no background fill or border pill

### Requirement: Status Indicators
Status SHALL render through the `StateDot` component (6px circle by default): `ok` → green,
`degraded` → amber, `error` → red, `waiting` → muted neutral. The `Sev` glyph is the same idea
as a 6px square, used inside attention rows where a dot would collide with bullets above.

#### Scenario: Status dot states
- **WHEN** a component reports operational status
- **THEN** it uses StateDot (or Sev in attention rows) with the four canonical states only

### Requirement: Butler Letter-Mark
The `ButlerMark` SHALL be the butler's visual identity and the **only** place butler hues
exist: a 16px square, 4px radius, butler hue, initial at weight 600 sized to 60% of the square. Two
tones: `fill` (solid hue background, white initial — active state) and `neutral` (transparent
background, hue initial, hairline border — default state).

#### Scenario: Letter-mark instead of butler icons
- **WHEN** a butler area would otherwise call for an icon
- **THEN** the letter-mark in the butler hue is used instead

### Requirement: Voice Surface
The **Voice** — a headline plus serif paragraph — SHALL be a distinct surface type reserved for places
the system is literally speaking in sentences: the Overview briefing, empty states ("Nothing
waiting."), and "why this shape" glosses. Voice is serif italic for empty states, serif roman
for briefings. It is never decorative: adding a serif paragraph because a page feels empty is a
violation.

#### Scenario: Voice reserved for sentences
- **WHEN** a serif paragraph appears on a surface
- **THEN** it is a briefing, an empty state, or an explanatory gloss — not filler for visual balance

### Requirement: Process Status Pill
The system SHALL report on its own process (briefing source, cache age, last sync, model
version) via the tiny 9px mono status pill: dot + label + ↻, with exactly three
states — `composing…` (amber), `llm · cached 5m` (green), `templated` (dim) — clickable to
refresh, and always honest about what is rendering.

#### Scenario: Briefing source is honest
- **WHEN** an LLM-composed surface renders from cache or a template fallback
- **THEN** the status pill states the actual source and age; a templated render is never presented as live LLM output

### Requirement: Iconography
Icons SHALL be stroke-only at a single 1.25–1.5px weight, 16×16 viewBox, round caps and joins,
one color (`currentColor`). No fills, no two-tone, no gradients, no soft shadows. **No emoji,
ever** — including empty states.

#### Scenario: Icon conformance
- **WHEN** a diff adds an icon
- **THEN** it is stroke-only, single-weight, `currentColor`, 16×16 — and not an emoji

### Requirement: Motion Vocabulary
The motion vocabulary SHALL be almost none. Only these animations exist:

| Where                              | Duration | Easing                       |
|------------------------------------|----------|------------------------------|
| Briefing paragraph cross-fade      | 200ms    | `cubic-bezier(0.22, 1, 0.36, 1)` |
| Sidebar chevron rotation           | 120ms    | linear                       |
| Theme toggle background fade       | 200ms    | ease                         |
| Tooltip appear/disappear           | 0ms      | (none — instant)             |

Forbidden: spring physics, bounce, parallax, scale-in, scale-on-hover, shimmer, skeleton-pulse,
count-up animations, "delight" of any kind. Calm is the feature.

#### Scenario: No decorative motion
- **WHEN** a diff introduces an animation or transition
- **THEN** it is one of the four vocabulary entries above (or an explicit spec change adds it here first)

### Requirement: Interaction Affordances
Links SHALL be underlined with `text-underline-offset: 4px` and `text-decoration-color:
var(--border-strong)` — visible but not loud. Hover on list rows is a 6% white tint on dark / 5%
black tint on light, with no transform. Focus is visible — 2px outline of `--fg` at 2px offset —
via `:focus-visible` only. Disabled state is opacity 0.4 with no pointer events; never grey out
by changing color.

#### Scenario: Keyboard focus visible
- **WHEN** a user tabs to an interactive element
- **THEN** a 2px `--fg` outline at 2px offset renders via `:focus-visible`

### Requirement: Interface Copy
Interface copy SHALL follow the settled voice doctrine
(`about/heart-and-soul/design-language.md` § Voice and Copy): register is technical, terse,
slightly formal, owner-direct. Past tense for events, present for state (no future tense); no
exclamation marks anywhere; no em-dashes in prose (use a comma, colon, or parentheses instead;
doctrine non-negotiable #6), the sole exception being a bare `"—"` null-display placeholder; no
first person (the system is a third party); "the" over "your"
where it works ("The calendar is paused"; "your" stays only when contrast matters); no hedging
adverbs (currently, presently, just, simply, basically); no celebration (no "Nice work!", no
green-check moments — quiet success is the success state); no filler ("Welcome back, Tze!" is
filler); numbers exact ("2 things need you", never "a few things") without false precision
(never "2.000"). Capitalization is sentence case everywhere except proper nouns ("Sync now",
not "Sync Now"). Button labels are active verbs with no marketing language and no punctuation
("Run patrol", not "Force Patrol Now!"). Empty states follow a two-tier model
(`about/heart-and-soul/design-language.md` § Empty states): a **page-level empty state** (an
ordinary page, panel, or table with nothing to show) uses a `{Noun} + verb phrase` title, one
short visible sentence of context if needed, and a single action button. A
**Voice-surface-inline empty state** (the briefing column, the attention list when nothing needs
attention, the Next list when nothing is upcoming — the surfaces where the system is literally
speaking in sentences) is held to the stricter rule: one serif-italic sentence with no trailing
explanation and no action button, e.g. *"Nothing waiting."*

#### Scenario: Page-level empty state copy
- **WHEN** an ordinary page, panel, or table has nothing to show
- **THEN** it renders a title plus, when needed, one short visible sentence of context and at
  most one action button, with no illustration

#### Scenario: Voice-surface-inline empty state copy
- **WHEN** a Voice surface (the briefing column, the attention list, the Next list) has nothing
  to show
- **THEN** it renders a single serif-italic sentence with no explanatory paragraph, no action
  button, and no illustration

#### Scenario: No celebration
- **WHEN** the system completes work or reports a healthy state
- **THEN** the copy states the fact without exclamation, emoji, or congratulation

### Requirement: Anti-Pattern Prohibitions
The following SHALL NOT appear on any dashboard surface: purple/pink gradients; glassmorphism
(except the sticky scenario bar, sparingly); drop shadows on cards (there are no cards); nested
cards; italic-serif headlines as a brand move; "Pro"/"New" badges or version stickers;
left-border accent stripes; icon-and-label chips floating in space; drawn SVG imagery (use
placeholders, ask for real materials); animating numbers from zero on load; sparkles, confetti,
success particles, micro-interactions for joy's sake; onboarding overlays or tour tooltips on a
familiar page; generic font stacks; emoji in interface chrome; multi-color gradients on text;
mock content filling a quiet day because the screen looks empty.

#### Scenario: Review rejects anti-patterns
- **WHEN** a change introduces any item on the prohibition list
- **THEN** design review rejects the change, citing this requirement

### Requirement: Page Conformance
A new or redesigned page SHALL be considered in the language only when all of the following hold: (1) it uses the
two-column editorial shell or a single 1280px-max column with the same gutter; (2) hierarchy is
type and rule, not card and shadow; (3) it uses the established palette with no new colors;
(4) numbers are tabular and mono; (5) butler hues appear only on letter-marks; (6) state color
appears only when state demands; (7) at most one commit button per surface; (8) every empty
state matches its tier — page-level empty states use a title plus at most one short visible
sentence of context, Voice-surface-inline empty states (briefing column, attention list, Next
list) are one serif-italic sentence — and neither tier renders an illustration; (9) headlines
are sans 500, not bold; (10) the page reads calm at 3am during an outage. When a page cannot be
made to look like the Overview, either the language is wrong or the page is — both are worth
investigating before shipping.

#### Scenario: Conformance gate before merge
- **WHEN** a dashboard page is added or redesigned
- **THEN** the change passes all ten conformance checks (the dispatch-kit review checklist operationalizes them) before merge
