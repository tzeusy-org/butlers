## ADDED Requirements

### Requirement: Token Source and Theme Mapping
Every design token SHALL be defined only in `frontend/src/index.css`: light values under `:root`, dark overrides under `.dark`, and the Tailwind utility mapping in a single `@theme inline` block. Component code consumes tokens through utilities or `var()` and introduces no custom property of its own.

The generic component-library aliases (`--background`, `--foreground`, `--muted-foreground`, `--border`, `--ring`, and their kin) resolve to the Dispatch surface and state tokens rather than carrying independent values. Corner radii derive from one `--radius` base (`0.625rem`), with the `sm` through `4xl` steps computed from it. Beyond the surface, state, butler-identity, categorical, and chart ramps, the token file carries two domain ramps: `--severity-low|medium|high` for health severity bands and `--tier-1..6` for the six Dunbar relationship layers (5/15/50/150/500/1500), each always shown with its label. The document root sets the sans family token as the body font at line height 1.5 and weight 400, with font smoothing on and font synthesis off.

#### Scenario: No token defined outside the token file
- **WHEN** a diff declares a CSS custom property or a raw color literal
- **THEN** the declaration is in `frontend/src/index.css`
- **AND** component code references it by token name

#### Scenario: Theme switch re-resolves every alias
- **WHEN** the `.dark` class is toggled on the document root
- **THEN** every generic alias and Dispatch token resolves to its dark value from the same file, with no per-component theme branch

#### Scenario: Radius steps derive from the base
- **WHEN** a component uses a rounded-corner utility
- **THEN** its radius is computed from the single `--radius` base

### Requirement: Primitive Component Library
The dashboard's generic primitives (button, badge, card, dialog, sheet, table, form controls, tabs, tooltip, dropdown menu) SHALL be local component files backed by Radix UI headless primitives, with variants declared through class-variance-authority and styled only with design tokens, so every primitive inherits the Dispatch forms rather than a vendor default.

Primitives carry the forms this spec defines — Button Forms, Kind Tags, Status Indicators, List Primitive, KPI Strip — and a destructive variant uses the red state token. Overlay primitives (dialog, sheet, dropdown menu) own focus while open and close on Escape.

#### Scenario: Primitive variant resolves to a token
- **WHEN** a primitive renders any of its variants
- **THEN** every color, border, and radius in that variant resolves to a design token

#### Scenario: Overlay primitive owns focus
- **WHEN** a dialog or sheet is open
- **THEN** keyboard focus stays within it until it closes, and Escape closes it

### Requirement: Ruled Panel Grid
Dense multi-panel bodies (the butler detail tabs and the Settings console) SHALL compose as a ruled panel grid: a multi-column grid at wide viewports (four columns on the butler detail tabs), collapsing to fewer columns on narrow viewports, whose frame carries the top and left hairline and whose panels each carry the right and bottom hairline, so interior edges are never doubled.

A panel spans one or more columns, is titled by an eyebrow (Requirement: Eyebrow Section Titles) with an optional muted sub-label, sizes to its content unless it declares a fixed-height scroll body, and has no background fill. When a panel grid opens with KPI cells, a cell's value takes a state color only when its metric signals a degraded or notable state; neutral values stay in the foreground color, and butler hues never appear.

#### Scenario: Continuous ruled grid
- **WHEN** a panel grid renders
- **THEN** the frame has top and left hairlines, each panel has right and bottom hairlines, and no interior edge is doubled

#### Scenario: Panel scroll body
- **WHEN** a panel declares a fixed-height scroll body
- **THEN** its height is constrained and overflowing content is reachable by scrolling within the panel

#### Scenario: KPI value tone follows state
- **WHEN** a KPI cell's metric indicates a degraded state (for example an error count above zero)
- **THEN** the value renders in the matching state token
- **AND** a neutral value renders without a tone

### Requirement: Time Range Toggle
A surface that aggregates data over a selectable time range SHALL expose one shared range toggle offering exactly `24h`, `7d`, and `30d`, defaulting to `24h`; a surface with no time dimension renders none.

The toggle is a labeled group (`Time range`) of mono uppercase options. The selected option is marked with `aria-pressed` and rendered in the inverted foreground/background form (Requirement: Button Forms), never with a state color. One toggle governs the whole surface body; panels that do not use the range ignore it.

#### Scenario: Range toggle default
- **WHEN** a range-aware surface first mounts
- **THEN** the toggle selects `24h` and the selected option is visually inverted and announced as pressed

#### Scenario: Range toggle absent without a time dimension
- **WHEN** a surface has no time-ranged data
- **THEN** no range toggle renders on it

### Requirement: Page Primitive and Archetypes
Every dashboard page SHALL render inside the shared `<Page>` primitive (`frontend/src/components/ui/page.tsx`) with a declared archetype — `overview`, `list`, `detail`, `workspace`, `editor`, `editorial`, or `status-board` — and SHALL NOT reimplement chrome the primitive owns.

`<Page>` owns the page title (and the document title), breadcrumbs, header actions, and page-level status, and resolves page state in the order loading, error, empty, content, rendering an archetype-matched skeleton while loading. The archetype sets the width frame: `detail` 1024px max, `editor` 672px max, `editorial` the 1280px readable column, and `status-board` a consumer-owned header and footer slot. A `list` page handles its empty state inside its own body rather than through the page-level empty state.

#### Scenario: Page uses the shared primitive
- **WHEN** a dashboard page renders
- **THEN** its outermost container is `<Page>` with a declared archetype
- **AND** its content is passed as children rather than wrapped in a second page-level layout container

#### Scenario: Page state priority
- **WHEN** a page's primary read is loading, has failed, or is empty
- **THEN** `<Page>` renders the archetype skeleton, the error state, or the empty state in that priority, and never the empty state for a failed read

## MODIFIED Requirements

### Requirement: Butler Category Hues
Each butler SHALL have one assigned identity hue from `--category-1..12` (defined in
`frontend/src/index.css`), and the hue SHALL appear **only on the butler's letter-mark** — the
colored squircle with the butler's initial — never on backgrounds, borders, buttons, headers, or
anywhere else. The mapping is generated from `KNOWN_BUTLERS` in
`frontend/src/components/ui/ButlerMark.tsx` and the private identity token family in
`frontend/src/lib/visual-token-roles.ts`; those executable sources are authoritative, and this
table MUST be regenerated by hand from them whenever the roster or ramp changes. Canonical mapping
(slot 12 is unused headroom for the next butler added to the roster):

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
