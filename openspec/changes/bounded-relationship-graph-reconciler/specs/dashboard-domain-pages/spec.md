## ADDED Requirements

### Requirement: [TARGET-STATE] Rule effectiveness ratio shows its denominator
The Rule detail page SHALL render the effectiveness ratio only when the rule's `applied_count` is at least 1, and in that case it SHALL render the ratio together with its tallies, which are always shown. When `applied_count` is exactly 0, the page SHALL NOT render a numeric ratio and SHALL render the exact text `No applications yet` in its place. The threshold is exactly the zero denominator and no other named threshold. The page SHALL NOT fabricate a ratio, SHALL NOT alter rule scoring, and SHALL preserve the displayed value for every positive denominator. This requirement refines the effectiveness numeral of "Rule detail page"; it does not change its other contents. The registers that deliberately render tallies but no ratio SHALL stay as they are.

ID: REQ-dashboard-domain-pages-050
Source: RFC0031 Amendment 1; bu-h40h2b.7 acceptance item 1(3) and design step 4; dashboard-domain-pages Rule detail page
Scope: v1-mandatory

#### Scenario: Never-applied rule shows the empty state
- **WHEN** a rule has `applied_count = 0`
- **THEN** the outcome record SHALL show `No applications yet` and no numeric effectiveness
- **AND** the `applied`, `helpful` and `harmful` tallies SHALL still render

#### Scenario: Applied rule keeps its ratio
- **WHEN** a rule has `applied_count >= 1`
- **THEN** the outcome record SHALL render the stored effectiveness value as before
- **AND** it SHALL NOT render a progress bar
