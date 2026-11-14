## ADDED Requirements

### Requirement: A door opens an owner-confirmed vendor page and spends nothing

A vendor door SHALL open a vendor page whose host the owner confirmed, and SHALL NOT carry an
amount, cart contents or confirmation token, so following it spends nothing. The kind SHALL be
exactly one of `cancel`, `manage`, `return` or `check_in`. No door, resolution or click SHALL be a
broker effect, SHALL create pay, book, confirm-purchase, token, login or `web_app` authority, or
SHALL be evidence that a cancellation or return occurred. The feature SHALL be dark behind
`VENDOR_DOORS_LIVE`, with no activation under RFC 0038 cutover rules.

ID: REQ-vendor-doors-001
Source: bu-kr3x6j (closed, option A); RFC 0038 D4; bu-jv1moa.40
Scope: v1-mandatory

#### Scenario: Door text cannot carry spend
- **WHEN** a door template or any slot value names an amount, cart, or confirmation token
- **THEN** confirmation refuses it and no row becomes `confirmed`

#### Scenario: A click is not evidence
- **WHEN** the owner follows a delivered door
- **THEN** no effect runs, no subscription status changes, and no cancellation is recorded

#### Scenario: Out-of-scope kinds are refused
- **WHEN** a row names a kind other than the four (for example `manage_booking`, `order_status` or `bill_portal`)
- **THEN** the registry rejects it

### Requirement: Registry rows are confirmed only by the authenticated dashboard owner

`public.vendor_doors` rows SHALL be created as inert `proposed` rows by extractors and SHALL become
`confirmed` only through the dashboard API behind `require_dashboard_owner_control`. Confirmation
and revocation SHALL be database functions applying a version compare-and-set; direct writes SHALL be
revoked from every role. A forged owner id, non-owner session, connector callback credential, goal
or MCP caller, exposed invocation, or email- or message-originated request SHALL be refused with a
content-free reason and no row change. An authenticated email sender SHALL NOT confirm a door.
The stored `ingestion_events.sender_authentication` verdict SHALL be copied into the proposal
lineage and consumed as stored; raw headers SHALL NOT be reparsed. Retries SHALL be idempotent.

ID: REQ-vendor-doors-002
Source: bu-jv1moa.40; core_271 sender_authentication (bu-s11n0s.20); design D1, D3
Scope: v1-mandatory

#### Scenario: Owner confirms a proposal
- **GIVEN** a `proposed` door at version 3
- **WHEN** the authenticated owner confirms with `expected_version=3`
- **THEN** the row is `confirmed` at version 4 with the confirming session hash

#### Scenario: Retried confirmation is idempotent
- **WHEN** the same confirmation is sent again after success
- **THEN** the response reports the same confirmed version and nothing else changes

#### Scenario: Stale or concurrent confirm and revoke
- **WHEN** a confirm with an old version races a revoke
- **THEN** exactly one wins by the CAS and the loser receives the current state without a write

#### Scenario: Forged, non-owner or exposed callers are refused
- **WHEN** the caller is a non-owner, a connector callback, a goal, an MCP tool, an exposed request, or an email-originated request, or the body names another owner
- **THEN** the call is refused content-blind and the row is unchanged

#### Scenario: Authenticated email is not approval
- **GIVEN** a proposal whose stored sender verdict is `authenticated`
- **WHEN** no owner confirmation has occurred
- **THEN** the door stays `proposed` and inert

#### Scenario: Verdict is consumed, not reparsed
- **WHEN** a proposal is created from an ingested email
- **THEN** its lineage holds the stored verdict value, an absent value is stored as `unverified`, and no header parser is invoked

### Requirement: Door URLs and slots follow fixed safety rules at confirmation and resolution

A door template SHALL be HTTPS, with no userinfo, no IP literal, no IDN or `xn--` label, no
non-default port, no redirect template and at most 300 characters after slot substitution, and its
host SHALL equal the stored registrable host. Slots SHALL be typed regex values that SHALL NOT
contain `/ ? # % @ :`. The same rules SHALL be re-checked at every resolution.

ID: REQ-vendor-doors-003
Source: self_links.py S1 URL rules; bu-jv1moa.40
Scope: v1-mandatory

#### Scenario: Unsafe templates are refused
- **WHEN** a template has userinfo, an IP host, a punycode label, port 8443, an embedded redirect parameter, or exceeds 300 characters
- **THEN** confirmation refuses it

#### Scenario: Hostile slot values are inert
- **WHEN** a reference supplies a slot containing `/`, `?`, `#`, `%`, `@` or `:`, or one outside its type
- **THEN** the door does not resolve

### Requirement: Typed door references resolve independently and fail inert

