## MODIFIED Requirements

### Requirement: Learning Velocity Calculation

The system SHALL compute `velocity_nodes_per_week` as the average number of nodes that transitioned to
`mastered` status per week over the last 4 calendar weeks prior to `snapshot_date`. Each week is a
7-day bucket. The average MUST be computed across all 4 weeks, including weeks with zero mastered-node
transitions.

A node counts as having "transitioned to `mastered`" only through a mastery transition ledger entry with
`to_status = 'mastered'` and `origin = 'evidence'`, bucketed by that entry's `occurred_at`. The calculation
MUST NOT use `mind_map_nodes.updated_at`, which changes for reasons unrelated to mastery, and MUST NOT count
`legacy_unknown` entries, which record only that a node was already mastered before the ledger existed. A node
that regresses and is mastered again contributes one transition per qualifying ledger entry. Legacy entries
likewise MUST NOT contribute to the inputs of `estimated_completion_days`.

ID: REQ-module-education-analytics-001
Source: bu-h40h2b.4 (education-evidence-honesty); bu-6e8j89
Scope: v1-mandatory

#### Scenario: velocity is the 4-week average of mastered-node transitions

WHEN the last 4 weeks saw 5, 3, 4, and 2 nodes reach `mastered` status respectively
THEN `metrics.velocity_nodes_per_week` equals 3.5

#### Scenario: weeks with no newly mastered nodes contribute zero to the average

WHEN the last 4 weeks saw 0, 0, 7, and 1 nodes reach mastered status
THEN `metrics.velocity_nodes_per_week` equals 2.0

#### Scenario: velocity is 0.0 when no nodes were mastered in any of the last 4 weeks

WHEN no nodes transitioned to mastered status in the last 28 days
THEN `metrics.velocity_nodes_per_week` equals 0.0


#### Scenario: node updates that are not mastery transitions do not count

WHEN a node is edited, re-labelled or re-sequenced in the last 28 days without any ledger entry to `mastered`
THEN `metrics.velocity_nodes_per_week` is unaffected

#### Scenario: legacy mastered nodes do not contribute to velocity

WHEN 6 nodes were already `mastered` before the ledger existed and carry `legacy_unknown` entries
AND no evidence-origin transition to `mastered` occurred in the last 28 days
THEN `metrics.velocity_nodes_per_week` equals 0.0
