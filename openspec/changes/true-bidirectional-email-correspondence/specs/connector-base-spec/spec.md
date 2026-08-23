## MODIFIED Requirements

### Requirement: Authentication and Token Management

Connector authentication with Switchboard SHALL use secret-authority managed
bearer tokens or an equivalently strong framework transport credential with
scope enforcement.  Every connector-to-Switchboard MCP call that can admit a
qualifying inbound email observation or obtain/write correspondence send or
confirmation data SHALL carry an authenticated principal derived by the
transport, not a caller-supplied `connector_type`, provider, or endpoint
identity.  Switchboard SHALL bind the principal to exactly one connector type
and canonical endpoint/account, enforce the tool scope, and reject missing,
expired, revoked, or mismatched credentials before executing the tool or routing
to Messenger.  It SHALL not treat an envelope's provider or endpoint field as
account provenance.

#### Scenario: [TARGET-STATE] Token scope enforcement

- **WHEN** a connector authenticates with `SWITCHBOARD_API_TOKEN` or its
  approved equivalent transport credential
- **THEN** the token scope must match the connector's source identity
- **AND** Switchboard derives the immutable connector/account principal from
  that validated scope rather than trusting tool arguments

#### Scenario: [TARGET-STATE] Token security requirements

- **WHEN** connector tokens are managed
- **THEN** tokens are stored in secret managers, rotated every 90 days
  (production) or 7 days (development), and revoked immediately if compromised
- **AND** tokens are never written to connector logs, persistence, MCP result
  payloads, or correspondence metadata

#### Scenario: Correspondence broker rejects a forged identity

- **WHEN** a connector calls a correspondence dispatch/send-report/lease/
  confirmation-report tool with an absent, invalid, expired, revoked, wrong-scope,
  or cross-account credential
- **THEN** Switchboard rejects the request before it reads or routes a lease
- **AND** it does not permit a caller argument to select a different provider
  or endpoint/account
- **AND** it returns only a bounded authentication/authorization failure class

#### Scenario: Authenticated ingress creates a qualifying epoch

- **WHEN** an enabled email connector ingests an event through an account-scoped
  authenticated transport
- **THEN** Switchboard derives and verifies the connector/provider/endpoint
  principal against the canonical account and assigns/continues an opaque ingress
  epoch before any correspondence-qualified observation is created
- **AND** a reset, gap, re-authentication, rebinding, or identity mismatch invokes
  `messenger.close_qualified_email_coverage(text, text, uuid, text, timestamptz)`
  through Switchboard with its checked category, prevents affected events from
  qualifying, and cannot leave a prior coverage watermark fresh
- **AND** caller-supplied envelope identity and pre-enforcement historical events
  cannot become same-account evidence

#### Scenario: Scoped ingress metadata remains transient and deduplicated

- **WHEN** an enabled connector derives the permitted provider-age assertion and
  account-scoped opaque source-deduplication token for qualified ingress
- **THEN** both values travel only in authenticated internal transport/projection
  context and no raw provider event ID or provider time is copied into the
  correspondence-specific projection, persistence, logs, traces, metrics, or
  aggregate
- **AND** a replay uses the same opaque token so the qualified projection returns
  its original categorical result without changing receipt time or coverage

#### Scenario: Authenticated connector calls use the cached transport safely

- **WHEN** a connector uses `CachedMCPClient` for correspondence native-send
  dispatch/send-report, confirmation lease/report, or qualifying-ingress calls
- **THEN** the client supplies the scoped credential through the framework
  transport without logging it
- **AND** its reconnect behavior preserves the same credential scope
- **AND** a connector has no direct MCP client, database grant, or endpoint
  credential for Messenger correspondence data
