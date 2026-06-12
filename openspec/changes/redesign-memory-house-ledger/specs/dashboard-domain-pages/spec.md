# dashboard-domain-pages

## REMOVED Requirements

### Requirement: Memory page layout
**Reason**: The fixed three-section layout (tier-cards row + tabbed browser +
right-sidebar activity card) forces three distinct memory kinds into one
rectangular table view, which `about/heart-and-soul/design-language.md:59-62`
names a regression ("Not a uniform information feed … Forcing them into one
rectangular table view is a regression"). It is replaced by the four-band
house-ledger layout (see ADDED `Requirement: Memory page band layout`).
**Migration**: The `/memory` route is preserved; its layout is rewritten to
overture / pipeline / registers+rail / housekeeping bands. No data or URL
migration; the route, breadcrumbs, and detail-route paths are unchanged.

### Requirement: Memory tier cards with health indicators
**Reason**: The three tier cards rendered green/amber/destructive **health
badges** computed from consolidated/active/proven ratios. Composite "health"
grades and status-as-a-word badges are banned by `MEMORY_LANGUAGE.md` §9 and by
the brief's "no health score" rule; a healthy page must render zero
red/amber/green pixels. The lifecycle health that these cards approximated is
relocated, honestly, to the pipeline band and the attention rail.
**Migration**: Tier-card stats are absorbed into the overture KPI strip (mono
mega-numbers, no fills, no badges) and the pipeline band; health is expressed
as the *absence* of alarm in the pipeline line plus rail rows when state
demands. See ADDED `Requirement: Memory overture band` and
`Requirement: Memory pipeline band`.

### Requirement: Memory browser with tabbed tier navigation
**Reason**: The tabbed Facts/Rules/Episodes browser rendered all three kinds
through one table shape with **per-tab search inputs**, **confidence progress
bars**, colored permanence/validity/maturity **word badges**, and page size 20
— every one of which the redesign retires (uniform-feed regression; one-search
rule; belief-typography rules banning bars and word badges). The
`butlerScope`-prop behaviour is preserved on the legacy component for
`ButlerMemoryTab` only (see ADDED `Requirement: Legacy MemoryBrowser preserved
for ButlerMemoryTab`).
**Migration**: Replaced by ADDED `Requirement: Memory registers — three shapes`
(ledger / standing orders / daybook), ADDED `Requirement: Belief typography`,
and ADDED `Requirement: Memory unified search`. Page size becomes 50,
offset-based. Row-click navigation to `/memory/{kind}/{id}` is preserved by the
register shapes.

### Requirement: Memory activity timeline
**Reason**: The activity timeline rendered as a right-**sidebar card** with
sky/violet/secondary **type badges** and a decorative vertical line+dots,
sitting at the same hierarchy as knowledge. The redesign demotes recent
activity to a quiet list inside the attention-rail column (mono time ·
ButlerMark · sans summary, no color, no badges, no card chrome).
**Migration**: Replaced by the Recent activity sub-surface in ADDED
`Requirement: Memory attention rail and recent activity`.

## ADDED Requirements

### Requirement: Memory page band layout

The dashboard SHALL render a Memory page at `/memory` as a single column
(max-width 1280px) composed of four vertically stacked bands, in this order:

1. **Overture band** — eyebrow, display headline, one Voice sentence, KPI strip.
2. **Pipeline band** — the memory lifecycle as a single mono line.
3. **Registers + rail band** — `grid-template-columns: 1.4fr 1fr` (gap 56px):
   the search input, kind pills, and the focused register on the left; the
   attention rail and recent activity on the right.
4. **Housekeeping band** — a quiet bottom band (retention policies, compaction
   log, embeddings).

The layout MUST NOT render any tier-card grid, any tabbed table browser, or any
right-sidebar activity card; those are retired. On narrow viewports the
`1.4fr 1fr` grid MUST collapse to a single column with the rail below the
register.

State color (`--red` / `--amber` / `--green`) MUST appear in only two places on
this page: the attention rail, and the pipeline band's dead-letter numeral when
it is non-zero. `--green` MUST NOT appear anywhere on this page (a healthy
pipeline is the absence of alarm, not a celebration). Butler category hues MAY
appear only on ButlerMark letter-marks (daybook gutter, recent-activity rows).

The register selection (`register`, default `facts`), search query (`q`), kind
filters (`kind` / `validity` / `status`), and pagination `offset` are URL query
params so the browser back button and deep-links work; default values are NOT
written to the URL so deep-links round-trip. The search text input itself is
local state until submitted.

#### Scenario: Healthy day renders no alarm color
- **WHEN** there are zero dead-letter episodes, no overdue write-up, no
  anti-pattern rules, no high-importance fading facts, and no stale embeddings
- **THEN** the page MUST render zero `--red`, `--amber`, or `--green` pixels
- **AND** the attention rail body MUST collapse to one serif-italic line reading
  "Nothing waiting."

#### Scenario: Deep-link round-trips through defaults
- **WHEN** the page loads at `/memory` with no query params
- **THEN** the Facts register MUST be the focused register
- **AND** the URL MUST NOT be rewritten to add `register=facts` or any other
  default param

---

### Requirement: Memory overture band

The overture band MUST contain, top to bottom:

1. A mono **eyebrow** reading "MEMORY".
2. A **display headline** (44px) reading "What the house believes."
3. One **Voice sentence** (serif) narrating the system's own process — cadence,
   last run, and output — in the third person, never first person, never
   narrating content. Example: "Forty-one observations await the evening
   write-up; the last ran at 06:00 and produced twelve facts." The Voice
   sentence MUST be templated from `/api/memory/stats` fields (it is NOT
   produced by an LLM).
4. A **KPI strip** of exactly four hairline-divided cells, each a mono eyebrow
   over a mega-number, with no fills, bars, or badges: **PENDING**
   (`unconsolidated_episodes`), **ACTIVE FACTS** (active fact count),
   **PROVEN RULES** (proven rule count), **LAST WRITE-UP**
   (`last_consolidation_at`, formatted, with `last_consolidation_facts_produced`).

All numerals in the band MUST use `tabular-nums`.

#### Scenario: Voice sentence is templated, not generated
- **WHEN** the overture band renders the Voice sentence
- **THEN** the sentence MUST be produced by string templating over
  `/api/memory/stats` fields
- **AND** the page MUST NOT issue any LLM inference call to produce it

#### Scenario: Last write-up cell shows time and facts produced
- **WHEN** `last_consolidation_at` is "2026-06-12T06:00:00Z" and
  `last_consolidation_facts_produced` is 12
- **THEN** the LAST WRITE-UP cell MUST display the formatted time and "12 facts"

---

### Requirement: Memory pipeline band

The pipeline band MUST render the memory lifecycle as a single line of mono
tabular numerals joined by `─→` connectors, reading left to right as the flow
of observation into durable knowledge: episodes → pending → facts (with a
fading count) → rules (with a proven count), and a terminal dead-letters count.

The dead-letter numeral MUST render in `--red` when, and only when, it is
greater than zero; at zero it MUST render in the neutral foreground like every
other numeral in the band. No other numeral in the band may take state color.
The band MUST NOT render progress bars, gauges, sparklines, or a composite
health score.

#### Scenario: Dead letters earn red only when non-zero
- **WHEN** `dead_letter_episodes` is 0
- **THEN** the dead-letters numeral MUST render in the neutral foreground
- **WHEN** `dead_letter_episodes` is 3
- **THEN** the dead-letters numeral MUST render in `--red`

#### Scenario: Consolidation health readable without scrolling
- **WHEN** the page loads
- **THEN** the pending count, last write-up time, and dead-letter count MUST all
  be visible in the overture and pipeline bands before any scroll

---

### Requirement: Memory registers — three shapes

The register area MUST render exactly one focused register at a time, selected
by single-select kind pills (`Facts` default, `Rules`, `Episodes`) bound to the
`register` URL param. The three registers MUST use three distinct row shapes;
the page MUST NOT render the three kinds through one shared table shape. UI
labels MUST remain "Facts", "Rules", "Episodes" — the metaphor nouns
("ledger", "standing orders", "daybook") MUST NOT appear as labels.

**The ledger (Facts)** — hairline-separated grid rows, three columns:
`subject · predicate` (subject sans; entity-anchored subjects link to
`/entities/:id`; predicate mono muted, joined with `·`), `content` (sans,
single line, truncated; the whole row is the hit target opening
`/memory/facts/:id`), and a right-aligned mono `belief` column (effective
confidence to two decimal places followed by a two-letter permanence tag —
`pm` permanent · `st` stable · `sd` standard · `vo` volatile · `ep` ephemeral).
A `derived_from` glyph (`↳`, mono, muted) MUST appear at row end when the fact
has a `source_episode_id`. Fading rows MUST dim their entire foreground
(including content) to `--dim`; the default ledger view MUST NOT render
`superseded`, `expired`, or `retracted` facts unless an explicit validity
filter selects them.

**Standing orders (Rules)** — numbered directives with generous row padding:
a zero-padded `§NN` mono gutter (ordered by maturity rank then confidence), the
directive content (sans, wrapping, clamped to 2 lines in the register), a mono
tally line `applied N · helpful N · harmful N`, and the maturity as a plain
lowercase mono word (`candidate` · `established` · `proven` · `anti_pattern`).
The word `harmful` and its numeral MUST take `--red` only when harmful > 0;
anti-pattern rules MUST additionally carry a 2px left sliver in `--red`. No
colored maturity chips or pills.

**The daybook (Episodes)** — a journal feed grouped by day under mono
day-header rules (TODAY / YESTERDAY / dated): a 50px mono time gutter, a butler
letter-mark (the only place butler hue appears in the register), content (sans,
clamped to 2 lines, expandable in place), and a single consolidation glyph at
row end — `◦` pending (hollow), `•` consolidated (filled), `✕` dead-letter
(`--red`). Importance ≥ 8 MUST render the time gutter in `--fg` instead of
muted. The glyph MUST NOT be replaced by a word badge or chip.

Each ledger and standing-orders and daybook row MUST be clickable and MUST
navigate to the corresponding detail page (`/memory/facts/{id}`,
`/memory/rules/{id}`, `/memory/episodes/{id}`), with a `cursor-pointer` hover
affordance.

#### Scenario: Confidence renders as a mono numeral, never a bar
- **WHEN** a fact has effective confidence 0.94 and permanence "stable"
- **THEN** the belief column MUST read "0.94" as a mono tabular numeral followed
  by the tag "st"
- **AND** the row MUST NOT render a progress bar, donut, gauge, or percent sign
  for confidence

#### Scenario: Fading fact dims rather than colors
- **WHEN** a fact's validity is "fading"
- **THEN** the entire row foreground (subject, content, belief) MUST be rendered
  at `--dim`
- **AND** the row MUST NOT use color, strikethrough, or an opacity animation to
  signal decay

#### Scenario: Default ledger hides non-active validities
- **WHEN** the ledger renders with no validity filter
- **THEN** only `active` (and `fading`) facts MUST appear
- **AND** `superseded`, `expired`, and `retracted` facts MUST be omitted until a
  validity filter selects them

#### Scenario: Anti-pattern rule sliver is the only register state color
- **WHEN** a rule's maturity is `anti_pattern` and its harmful count is 4
- **THEN** the rule MUST render a 2px left sliver in `--red` and the `harmful 4`
  fragment of the tally in `--red`
- **AND** the maturity MUST render as the lowercase mono word "anti_pattern"
  with no colored chip

#### Scenario: Episode consolidation state is a glyph
- **WHEN** an episode is pending, consolidated, or dead-lettered
- **THEN** its row MUST render `◦`, `•`, or `✕` respectively at row end
- **AND** it MUST NOT render a word badge such as "Consolidated" or a colored
  chip

#### Scenario: Derived-from glyph links provenance
- **WHEN** a fact has a non-null `source_episode_id`
- **THEN** the ledger row MUST render a muted mono `↳` glyph at row end
- **AND** clicking the row MUST open `/memory/facts/{id}` (the detail page
  carries the episode link)

---

### Requirement: Belief typography

Every memory belief signal MUST render per the following table; the listed
"Never" renderings are prohibited on the `/memory` page and its detail pages:

| Signal | Rendering | Never |
|---|---|---|
| Effective confidence | mono tabular numeral, 2 decimal places | progress bar, donut, gauge, percent sign |
| Decay | foreground dims to `--dim` at the fading threshold | color, strikethrough, opacity animation |
| Permanence | two-letter mono tag, muted | colored chip, icon |
| Confirmation | detail-page mono stamp (`confirmed <date> · healthy`) | green check, toast celebration |
| Consolidation state | glyph `{◦ • ✕}` | word badge ("Consolidated") |
| Rule maturity | lowercase mono word | colored pill, star rating |
| Rule harm | `--red` on the harmful tally + 2px left sliver when anti-pattern | red row background |
| Importance | ink weight (muted → `--fg`) | flame icons, numbered badges |

The fading threshold MUST be computed from **effective (decayed)** confidence,
not raw stored confidence. There MUST be no composite "memory health score" or
any aggregate letter/colour grade anywhere on the memory surface.

#### Scenario: No health score anywhere
- **WHEN** any memory page or detail page renders
- **THEN** it MUST NOT display a composite health score, grade, or traffic-light
  summary of memory health

---

### Requirement: Memory unified search

The memory page MUST expose exactly one search affordance: a single input at the
top of the register area, scoped by the kind pills, backed by
`GET /api/memory/inspect`. There MUST NOT be a second search box anywhere on the
page (no per-register or per-tab search inputs).

Pressing `/` MUST focus the input; pressing Enter MUST submit the query and kind
to the `q` and `register`/`kind` URL params. Results MUST render in the register
shape of their kind (under mono kind-group headers when the search spans kinds);
search MUST NOT introduce a fourth row shape. Clearing the query MUST restore
the browsing register. An empty result set MUST render one serif-italic line —
"Nothing in the books."

Register pagination MUST be offset-based with page size **50**, rendered as a
footer `1–50 of N` with prev/next pills.

#### Scenario: Single search affordance
- **WHEN** the page renders any register
- **THEN** there MUST be exactly one search input on the page
- **AND** no per-register or per-tab search input MUST be rendered

#### Scenario: Page size is 50
- **WHEN** a register has more than 50 rows
- **THEN** the register MUST show the first 50 rows and a pagination footer
  reading "1–50 of N" with prev/next pills bound to the `offset` URL param

#### Scenario: Empty search result
- **WHEN** a submitted search returns no rows
- **THEN** the register area MUST render the serif-italic line "Nothing in the
  books."

---

### Requirement: Memory attention rail and recent activity

The registers+rail band MUST render an **attention rail** in its right column as
the only surface (besides the pipeline dead-letter numeral) where state color
appears. The rail MUST render at most these five condition rows, each only when
its state exists, each carrying at most one commit-class action:

| Condition | Severity | Reads | Action target |
|---|---|---|---|
| dead-letter episodes > 0 | red | "N episodes dead-lettered" | `/memory?register=episodes&status=dead_letter` |
| consolidation stalled (last run > 2× cadence) | amber | "write-up overdue · last <time>" | **none — action-less** |
| rule turned anti-pattern / harmful streak | red | "§NN harmful ×N" | rule detail page |
| high-importance fact entering fading | amber | "N important facts fading" | `/memory?register=facts&validity=fading` |
| stale embeddings (model drift) | amber | "N rows on old embedding" | housekeeping band |

The **"write-up overdue" row MUST be action-less**: it MUST NOT carry a "run
consolidation now" affordance, nor any other control that triggers a
consolidation run. This is a permanent cost guard — consolidation is a
pre-existing scheduled cron, and a run-now affordance is the only place a future
change could multiply that spawn cost. `--amber` MUST appear only in the rail.

When no condition holds, the rail header MUST remain and the body MUST collapse
to one serif-italic line — "Nothing waiting."

Below the attention rail, a **Recent activity** sub-surface MUST render the
most recent memory events as a quiet list (mono time · ButlerMark · sans
summary), with no color, no type badges, and no card chrome. It MUST default to
20 rows and MUST NOT render a decorative vertical-line-with-dots timeline.

#### Scenario: Write-up overdue row has no run-now affordance
- **WHEN** the rail renders the "write-up overdue" row
- **THEN** the row MUST NOT contain a "run consolidation now" button or any
  control that triggers a consolidation run

#### Scenario: Empty rail collapses to a serif line
- **WHEN** no rail condition holds
- **THEN** the rail header MUST remain and the body MUST read "Nothing waiting."
  in serif italic

---

### Requirement: Legacy MemoryBrowser preserved for ButlerMemoryTab

The legacy `MemoryBrowser` component (and its optional `butlerScope` prop that filters all queries to a specific butler) MUST be preserved for consumption by `ButlerMemoryTab` on butler detail pages, which is out of scope for this redesign. The new `/memory` page MUST NOT depend on `MemoryBrowser` or its `butlerScope` prop; the house-ledger registers replace it on `/memory` only.

A future change MAY migrate `ButlerMemoryTab` onto the house-ledger registers;
until then the legacy component MUST continue to function unchanged so the
butler-scoped tab does not break silently.

#### Scenario: ButlerMemoryTab keeps the legacy browser
- **WHEN** a butler detail page renders its memory tab
- **THEN** it MUST continue to use the legacy `MemoryBrowser` with
  `butlerScope` set to that butler
- **AND** the new `/memory` page MUST NOT render `MemoryBrowser`

---

### Requirement: Memory hooks (house-ledger)

The redesigned memory domain MUST use TanStack Query hooks for stats, the three
registers, the unified search, recent activity, and the three detail records.
Register and stats queries MUST be parameterised by the URL state
(`register` / `q` / `kind` / `validity` / `status` / `offset`). The fact detail
mutations MUST be exposed as `useConfirmFact()` and `useRetractFact()` hooks
that invalidate the affected fact and stats query keys on success, and these
hooks MUST only render their corresponding commit pills when the backend
confirm/retract endpoints are present (no dead buttons).

#### Scenario: Confirm/Retract gated on backend
- **WHEN** the confirm and retract endpoints are unavailable
- **THEN** the fact detail page MUST NOT render the Confirm or Retract commit
  pill (rather than rendering a non-functional button)

## MODIFIED Requirements

### Requirement: Fact detail page

The dashboard SHALL render a Fact detail page at `/memory/facts/:factId` using
the editorial detail skeleton (eyebrow / content-as-heading / state line / KV
band / kind section / provenance / commit footer), not a card-and-badge stack.

The page MUST display breadcrumb navigation: Memory > Facts > {subject}.

The page MUST display:

- **Heading region:** a mono eyebrow ("FACT"), the fact `content` rendered as
  the editorial heading, the `subject` and `predicate` as supporting identity
  (subject links to `/entities/:id` when entity-anchored).
- **Belief state line:** one mono line stating the decay arithmetic honestly —
  `confidence <raw> · decays <decay_rate>/day · last confirmed <relative> ·
  effective <effective>` — plus the two-letter permanence tag, the validity,
  and the scope. Confidence and effective confidence MUST be mono numerals
  (never bars); a fading fact's state line dims to `--dim`. There MUST be no
  confidence progress bar and no colored permanence/validity/scope word badges.
- **Provenance:** Source butler (when present), Source episode (a link to
  `/memory/episodes/{source_episode_id}` when present), Supersedes (a link to
  `/memory/facts/{supersedes_id}` when present), and Superseded-by (a link to
  `/memory/facts/{superseded_by}` when the reverse lookup returns one). When the
  fact has no provenance at all, the provenance section AND its eyebrow MUST be
  omitted (no empty section).
- **KV band:** Reference count, tags, and metadata (metadata as a mono code
  block when non-empty) and timestamps (Created at, Last referenced at, Last
  confirmed at).
- **Commit footer:** a primary **Confirm** pill (the sole commit-class action)
  and a secondary **Retract** pill, each with a 5s pill-morph confirm, **gated
  on the backend `confirm` / `retract` endpoints** — when an endpoint is
  absent the corresponding pill MUST NOT render (never a dead button).

The page MUST delegate loading and error states to the detail-page shell.

#### Scenario: Fact decay arithmetic line
- **WHEN** a fact has confidence 0.94, decay_rate 0.002, was last confirmed 12
  days ago, and has effective confidence 0.92
- **THEN** the belief state line MUST read "confidence 0.94 · decays 0.002/day ·
  last confirmed 12d ago · effective 0.92" in a mono line
- **AND** it MUST NOT render a confidence progress bar

#### Scenario: Fact with source episode link
- **WHEN** a fact has `source_episode_id` set
- **THEN** the provenance section MUST render a clickable link to
  `/memory/episodes/{source_episode_id}`

#### Scenario: Fact superseded-by reverse link
- **WHEN** `GET /api/memory/facts/:id` returns a non-null `superseded_by`
- **THEN** the provenance section MUST render a "Superseded by" link to
  `/memory/facts/{superseded_by}`

#### Scenario: Empty provenance omits its eyebrow
- **WHEN** a fact has no source butler, no source episode, no supersedes, and no
  superseded-by
- **THEN** the provenance section AND its eyebrow MUST both be omitted

#### Scenario: Confirm/Retract pills gated on backend
- **WHEN** the `POST /api/memory/facts/:id/confirm` endpoint is available
- **THEN** the Confirm commit pill MUST render and dispatch to it on confirm
- **WHEN** the endpoint is unavailable
- **THEN** the Confirm pill MUST NOT render

---

### Requirement: Rule detail page

The dashboard SHALL render a Rule detail page at `/memory/rules/:ruleId` using
the editorial detail skeleton, not a card-and-badge stack.

The page MUST display breadcrumb navigation: Memory > Rules > Rule.

The page MUST display:

- **Heading region:** a mono eyebrow ("RULE"), the rule `content` rendered as
  the editorial heading (this is the record identity; "Rule" MUST NOT be used as
  the title).
- **State line:** maturity as a lowercase mono word, scope, and permanence as a
  two-letter mono tag, in one line; no colored maturity/scope/permanence word
  badges. Anti-pattern rules MUST carry the `--red` left sliver and the `harmful`
  tally fragment in `--red`.
- **Outcome record:** a mono tally `applied N · helpful N · harmful N`, the
  effectiveness as a mono numeral (never a progress bar), and the confidence /
  decay arithmetic line as on the fact page.
- **Provenance:** Source butler and Source episode (link to
  `/memory/episodes/{id}`) when present; the section and its eyebrow MUST be
  omitted when no provenance exists.
- **KV band:** tags, metadata (mono code block when non-empty), and timestamps
  (Created at, Last applied at, Last evaluated at).

The page MUST delegate loading and error states to the detail-page shell.

#### Scenario: Rule detail page title shows content summary
- **WHEN** a rule has `content = "Always acknowledge messages within 24 hours of receipt"`
- **THEN** the editorial heading MUST read that content
- **AND** it MUST NOT read "Rule"

#### Scenario: Rule effectiveness renders as a numeral
- **WHEN** a rule has an effectiveness score
- **THEN** the outcome record MUST render it as a mono numeral
- **AND** it MUST NOT render an effectiveness progress bar

---

### Requirement: Episode detail page

The dashboard SHALL render an Episode detail page at `/memory/episodes/:episodeId`
using the editorial detail skeleton, not a card-and-badge stack.

The page MUST display breadcrumb navigation: Memory > Episodes > Episode.

The page MUST display:

- **Heading region:** a mono eyebrow ("EPISODE"), the episode `content` rendered
  as the editorial heading; the record-identity subtitle is the `session_id`
  when present, otherwise `Episode {id.slice(0,8)}`. A butler letter-mark
  (ButlerMark) carries the only butler hue on the page.
- **State line:** a single consolidation glyph `{◦ • ✕}` (never a word badge),
  the importance conveyed by ink weight (importance ≥ 8 in `--fg`), and the
  reference count.
- **Derived facts:** a list of facts whose `source_episode_id` equals this
  episode (fetched via the facts `source_episode_id` filter), each linking to
  `/memory/facts/{id}`; the section and its eyebrow MUST be omitted when empty.
- **KV band:** Session ID, Expires at (when present), metadata (mono code block
  when non-empty), and timestamps (Created at, Last referenced at).

The page MUST delegate loading and error states to the detail-page shell.

#### Scenario: Episode consolidation state is a glyph, not a badge
- **WHEN** an episode is consolidated
- **THEN** the state line MUST render the `•` glyph
- **AND** it MUST NOT render a "Consolidated" word badge or colored chip

#### Scenario: Episode shows its derived facts
- **WHEN** facts exist with `source_episode_id` equal to this episode's id
- **THEN** the page MUST list those facts, each linking to `/memory/facts/{id}`
- **WHEN** no such facts exist
- **THEN** the derived-facts section AND its eyebrow MUST be omitted
