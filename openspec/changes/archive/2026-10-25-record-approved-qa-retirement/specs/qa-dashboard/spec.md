## MODIFIED Requirements

### Requirement: Investigation Detail Page
The dashboard SHALL have a drill-down page at `/qa/investigations/:attemptId` that renders the same case dossier as `/qa?case=:attemptId`. The two routes mount the same `CaseDossier` component with no UX divergence.

#### Scenario: Investigation detail layout matches Case Dossier
- **WHEN** a user navigates to `/qa/investigations/:attemptId`
- **THEN** the page renders the Case Dossier layout (header + two-column diagnosis/PR + patrol journal) as defined by the Case Dossier Layout requirement
- **AND** the page includes a top eyebrow "QA Investigation · #<short_id>" and a back-link to `/qa`
- **AND** Dismiss and remove-dismissal remain available in their eligible states; no Retry action, client helper or mutation hook is exposed

#### Scenario: Investigation not found
- **WHEN** the attempt ID does not exist or has no `qa_patrol_id`
- **THEN** the page renders a serif-italic "Investigation not found." with the same chrome as the rest of `/qa/*`
