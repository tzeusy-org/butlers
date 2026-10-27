## ADDED Requirements

### Requirement: Baseline watch schedule

The Health butler SHALL register a daily `baseline_watch` scheduled job with `dispatch_mode = "job"` whose handler is the deterministic personal-baselines watch.

#### Scenario: The schedule resolves to a handler

- **WHEN** the Health butler configuration is loaded
- **THEN** a `baseline_watch` schedule SHALL exist and resolve to a registered deterministic job handler
