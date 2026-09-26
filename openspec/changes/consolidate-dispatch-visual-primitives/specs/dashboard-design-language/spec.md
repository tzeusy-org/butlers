## ADDED Requirements

### Requirement: Dispatch Surface Primitives

Dashboard surfaces SHALL use the semantic `Section` primitive as the default container. Section
uses semantic section markup, titles the region with the canonical Eyebrow treatment, and keeps
hierarchy in type and rules rather than card chrome. A quiet section with no active content SHALL
remove its chrome or render one calm serif-italic sentence, with no illustration or mock filler.

`Tile` SHALL be used only for a module in a dense status grid that can load or degrade
independently from its neighboring modules. A Tile exposes that module boundary without changing
the consumer's existing loading, error, empty, or retry semantics. Ordinary page composition and
single-surface content use Section.

The retired `ui/card` module SHALL have no production imports, re-export, compatibility alias, or
path shim. New code SHALL use Section or the narrow Tile role directly.

#### Scenario: Section is the default semantic surface

- **WHEN** a dashboard page composes an ordinary titled region
- **THEN** it uses Section with semantic section markup and an Eyebrow title
- **AND** the region has no elevated Card shell or nested Card chrome

#### Scenario: Quiet Section removes decoration

- **WHEN** a Section has no active content
- **THEN** it renders without chrome or as one calm serif-italic sentence
- **AND** it adds no illustration, mock content, or decorative filler

#### Scenario: Tile isolates dense-grid module state

- **WHEN** a dense status-grid module can load or degrade independently
- **THEN** it uses Tile and marks its own loading or degraded boundary
- **AND** a sibling module remains renderable when that Tile fails

#### Scenario: Card primitive is retired without an alias

- **WHEN** production frontend code imports or composes a dashboard container
- **THEN** it has no `ui/card` import or Card compatibility alias
- **AND** the canonical Section or narrow Tile primitive is used directly

#### Scenario: Operational state uses one semantic resolver

- **WHEN** a connector, Google Health account, or topology node renders operational state
- **THEN** its state mark uses StateDot, Sev, or `stateColorVar`, and operational state text uses `stateTextColorVar`
- **AND** both resolvers read the Operational state mappings in `VISUAL_TOKEN_ROLE_REGISTRY.state`
- **AND** degraded and error text resolve to the AA-safe `--amber-text` and `--red-text` tokens rather than the base fill tokens
- **AND** the consumer does not define a duplicate state-to-token map or a second visible status affordance

## MODIFIED Requirements

### Requirement: Semantic Visual Role Matrix
Every visual color request SHALL resolve through exactly one semantic role:

| Role | Resolver | Token family | Required signal |
|------|----------|--------------|-----------------|
| Butler identity | `ButlerMark` (private) | `--category-1..12` | letter-mark only |
| Operational state | `StateDot` / `stateColorVar` / `stateTextColorVar` | `--red`, `--red-text`, `--amber`, `--amber-text`, `--green`, `--dim`, `--state-unidentified`, `--muted-foreground` | state affordance |
| Local category | `categoricalHueVar` / `categoricalColor` | `--categorical-1..12` | label, icon, position, or legend |
| Chart series | `chartSeriesColor` / `chartColor` | `--chart-1..5` | series label or legend |
| Owner custom color | `ownerCustomColor` / `labelFillColors` | normalized opaque owner hex | owner label or legend |

Identity resolvers SHALL NOT be exported for general consumers. A local
category, chart series, or state SHALL never request a Butler identity token.
The registry in `frontend/src/lib/visual-token-roles.ts` is the executable
source for this table; the table and registry MUST be checked for parity.

#### Scenario: Every categorical and chart use is labeled
- **WHEN** a surface renders a local category or chart series
- **THEN** it provides a text label, icon, stable position, direct data label, or legend
- **AND** color is not the sole carrier of meaning
