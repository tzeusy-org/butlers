# Dashboard Design Language

> Status: doctrine. This is the WHY for the dashboard; the concrete tokens,
> type scale, components, and motion live in
> `openspec/specs/dashboard-design-language/spec.md`. Em-dashes in this file
> appear only inside code spans, which are exempt from the ban.

The Butlers dashboard is the human window into a system that mostly runs
without humans watching. It is read first, controlled second. The design
language must respect that.

This document does **not** specify components, tokens, or pages. Those belong
in [`about/lay-and-land/frontend.md`](../lay-and-land/frontend.md) (where
things are) and in capability specs (what they do); the concrete visual
language itself (tokens, type scale, components, motion) is the Dispatch
spec at `openspec/specs/dashboard-design-language/spec.md`. This document is
**WHY**: the principles a Butlers dashboard must satisfy regardless of
which framework or visual style we land on.

---

## What the Dashboard Is

The dashboard is a **read-mostly observability surface for a personal
multi-agent system**. It exists so the owner can:

1. Trust that butlers are alive and behaving.
2. Investigate when one of them isn't.
3. Override or correct behavior when needed.
4. Understand what the system *did with their data* (episodes, contacts,
   memory facts, notifications, costs).

Every screen is in service of one of those four jobs. If a screen does not
serve them, it does not belong.

The dashboard is **single-tenant**. There is exactly one user. There is no
"team," no "permissions tier," no "workspace switcher." Every design pattern
that assumes multi-user SaaS conventions (avatars in nav, role badges,
"invite teammates" CTAs) is a category error here.

## What the Dashboard Is Not

- **Not a control plane for end users.** The end user of a butler is the
  owner, but their channel is messaging, not the dashboard. The dashboard
  is the *operator's* surface (the same person, but in a different mode).
- **Not a chat app.** Conversational flows belong in the messaging layer.
  The dashboard's job is to *show what happened*, not to *be the place
  things happen*.
- **Not a generic admin template.** The dashboard's structure should be
  legible to its single user, not to an imagined enterprise admin
  persona. Patterns like "user management → roles → permissions" do not
  apply.
- **Not a marketing surface.** Hero illustrations, gradient banners,
  "delight" microinteractions for their own sake. None of these earn
  their pixels here.
- **Not a uniform information feed.** Different butlers produce
  fundamentally different data shapes (timelines, graphs, episodes,
  facts, conversations). Forcing them into one rectangular table view is
  a regression.

---

## Non-negotiable rules

These rules are binding. A change that contradicts one either fixes the work
or amends the rule here; both are legitimate, doing neither is not.

1. **One token system or none.** Every color, radius, font size, and
   spacing value used in a page must come from the token system. Hex
   literals and inline `style={{ ... }}` for visuals are bugs. If a
   token does not exist for what the page needs (e.g., severity tiers,
   chart category palettes), the right move is to *add a named token*,
   not to inline a value.
   - **Exemption: chart palettes.** The `--chart-*` tokens in
     `index.css` and recharts color props that consume them are
     intentionally a separate visual axis (categorical data
     differentiation). Only ad-hoc hex in JSX is a bug.
   - **Exemption: typed primitives that own one style prop.** A
     `<Progress value={0.42}/>` whose internal `style={{ width: '42%' }}`
     is unavoidable does not violate the rule. The ban targets ad-hoc
     inline styles, not encapsulated dynamic values inside a typed
     primitive.
