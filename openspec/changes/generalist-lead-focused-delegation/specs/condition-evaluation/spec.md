## ADDED Requirements

### Requirement: Minimal predicates are bounded and source-bound
Conditions SHALL use exactly S4's tagged AST, bounds, immutable source/grant boundary, instance fields and three-valued evaluation. Text/code predicates, implicit subscribers and model evaluation SHALL be refused.

ID: REQ-condition-evaluation-001
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, proposed shared switch interfaces S4; bu-dhloi1.19
Scope: v1-mandatory

#### Scenario: An AST exceeds its language
- **WHEN** a predicate has an unknown tag/field, depth over2, more than16 leaves or more than8KiB
- **THEN** registration refuses before any cursor, subscription or intent is created

#### Scenario: Unknown input is evaluated
- **WHEN** a declared input is unmeasurable or unavailable
- **THEN** unknown propagates by all/any logic without becoming false or healthy

#### Scenario: A leaf names another subject
- **WHEN** a decision/inbox/effect ID belongs to a different bound goal or source
- **THEN** registration refuses the mismatch rather than widening its subscriber/grant

### Requirement: Evaluation persists one fire and recoverable intent
The evaluator SHALL implement S4's register/evaluate/consume_fire contract and S2's transactional fire/outbox boundary, stable events, durable cursor, generation CAS and finite expiry. Restart, duplicates and concurrency SHALL neither lose accepted work nor create a second successor.
Each instance SHALL be owned by one envelope generation and use S2.2's exclusive prior-fire cursor and cumulative envelope bound. Fired instances SHALL NOT be reset; the fenced lifecycle owner alone creates the next generation. Rearm/cursor replay/fire/consumption SHALL preserve the exact original approval and no-second-wake identity.

ID: REQ-condition-evaluation-002
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, proposed shared switch interfaces S4/S2; bu-dhloi1.19
Scope: v1-mandatory

#### Scenario: Two evaluators race on one event
- **WHEN** both attempt open-to-fired for the same instance/generation
- **THEN** one receipt/intent commits and the loser returns the existing outcome

#### Scenario: Crash occurs after commit
- **WHEN** the evaluator restarts before launch consumer acknowledgement
- **THEN** cursor replay returns the same unique intent, with no second fire or implicit effect execution

#### Scenario: An expired or cancelled condition reappears
- **WHEN** late events replay after expiry/Stop
- **THEN** no new fire occurs and the historical result remains visible

#### Scenario: Evaluator access fails
- **WHEN** snapshot or cursor persistence is unavailable
- **THEN** accepted work remains recoverable and system.dep.conditions reports unavailable, never an invented wake

#### Scenario: A subsequent generation resumes from the prior fire
- **WHEN** a successor returns to waiting and its bounded rearm commits, then the evaluator crashes
- **THEN** restart resumes that generation and complete unseen cursor interval, returns the same intent for duplicate events and never resets count or fired instances

#### Scenario: Widening is not a rearm
- **WHEN** a request changes predicate/plan/source/effect digest, deadline or maximum rather than narrowing effective grants
- **THEN** the existing approval cannot cover it; the rearm refuses and a genuinely new envelope must satisfy its original approval boundary
