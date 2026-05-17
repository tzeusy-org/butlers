# Butlers — Design Language

> A working name for this language: **Dispatch.** A dispatch from your
> house, written by the staff, set in type that respects you. This
> document is the canonical reference; extend every other page from
> these rules.

---

## 0. The thesis

> A butler announcing, not a chatbot reporting.

Every decision below ladders up to that line. When a question comes up
that this document doesn't cover, ask: *would a discreet, competent
butler do this?* They wouldn't gradient-mesh anything, they wouldn't
exclaim, they wouldn't decorate a quiet day with an empty-state
illustration. They'd stand in the doorway and tell you what's true, in
order of importance, then leave.

Five inviolable rules:

1. **Composure is the brand.** The page reads calm even when the system
   is broken. Color and motion appear only when state demands.
2. **Type is the system.** Hierarchy comes from type and rules, not
   shadows or fills.
3. **Surfaces, not cards.** One elevation. Structure is rules and rhythm.
4. **Every element earns its place against state.** If nothing is
   happening, the section disappears its borders or shows a single
   serif-italic line. We do not decorate.
5. **One affordance per signal.** Status is one of: dot, sliver, numeral,
   color. Never a word like "active." Never two of the four together.

---

## 1. Color

### 1a. Surfaces (dark mode is canonical)

| Token            | Dark                       | Light                       | Use |
|------------------|----------------------------|-----------------------------|-----|
| `--bg`           | `oklch(0.145 0 0)`         | `oklch(0.985 0.003 85)`     | Page |
| `--bg-elev`      | `oklch(0.205 0 0)`         | `oklch(1 0 0)`              | Code blocks, tooltips |
| `--bg-deep`      | `oklch(0.115 0 0)`         | `oklch(0.965 0.005 85)`     | Sidebar, sticky bars |
| `--fg`           | `oklch(0.985 0 0)`         | `oklch(0.18 0 0)`           | Primary text |
| `--mfg`          | `oklch(0.708 0 0)`         | `oklch(0.46 0 0)`           | Muted text, eyebrows |
| `--dim`          | `oklch(0.55 0 0)`          | `oklch(0.62 0 0)`           | Tertiary text, deltas |
| `--border`       | `oklch(1 0 0 / 0.10)`      | `oklch(0 0 0 / 0.10)`       | Hairline rules |
| `--border-soft`  | `oklch(1 0 0 / 0.06)`      | `oklch(0 0 0 / 0.05)`       | List separators |
| `--border-strong`| `oklch(1 0 0 / 0.18)`      | `oklch(0 0 0 / 0.20)`       | Buttons, link underlines |

The light variant is **paper-warm**, not stark white. A faint yellow
cast (oklch hue 85) keeps the dispatch feeling — paper, not screen. Do
not move to true neutral.

### 1b. State color

Three colors only. Use them sparingly. They appear when state demands;
they do not decorate.

| Role     | Dark                     | Light                    | Used for |
|----------|--------------------------|--------------------------|----------|
| `--red`  | `oklch(0.685 0.250 29)`  | `oklch(0.527 0.235 29)`  | High severity, error, blockers, reauth |
| `--amber`| `oklch(0.810 0.185 84)`  | `oklch(0.66 0.145 75)`   | Medium severity, degraded |
| `--green`| `oklch(0.790 0.195 148)` | `oklch(0.50 0.140 152)`  | Healthy, positive delta |

Rules:
- A page can show all three but **only if all three states are present**.
- Never use any of these as a brand accent or hover.
- Never use them on background fills. Foreground or border only.

### 1c. Butler category hues

Each butler has one assigned hue from `--category-1..8` (defined in
`frontend/src/index.css`). The hue is the butler's identity throughout
the system.

The hue **only ever shows on the butler's letter-mark** — the colored
squircle with their initial. It does not appear on backgrounds, borders,
buttons, headers, or anywhere else. This rule is what keeps the system
from looking like a SaaS dashboard.

Mapping (canonical):

