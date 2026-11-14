## ADDED Requirements

### Requirement: Safety mitigations are exact, per item and always owner-approved

This requirement takes effect only when the owner adopts RFC 0028 Amendment 1; until then it is proposed and binds nothing.

The Home butler SHALL offer a mitigation for a safety condition only when the owner has declared one in `home.safety_mitigations` for a sensor in `home.safety_watch`: one exact `(domain, service)` from a closed allowlist of pairs that have a declared post-condition verifier, one explicit target entity and allowed data keys. The declaration SHALL be written only through an owner-authenticated route and SHALL be refused for an area, device, label, `all`, wildcard or multi-entity target, a data key outside the pair's allowlist, a data-carried selector, or a sensor not in the safety watch. A mitigation's target SHALL be treated as critical for that call. When the safety sentinel opens a `home:safety` condition it SHALL first attempt the owner alert unchanged, and SHALL then, separately, park one prepared action per declared mitigation for that sensor through `park_prepared_action` with `origin='prepared'`, dedup key `home:safety-mitigation:<condition id>:<target entity>` and the exact stored call. A parking failure SHALL NOT delay, alter or retract the alert. The job SHALL NOT dispatch any Home Assistant request, SHALL require an owner approval for every mitigation whatever its risk class, SHALL NOT offer a bundle that covers more than one item, SHALL run with no model in the loop and SHALL stay dark behind its own flag, off by default.

ID: REQ-home-deterministic-jobs-013
Source: RFC 0028 Amendment 1 (proposed, owner adoption required); bu-jv1moa.6; bu-jv1moa.29; bu-jv1moa.33
Scope: v1-mandatory

#### Scenario: An opened condition parks one action per declared mitigation

- **WHEN** the sentinel opens a leak condition for a sensor with one declared mitigation
- **THEN** the alert SHALL be sent as it is today
- **AND** exactly one prepared action SHALL be parked carrying the stored call unchanged, and a later run for the same condition SHALL NOT park a second

#### Scenario: Broad or bundled mitigations are refused

- **WHEN** a mitigation declaration names an area, device, label, `all` or several entities, or asks to secure several items at once
- **THEN** the route SHALL store nothing and SHALL answer with one fixed content-blind code

#### Scenario: Parking cannot hurt the alert

- **WHEN** parking a mitigation fails
- **THEN** the alert SHALL still be sent unchanged and the failure SHALL be recorded by outcome only

#### Scenario: A model cannot author or trigger a mitigation

- **WHEN** a model session or tool argument supplies a call for a safety sensor
- **THEN** no mitigation SHALL be parked from it, and only the owner's declared call SHALL ever be stored

### Requirement: Mitigation replay holds only while the condition is open and the call is unchanged

This requirement takes effect only when the owner adopts RFC 0028 Amendment 1; until then it is proposed and binds nothing.

Before dispatching every replay of an approved mitigation, the shared replay gate SHALL confirm that the originating `home:safety` condition is still active and that the stored call's `args_digest` equals the digest of the current declaration's call. A resolved, missing or different condition, an edited or removed declaration, or an unreadable condition ledger SHALL send no request and write no receipt. Each sentinel run SHALL withdraw a still-pending mitigation with system actor `system:home-safety-mitigation` and a content-blind reason `condition_resolved`, `declaration_changed`, `mitigation_removed` or `watch_removed`, recorded so that it is distinguishable from an owner rejection, and SHALL leave it pending when the ledger cannot be read. A prepared mitigation SHALL expire 2 hours after parking if not approved. No receipt SHALL be edited, and no text SHALL say a mitigation was undone, reverted or restored.

ID: REQ-home-deterministic-jobs-014
Source: RFC 0028 Amendment 1 (proposed, owner adoption required); module-approvals status transitions; bu-jv1moa.2 (args digest); bu-jv1moa.33
Scope: v1-mandatory

#### Scenario: A resolved condition withdraws a pending mitigation

- **WHEN** the leak condition resolves while its mitigation is still pending
- **THEN** the mitigation SHALL be withdrawn by the system actor with reason `condition_resolved` and no request SHALL be sent

#### Scenario: An old approval cannot actuate after resolution

- **WHEN** an approved mitigation replays after its condition resolved
- **THEN** the replay SHALL send nothing and write no receipt

#### Scenario: An edited declaration does not run the old call

- **WHEN** the owner edits the mitigation declaration after the action was parked and then approves the old action
- **THEN** the replay SHALL refuse as `declaration_changed` and no request SHALL be sent

#### Scenario: An unreadable ledger fails closed

- **WHEN** the condition ledger cannot be read at replay
- **THEN** no request SHALL be sent

#### Scenario: An unreadable ledger leaves pending mitigations pending

- **WHEN** a sentinel run cannot read the condition ledger
- **THEN** no pending mitigation SHALL be withdrawn or dispatched, and the next run SHALL read it again

#### Scenario: A removed declaration or watch withdraws a pending mitigation

- **WHEN** the owner removes the mitigation declaration, or removes its sensor from the safety watch, while the mitigation is pending
- **THEN** the mitigation SHALL be withdrawn with reason `mitigation_removed` or `watch_removed` respectively, and no request SHALL be sent

#### Scenario: An unapproved mitigation expires

- **WHEN** a parked mitigation is still pending 2 hours after it was parked
- **THEN** the approvals sweep SHALL expire it and no request SHALL be sent

### Requirement: One physical executor for prepared Home calls

Its safety-mitigation parts take effect only when the owner adopts RFC 0028 Amendment 1; for departure fixes it restates the adopted RFC 0028 executor rule.

Every prepared Home call, including departure fixes and safety mitigations, SHALL be dispatched only by the Home module's `ha_call_service` handler under the approvals executor. No job or consumer SHALL send a Home Assistant service request itself. One replay gate, dispatching on the dedup-key prefix, one status resolver and one receipt ledger SHALL serve every consumer. Missing approval, a refused premise or missing sensor evidence SHALL NOT be reported as success.

ID: REQ-home-deterministic-jobs-015
Source: RFC 0028; bu-jv1moa.29 (S-A, S-B); bu-jv1moa.33
Scope: v1-mandatory

#### Scenario: Consumers never post to Home Assistant

- **WHEN** the deterministic job modules are inspected
- **THEN** none SHALL send a request to the Home Assistant services API

#### Scenario: Unapproved calls make no request

- **WHEN** a fix or mitigation is pending, rejected, cancelled, expired or refused at replay
- **THEN** no Home Assistant service request SHALL have been made for it
