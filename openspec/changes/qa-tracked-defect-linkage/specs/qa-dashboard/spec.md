## ADDED Requirements

### Requirement: Owner Links A Finding To Its Tracked Defect

The QA dashboard API SHALL let the authenticated owner link a finding fingerprint to a bead, GitHub
PR or GitHub issue with `POST /api/qa/known-issues/{fingerprint}/link`, and close the link with
`DELETE` on the same path. A first link SHALL return 201 and a repeat of the same fingerprint and
ref SHALL return 200 without changing its progress. A caller who is not the authenticated owner
SHALL receive 403. A link SHALL carry an expiry, defaulting to 14 days, so it cannot suppress a
finding forever. A bead ref SHALL be accepted as an unverified owner assertion; a GitHub PR ref SHALL
be refused with 422 when GitHub reports the pull request merged or closed, and accepted as an owner
assertion when GitHub cannot say.

ID: REQ-qa-dashboard-904
Source: bu-ejow5p.9 (run 25 move 9), slice S1; owner decisions on bu-ejow5p.9
Scope: v1-mandatory

#### Scenario: A link is created once

- **WHEN** the owner links a fingerprint to a bead
- **THEN** the response is 201 and a repeat returns 200

#### Scenario: A non-owner cannot link

- **WHEN** a caller without the owner principal posts a link
- **THEN** the response is 403 and nothing is written

#### Scenario: A finished pull request is refused

- **WHEN** the owner links a GitHub PR that is merged or closed
- **THEN** the response is 422
