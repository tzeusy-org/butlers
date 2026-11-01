## MODIFIED Requirements

### Requirement: Async Core Credential Validation
Runtime authentication SHALL use either CLI-level OAuth tokens (device-code flow) or API keys, depending on the provider's `auth_mode` as configured in the CLI auth registry. API-key providers (e.g. Claude with `ANTHROPIC_API_KEY`) store their keys in the credential store via the dashboard Settings → CLI Runtime Authentication card. The `validate_core_credentials_async()` function is a no-op; credential availability SHALL be checked lazily when the goal runner launches an invocation, and the credential SHALL reach the invocation only as its staged model credential on the session host. Domain daemons SHALL NOT resolve runtime credentials to start reasoning sessions, and neither the session host nor an invocation SHALL read the credential store or the database.

Source: RFC 0038 D2 and D3
Scope: v1-mandatory

#### Scenario: Core credential validation is lazy
- **WHEN** `validate_core_credentials_async()` is called
- **THEN** it SHALL not reject an unavailable runtime credential
- **AND** the goal runner checks that credential lazily when it launches an invocation that needs it

#### Scenario: An invocation holds only its staged credential
- **WHEN** the goal runner launches an invocation on the session host
- **THEN** the invocation SHALL hold its provider credential only as its staged model credential
- **AND** neither the invocation nor the session host SHALL hold credential-store, database or `butler_secrets` access

## ADDED Requirements

### Requirement: Goal Broker Signing Key Custody
`GOAL_BROKER_SIGNING_KEY` SHALL be the second named process-bound deployment control key. Its private key SHALL be provisioned as a file-backed deployment secret mounted only into the `butlers-control` process, and SHALL NOT be supplied through an environment value, `CredentialStore`, a `butler_secrets` row, `public.entity_info` or git. No other process (domain daemons in `butlers-up`, the session host, the dashboard API or connectors) SHALL receive the private key; domains receive only verification keys (core-daemon `REQ-core-daemon-004`). A missing private key, or one that does not match the published current key identifier, SHALL make the broker refuse every call, with no fallback to an unsigned request or a shared secret. Rotation SHALL publish the next public key and restart domains before the signer switches, and SHALL remove the old key only after twice the assertion lifetime.

ID: REQ-core-credentials-003
Source: RFC 0038 D7 and owner decision 6; about/heart-and-soul/security.md, process-bound deployment control keys
Scope: v1-mandatory

#### Scenario: Only the control plane holds the private key
- **WHEN** `butlers-up`, the session host, the dashboard API or a connector starts
- **THEN** none of them has the private signing key mounted or resolvable
- **AND** a request to store `GOAL_BROKER_SIGNING_KEY` as a credential-store or `butler_secrets` secret is rejected

#### Scenario: A missing or mismatched private key fails closed
- **WHEN** `butlers-control` starts without a private key that matches the published current key identifier
- **THEN** the broker refuses every call
- **AND** no unsigned request or shared-secret path is used instead

#### Scenario: Rotation overlaps verification before the signer switches
- **WHEN** the operator rotates the signing key
- **THEN** every domain accepts both the current and the next key identifier before the signer switches to the next key
- **AND** the old key is removed no earlier than twice the assertion lifetime after the switch

### Requirement: Session Host Credential Boundary
Provider credentials and runtime OAuth state used by goal invocations SHALL be held on the session host, not in `butlers-up`. The session host SHALL NOT receive domain, database or connector credentials, the goal broker signing key, or credential-store access, and each invocation SHALL receive only its own staged model credential.

ID: REQ-core-credentials-004
Source: RFC 0038 D3
Scope: v1-mandatory

#### Scenario: Channel credentials never reach a session
- **WHEN** a goal needs to send a Telegram message or an email
- **THEN** the invocation reaches Messenger's capability only through the broker
- **AND** neither the invocation nor the session host holds the Telegram bot token, the email password or any other connector credential

#### Scenario: Runtime OAuth state leaves the domain container
- **WHEN** `butlers-up` starts after cutover
- **THEN** it mounts no runtime OAuth volume used by goal invocations
- **AND** no domain daemon holds a provider credential for launching a reasoning session