| Butler         | Token          |
|----------------|----------------|
| relationship   | `--category-1` |
| memory         | `--category-2` |
| calendar       | `--category-3` |
| health         | `--category-4` |
| household      | `--category-5` |
| education      | `--category-6` |
| qa             | `--category-7` |
| chronicler     | `--category-8` |

---

## 2. Type

### 2a. Families

| Family            | Role                           |
|-------------------|--------------------------------|
| **Inter Tight**   | Everything UI — display, body, labels, numbers in interfaces |
| **Source Serif 4**| The system's *voice*. Used for LLM-written elaborations, empty-state lines, "why this shape" prose |
| **JetBrains Mono**| Times, IDs, deltas, KPI numbers, eyebrows, code, file paths |

The serif/sans split is meaningful: **sans is the system speaking in
data, serif is the system speaking in sentences.** A page may use one,
two, or all three; never invent a fourth family.

Avoid: Inter (the regular one — too generic), Roboto, Arial, Fraunces,
Helvetica, system-ui as a primary face. We picked Inter *Tight* on
purpose for its compressed metrics.

### 2b. Scale

| Role        | Family   | Size  | Weight | Tracking | Leading |
|-------------|----------|-------|--------|----------|---------|
| Display     | sans     | 44px  | 500    | -0.025em | 1.08    |
| Title       | sans     | 24px  | 500    | -0.015em | 1.2     |
| Body        | sans     | 14px  | 400    | normal   | 1.5     |
| Body small  | sans     | 13px  | 400    | normal   | 1.5     |
| Voice       | serif    | 16px  | 400    | normal   | 1.6     |
| Eyebrow     | mono     | 10px  | 400    | 0.14em   | 1.0     |
| Mono inline | mono     | 11px  | 400    | normal   | 1.4     |

**Display weight is 500, never 700.** Bold display is loud. Tight
tracking does the work that weight would do.

### 2c. Numerals

Every numeric value gets `font-variant-numeric: tabular-nums`. Always.
Costs, counts, deltas, KPI mega-numbers, mono timestamps, badge digits.
This is non-negotiable — it's what makes lists of numbers scannable
without alignment hacks.

### 2d. Eyebrows

`10px / mono / uppercase / 0.14em letter-spacing / muted color`. Used
to title sections in lieu of a heading. They establish rhythm without
shouting.

```html
<div class="eyebrow">Overview · Wed, 7 May 2026 · 14:21</div>
```

---

## 3. Layout

### 3a. Page shell

- Sidebar: **56px icon rail.** Fixed, full height.
- Main column: max-width **1280px**, margin: 0 auto.
- Page padding: `48px 56px`.
- Section gutter (between major columns): `56px`.

### 3b. Two-column editorial

When a page is structured as "what's happening" + "what to look at," use:

```
grid-template-columns: 1.4fr 1fr;
gap: 56px;
```

The left column is the narrative — display headline, voice paragraph,
attention list, KPI strip. The right column is the index — quiet lists
with eyebrow titles. They read as separate documents on the same page.

### 3c. Reading widths

- Display headline: `max-width: 14ch` — forces the dramatic line break
  that gives the page its shape.
- Voice paragraph: `max-width: 50ch` — readable measure.
- Lists: full width of column.

### 3d. Density

Information-dense without claustrophobia. Achieved with:
- **Rule-separated rows**, not cards. Every list item is a CSS grid of
  `time / mark / content / meta` separated by hairlines. This gets
  Bloomberg density at maybe 30% of Bloomberg's noise.
- Vertical row padding: 8–18px depending on importance. Attention list
  rows use 18px (they are read); butler-index rows use 10px (scanned).
- Never `padding: 24px` on a list item. That's card thinking.

### 3e. Spacing scale

Use multiples of 4px exclusively. Common values: 4, 8, 12, 14, 16, 18,
24, 32, 36, 48, 56. No magic numbers.

---

## 4. Components

### 4a. Lists (the canonical primitive)

```
display: grid;
grid-template-columns: <mark> 1fr <meta>;
gap: 10–18px;
padding: <vertical> 0;
border-bottom: 1px solid var(--border);
```

