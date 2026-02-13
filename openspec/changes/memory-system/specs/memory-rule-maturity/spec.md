## ADDED Requirements

### Requirement: Rule maturity levels

Rules SHALL have a `maturity` field with four levels: `candidate` (new rules, lower retrieval weight), `established` (reliable, full retrieval weight), `proven` (highest weight, core system knowledge), and `anti_pattern` (warning state for repeatedly harmful guidance).

#### Scenario: New rule starts as candidate
- **WHEN** a rule is created via memory_store_rule
- **THEN** its maturity SHALL be 'candidate'

#### Scenario: Candidate retrieved with lower weight
- **WHEN** memory_recall returns a candidate rule and a proven rule with equal relevance
- **THEN** the proven rule SHALL rank higher due to maturity weighting

### Requirement: Maturity promotion from candidate to established

A rule SHALL be promoted from `candidate` to `established` when `success_count >= 5` AND `effectiveness_score >= 0.6`.

#### Scenario: Rule promoted to established
- **WHEN** memory_mark_helpful is called on a candidate rule
- **AND** the rule's success_count reaches 5 with effectiveness_score 0.7
- **THEN** the rule's maturity SHALL be updated to 'established'

#### Scenario: Rule not promoted with low effectiveness
- **WHEN** a candidate rule has success_count=5 but effectiveness_score=0.4
- **THEN** the rule SHALL remain a 'candidate'

### Requirement: Maturity promotion from established to proven

A rule SHALL be promoted from `established` to `proven` when `success_count >= 15` AND `effectiveness_score >= 0.8` AND the rule is at least 30 days old.

#### Scenario: Rule promoted to proven
- **WHEN** memory_mark_helpful is called on an established rule
- **AND** the rule has success_count=15, effectiveness_score=0.85, and was created 35 days ago
- **THEN** the rule's maturity SHALL be updated to 'proven'

#### Scenario: Rule not promoted due to age
- **WHEN** an established rule has success_count=15 and effectiveness_score=0.85 but was created 20 days ago
- **THEN** the rule SHALL remain 'established'

### Requirement: Effectiveness scoring with 4x harmful weight

Effectiveness SHALL be calculated as: `effectiveness_score = success_count / (success_count + 4 × harmful_count + 0.01)`. The 4x multiplier on harmful_count ensures bad rules are penalized aggressively.

#### Scenario: Effectiveness calculation
- **WHEN** a rule has success_count=10 and harmful_count=2
- **THEN** its effectiveness_score SHALL be `10 / (10 + 8 + 0.01)` ≈ 0.555

#### Scenario: Effectiveness with no feedback
- **WHEN** a rule has success_count=0 and harmful_count=0
- **THEN** its effectiveness_score SHALL be `0 / (0 + 0 + 0.01)` = 0.0

### Requirement: Maturity demotion on harmful marks

When `memory_mark_harmful` is called and the recalculated effectiveness_score drops below the threshold for the current maturity level, the rule SHALL be demoted. Established rules drop to candidate if effectiveness < 0.6. Proven rules drop to established if effectiveness < 0.8.

#### Scenario: Established rule demoted
- **WHEN** memory_mark_harmful is called on an established rule
- **AND** the recalculated effectiveness_score is 0.45
- **THEN** the rule's maturity SHALL be demoted to 'candidate'

### Requirement: Anti-pattern inversion for repeatedly harmful rules

When a rule has `harmful_count >= 3` AND `effectiveness_score < 0.3`, the rule SHALL be inverted into an anti-pattern. The content SHALL be rewritten as: "ANTI-PATTERN: Do NOT {original rule content}. This caused problems because: {accumulated harmful reasons}". Anti-patterns SHALL remain in the system as warnings.

#### Scenario: Rule inverted to anti-pattern
- **WHEN** memory_mark_harmful is called with reason "caused incorrect dietary advice"
- **AND** the rule now has harmful_count=3 and effectiveness_score=0.2
- **THEN** the rule's content SHALL be rewritten as an anti-pattern warning
- **AND** the rule's maturity SHALL be set to `anti_pattern`

#### Scenario: Anti-pattern included in retrieval
- **WHEN** memory_recall is called
- **AND** an anti-pattern rule is relevant to the query
- **THEN** the anti-pattern SHALL appear in results as a warning
