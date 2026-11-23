## MODIFIED Requirements

### Requirement: Durable Location Evidence Fidelity

Every successfully persisted OwnTracks location evidence write SHALL preserve
the accepted webhook payload in `connectors.owntracks_points` without deleting,
renaming, normalizing, or synthesizing optional fields. The evidence row is committed
before the Switchboard submit, the checkpoint and the webhook acknowledgement: a
failed evidence write raises, the webhook answers 503 and the phone keeps the
point, with nothing submitted. A failed submit keeps the row. The only exception to payload
fidelity is bearer material in free-text string fields, which the connector-base
placeholder replaces before the write; a detector failure skips the evidence
write. This evidence store is distinct
from the `ingest.v1` envelope's privacy tier: metadata-tier envelopes still omit
`payload.raw`, while the restricted durable evidence row retains the accepted
location payload required by deterministic adapters.

#### Scenario: SSID and region membership survive evidence persistence

- **WHEN** an accepted location payload contains uppercase `SSID` and an
  `inregions` list
- **THEN** `connectors.owntracks_points.raw_payload` contains those fields with
  the same names, values, list ordering, and element values received from the
  phone
- **AND** the persistence path does not mutate the caller's payload object

#### Scenario: Optional evidence fields stay absent when not reported

- **WHEN** the phone omits `SSID` or `inregions`
- **THEN** evidence persistence does not fabricate that field

ID: REQ-connector-owntracks-001
Source: bu-uhbhzq.4 S1; owner decision run24-inbox-not-archive (2026-11-22); RFC 0014 D2
Scope: v1-mandatory

#### Scenario: Evidence is committed before the submit

- **WHEN** an accepted location event is processed
- **THEN** its `connectors.owntracks_points` row exists before the Switchboard submit is called
- **AND** a failed evidence write submits nothing, leaves the checkpoint unchanged and answers 503
- **AND** a failed submit leaves the evidence row in place
