## Tasks

### 1. Amend Education MANIFESTO.md

- [x] 1.1 Replace the content-sourcing exclusion with a scoped source-grounding
      commitment. Strengthen the pedagogy commitment to name evidence-based technique
      selection and transparency. Preserve all other manifesto boundaries.

Acceptance:
- Content-sourcing exclusion replaced with scoped statement
- Pedagogy transparency commitment added
- All other "What We Do Not Do" boundaries unchanged
- REQ-butler-education-007 scenario passes

### 2. Source material registry tools

- [x] 2.1 Add `source_material_register`, `source_material_list`, and
      `source_material_remove` MCP tools to the Education butler's tool module.
      Storage in the state store under `education/source/<source_id>`. Validate
      required fields (title, type). No external network requests.

Acceptance:
- REQ-education-source-grounding-001 scenarios pass
- Tool creates, lists, and removes source records
- No migration required (state store only)
- Dangling source_refs handled gracefully on source removal

### 3. Concept-type classification in curriculum planner

- [x] 3.1 Extend `curriculum_generate` to assign `metadata.concept_type` per node.
      Classification inferred from label/description. Source mapping when registered
      sources exist. Update curriculum-planning skill instructions.

Acceptance:
- REQ-module-education-curriculum-001 modified scenarios pass
- Concept types assigned to classifiable nodes
- Source refs populated for mappable nodes
- Unclassifiable nodes gracefully degrade (no concept_type)

### 4. Pedagogy-aware teaching session

- [ ] 4.1 Extend teaching-session skill to select technique by concept type. Add
      pedagogy transparency ("why this approach?"). Add source citation in
      explanations and reading pathway suggestions after concept completion.

Acceptance:
- REQ-module-education-teaching-flows-006 modified scenarios pass
- REQ-education-source-grounding-002 and 003 scenarios pass
- Technique varies by concept type; default is Socratic
- Citations include source_id and location
- Reading pathways suggested when relevant sources exist

### 5. Dashboard source annotation display

- [x] 5.1 Extend the mind map node detail view to show source annotations and concept
      type. Source refs displayed with title (from registry lookup), location, and
      provenance label (referenced vs. model-recalled).

Acceptance:
- Source refs render on node detail with correct provenance labels
- Concept type displayed as a tag on the node
- Nodes without source refs or concept type display unchanged
- Dangling source refs (removed source) show "source no longer registered"
- An unreachable source registry is reported as unchecked, never as unregistered
- REQ-dashboard-education-ui and the new sources endpoint scenarios pass
