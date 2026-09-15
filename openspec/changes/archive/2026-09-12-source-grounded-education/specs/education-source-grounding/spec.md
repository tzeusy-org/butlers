## ADDED Requirements

### Requirement: Source Material Registry

The Education butler SHALL provide tools to register, list, and remove source
material metadata. Each source record contains a title, author(s), type (book,
paper, documentation, article), an optional structured table of contents, and
an optional URL. Source records are stored in the education butler's `state`
store under `education/source/<source_id>` as JSON objects. No document content
is stored or parsed — only metadata. Source records are education-scoped and not
shared with other butlers.

ID: REQ-education-source-grounding-001
Source: Education MANIFESTO.md amendment (source-grounded instruction)
Scope: v1-mandatory

#### Scenario: Registering source material

- **WHEN** the owner provides source material metadata (title, author, type)
  via conversation or a `source_material_register` tool call
- **THEN** a source record is created in the state store under
  `education/source/<generated-uuid>` with the provided metadata and a
  `registered_at` timestamp
- **AND** no external network request is made to fetch or verify the source

#### Scenario: Listing registered sources

- **WHEN** the owner or a teaching session queries registered source material
- **THEN** all active source records are returned with their IDs, titles,
  authors, and types

#### Scenario: Removing a source

- **WHEN** the owner asks to remove a registered source
- **THEN** the source record is deleted from the state store
- **AND** existing `source_refs` on mind map nodes referencing the removed
  source are NOT automatically deleted (they become dangling references
  rendered as "source no longer registered")

### Requirement: Source Citations on Mind Map Nodes

Mind map nodes SHALL support a `metadata.source_refs` array. Each entry
contains a `source_id` (referencing a registered source or `null` for
model-recalled citations), a `location` (chapter, page range, section — free
text), an optional `provenance` of `"referenced"` or `"model-recalled"`, and an
optional `note`.
Provenance records whether the location was read out of the source itself
(`"referenced"`) or produced from the model's own knowledge of it
(`"model-recalled"`). A writer that knows which it did SHALL record
`provenance` explicitly, and that value SHALL win in any user-facing display:
a registered `source_id` establishes that the source exists, not that the
location was read from it. When `provenance` is absent it is derived from the
reference itself, a named `source_id` reading as "referenced" and a null
`source_id` as "model-recalled". An unrecognized `provenance` value SHALL be
displayed as "model-recalled", never promoted to a citation.
A display SHALL make the weaker provenance unmistakable in words, not by colour
alone, and SHALL withhold citation affordances (a resolved source title, a link
to the source) from every state other than "referenced".

ID: REQ-education-source-grounding-002
Source: Education MANIFESTO.md amendment (source-grounded instruction)
Scope: v1-mandatory

#### Scenario: Teaching session adds a source citation to a node

- **WHEN** the teaching session explains a concept and a relevant registered
  source exists
- **THEN** the session adds a `source_refs` entry to the node's metadata with
  the `source_id`, `location`, and optional `note`
- **AND** the citation is included in the teaching session's conversational
  output as a reading pathway suggestion

#### Scenario: Model-recalled location against a registered source

- **WHEN** a writer records a `source_refs` entry naming a registered
  `source_id` with `provenance: "model-recalled"` (the curriculum planner's
  default, since it works from model knowledge of the source rather than from
  the source itself)
- **THEN** the entry is labeled "model-recalled" in any display
- **BECAUSE** the registry confirms the source exists, not that anything read
  it: presenting the location as referenced would overstate what the butler did

#### Scenario: Model-recalled citation without registered source

- **WHEN** the teaching session cites a source that is not in the source
  registry (e.g., a well-known textbook the owner hasn't registered)
- **THEN** the citation is added with `source_id: null` and labeled
  "model-recalled" in any display
- **BECAUSE** model-recalled citations may have imprecise locations and should
  be distinguished from owner-verified registered sources

#### Scenario: Node with no source refs

- **WHEN** a mind map node has no `source_refs` in its metadata (legacy or
  model-knowledge-only concept)
- **THEN** the node is displayed without citation annotations
- **AND** all existing education functionality continues unchanged

### Requirement: Reading Pathway Suggestions

The teaching session SHALL suggest specific reading pathways after explaining a
concept when relevant source material is available. A reading pathway is a
reference to a specific location in a registered source (e.g., "for the formal
proof, see chapter 3, pp. 45–52 of [title]"). Reading pathways are
conversational suggestions, not mandatory prerequisites — the owner can ignore
them and continue learning.

ID: REQ-education-source-grounding-003
Source: Education MANIFESTO.md amendment (source-grounded instruction)
Scope: v1-mandatory

#### Scenario: Reading pathway suggested after concept explanation

- **WHEN** the teaching session finishes explaining a concept and at least one
  registered source covers the concept
- **THEN** the session suggests a reading pathway with the source title and
  specific location
- **AND** the suggestion is phrased as optional ("for deeper study, see…"),
  not as a prerequisite

#### Scenario: No reading pathway when no source is relevant

- **WHEN** the teaching session explains a concept and no registered source
  covers it
- **THEN** no reading pathway is suggested
- **AND** the teaching continues normally from model knowledge
