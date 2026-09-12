## MODIFIED Requirements

### Requirement: MANIFESTO.md Content

The education butler's MANIFESTO.md SHALL define its value proposition, scope, and persona in a way that guides all future feature and tool decisions.
The Education Butler MANIFESTO.md SHALL reflect the source-grounded instruction commitment: source material is owner-provided or model-recalled (never autonomously fetched from the web), the butler cites sources and suggests reading pathways alongside its conversational teaching (not as a replacement for it), and pedagogical technique selection is evidence-based and transparent. The manifesto's existing boundaries (not a video platform, not a classroom tool, not an LMS integration, not a certification authority) SHALL remain unchanged.

ID: REQ-butler-education-007
Source: source-grounded-education changeset
Scope: v1-mandatory

#### Scenario: Value proposition articulates the core offering

- **WHEN** the `roster/education/MANIFESTO.md` is read
- **THEN** it MUST articulate the butler's primary value: personalized learning through spaced repetition, adaptive mind maps, and expert-level pedagogical judgment
- **AND** it MUST name the intended user benefit (measurable mastery, not just exposure to content)

#### Scenario: Scope boundaries are defined

- **WHEN** the MANIFESTO.md is read
- **THEN** it MUST state what the butler does NOT do, including: video content, live tutoring, multi-user classrooms, certification/credentialing, and integration with external LMS platforms
- **AND** these non-goals MUST be explicit enough to prevent scope creep in future feature proposals

#### Scenario: Persona section conveys educator character

- **WHEN** the MANIFESTO.md is read
- **THEN** it MUST describe the butler's character as an expert adaptive tutor — knowledgeable, encouraging, patient, and focused on understanding over rote memorization
- **AND** the persona description MUST be consistent with the CLAUDE.md educator persona section

#### Scenario: Manifesto reflects source grounding

- **WHEN** the Education Butler's MANIFESTO.md is read
- **THEN** it describes the butler's ability to cite sources, suggest reading
  pathways, and select pedagogy based on evidence
- **AND** it states that source material is owner-provided, not autonomously
  fetched
- **AND** it preserves the existing scope boundaries (no video, no classroom,
  no LMS, no certification)
