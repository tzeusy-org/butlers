## ADDED Requirements

### Requirement: The Investigator Receives A Dispatcher-Built Evidence Bundle

Before launching an investigation the dispatcher SHALL build an evidence bundle from data it can
observe: the failing sessions and tool-call failures of the finding, log lines matching its
fingerprint in its window, the call-site file's recent git history, and recent dispatch outcomes
for the source butler. Every body SHALL pass the anonymizer and its validator, each item SHALL
carry a stable id and a content digest, and the bundle SHALL carry a revision and a total digest.
A source that cannot be read SHALL be marked unavailable and never invented; items and bytes SHALL
be capped with an explicit truncated status. The dispatcher SHALL record the bundle on the
finding before launch and SHALL write only a read-only copy to the agent's input area. Git history
SHALL run on the dispatcher's own repository with a pinned argument vector, no external diff or
textconv, and no repository, global or system configuration. If any body cannot be made clean the
launch SHALL be refused, and that refusal SHALL NOT count as a launched execution for the circuit
breaker.

ID: REQ-qa-investigation-dispatch-017
Source: bu-ejow5p.13 (run 25)
Scope: v1-mandatory

#### Scenario: A source is unreadable

- **WHEN** the tool-call failure view cannot be read
- **THEN** that section is marked unavailable and the investigation still launches with the rest

#### Scenario: A body cannot be anonymized

- **WHEN** an evidence item still carries sensitive content after anonymization
- **THEN** no agent is launched and the attempt is not counted toward the breaker

### Requirement: Patrol Binds Verified Owner-Published Proposals

Patrol SHALL associate an owner-published pull request with a retained QA proposal only when
read-only evidence proves the uniquely observed PR has the exact repository, head branch, base
ref and sealed head captured for that attempt. The repository and base ref SHALL be captured from
the trusted dispatch context with the immutable export; the retained base commit remains unchanged
even when the live base advances. The repository SHALL remain allowed. An unbound legacy export,
expired or unavailable export, ambiguous observation, foreign repository or mismatched ref SHALL
leave the attempt unchanged. An unbound legacy export SHALL report a content-free reason. A database-time conditional write
SHALL associate the PR once and record `owner_published` provenance; repeated or concurrent
patrols SHALL be idempotent. Existing PR tracking SHALL continue without autonomous follow-ups for
owner-published PRs.

ID: REQ-qa-investigation-dispatch-020
Source: bu-ejow5p.7.4 (run 25)
Scope: v1-mandatory

#### Scenario: Owner publishes the exact retained proposal

- **WHEN** a unique PR matches the retained target, branch, base and sealed head before expiry
- **THEN** one patrol associates it with the attempt as `pr_open` and records owner provenance
- **AND** repeated or competing patrols do not create another association

#### Scenario: The target or observation cannot be proven

- **WHEN** the origin is missing, the export has expired, or PR evidence is unavailable, ambiguous or mismatched
- **THEN** the proposal remains retained and the attempt is unchanged

### Requirement: Investigation Notes Cite The Evidence Bundle And Unsupported Lines Are Marked

Investigation notes at schema version 2 SHALL carry a `ref` on each evidence line naming the
evidence bundle item it quotes. When persisting notes the dispatcher SHALL verify each line
against the evidence it recorded before launch, never against any agent-visible copy: a line is
supported only if its `ref` names a recorded item and its `msg`, after whitespace normalisation,
is either that item's whole text or a span of at least 16 characters of it. All other lines SHALL
be stored with `unsupported = true`, and a claim that cites no supported line SHALL be stored with
`unsupported = true`. A case with no supported claim, including one with no claims, SHALL NOT be
`patch_ready`. The dispatcher SHALL discard every agent-supplied `unsupported` flag and
`verification` value and store an explicit `verification` of `verified` (with the bundle digest)
or `unverified`. When no bundle is available a later persist SHALL keep flags already stored by a
`verified` pass and otherwise store `unverified`. The dossier SHALL label unsupported lines and
claims and SHALL state when citations were not verified.

ID: REQ-qa-investigation-dispatch-018
Source: bu-ejow5p.13 (run 25)
Scope: v1-mandatory

#### Scenario: An invented citation

- **WHEN** the agent cites a ref that is not in the recorded bundle, or a one-character quote of a longer item
- **THEN** that line and any claim resting only on it are stored unsupported and shown as such

#### Scenario: Only unsupported claims

- **WHEN** every claim of a case rests only on unsupported lines
- **THEN** the dossier renders them as an unsupported claim and the case cannot be `patch_ready`

#### Scenario: An unsupported diagnosis retains no patch

- **WHEN** an investigation commits a change but every claim in its notes is unsupported (or, with a recorded bundle, its notes are missing or unparsable)
- **THEN** nothing is retained or published, and the attempt ends `failed` with a `diagnosis_unsupported` reason
- **AND** the breaker counts it as an execution success and the fingerprint is held like a retained proposal
