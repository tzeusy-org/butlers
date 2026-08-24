## ADDED Requirements

### Requirement: Dispatch Intent Derivation
The system SHALL derive a `DispatchIntent` for every dispatch deterministically from
its trigger source and complexity tier, with no model call, no clock, no randomness,
and no read of prompt content. The intent records what the dispatch requires
(required capabilities, optional context floor, deadline, per-call budget), what it
would prefer, and how consequential the run is.

#### Scenario: Trigger source reduces to a trigger class
- **WHEN** a dispatch intent is derived from a trigger source
- **THEN** `schedule:<task>` and `deadline:<task>` reduce to the `schedule` and
  `deadline` classes, because the task name identifies which job runs, never what
  that job needs from a model
- **AND** an unrecognized trigger source reduces to the `unknown` class

#### Scenario: Tool-wired triggers require tool use
- **WHEN** a dispatch intent is derived for any trigger source that the spawner
  wires MCP servers for — every source except `healing` and `qa`
- **THEN** the intent requires the `tool_use` capability
- **AND** intents for `healing` and `qa` require no capabilities, matching their
  empty MCP wiring

#### Scenario: Unknown trigger is treated as the most consequential
- **WHEN** a dispatch intent is derived from an unrecognized trigger source
- **THEN** its consequence is `external`, not `observe`, so an unclassified caller
  never gets the most permissive fit rules by default

#### Scenario: Intent is prompt-free and JSON-safe
- **WHEN** an intent is projected for a resolution receipt
- **THEN** every field is a trigger-derived or caller-supplied scalar or feature
  name, so the projection can be stored and rendered without redaction

### Requirement: Model Capability Descriptors
The system SHALL describe a candidate model's capabilities with three-valued support
— supported, unsupported, unknown — layering a per-catalog-entry envelope over the
capability baseline declared by its runtime adapter. An absent feature reads as
unknown, never as unsupported.

#### Scenario: Adapter baseline answers for every registered runtime type
- **WHEN** a capability baseline is requested for a registered runtime type
- **THEN** it reports `session_resume` from the adapter's `supports_resume` and any
  further capabilities the adapter class declares
- **AND** an unregistered runtime type yields an all-unknown baseline, so it fails
  closed rather than being assumed capable

#### Scenario: Per-entry envelope refines rather than replaces the baseline
- **WHEN** a catalog entry declares a capability envelope
- **THEN** its keys override the adapter baseline key by key and every other
  baseline key survives
- **AND** an empty envelope — the default for every pre-existing entry — leaves the
  baseline unchanged

#### Scenario: Unusable envelope is rejected without echoing its value
- **WHEN** a stored envelope is not a JSON object, names a feature the system does
  not define, or carries a non-boolean value
- **THEN** the descriptor layer raises rather than silently reading the entry as
  requirement-free
- **AND** the error text names the offending key and the value's type, never the
  value itself

### Requirement: Hard Fit Evaluation
The system SHALL evaluate a candidate's hard fit against a dispatch intent and
return every reason it does not fit, not only the first.

#### Scenario: Explicitly unsupported required capability always excludes
- **WHEN** a candidate declares a required capability as unsupported
- **THEN** it is excluded at every consequence level, because that is proof of misfit

#### Scenario: Unknown required capability fails closed above observe
- **WHEN** a required capability's support is unknown for a candidate
- **THEN** the candidate is excluded when the intent's consequence is `reversible`
  or `external`
- **AND** at `observe` consequence the unknown is tolerated and recorded as an
  advisory, because an internal artifact nobody is waiting on is the one place where
  an unproven capability costs nothing outside the system

#### Scenario: Context floor excludes an undeclared window
- **WHEN** an intent sets a minimum context requirement
- **THEN** a candidate with a smaller declared window is excluded
- **AND** a candidate with no declared window is also excluded, because an
  undeclared envelope cannot satisfy a requirement

#### Scenario: Deadline and budget exclude only on evidence
- **WHEN** an intent sets a deadline or a per-call budget
- **THEN** a candidate is excluded only when its observed latency percentile is
  known to overrun, or its reference cost is known to exceed the budget
- **AND** a candidate with no latency history and a candidate with no known price
  are not excluded, so the fleet can still try something new and can still run
  models that are free, local, or subscription-covered

#### Scenario: Preference never excludes
- **WHEN** a candidate does not provide a preferred (not required) capability
- **THEN** it remains eligible and the unmet preference is recorded as an advisory
- **AND** the preference does not change the candidate's rank

### Requirement: Resolution Receipt
The system SHALL produce a prompt-free receipt for every intent-aware resolution,
recording the requested intent, the effective intent actually resolved against, each
candidate's outcome and fit findings, the age of the evidence behind any score, and
the reason the winner won.

#### Scenario: Requested and effective intent are both recorded
- **WHEN** tier fallthrough moves resolution to a lower tier
- **THEN** the receipt carries both the requested intent and an effective intent
  that differs only in its complexity tier, so the tier change is auditable rather
  than re-derived
- **AND** the requirement envelope is not rewritten during fallthrough

#### Scenario: Every candidate's fate is recorded
- **WHEN** a resolution completes
- **THEN** each candidate is recorded as selected, eligible, excluded on hard fit,
  excluded on quota, not top priority, or in a tier that was never reached
- **AND** a candidate in a tier ABOVE the winning tier is recorded as excluded on
  hard fit, since that tier lost only because none of its entries fit

#### Scenario: Winner reason is explicit
- **WHEN** a winner is selected
- **THEN** the receipt names the reason as sole candidate, evidence score, or
  round robin

#### Scenario: Receipt survives a quota-exhausted resolution
- **WHEN** an intent-aware resolution raises tier quota exhaustion
- **THEN** the receipt is carried on the raised error, because a quota-blocked
  dispatch is exactly when the record of what was considered is most useful
