## MODIFIED Requirements

### Requirement: Fleet Case Tool Admission And Posture Forwarding

The fleet_cases core group SHALL provide find_open_case, open_case, contribute_case_evidence, propose_case_posture, close_case, record_case_link, and read_case wherever the governing runtime tool-surface contract enables the group, including Switchboard staffer registration. Reads and evidence INSERT SHALL use the caller own pool; non-Switchboard case/link writes SHALL forward through Switchboard's service-plane relay, which is not LLM-presentable and is not on the session listener, to its own writer pool; the generic `route` tool is deleted at cutover. Posture proposals SHALL be plain last-write-wins at Switchboard, without quorum, decay, or per-butler cooldown. Typed domain-event evidence SHALL accept case references.

ID: REQ-fleet-case-file-008
Source: RFC 0032 §Decision posture and §Slice plan S3/S7; governing active add-runtime-tool-surface-discovery REQ-core-daemon-002; [Observed] EVIDENCE_KINDS; RFC 0038 D7
Scope: v1-mandatory

#### Scenario: Non-Switchboard write

- **WHEN** a non-Switchboard butler opens, proposes posture, closes, or records a link
- **THEN** the write is forwarded to Switchboard instead of mutating case/link rows through a peer role

#### Scenario: Switchboard writer reachable

- **WHEN** Switchboard staffer has fleet_cases enabled by governing group policy
- **THEN** all seven tools are registered and its case/link writes use its own pool

#### Scenario: Posture proposal

- **WHEN** Switchboard accepts a valid proposal on a non-closed case
- **THEN** that write sets the posture in last-write-wins order with no voting mechanism

#### Scenario: Typed case reference

- **WHEN** a domain-event reaction includes valid evidence kind case with a case ref
- **THEN** the shared typed-evidence validator accepts that kind