A notification SHALL reference a door only through `DoorReferenceV1` (door id, version, slots);
the notify contract SHALL accept no URL, host or template. The Switchboard sender and the
Messenger receiver SHALL each resolve the reference against current confirmed rows and discard any
wire label, URL, host or provenance. A reference SHALL resolve only for a current `confirmed`
door of the same owner whose version equals the referenced version. Missing, revoked, stale,
foreign, unconfirmed, proposal, unreadable or timed-out references and any reference in a message
produced under exposure SHALL degrade to `open_dashboard` plus `[link not verified]`. The delivered
link SHALL show the actual registrable domain and provenance with previews off.

ID: REQ-vendor-doors-004
Source: bu-jv1moa.40; S1 PR #5015; bu-kr3x6j (no D4 carve-out)
Scope: v1-mandatory

#### Scenario: Current confirmed door resolves
- **WHEN** sender and receiver each read a confirmed same-owner door at the referenced version
- **THEN** both produce the same URL, domain label and `vendor_door` provenance, previews off

#### Scenario: Edited door never executes the prior template
- **GIVEN** a reference minted at version 4
- **WHEN** a revoke or replacement bumps the row before delivery
- **THEN** the reference does not resolve and the inert form is delivered

#### Scenario: Forged wire fields are discarded
- **WHEN** a delivered payload carries a different label, url or host
- **THEN** the receiver rebuilds from the registry and ignores them

#### Scenario: Exposed message gets no live door
- **WHEN** the notification originates from an exposed invocation
- **THEN** no live door is minted and the message carries the inert fallback

#### Scenario: Bounded reads
- **WHEN** the registry read fails, is cancelled, or times out
- **THEN** the inert fallback is delivered with no partial link

### Requirement: Legacy subscription links migrate by stored provenance only

The finance backfill SHALL create a `confirmed` door with origin `legacy_owner_set` only for a
subscription whose `cancellation_url_source` is `owner` and whose URL passes the door rules;
every other link SHALL become an inert `proposed` row, retaining lineage. Owner provenance SHALL
NOT be reconstructed from old text or metadata. The backfill SHALL be idempotent and SHALL NOT
change or delete any subscription column.

ID: REQ-vendor-doors-005
Source: finance_017 (bu-svqpzo.10); bu-jv1moa.40; design D5
Scope: v1-mandatory

#### Scenario: Owner-set link becomes a confirmed door
- **GIVEN** a subscription with an owner-sourced, rule-clean URL
- **WHEN** the backfill runs
- **THEN** one confirmed `cancel` door exists with lineage naming the subscription and column

#### Scenario: Extracted or unknown link stays a proposal
- **WHEN** the source is `extracted`
- **THEN** a `proposed` row exists and is never confirmed by the backfill

#### Scenario: Owner-set link that breaks a rule is not trusted
- **WHEN** an owner-sourced URL fails a door rule
- **THEN** it becomes a `proposed` row with the failing rule recorded

#### Scenario: Re-run changes nothing
- **WHEN** the backfill runs twice
- **THEN** no duplicate rows and no changes to subscription columns exist

### Requirement: Renewal warning uses confirmed doors and never trusts unverified cancellation

With `VENDOR_DOORS_LIVE` on, the renewal warning SHALL present a link only from a confirmed
`cancel` door; a proposal SHALL print as unverified. An extracted or email-reported `cancelled` or
`paused` status SHALL NOT suppress the renewal warning, and only an owner-sourced status SHALL.
An extracted write SHALL NOT overwrite a confirmed door. With the flag off, behavior SHALL be
unchanged from the current column logic.

ID: REQ-vendor-doors-006
Source: roster/finance/jobs/finance_jobs.py insight-scan section 4; bu-svqpzo.10
Scope: v1-mandatory

#### Scenario: Confirmed door is shown
- **WHEN** a renewal is within 14 days and a confirmed cancel door exists
- **THEN** the warning carries the door through the typed reference

#### Scenario: Unverified cancelled keeps warning
- **WHEN** the subscription is `cancelled` with `status_source='extracted'`
- **THEN** the renewal warning is still sent and asks the owner to confirm

#### Scenario: Owner-set cancelled suppresses
- **WHEN** it is `cancelled` with `status_source='owner'`
- **THEN** no renewal warning is sent

#### Scenario: Extracted write cannot replace a door
- **WHEN** an extracted write names a different cancellation URL for a confirmed door
- **THEN** only a new `proposed` row is created

### Requirement: Rollback makes doors inert without losing records

Disabling `VENDOR_DOORS_LIVE` SHALL make every door reference resolve to the inert form and
disable confirmation routes, retain all registry rows and lineage, never promote a proposal, and
never delete subscription history.

ID: REQ-vendor-doors-007
Source: bu-jv1moa.40; RFC 0038 cutover rules
Scope: v1-mandatory

#### Scenario: Flag off
- **WHEN** the flag is false
- **THEN** confirmed doors resolve inert, confirmation returns 404, and rows are unchanged
