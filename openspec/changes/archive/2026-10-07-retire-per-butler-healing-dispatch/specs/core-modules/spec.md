## MODIFIED Requirements

### Requirement: Load-All Module Loading
The `load_all()` method SHALL instantiate ALL registered modules regardless of `butler.toml` config presence. Modules listed in config receive their explicit config dict; unconfigured modules receive `{}`. Discovery SHALL NOT admit a module to daemon startup: the daemon SHALL select only explicitly declared module names, including modules without a config schema, before validation, migrations, startup hooks and tool registration. The selector SHALL reject unknown configured names before provisioning or any module migration, startup or registration effect. Existing failed/cascade-failed and runtime enabled-state semantics SHALL remain separate from declaration eligibility.

ID: REQ-core-modules-002
Source: heart-and-soul v1 opt-in modules; bu-1fe7xv round 2; src/butlers/daemon.py _select_startup_modules correction
Scope: v1-mandatory

#### Scenario: Unconfigured module loaded with empty config
- **WHEN** `load_all(modules_config)` is called and a registered module is not in `modules_config`
- **THEN** the module is instantiated with an empty dict `{}` as its config

#### Scenario: Omitted modules do not start regardless of schema shape
- **WHEN** a registered module with no config schema or an all-default or required-field schema is omitted from the butler's module declarations
- **THEN** registry discovery still instantiates it
- **AND** daemon startup does not invoke its migrations, startup hook or tool registration

#### Scenario: Actual roster startup respects declarations
- **WHEN** a healthy daemon starts from any current roster's valid explicit module configuration
- **THEN** its startup hook-invocation membership equals that roster's declared module names in dependency order
- **AND** module health failures and user-disabled state remain explicitly reported rather than becoming undeclared startup admission

## ADDED Requirements

### Requirement: QA Error Relay Module
Every roster butler SHALL declare the self_healing module and make its shared self-healing guidance reachable. The module SHALL register report_error and read-only get_healing_status, relay error reports only through Switchboard MCP routing to QA, and perform no local investigation dispatch, redispatch, worktree or shared recovery operation. Relay acceptance SHALL mean actual target reception rather than investigation or publication. Existing canonical identity, sensitive metadata, configuration and independent QA ownership SHALL remain authoritative.

ID: REQ-core-modules-003
Source: accepted RFC 0015 centralized pipeline; roster/qa/MANIFESTO.md; bu-1fe7xv rounds 2-4; archived qa-staffer D3 non-blocking relay contract
Scope: v1-mandatory

#### Scenario: Every roster exposes declared relay tools and guidance
- **WHEN** any current roster butler starts with healthy declared self_healing configuration
- **THEN** report_error and get_healing_status are registered under its canonical daemon identity and its shared skill is reachable
- **AND** retry_healing is not registered by this relay module
- **AND** report_error error_message, traceback and context keep their sensitive metadata

#### Scenario: Accepted QA reception is relayed truthfully
- **WHEN** registered report_error receives valid structured input and QA accepts its Switchboard-routed report_finding call
- **THEN** the reporter returns accepted=true with its fingerprint and a relay-reception message
- **AND** the existing fingerprint/severity computation, allow_stale=true and optional context omission are preserved
- **AND** no local investigation or claim of a dispatched agent, PR or deployed fix is produced

#### Scenario: Wrapped target rejection is not acceptance
- **WHEN** actual MCP routing returns a tool error, rejected target acceptance, empty or malformed target payload, or route error
- **THEN** report_error returns accepted=false with an explicit unavailable or rejected outcome
- **AND** it creates no local investigation or automatic ambiguous retry

#### Scenario: Missing QA or Switchboard remains bounded and visible
- **WHEN** the Switchboard client, QA registration or registry query is unavailable, or a relay exceeds its deadline
- **THEN** report_error returns accepted=false within the overall two-second relay budget
- **AND** ordinary caller work and error evidence remain available without per-butler fallback

#### Scenario: Local active history cannot suppress centralized relay
- **WHEN** a valid report matches an existing legacy active healing attempt and QA can receive it
- **THEN** report_error still relays the finding to QA's authoritative triage boundary
- **AND** it does not locally create, mutate or redispatch a healing attempt

#### Scenario: Disabled and legacy configuration remain compatible
- **WHEN** relay admission is disabled or legacy threshold keys are supplied
- **THEN** disabled admission gives an explicit rejection without routing
- **AND** accepted legacy dispatch threshold keys cannot alter QA policy or enable a local dispatcher

#### Scenario: Relay startup and shutdown do not run shared investigation cleanup
- **WHEN** a declared relay module starts or shuts down with an available pool
- **THEN** it performs no shared attempt recovery, worktree reaping or watchdog dispatch
- **AND** the central QA lifecycle and shared utility interfaces remain unchanged

#### Scenario: Switchboard self-report uses its existing MCP route tools
- **WHEN** Switchboard's declared relay reports an error without an external self-client
- **THEN** it calls its existing list_butlers and route MCP tools through the bounded local FastMCP client
- **AND** the QA call follows the same normalization, identity and non-dispatch rules

#### Scenario: QA self-report preserves the recursion barrier
- **WHEN** QA's relay reports a QA-origin finding with unknown or QA/healing source trigger provenance
- **THEN** the existing QA meta/self-recursion handling remains authoritative
- **AND** the relay creates no local recursive investigation

#### Scenario: Read-only status is history rather than deployment proof
- **WHEN** get_healing_status queries a fingerprint or the reporter's recent attempts
- **THEN** it preserves the existing read-only status query behavior
- **AND** relay guidance distinguishes reception, investigation, PR merge and deployment rather than treating a merged PR as a deployed fix
