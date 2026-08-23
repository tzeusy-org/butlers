## ADDED Requirements

### Requirement: Content-Blind Secrets Probe and Reauthorize Mutations

The `/api/secrets/*` probe and reauthorize mutations SHALL publish credential evidence only as members of a fixed published vocabulary, never as text read out of a credential row or handed back by a provider. This extends to the mutation surface the discipline the `Secrets Inventory and Per-Credential Read Endpoints` requirement already binds on the read surface: the credential's capabilities and failure category are published, its content never is. Each mutation response SHALL be an explicit field-by-field projection of the router's internal record, so a field added to that record cannot reach a client without being consciously allowed through.

#### Scenario: Probe outcomes name a category, never a diagnostic

- **WHEN** `POST /api/secrets/user/<provider>/probe`, `POST /api/secrets/system/<key>/probe`, or `POST /api/secrets/probe-all` returns a probe outcome
- **THEN** the outcome's failure evidence SHALL be a member of the fixed published vocabulary `not_set`, `expired`, `rejected`, `rate_limited`, `provider_error`, `malformed`, `unverified`, `other` (`PROBE_FAILURE_VOCABULARY` in `src/butlers/api/routers/secrets_v2.py`), selected out of that vocabulary rather than derived from any input string, so a provider message or a `probe_status` token this system has not seen before cannot widen what escapes
- **AND** a successful probe SHALL publish no failure evidence at all
- **AND** the outcome SHALL NOT contain the credential's persisted failure tail, the cached `last_test_message` column, the provider's own response text, an exception string, an audit note, or the persisted `entity_info.type` or `label`
- **AND** where a probe or reauthorize response names a capability family it SHALL use a member of the fixed capability vocabulary `calendar`, `gmail`, `drive`, `health`, `connectivity`, `other`, an input that maps to no known family becoming `other`; those two vocabularies are the complete set of evidence these mutations may publish, and the capability roll-up's naming of which families failed belongs to the persisted message, not the response
- **AND** an outcome produced outside this router and re-published by the sweep (the CLI credential tester's result, whose detail is provider free text) SHALL be clamped to the same vocabulary, an unrecognised value collapsing to `other` rather than riding along

#### Scenario: Withheld probe diagnostics survive as server-side evidence

- **WHEN** a probe classifies a failure into a vocabulary member
- **THEN** the free-text diagnostic SHALL still be written to `public.secret_probe_log`, to the credential's `last_test_message` cache column, and to the audit row for that probe
- **AND** the classification SHALL read only this system's own state and `probe_status` tokens and the provider's HTTP status code, never the diagnostic text, so the text has no path to the wire even through the category it selects
- **BECAUSE** the diagnostic is withheld from the API caller, not destroyed; a probe that discarded it would trade a leak for an undiagnosable failure

#### Scenario: Reauthorize hands back an issued reference, never a stored hint

- **WHEN** `POST /api/secrets/user/<provider>/reauthorize?identity=<uuid>` builds the `redirect_url` for a credential that has a stored account
- **THEN** the URL SHALL carry `account_ref=<entity uuid>` — the system-issued entity identifier the caller already supplied on this same request, which holds no credential content — and SHALL NOT carry `account_hint`, the persisted `entity_info.label`, or any other value read out of the credential row
- **AND** `/api/oauth/<provider>/start` SHALL resolve that reference back into the provider hint server-side, so the stored label never leaves the process
- **AND** when the reference resolves to no credential, or its resolution fails, the dance SHALL continue hint-free rather than falling back to publishing the stored value
- **AND** a first-time connect, where no stored account exists, SHALL produce a hint-free URL as before
- **AND** this requirement binds what the reauthorize mutation may put on that URL; the `account_hint` parameter of `/api/oauth/<provider>/start` itself is unchanged and remains available to a caller that legitimately holds an account address (see `google-multi-account-oauth`)
