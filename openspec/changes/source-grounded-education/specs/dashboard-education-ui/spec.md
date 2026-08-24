## MODIFIED Requirements

### Requirement: Mind map graph visualization in Curriculum tab

The Curriculum tab SHALL render the selected mind map as an interactive directed acyclic graph (DAG) using XYFlow with dagre top-to-bottom layout.

Each node SHALL display the concept label and a mastery score badge. Nodes SHALL be color-coded by `mastery_status`:
- `mastered`: emerald (`#10b981`)
- `reviewing`: blue (`#3b82f6`)
- `learning`: amber (`#f59e0b`)
- `diagnosed`: slate (`#64748b`)
- `unseen`: gray (`#d1d5db`)

Edges of type `prerequisite` SHALL render as solid arrows. Edges of type `related` SHALL render as dashed lines.

Frontier nodes (from the `/frontier` endpoint) SHALL have a pulsing ring indicator to highlight them as next teachable concepts.

Clicking a node SHALL select it through the shared page-level handler and reveal the shared detail panel showing: node label, description, mastery score, mastery status, next review date (if scheduled), effort estimate, the spaced-repetition internals `ease_factor` and `repetitions`, and a link to view quiz history for that node.

The detail panel SHALL additionally render the node's pedagogy annotations when its `metadata`
carries them: `concept_type` as a tag beside the mastery status, and each `source_refs` entry as a
row bearing a leading provenance label in plain words, the entry's `location`, and its optional
`note`. A node whose metadata carries neither annotation SHALL render exactly as before.

Provenance SHALL be resolved against the source registry
(`GET /api/education/sources`) and rendered in exactly one of four states:

- **Referenced** when the entry records `provenance: "referenced"` (or names a source and records
  no provenance) AND the registry resolves its `source_id`. Only this state SHALL render the
  resolved source title or a link to the registered source's URL.
- **Model-recalled** when the entry records `provenance: "model-recalled"` or carries a null
  `source_id`, regardless of whether the registry resolves the source, together with text stating
  that the butler did not read the source and the location is unverified.
- **Source no longer registered** when the registry resolves and does not contain the entry's
  `source_id`. The panel SHALL show no title and no citation link, and SHALL surface the
  unresolved `source_id` so the owner can act on it.
- **Not checked against the registry** when the registry request is loading or failed. The panel
  SHALL NOT report the entry as registered or as unregistered, because it has no basis for either.

An entry with an unrecognized `provenance` value SHALL be rendered as model-recalled: a malformed
annotation is never promoted to a citation.

#### Scenario: Referenced source annotation on node detail

- **WHEN** a node's `metadata.source_refs` carries an entry with `provenance: "referenced"` whose
  `source_id` is present in the source registry
- **THEN** the detail panel SHALL show the label "Referenced", the registered source's title, and
  the entry's location
- **AND** a link to the registered source's URL SHALL be offered when the record has one

#### Scenario: Model-recalled location against a registered source

- **WHEN** a node's `metadata.source_refs` carries an entry with `provenance: "model-recalled"`
  whose `source_id` is present in the source registry
- **THEN** the detail panel SHALL label the entry "Model-recalled" and state that the location is
  unverified
- **AND** SHALL NOT offer a link to the registered source

#### Scenario: Dangling source reference after the source is removed

- **WHEN** a node's `metadata.source_refs` names a `source_id` the registry does not contain
- **THEN** the detail panel SHALL label the entry "Source no longer registered"
- **AND** SHALL show no source title and no citation link
- **AND** SHALL show the unresolved `source_id`

#### Scenario: Source registry unavailable

- **WHEN** the source registry request is still loading or has failed
- **AND** a node's `metadata.source_refs` names a `source_id`
- **THEN** the detail panel SHALL label the entry as not checked against the registry
- **AND** SHALL NOT label it registered or unregistered, and SHALL show no source title

#### Scenario: Concept type tag on node detail

- **WHEN** a node's `metadata.concept_type` is one of `factual`, `procedural`, `conceptual`, or
  `creative`
- **THEN** the detail panel SHALL render it as a tag beside the mastery status
- **AND** a node with no `concept_type`, or an unrecognized one, SHALL render no such tag

#### Scenario: Render a mind map with mixed mastery statuses

- **WHEN** the Curriculum tab loads for a mind map with 10 nodes
- **AND** 3 are mastered, 2 reviewing, 2 learning, 1 diagnosed, 2 unseen
- **THEN** the graph SHALL render 10 nodes with the correct color for each status
- **AND** prerequisite edges SHALL be solid arrows
- **AND** the layout SHALL flow top-to-bottom (root concepts at top)

#### Scenario: Frontier nodes highlighted

- **WHEN** the graph renders
- **AND** the frontier endpoint returns 2 nodes
- **THEN** those 2 nodes SHALL have a pulsing ring indicator

#### Scenario: Node click opens detail panel

- **WHEN** the user clicks a node labeled "List Comprehensions"
- **THEN** a detail panel SHALL appear showing the node's label, description, mastery score, mastery status, and next review date

#### Scenario: Empty mind map (no nodes)

- **WHEN** the Curriculum tab loads for a mind map with 0 nodes
- **THEN** the graph area SHALL display "This curriculum has no concepts yet — the butler is still building it"
