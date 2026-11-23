## ADDED Requirements

### Requirement: Owner Links A Finding To Its Tracked Defect

The QA dashboard API SHALL let the authenticated owner link a finding fingerprint to a bead, GitHub
PR or GitHub issue with `POST /api/qa/known-issues/{fingerprint}/link`, and close the link with
`DELETE` on the same path. A first link SHALL return 201 and a repeat of the same fingerprint and
ref SHALL return 200 without changing its progress. A caller who is not the authenticated owner
SHALL receive 403. A link SHALL carry an expiry, defaulting to 14 days and refused above 90 days
or in the past, so it cannot suppress a finding forever; a lapsed link admits the finding as novel.
A tracker ref SHALL match the pattern for its kind (bead id, GitHub PR URL, GitHub issue URL) and
be at most 300 characters, else 422. Link writes are recorded in the dashboard audit log with the
caller, time and ref, as dismissals are. A bead ref SHALL be accepted as an unverified owner assertion; a GitHub PR ref SHALL
be refused with 422 when GitHub reports the pull request merged or closed, and accepted as an owner
assertion when GitHub cannot say.

ID: REQ-qa-dashboard-904
Source: bu-ejow5p.9 (run 25 move 9), slices S1 and S2; owner decisions on bu-ejow5p.9
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

#### Scenario: An over-long or malformed link is refused

- **WHEN** the owner posts a ref that does not match its kind, or an expiry beyond 90 days
- **THEN** the response is 422

### Requirement: QA Tracks Its Own Pull Requests And Shows The Link

The QA patrol SHALL link the fingerprint of each open QA pull request to that PR as `qa_auto`,
move the link to `fix_merged` when the PR merges, and close a QA-made link when the PR closes
unmerged; it SHALL NOT reopen a link the owner closed. A failure to write a link SHALL NOT fail
the PR status check. The case dossier SHALL carry the live link and the count of recurrences it
held back since it was made.

ID: REQ-qa-dashboard-905
Source: bu-ejow5p.9 (run 25 move 9), slice S2
Scope: v1-mandatory

#### Scenario: A QA pull request holds its fingerprint

- **WHEN** a QA PR is open or merged and the finding recurs
- **THEN** triage records it as `tracked` and the dossier shows the PR with the held-back count
