## ADDED Requirements

### Requirement: Timed insight lane and shared delivery claim

ID: REQ-insight-delivery-003
Source: bu-jv1moa.8 and RFC 0011 Amendment 8
Scope: v1-mandatory

A timed candidate is delivered between deliver_after and expires_at or not at all; dnd and sleeping always hold it; quiet hours do not hold a candidate whose premise is a confirmed presence edge under 15 minutes old.

A timed candidate SHALL be sent standalone by the timed sub-cycle only; the digest and urgent cycles SHALL NOT select it. One timed candidate SHALL exist per `dedup_key`. An unknown premise SHALL hold a timed candidate and a false premise SHALL withdraw it. A timed send SHALL count against the owner's configured budget for the UTC day across all lanes, and the timed lane SHALL send at most six candidates a day.

Every delivery worker SHALL claim the candidates it sends, all or none, before calling the transport, and SHALL write the final disposition only while holding that claim, so overlapping cycles send a candidate at most once. A timed candidate whose send may have started SHALL NOT be resent; a stale or failed claim with proof that no transport call started SHALL be released for a retry. The lane SHALL ship dark: while `TIMED_INSIGHTS_LIVE` is off, timed proposals are refused and the sub-cycle does nothing.

#### Scenario: Overlapping digest cycles

- **WHEN** two digest cycles select the same pending candidate at the same time
- **THEN** exactly one claims it and sends it, and the other sends nothing

#### Scenario: Timed window

- **WHEN** a timed candidate's `deliver_after` has not passed, or its `expires_at` has
- **THEN** it is not sent; once due and unexpired it is sent exactly once

#### Scenario: Holds

- **WHEN** dnd or sleeping is active, however long ago it was set
- **THEN** a due timed candidate stays pending
- **WHEN** quiet hours are active and the premise is a presence edge confirmed under 15 minutes ago
- **THEN** the timed candidate is sent; with an older edge or another premise it stays pending

#### Scenario: Ambiguous send

- **WHEN** a timed send raised, reported a terminal partial result, or its claim went stale after `attempt_started_at`
- **THEN** the candidate expires as `delivery_ambiguous` and is never resent
