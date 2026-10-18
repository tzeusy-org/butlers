# Finance Payment Destination Integrity

## Purpose

Finance remembers, per counterparty, which payment destinations (bank account, IBAN, payment handle) its bills have asked the owner to pay, without storing the raw value in its own tables, and stamps each bill with a destination verdict. A changed destination on an otherwise familiar bill is the core signal of invoice-redirect fraud; security doctrine keeps payment initiation out of scope, so detection is what Finance offers. This capability is slice S1 of the payment-redirect guard (bu-s11n0s.9): it is Finance-local, spawns no sessions and sends no notification. The joint sender, destination and verified-channel check, the owner confirmation verb, and sender authentication are separate work. Known exception: session tool-call records (`sessions.tool_calls`) currently keep a tool call's raw arguments, so the raw destination can persist there outside Finance's control; stripping them to an allowlist is follow-up bu-4mfhdq.

## Requirements

### Requirement: Raw payment destinations are never stored

When `track_bill` receives a `payment_destination`, Finance SHALL normalize and classify it, reduce it to a keyed fingerprint, and discard the raw value. The raw destination, and any normalized form of it, SHALL NOT appear in `finance.bills`, in `finance.payment_destinations`, in the SPO mirror written to `public.facts`, in any log line, or in the tool result. Finance SHALL store only the fingerprint, the kind (`iban`, `account` or `handle`), and the last four characters of the normalized value.

ID: REQ-finance-payment-destination-integrity-001
Source: bu-s11n0s.21 payment-redirect guard S1; heart-and-soul/security.md
Scope: v1-mandatory

#### Scenario: Normalization before fingerprinting

- **WHEN** a destination is supplied with spaces, hyphens or mixed case
- **THEN** separators are stripped, IBAN letters are uppercased and handles are lowercased before the fingerprint is computed
- **AND** two spellings of the same destination yield the same fingerprint

#### Scenario: Fingerprint is keyed

- **WHEN** a fingerprint is computed
- **THEN** it is an HMAC-SHA256 over `kind:normalized` under the Finance destination key
- **AND** a different key yields a different fingerprint for the same destination

#### Scenario: No raw value anywhere

- **WHEN** bills with payment destinations have been tracked, including re-ingested ones
- **THEN** a scan of every text and jsonb column of `finance.bills`, `finance.payment_destinations` and the SPO mirror rows for the payee finds none of the raw destination strings

### Requirement: Destination verdict per counterparty

`track_bill` SHALL stamp `destination_fingerprint` and `destination_verdict` on the bill, with `destination_verdict` one of `known`, `new_for_counterparty` or `unverifiable`, and SHALL keep one `finance.payment_destinations` row per `(payee_key, fingerprint)` carrying `seen_count`, `first_seen_at` and `last_seen_at`. The first destination ever seen for a payee SHALL become that payee's baseline row and the bill SHALL be stamped `unverifiable`, because there is nothing to compare against and `known` would vouch for an unchecked value. A destination already recorded for the payee SHALL be stamped `known`. A different destination for a payee with history SHALL be stamped `new_for_counterparty`. When no `payment_destination` is passed, the bill's destination columns SHALL stay untouched (NULL on a bill never stamped, unchanged on an already-stamped one).

ID: REQ-finance-payment-destination-integrity-002
Source: bu-s11n0s.21 payment-redirect guard S1 behavior matrix
Scope: v1-mandatory

#### Scenario: A changed destination is new for the counterparty

- **WHEN** a payee has bills to destination F1 and a later bill shows destination F2
- **THEN** the later bill is stamped `new_for_counterparty` and the payee has two destination rows

#### Scenario: A seen destination is known

- **WHEN** a later bill shows a destination already recorded for the payee
- **THEN** the bill is stamped `known`, that row's `seen_count` increases by one and no row is added

#### Scenario: The first destination is an unverifiable baseline

- **WHEN** the first bill ever tracked for a payee shows a destination
- **THEN** one baseline row is stored and the bill is stamped `unverifiable`

#### Scenario: No destination passed

- **WHEN** `track_bill` is called without `payment_destination`
- **THEN** `destination_fingerprint` and `destination_verdict` stay untouched (NULL on a bill never stamped) and the existing return shape is unchanged

### Requirement: Destination checking fails closed

When the Finance destination key is not available, or the supplied value has no usable content after normalization, `track_bill` SHALL stamp `unverifiable`, SHALL store nothing about the destination, and SHALL return a `destination_reason` (`fingerprint_key_unavailable` or `unparseable_destination`) that is not persisted; the baseline stamp of a payee's first destination carries `no_prior_destination` in the same way. An `unverifiable` stamp that carries no fingerprint SHALL NOT overwrite a bill that already carries a fingerprint: both the stored fingerprint and verdict stay untouched while the reason is still returned. The key SHALL be resolved once at module startup from the shared credential store with `env_fallback=False`, under the name `FINANCE_PAYMENT_DESTINATION_KEY`, and a missing key SHALL produce one startup warning rather than a log line per call. The key name SHALL be listed in the secrets catalog as used by Finance. Provisioning the key value is an owner or operations step.

ID: REQ-finance-payment-destination-integrity-003
Source: bu-s11n0s.21 payment-redirect guard S1 fail-closed decision; APPROVAL_CALLBACK_SECRET resolution pattern
Scope: v1-mandatory

#### Scenario: Missing key

- **WHEN** the credential store returns no key and a bill with a destination is tracked
- **THEN** the bill is stamped `unverifiable` with reason `fingerprint_key_unavailable`
- **AND** `finance.payment_destinations` gains no row and the return payload carries no raw destination

#### Scenario: An unverifiable re-ingest keeps a stamped verdict

- **WHEN** a bill stamped `new_for_counterparty` is re-ingested while the key is unavailable or with an unparseable value, and later again with the same destination once the key is back
- **THEN** every result still reports `new_for_counterparty` and the stored fingerprint is unchanged
- **AND** `seen_count` is unchanged and the unavailable or unparseable call returns its `destination_reason`

#### Scenario: Unparseable destination

- **WHEN** the destination has no digits and no handle shape after normalization
- **THEN** the bill is stamped `unverifiable` with reason `unparseable_destination` and nothing is stored

#### Scenario: Key rotation resets baselines

- **WHEN** the key is rotated
- **THEN** every earlier fingerprint stops matching and the next sightings read as `new_for_counterparty` until relearned, so rotation MUST be paired with clearing `finance.payment_destinations`

### Requirement: Destination counting is idempotent and concurrency-safe

Re-ingesting the same bill (same `payee_key` and `due_date`) with the same destination SHALL NOT change `seen_count`; `seen_count` SHALL increase only when a new bill row is inserted with that destination or an existing bill's destination changes to it. Concurrent bills for one payee with the same new destination SHALL produce one row, enforced by the `UNIQUE (payee_key, fingerprint)` constraint with an upsert, and the baseline decision SHALL be serialized per payee.

ID: REQ-finance-payment-destination-integrity-004
Source: bu-s11n0s.21 payment-redirect guard S1 idempotence and concurrency rows
Scope: v1-mandatory

#### Scenario: Re-ingesting a bill

- **WHEN** a bill is tracked twice with the same payee, due date and destination
- **THEN** the destination row's `seen_count` is unchanged by the second call

#### Scenario: Concurrent first sightings

- **WHEN** two bills for one payee carry the same new destination at once
- **THEN** exactly one destination row exists for the pair