Variants:
- **Attention list** — 24px sev-glyph / 1fr title+serif-detail / auto action.
- **Butler index** — 8px status-dot / 1fr name / auto sessions / auto cost.
- **Next list** — 50px mono-time / 1fr label / auto kind tag.
- **Sidebar item** — 20px icon / 1fr label (when expanded) / auto badge.

### 4b. KPI strip

Four-column grid divided by hairline borders. Each cell:

```
mono-eyebrow  (10px, muted, uppercase)
mega-number   (32px, sans 500, tracking -0.03em, tnum)
mono-delta    (10px, muted)
```

No background fills. No card chrome.

### 4c. Buttons

Three forms only:

- **Action arrow `→`** in a list row — universal "go look at this"
  signal. No button styling, just an underlined word ending in →.
- **Pill button** — `4px 10px / 1px border / 3px radius / mono 11px`.
  Used for filters, scenario picks, theme toggles. Active state:
  inverted bg/fg. Never colored.
- **Commit button** — `Approve`, `Re-authorize`, `Send`. Same shape as
  pill, with `--fg` background and `--bg` text. Used at most once per
  surface.

We do not have a single rounded gradient CTA on the system.

### 4d. Tags / chips

Mono uppercase, muted color. No background. Used to label a kind, not
to celebrate one.

```html
<span class="kind-tag">approval</span>
```

### 4e. Status indicators

The `StatusDot` component — circle, 6px default, three colors:
- `ok` → green
- `degraded` → amber
- `error` → red
- `waiting` → muted neutral

A `Sev` glyph is the same idea but a 6px square — used inside attention
rows where dots would conflict with bullets in the row above.

### 4f. The letter-mark (`ButlerMark`)

```
16px square, 4px radius, butler hue, white initial.
font-weight: 600, font-size: 60% of the square.
```

Two tones:
- `fill` — solid hue background, white initial. Active state.
- `neutral` — transparent background, hue initial, hairline border.
  Default state.

This is the only place butler hues exist. Period.

### 4g. The briefing surface

The Overview's headline + serif paragraph is a **distinct surface
type** — call it the **Voice**. Reserve it for places the system is
literally speaking in sentences:
- Overview briefing.
- Empty states ("Nothing waiting.")
- Backend sketch's "Why this shape" gloss.

Voice is always serif italic for empty states, serif roman for
briefings. It is **never decorative**. If you find yourself adding a
serif paragraph because the page feels empty, you are wrong.

### 4h. The status pill (briefing source indicator)

Tiny 9px mono pill: dot + label + ↻. Three states only — `composing…`
(amber), `llm · cached 5m` (green), `templated` (dim). Click to
refresh. Always honest about what's rendering.

This pattern generalizes: **anywhere the system is reporting on its own
process, use a status pill.** Cache age, last sync, model version.

---

## 5. Iconography

- **Stroke-only**, single weight: 1.25–1.5px.
- 16×16 viewBox standard. Round caps, round joins.
- One color: `currentColor`. No two-tone.
- No fills, no gradients, no soft shadows.
- No emoji. Ever. Even on empty states.

When a butler area would otherwise call for an icon, use the **letter-mark
in the butler hue** instead. That's the typographic equivalent of an
avatar.

---

## 6. Motion

The system's motion vocabulary is **almost none**. Only:

| Where                              | Duration | Easing                       |
|------------------------------------|----------|------------------------------|
| Briefing paragraph cross-fade      | 200ms    | `cubic-bezier(0.22, 1, 0.36, 1)` |
| Sidebar chevron rotation           | 120ms    | linear                       |
| Theme toggle background fade       | 200ms    | ease                         |
| Tooltip appear/disappear           | 0ms      | (none — instant)             |

Forbidden: spring physics, bounce, parallax, scale-in, scale-on-hover,
shimmer, skeleton-pulse, count-up animations, "delight" of any kind.

Calm is the feature.

---

## 7. Affordance

- **Links** are underlined with `text-underline-offset: 4px` and
  `text-decoration-color: var(--border-strong)`. Visible but not loud.