2. **The `Page` is a primitive.** Every route renders inside a
   `<Page>` shell that owns title, description, breadcrumbs, action
   bar, loading state, error state, and empty state. Pages compose
   sections inside it; they do not reinvent the chrome.
   - **Standard H1 size is `text-3xl font-bold tracking-tight`.**
     The `<h1>` is owned by `<Page>` (`frontend/src/components/ui/page.tsx`)
     for all standard (non-editorial) pages. The heading weight stays
     `font-bold` (700) for standard chrome: the "Display weight is 500"
     rule applies only to the Display/Editorial tier, not the standard H1.
   - **Editorial archetype gets a Display tier.** The Overview opens
     with a Display headline instead of the standard H1. The Display
     tier is reserved for editorial pages where the system is speaking
     in sentences (see [Editorial archetype](#editorial-archetype)).
     Other archetypes keep the standard `text-3xl` H1. Display sizing
     is owned by the spec's Type System requirement.
   - **Entity detail has Editorial and Workbench modes.** Editorial
     renders via `<Page archetype="editorial">` and gets the Display
     tier. Workbench is a workspace-grade record page: it renders via
     `<Page archetype="overview">` and keeps the standard `text-3xl` H1.
     The toggle is a `localStorage`-persisted mode switch in the Page
     shell's actions slot; the two modes share one route and one
     `<Page>` mount.
   - **Workspace-grade record pages do not get a tier-2 hero.**
     Butler detail, contact detail, conversation detail, and similar
     operator record pages keep identity in the `<Page>` shell title
     and in the overview tab's identity card. Status pills and primary
     actions belong in the `<Page>` actions slot when they need page
     reach. Do not introduce a page-level identity strip, letter-mark
     hero, or second header tier between the shell and the tab body.
     The tier-1 header keeps title and breadcrumbs, actions migrate into
     the shell, and the overview tab remains the identity surface.
   - **Type ratio is 1.2** (product-register override of impeccable's
     shared `≥1.25` floor). Per `impeccable/reference/product.md`:
     "tighter scale ratio. 1.125–1.2 between steps is typical for
     product UI." The dashboard is product, not brand.
3. **Information density is a deliberate dial, not an accident.**
   Each page declares its archetype, and the archetype determines
   layout, not the author. The shipped archetype set is the seven-member
   union owned by the `<Page>` primitive (`frontend/src/components/ui/page.tsx`):
   `overview`, `list`, `detail`, `workspace`, `editor`, `editorial`,
   and `status-board`. `detail` covers drill-downs, `list` covers feeds
   and logs, and graph-heavy pages render inside `workspace` or
   `status-board`. New pages pick from the shipped seven; adding an
   eighth is a `<Page>` change, not a per-page choice.
4. **Time is a typed primitive.** All timestamps render via a single
   `<Time>` component that knows the user's timezone, the butler's
   timezone, the desired precision, and the relative-vs-absolute mode.
   `new Date(x).toLocaleString()` in a page file is a bug.
   - **Exemption: calendar layout helpers.** The `CalendarWorkspacePage`
     uses `date-fns format()` for calendar-grid structural labels:
     navigation headers (`"MMMM yyyy"`, `"EEE, MMM d, yyyy"`), date
     ranges (`"MMM d, yyyy"`), grid cell day labels (`"d"`, `"EEE d"`),
     time-axis labels (`"h a"`), and inline event time ranges
     (`"MMM d, HH:mm"`, `"h:mm a"`). These are grid-coordinate displays
     intrinsic to the calendar layout, not user activity timestamps.
     They are exempt from `<Time>` migration. Do not extend this
     exemption beyond `CalendarWorkspacePage`.
5. **Voice is owner-direct.** Sentence case for everything except
   proper nouns and product names. Active verbs in buttons. No
   chatty marketing language. No empty enthusiasm.
   Full rules are in the [Voice and Copy](#voice-and-copy) section
   under Settled Direction.
6. **No em-dashes in prose.** The em-dash (`—`) is banned from all
   copy written for the dashboard and from all doctrine documents.
   Replacements: a comma, a colon, or parentheses, depending on
   the relationship the dash was carrying. This rule applies to
   JSX strings, `description` props, `CardDescription`, `EmptyState`
   descriptions, toast messages, and doc prose. It does not apply
   to code inside code blocks or to strings used as data values
   (e.g., a null-display fallback `"—"` is acceptable as a
   typographic convention, not prohibited prose).

### Worth debating

- Whether to keep a single `Card` as the dominant container or to
  introduce a flatter "section" pattern for high-density screens like
  QA and Chronicles.
- Whether the sidebar should keep its first-letter-as-icon glyphs or
  commit to real icons (the current approach is honest but cheap).
- Whether the breadcrumb autobuilder is worth keeping (it produces
  awkward output: "Qa / Investigations") or whether each page should
  own its breadcrumbs explicitly.

---

## Theme commitment

**Physical scene:** I open this dashboard at 10pm from a dim room after
reviewing my day, and again at 8am from a bright kitchen while coffee is
brewing; the evening glance is more deliberate and more frequent than the
morning check.

**Decision: dark-primary with light fallback.**

Dark is the designed-first mode. Every color token, chart palette, and contrast
ratio is tuned against a dark background under dim ambient. Light mode is a
supported fallback, available via the theme toggle, but it is not the primary
design target. If a design decision requires a trade-off, the dark experience
wins.

This is not "dual-with-dark-default." That pattern keeps both modes at equal
weight and treats the default as a preference setting. This pattern treats dark
as the canonical surface. Light degrades gracefully but is not independently
designed from first principles.

---

## Light-mode accessibility floor

Companion to "Theme commitment" above.

Light mode is a supported fallback, not the canonical surface. The minimums
below are what "degrades gracefully" means in concrete, auditable terms. Each
minimum is anchored to a WCAG 2.1 criterion or a stated product rationale.

We promise **WCAG AA contrast minimums** (color contrast and non-text contrast)
for the light-mode fallback. We do not promise full AAA, nor do we promise
every WCAG AA criterion beyond contrast. AAA contrast (7:1 normal, 4.5:1
large) is desirable but not required; if chasing it breaks the palette
coherence established in dark mode, AA wins.

### Body text and primary UI labels

**Minimum: 4.5:1 against the page background (WCAG 1.4.3, AA normal text).**

**Muted text (`--muted-foreground`) is not body text.** It is a supplemental
label tier (secondary stats, metadata, timestamps). Muted-foreground text must
never be the primary semantic carrier for a piece of information. If a string
is the only place where critical meaning appears, it must use `--foreground`,
not `--muted-foreground`.

### Interactive elements and component boundaries

**Minimum: 3:1 against adjacent background colors (WCAG 1.4.11, AA
non-text contrast).**

Button fills, badge backgrounds, input borders, and icon-only controls must
meet 3:1 against their containing surface in light mode. The current
`--primary oklch(0.205 0 0)` on white substantially exceeds this. Borderline
cases are outlined icon buttons and ghost variants where only the border
provides the boundary signal: the border must not drop below 3:1 against the
page background.

### Focus states

**Minimum: 3:1 between the focus indicator and its adjacent color
(WCAG 2.4.7 requires focus visible; the 3:1 ratio is defined in WCAG 2.4.11,
a WCAG 2.2 criterion for Focus Appearance).**

The global `:focus-visible` outline in `--focus` is the focus carrier and a
non-overridable floor (spec requirement Interaction Affordances). A component
may add a ring but must not suppress or recolor that outline.

### Semantic and categorical colors used as information carriers

**Minimum: 3:1 on white for any semantic color used as the sole carrier of
meaning (WCAG 1.4.11).**

This applies to severity badges, permanence indicators, role badges, state
badges, and Dunbar tier ramp colors.

Any token that falls below 3:1 on white in light mode (for example the green
and amber severity tones) must never be the only visual signal: pair it with a
text label or icon. The color is a reinforcement, not the carrier. This is the
same treatment as WCAG's color-not-alone rule (1.4.1).

### Chart distinguishability under common color vision deficiencies

**Minimum: adjacent chart series must differ by at least 0.15 L in OKLCH, or
by hue angle separation exceeding 60 degrees, when rendered in light mode.
Pairs that fail both criteria are permitted only when the chart includes a
legend or direct data labels so that color is not the sole distinguishing
signal. This is not a WCAG criterion; it is a stated product floor anchored
in practical legibility for deuteranopic and protanopic users.**

When two series in the same chart fail both criteria, the chart must include a
legend or direct label; relying on color alone is not allowed in light mode.

### What is out of scope

- **AAA compliance.** We do not promise 7:1 or 4.5:1 for large text in light
  mode. If AAA is achievable without forcing a palette divergence between dark
  and light modes, take it. If it creates divergence, AA wins.
- **Non-semantic decorative elements.** Dividers, card borders, background
  fills used purely for visual grouping do not carry meaning and are not
  subject to these minimums (they are subject only to the spirit of WCAG 1.4.1
  color-not-alone for adjacent informative elements).
- **Third-party embeds.** Map tiles (maplibre-gl) and external widget surfaces
  are outside our token system. They are excluded from this floor.
- **Print or high-contrast mode.** We do not currently design or test for
  Windows High Contrast or forced-colors media queries. These are future
  backlog items, not covered by this doctrine.

---

## Settled Direction (owner-confirmed)

1. **Audience.** The dashboard serves the owner today, with possible
   extension to close family members later. There is no team, no
   permission tier. **Both** calm-morning monitoring and incident
   investigation are valid use cases. Calm-morning is more frequent;
   incident is higher-stakes. The design must hold both, never
   sacrificing the second to optimize the first.
2. **Chronicles is the reference implementation.** Every page should
   eventually deliver Chronicles-grade feature richness: a real
   primary visualization, scrubber/control affordances where time
   applies, secondary aggregations, drill-down drawers. The "table
   of rows" archetype is acceptable as a transitional state, not as
   the destination. New work on existing pages should aim toward
   Chronicles, not regress further away from it.
3. **Owner sovereignty gets its own surface.** A new top-level
   **System** page will collect the plumbing-visibility facts:
   instance version, uptime, database size and growth, backup
   recency, "your data has only ever been seen by these endpoints,"
   per-butler last-touch timestamps, etc. Sovereignty becomes a
   page, not a sprinkle.
4. **Operator and resident modes are different projections.**
   Workspace-grade record pages may carry high tab counts when the
   operator surface needs them. Butler detail preserves the ten
   spec-mandated base tabs in operator mode: Overview, Sessions,
   Config, Skills, Schedules, Trigger, MCP, State, CRM, and Memory.
   It also preserves non-spec operator tabs that already carry
   capability, including Models.
   Resident mode may be the default narrow view and may use the
   Dispatch vocabulary, but it is a filtered projection of the
   operator surface, not a replacement for it. Deep links and
   conditional tabs must preserve access to the fuller operator
   surface.
5. **Hero metric: butler sessions.** The single number that tells
   the owner whether their system is doing its job today is
   **sessions**: how many times butlers spun up to act on the
   owner's behalf. Cost, health, and pending approvals stay on the
   home page as supporting context, but session count is the one
   that gets visual primacy.

## Voice and Copy

The dashboard is an operator tool. Its copy must be legible,
direct, and unadorned. The rules below govern every string rendered
in JSX (descriptions, labels, empty states, toasts, error messages)
and every prose sentence written in doctrine documents.

### Register

Technical, terse, slightly formal, owner-direct. The owner is a
sysadmin who already knows the domain. Do not explain things they
already know. Do not soften things that are just facts.

| What you want to say | Write this |
|---|---|
| The butler has not synced recently | "Last sync: 3 hours ago." |
| Nothing to show yet | "No sessions today." |
| A dangerous operation | "This will delete the connector and all its history." |

### Tense, person, and address

These rules sharpen the register above.

- **Past tense for events, present tense for state.** No future tense
  in interface copy: "is" or "did," not "will be" or "is going to."
  The dashboard reports what happened and what is true now; it does
  not promise.
- **No first person.** "I," "we," "us," "our" do not appear in
  rendered copy. The system is a third party. Write "Authentication
  failed," not "I could not authenticate."
- **Avoid "your" when "the" works.** "The calendar is paused," not
  "Your calendar is paused." The owner already knows whose dashboard
  this is. "Your" stays only when contrast matters ("Your timezone
  is Asia/Singapore. The butler runs in UTC.").
- **No hedging adverbs.** Strike: currently, presently, just, simply,
  basically, actually, essentially. Write "Loaded 14 sessions," not
  "Currently showing 14 sessions."
- **No celebration.** No checkmarks, no green-check moments, no
  "Nice work," no "All set." Quiet success is the success state.
  When everything is fine the page says it once and stops.
- **No filler.** "Welcome back" is filler. "Today, in numbers" is a
  fact. Delete the filler, keep the fact.
- **Numbers are exact.** "2 things need you," not "a few things." But
  do not state precision the data does not have: "2.0" when the
  source is integer is wrong, and so is "approximately 2."

### Capitalization

Sentence case for everything except proper nouns and product names.

- Page titles: sentence case. "Knowledge graph", not "Knowledge Graph".
- Section headings: sentence case. "Token leaks", not "Token Leaks".
- Button labels: sentence case. "Sync now", not "Sync Now".
- Proper nouns and product names are always capitalized: Claude,
  Telegram, PostgreSQL, Tailwind.

### Buttons

Active verbs. No marketing language. No punctuation.

| Bad | Good |
|---|---|
| "Force Patrol Now" | "Run patrol" |
| "Enable Smart Sync!" | "Enable sync" |
| "Request New Curriculum" | "Request curriculum" |
| "View All Notifications" | "View all" |
| "Load More Data" | "Load more" |

Destructive buttons are plain: "Delete", not "Delete forever" or
"Remove permanently". The `variant="destructive"` signals danger;
the copy does not need to amplify it.

### Empty states

State the fact, then offer the next action. Avoid prose sentences
that describe what the user could do if they were not there.

**Page-level empty states** use `{Noun} + verb phrase` as the title,
one short sentence of context if needed, and a single action button.

| Bad | Good |
|---|---|
| "No butlers found. Butlers are long-running agents that act on your behalf. Add one to get started!" | Title: "No butlers active." Action: "Open setup guide" |
| "Patrol cycles will dispatch investigations when novel issues are detected." | "No active investigations." |
| `"Browse the knowledge graph — people, organizations, places, and more."` | "Knowledge graph is empty." |

**Inline empty states inside a Voice surface** (the briefing column,
the attention list when there is nothing to attend to, the Next list
when nothing is upcoming) use a single serif italic sentence in muted
color, no period of explanation, no action button. Example:
*"Nothing waiting."* The Voice surface is the place the system
literally speaks; one quiet line is the entire response.

Empty states do not get exclamation marks. They do not use em-dashes.
They do not editorialize.

### Errors

Passive voice for system-side failures. Never blame the user.
Describe what failed, not who failed.

| Bad | Good |
|---|---|
| "You provided an invalid token." | "Authentication failed. Check the token in Settings." |
| "Your request failed." | "Failed to load sessions." |
| `"This butler isn't authenticated — please re-authenticate."` | "{Butler} is not authenticated. Re-authenticate in Settings." |

Error copy ends with a period. If there is an action to take, offer
it as a button or a link, not inline instructions.

### Bans

The following are banned in all dashboard copy and doctrine prose:

1. **Em-dashes (`—`).** Use a comma, colon, or parentheses instead.
   See non-negotiable rule 6.
2. **Exclamation marks.** The owner is not excited by dashboard
   notifications. If something is urgent, the visual treatment
   (destructive color, alert badge) carries that weight.
3. **Emoji** (unless the owner explicitly requests one in a
   specific context). Emoji in UI copy reads as consumer-product
   informality. This is an operator tool.
4. **"Please".** Do not apologize for the system's behavior. State
   what happened and what to do.
5. **Ellipsis as decoration.** Loading states may use "Loading..."
   as a terminal state indicator, but ellipsis is not a substitute
   for a complete sentence.

### Before/after examples

**Em-dash in a description.** Before:
`"Browse the knowledge graph — people, organizations, places, and more."` After: "Browse the knowledge graph: people,
organizations, places, and more."

**Title case and extra words in a button.** Before: "Force Patrol Now".
After: "Run patrol".

**Empty-state prose.** Before: "Patrol cycles will dispatch investigations
when novel issues are detected." After (EmptyState title): "No active
investigations."

---

## Type system

The dashboard adopts a three-family type system. The split is
meaningful: sans is the system speaking in data, serif is the system
speaking in sentences, mono is the system speaking in numerals. A page
may use one, two, or all three families; never invent a fourth.

**Family identity.** Inter Tight is the sans family. Inter *Tight* is
the deliberate pick over plain Inter; the compressed metrics carry the
operator-tool register. Source Serif 4 is the Voice family, used where
the system literally speaks in sentences (briefings, empty-state lines,
process glosses). JetBrains Mono is the numerals family: timestamps,
IDs, deltas, KPI mega-numbers, eyebrows, code, file paths. Generic
stacks (Inter, Roboto, Arial, Helvetica, system-ui as a primary face)
are not in the language.

**Tabular numerals are non-negotiable.** Every numeric value the
dashboard renders uses tabular-nums: costs, counts, deltas, KPI
mega-numbers, mono timestamps, badge digits. Lists of numbers must
align without alignment hacks. Scannability of an operator tool is
defeated when digits jitter as they update.

**Display weight is 500, not 700.** Tight tracking does the work that
weight would do; bold display reads as loud, which violates the calm
contract. This governs the **Display/Editorial tier**; it does not override
the standard `<Page>` H1, which stays `text-3xl font-bold` (700).

**Eyebrows title sections in lieu of a heading.** They establish
rhythm without adding shouting weight. They are not subtitles, they
are the section's name. An eyebrow above a list is the list's name; a
heading above the same list would be louder than the list it
introduces.

The type scale and token values are owned by the spec requirements Type
System, Tabular Numerals, and Eyebrow Section Titles.

---

## Editorial archetype

> Status: **settled** (governs the Overview surface and any future
> page that opens with a system-spoken briefing). Companion to the
> Type system above and to the Voice and Copy section.

The dashboard supports a small set of page archetypes (Non-negotiable
rule 3). The **editorial archetype** is a two-column page whose left
column is the system speaking in sentences and whose right column is
a quiet index of facts. The two columns read as separate documents
that share a page.

### The Voice surface

The Overview headline plus its serif elaboration is a distinct surface
type called the **Voice**. Reserve it for places the system literally
speaks in sentences: the Overview briefing, empty states ("Nothing
waiting."), process glosses where the system explains its own shape.
Voice is serif italic for empty states, serif roman for briefings. It
is never decorative. If a serif paragraph feels like it would "fill"
a quiet page, the page is not actually quiet enough; the serif
paragraph is wrong.

### The status pill

Anywhere the system reports on its own process (cache age, last sync,
model version, briefing source), use a status pill. The pill is
always honest about what is rendering. The three states for the
briefing pill are `composing…`, `llm · cached 5m`, and `templated`;
the pill names what it is showing rather than pretending the source
is invisible.

### Attention list

The attention list is the dashboard's register-aware list primitive:
rule-separated rows, no card chrome. The mark column carries severity
for read-rows and status for scan-rows; the meta column carries
action for read-rows and metric for scan-rows. The list reaches
Bloomberg-grade density at a fraction of Bloomberg's noise. Empty
state for the attention list is the Voice register doing its job:
`Nothing waiting.` in serif italic, no celebration, no illustration.

#### Attention-tint exception

> Status: **single permitted exception** to the state-color-on-background
> prohibition, approved by openspec/changes/redesign-settings-dispatch-console/.

Rows or panels that *demand human attention right now* may carry a
**4–7% alpha background tint** in the state color, paired with a **2px left
rail** in the same color. This is one affordance, not two: the tint and
the rail travel together as a single signal unit.

The tone, alpha bounds, permitted states, and the `.attention-row`
implementation are owned by the spec requirement State Color Discipline. The
rule that matters here: the pattern applies **only** to "demands attention
now" states, and *one affordance per signal* still applies.

### KPI strip

The KPI strip replaces card chrome with tabular-nums plus hairline
dividers. Numbers align across columns because every numeric cell is
tabular. There are no background fills, no per-cell cards, and no
mega-number that screams; the alignment is the design.

### Butler hue scope

> Status: non-negotiable (with one documented exception, see
> [Attention-tint exception](#attention-tint-exception) above).

Each butler has one hue from the categorical palette. The hue appears
**only on the butler letter-mark** (the colored squircle with the
initial). It does not appear on backgrounds, borders, buttons,
headers, or any other chrome. This rule is what keeps the dashboard
from reading like a SaaS product. It augments the existing token
rule (Non-negotiable 1): named hues only resolve onto the letter-mark.

The sole exception is the attention-tint pattern described above, which
permits a 4–7% alpha state-color tint on rows or panels requiring
immediate human action. That exception is scoped, bounded, and single-
purpose; it does not weaken the general prohibition.

### Non-butler categorical and decorative hue scope

`--categorical-1` through `--categorical-12` are a separate, theme-aware
palette for a local discrete axis that is neither a butler identity nor an
operational state: activity lanes, sleep-stage legends, syntax tokens, model
tiers, and similar labeled categories. The ramp may appear on foregrounds,
borders, chart marks, and compact legend markers. It must not be used to
signal healthy, degraded, warning, or error state, and it must not turn into
general page chrome.

Every categorical use keeps its label, icon, position, or direct data label;
color reinforces a distinction but never carries it alone. The token values
are tuned in both themes for text against the canonical surfaces. Consumers
must use the named tokens from `frontend/src/index.css`, not raw palette
utilities or hex values.

### Motion budget

The editorial archetype obeys the existing motion contract (see
[Motion](#motion)). The briefing introduces two motion events: a
paragraph cross-fade on refresh and a status-pill icon rotation
while loading. No staggered entries, no count-up animations, no
scale-in. Calm is the feature.

Row anatomies and the source files that embody these patterns are listed in
[`about/lay-and-land/frontend.md`](../lay-and-land/frontend.md); the values
are owned by the spec requirements KPI Strip, Butler Letter-Mark, Voice
Surface, and Process Status Pill.

---

## Motion

Motion conveys state change, not personality. Every interactive state
transition honors these rules:

- **Three duration tiers** (`--duration-fast/base/slow` in
  `frontend/src/index.css`): fast for micro-interactions, base for
  layout-affecting changes, slow only for long travel.
- **Ease-out only** (`--ease-out-quart`). No bounce, no elastic, no
  ease-in-out for state changes.
- **Animate only `transform`, `opacity`, and paint properties.** Animating
  `width`, `height`, `max-height`, position offsets, `margin`, or `padding`
  causes layout reflow and is forbidden.
- **No page-load orchestration and no decorative motion.** No staggered
  entries or cascading fade-ins; if a transition does not communicate a
  state change, it does not belong.

The permitted animation vocabulary is owned by the spec requirement Motion
Vocabulary. Encapsulated dynamic values inside a typed primitive (a
`<Progress>` width) fall under the typed-primitive exemption of
Non-negotiable rule 1.

---

## Trend charts

A trend chart tells the owner how often something was measured, where the
silence is, and how old the latest point is, without reading a caption. So
the x axis is time, not a list of labels: readings sit where they happened,
every reading wears a mark, joins are straight (a smoothed curve invents
values nobody measured), a long silence breaks the line, and a stale series
shows a shaded, labelled tail up to now instead of ending quietly at its last
point. One primitive, `TimeSeriesChart`, owns this grammar; the normative
rules are the spec requirement Time-True Trend Grammar.

---

## How To Use This Document

- **Adding a page or component?** Read this first. If your work
  contradicts a non-negotiable rule, either fix the work or argue
  here for the rule to change. Both are legitimate moves, but
  don't do neither.
- **Reviewing a PR?** "Does this drift the design language?" is a
  fair review comment, and this doc is what you point to.

The companion topology document [`frontend.md`](../lay-and-land/frontend.md)
inventories *where* the language is currently embodied.
