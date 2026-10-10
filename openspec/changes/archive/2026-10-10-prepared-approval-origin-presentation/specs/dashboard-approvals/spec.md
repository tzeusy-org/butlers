## ADDED Requirements

### Requirement: Prepared approval presentation is deliberate digest truth

The approval rail and selected dossier SHALL present an explicit server-observed `origin="prepared"` as a calm, accessible `Prepared` origin label while retaining the action's actual decision and execution status. Intended absence of a prepared-action push SHALL NOT render a failed-push warning. An ordinary confirmed failed push and independently observed failure or degraded evidence SHALL retain their existing truthful presentation. Unknown origin SHALL remain distinguishable from Prepared and SHALL NOT be inferred from absence or treated as an all-clear. This classification SHALL add no prepare, approve, execute, send, retry or provider authority.

ID: REQ-dashboard-approvals-004
Source: prepared-approval-origin-presentation/design.md; dashboard-design-language Kind Tags and Page Primitive; owner-released run13 S3 (bu-3b6goa)
Scope: v1-mandatory

#### Scenario: Prepared draft is calm in the rail and dossier

- **WHEN** real list and detail responses identify a pending digest-only action with explicit prepared origin and no actual failed push
- **THEN** both the mounted rail item and its selected dossier expose `Prepared` through neutral Dispatch category treatment and accessible text
- **AND** intended non-send does not create a failed-push warning, delivery-success claim or green executed state.

#### Scenario: Ordinary failed delivery remains a real failure

- **WHEN** an ordinary pending action has an explicit confirmed failed push, including a row whose origin classification is unknown
- **THEN** the rail and dossier retain the existing failure warning and safe failure context
- **AND** the Prepared classification rule neither silences real failure nor weakens existing action permissions.

#### Scenario: Unknown and malformed origin remain honest

- **WHEN** a list or detail response omits origin or carries null, malformed or unrecognized origin and lacks confirmed delivery success
- **THEN** the consumer distinguishes unknown classification from Prepared without inventing a prepared badge, a failed push or an all-clear
- **AND** actual decision status and independent delivery evidence remain visible under their existing contracts.

#### Scenario: Loading and degraded reads do not fabricate Prepared

- **WHEN** the list or dossier is loading, fails before data, or fails during refresh with cached data
- **THEN** existing loading, named error, partial-coverage and cached-evidence states remain explicit instead of fabricating a Prepared row or clean empty result
- **AND** any retained cached Prepared evidence is accompanied by its existing unavailable or stale indication and does not certify current delivery health.

#### Scenario: Origin label preserves selection status and decision boundaries

- **WHEN** an explicit prepared action is selected, changes decision or execution status, or the owner selects a different action
- **THEN** the origin label stays bound to that action and remains separate from its actual pending, approved, executed, rejected, expired or abandoned status
- **AND** existing keyboard selection, focus, dossier URL, expiry and server-authorized action controls remain intact
- **AND** no origin label triggers a prepare, approve, execute, send, retry or provider request.
