## MODIFIED Requirements

### Requirement: Renewal warnings treat email-derived cancellation fields as unverified

A subscription's `cancellation_url` and `status` MUST be treated as trusted by the renewal warning
only when an owner-attested session wrote them. Each field carries a stored source of `owner` or
`extracted`; the source MUST be derived from the server-held runtime routing context, never from a
tool argument, and a write whose attestation cannot be read MUST be stored as `extracted`. An
`extracted` write MUST NOT overwrite a value whose stored source is `owner`, and that rule MUST hold
under concurrent writers. Rows that predate the source columns MUST read as `extracted`.

ID: REQ-finance-alerts-002
Source: bu-svqpzo.10; RFC 0038 D6; about/heart-and-soul/security.md
Scope: v1-mandatory

#### Scenario: Extracted cancellation link is rendered inert

- **WHEN** a yearly subscription renews within 14 days and its `cancellation_url_source` is
  `extracted`
- **THEN** the `subscription-renewal` message MUST name only the link's registrable domain,
  defanged (`example[.]com`) so that no chat client can render it as a live link, followed by
  "(link from an email, not verified)" (or "(link from an unverified source, not verified)" when
  the last writer was not an email)
- **AND** neither the message nor the candidate metadata MUST contain the URL, and the metadata
  MUST carry `door_verified: false`

#### Scenario: Owner-set cancellation link is unchanged

- **WHEN** the `cancellation_url_source` is `owner`
- **THEN** the message and metadata MUST carry the URL as before, with `door_verified: true`
- **AND** ordinary live-link rendering is preserved only for an independently unexposed envelope
  whose contributing display values are all owner-origin; an extracted or unknown contributor, or
  independent exposure, MUST instead apply REQ-insight-delivery-001 to the whole final envelope
  without changing the stored URL, its owner source or `door_verified: true`

#### Scenario: Extracted cancelled or paused status still warns

- **WHEN** a yearly subscription renewing within 14 days has status `cancelled` or `paused` with
  `status_source` of `extracted`
- **THEN** the renewal warning MUST still be proposed and MUST ask the owner to confirm the
  subscription status with Butlers, naming an email as the reporter only when the last writer was an
  email
- **AND** a `cancelled` or `paused` status whose source is `owner` MUST still suppress the warning

#### Scenario: Extracted write cannot displace an owner-set value

- **WHEN** a session whose routing context is not owner-attested tracks a subscription whose stored
  `cancellation_url_source` or `status_source` is `owner`
- **THEN** the stored URL or status MUST be kept and its source MUST remain `owner`

## ADDED Requirements

### Requirement: Renewal and price warnings preserve unresolved extracted disagreements

Finance MUST retain a baseline renewal date and amount/currency pair, treat later extracted disagreements as unresolved claims, and select the earliest still-actionable renewal date before filtering. Explicit owner values SHALL be authoritative only when derived from the authenticated server-held writer context; the current alert windows and absence policy remain unchanged.

ID: REQ-finance-alerts-003
Source: bu-svqpzo.19 proposed amendment to bu-svqpzo.16; RFC 0038 D4/D6; REQ-finance-alerts-001
Scope: v1-mandatory

#### Scenario: W01 Earlier owner date survives a later extracted date

- **WHEN** an active yearly subscription has an owner baseline within the existing 14-day window and an extracted later date outside it
- **THEN** the earlier date MUST remain selectable before the SQL window filter and a renewal warning MUST remain eligible
- **AND** the warning, ledger period, deduplication identity and expiry MUST use that same selected date; disagreement MUST be described as unverified

#### Scenario: W02 Extracted baseline is not replaced by later extraction

- **WHEN** an extracted-only baseline is followed by a later extracted date
- **THEN** both values MUST remain recorded without assigning owner origin
- **AND** the earlier still-actionable date MUST remain eligible and the disagreement MUST request explicit owner confirmation

#### Scenario: W03 Earlier pending date remains unverified

- **WHEN** an unresolved extracted date is earlier than the baseline and lies within the reminder window
- **THEN** the warning MUST select that earlier date without turning the claim into confirmed state
- **AND** wording MUST distinguish the reported date from an owner-confirmed date

#### Scenario: W04 Repeated and distinct disagreements remain bounded by semantic deduplication

- **WHEN** distinct date claims or replays of the same source event arrive
- **THEN** distinct unresolved claims MUST remain available and a repeated identical claim MUST NOT create another claim or another warning
- **AND** the warning key MUST remain subscription identity plus selected date, not each row revision; expiry MUST remain the next owner-local midnight after the selected date under the existing reminder policy

#### Scenario: W05 Elapsed history cannot hide an actionable date

- **WHEN** an older unresolved date has elapsed while another eligible date is within the current local-day through 14-day window
- **THEN** selection MUST filter each baseline or pending date to that window before choosing the earliest
- **AND** elapsed history MUST NOT create a missed-payment, failed-renewal or new overdue alert; local timezone and DST behavior MUST remain intact

#### Scenario: W06 Explicit owner date changes resolve prior claims

- **WHEN** an authenticated owner explicitly supplies a different renewal date through the existing registered writer
- **THEN** the exact date MUST commit with a new confirmation epoch and prior date claims MUST become resolved history
- **AND** pending warnings known to describe the superseded date or epoch MUST be withdrawn at the final currentness check; delivered history and cooldowns MUST NOT be erased
- **AND** an unavailable currentness read MUST remain unknown under the existing as-of policy, never a fabricated confirmation or known supersession

#### Scenario: W09 Cancellation status protection remains effective

- **WHEN** a renewal candidate has a cancelled or paused status
- **THEN** an owner-sourced status MUST still suppress the warning and an extracted status MUST still warn as specified by REQ-finance-alerts-002
- **AND** unresolved dates MUST NOT turn an extracted status into owner authority

#### Scenario: W10 Amount and currency disagreements form one claim

- **WHEN** an extracted update changes either amount or currency
- **THEN** the complete supplied amount/currency pair MUST be retained atomically as one pending claim while the baseline pair remains unchanged
- **AND** a writer MUST NOT combine one field from the new claim with one from the old baseline or perform a numeric comparison across different currencies

#### Scenario: W11 Extracted-only price baseline survives disagreement

- **WHEN** a subscription with an initial unverified extracted price receives an unequal extracted pair
- **THEN** the original baseline MUST remain the tracked price for the existing price detector and the new pair MUST remain pending
- **AND** the claim MUST NOT silently replace the comparison denominator or become a confirmed upcoming charge; metadata such as `next_amount` MUST NOT supply authority

#### Scenario: W13 Owner confirmation sets the exact supplied price

- **WHEN** an authenticated owner explicitly supplies amount and currency through the existing registered writer
- **THEN** the exact complete pair and owner origin MUST commit together and prior pair claims MUST become retained resolved history
- **AND** confirmation MUST NOT mean accepting a caller-selected pending identifier or whichever claim happens to be latest

#### Scenario: W14 Currency changes and zero baselines remain honest

- **WHEN** a pending pair changes currency or the tracked amount is zero
- **THEN** a currency change MUST be shown as unverified without FX conversion or cross-currency percentage claims
- **AND** the existing zero-division guard and same-currency price-change threshold greater than five percent over the existing 60-day comparison window MUST remain
