## MODIFIED Requirements

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
  effectiveness as a mono numeral when `applied_count >= 1` (never a progress
  bar) and otherwise, when `applied_count` is exactly 0, the exact text
  `No applications yet` in place of the numeral, and the confidence /
  decay arithmetic line as on the fact page.
- **Provenance:** Source butler and Source episode (link to
  `/memory/episodes/{id}` only when `source_episode_status = 'available'`; a
  visible matching `Source expired` or `Source unresolved` state otherwise)
  when present; the section and its eyebrow MUST be omitted when no provenance
  exists.
- **KV band:** tags, metadata (mono code block when non-empty), and timestamps
  (Created at, Last applied at, Last evaluated at).

The page MUST delegate loading and error states to the detail-page shell.

The effectiveness threshold is exactly the zero denominator and no other named threshold. The page MUST NOT fabricate a ratio and MUST NOT alter rule scoring, and the displayed value for every positive denominator is unchanged. The registers that deliberately render tallies but no ratio stay as they are.

ID: REQ-dashboard-domain-pages-053
Source: RFC0031 Amendment 1; bu-h40h2b.7 acceptance item 1(3) and design step 4; dashboard-domain-pages Rule detail page
Scope: v1-mandatory

#### Scenario: Rule detail page title shows content summary

- **WHEN** a rule has `content = "Always acknowledge messages within 24 hours of receipt"`
- **THEN** the editorial heading MUST read that content
- **AND** it MUST NOT read "Rule"

#### Scenario: Rule effectiveness renders as a numeral

- **WHEN** a rule has `applied_count >= 1` and an effectiveness score
- **THEN** the outcome record MUST render it as a mono numeral
- **AND** it MUST NOT render an effectiveness progress bar

#### Scenario: Rule source availability has no dangling door

- **WHEN** a rule has `source_episode_id` set and its status is `expired` or
  `unresolved`
- **THEN** its provenance MUST render the matching visible source state
- **AND** it MUST NOT render a link to `/memory/episodes/{source_episode_id}`

#### Scenario: Never-applied rule shows the empty state

- **WHEN** a rule has `applied_count = 0`
- **THEN** the outcome record MUST show `No applications yet` and no numeric effectiveness
- **AND** the `applied`, `helpful` and `harmful` tallies MUST still render

#### Scenario: Applied rule keeps its ratio

- **WHEN** a rule has `applied_count >= 1`
- **THEN** the outcome record MUST render the stored effectiveness value as before
- **AND** it MUST NOT render a progress bar