- **Hover** on list rows: a 6% white tint on dark, 5% black tint on
  light. No transform.
- **Focus** is visible: 2px outline of `--fg` at 2px offset. Match
  `:focus-visible` only — keyboard users only.
- **Disabled** state: opacity 0.4, no pointer events. Don't grey out by
  changing color.

---

## 8. Voice (copywriting rules)

The same rules that govern the LLM briefing govern the rest of the UI:

- Past tense for events, present for state. No future tense ("will be",
  "is going to") in interface copy.
- No exclamation marks. Anywhere.
- No first person ("I", "we"). The system is a third party.
- Avoid "your" when "the" works. *"The calendar is paused"* not *"Your
  calendar is paused"*.
- No hedging adverbs: currently, presently, just, simply, basically.
- No celebration: no "", no "Nice work!", no green-check moments.
  When everything is fine, the page says "Everything is in hand." once
  and shuts up.
- No filler. "Welcome back, Tze!" is filler. Delete it.
- Numbers are exact. "2 things need you" not "a few things need you".
  But also don't state precision you don't have — never "2.000".

Empty states use serif italic, single sentence, no period of
explanation. *"Nothing waiting."* not *"You don't have anything to
review at this time. Items will appear here when…"*

---

## 9. Anti-patterns (the explicit do-not list)

- Purple/pink gradients
- Glassmorphism (except the sticky scenario bar, sparingly)
- Drop shadows on cards (we don't have cards)
- Nested cards / cards-on-cards
- Italic-serif headlines as a brand move ("Welcome, *Tze*")
- "Pro" badges, "New" badges, version stickers
- Left-border accent colored stripes on cards
- Icon-and-label chips floating in space
- Drawing imagery in SVG (use placeholders, ask for real materials)
- Animating numbers from zero on load
- Sparkles, confetti, success particles, micro-interactions for joy's sake
- Onboarding overlays / tour tooltips on a familiar page
- Generic font stacks (Inter, Roboto, Arial)
- Emoji in interface chrome
- Multi-color gradients on text
- Filling a quiet day with mock content because the screen looks empty

---

## 10. Extending to other pages

A new page is in the language when:

1. It uses the **two-column editorial** shell, OR a single 1280-max
   readable column with the same gutter.
2. **Hierarchy is type and rule, not card and shadow.**
3. It uses the established palette — no new colors invented.
4. Numbers are tabular and mono.
5. Butler hues appear only on letter-marks.
6. State color (red/amber/green) appears only when state demands.
7. There is at most one **commit** button per surface.
8. Every empty state is one serif-italic sentence, no illustration.
9. Headlines are sans 500, not bold.
10. The page reads calm at 3am during an outage.

### Page-by-page extensions (suggested order)

| Page         | Hardest call | Notes |
|--------------|--------------|-------|
| Approvals    | List → action density | Lean on the attention-list pattern. Each row has a primary commit button. |
| Butler detail| Charts vs prose | Stripe chart for 24h sessions; serif gloss for "what this butler does". One letter-mark hero. |
| Calendar     | Density vs whitespace | Day view as a single column with mono times in the gutter. No grid lines. |
| Audit Log    | Volume | Pure rule-list, zero chrome. Mono timestamps, sans actor, serif description. |
| Settings     | Form chrome | Two-column: section eyebrow + description left, controls right. No card around the form. |
| Issues       | Severity + grouping | Group by severity; within group, attention-list pattern. Severity glyph in the gutter. |

When in doubt, prototype it next to `Overview.html` first. The prototype
is canonical; if you can't make it look like the Overview, the language
isn't extending — and either the language is wrong or the page is. Both
are worth investigating before shipping.

---

## 11. The North Star

Every page should pass this test:

> If the system were a person handing me a sheet of paper, would I trust
> the typography of that sheet?

Newspapers from 1965 pass. Bank statements from a private bank pass.
Default Bootstrap dashboards do not pass. Gradient hero sections do not
pass. Most SaaS apps do not pass.

The Butlers UI passes.
