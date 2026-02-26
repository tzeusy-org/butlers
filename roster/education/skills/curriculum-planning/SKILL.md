# Skill: Curriculum Planning

## Purpose

Two-phase curriculum generation: (1) LLM-driven concept decomposition — decompose a topic into
a DAG of concepts with prerequisite edges; (2) deterministic ordering — topological sort with
depth and effort weighting to produce a learning sequence. The output is a mind map with sequenced
nodes, ready for the teaching phase.

## When to Use

Use this skill when:
- The teaching flow state is `PLANNING`
- `curriculum_generate()` needs to be called with the topic and diagnostic results
- The curriculum needs re-planning after analytics feedback (struggling subtree, mastery deviation)

## Phase 1: LLM Concept Decomposition

### Instructions

Decompose the topic into a DAG of concepts:

1. Identify all key concepts needed to understand the topic fully.
2. For each concept, identify its direct prerequisite concepts (concepts that must be understood
   first).
3. Structure the output as nodes and directed prerequisite edges.

### Structural Constraints (Enforce Before Creating)

- **Maximum depth: 5** — the longest prerequisite chain must not exceed 5 levels.
- **Maximum 30 nodes per topic** — scope down if needed; prefer coverage depth over breadth.
- **No cycles** — prerequisite edges must form a DAG. If a cycle would form, remove the weaker
  dependency edge (the less fundamental of the two).
- Validate acyclicity before calling `mind_map_edge_create()`. If a proposed edge would create
  a cycle, skip it and continue.

### Tool Calls

For each concept node:
```
mind_map_node_create(mind_map_id, label=<concept_name>, description=<brief_description>,
                     depth=<distance_from_root>, effort_minutes=<estimated_effort>)
```

For each prerequisite relationship:
```
mind_map_edge_create(parent_node_id=<prerequisite>, child_node_id=<dependent>, edge_type="prerequisite")
```

The tool performs DAG acyclicity validation before persisting. If it rejects an edge, skip it.

## Phase 2: Topological Sort and Sequencing

Called automatically by `curriculum_generate()`. Tie-breaking order:
1. Depth (shallower first — prerequisites before dependents)
2. `effort_minutes` (lower effort first within same depth — quick wins build momentum)
3. Diagnostic mastery (partially-known concepts before fully-unknown — reinforce rather than start cold)

The tool writes the computed `sequence` integer to each node.

## Re-planning

Call `curriculum_replan(mind_map_id, reason=<reason>)` when:
- Analytics feedback: `retention_rate_7d < 0.60` or `struggling_nodes >= 3`
- User explicitly requests a different learning path
- A new prerequisite gap is discovered mid-teaching

Re-planning recomputes sequence based on current mastery state. It may also add or remove nodes
(via LLM) if the topic scope needs adjustment.

## Exit Criteria

- All concept nodes exist in the mind map with `sequence` integers assigned
- All prerequisite edges are created (DAG validated — no cycles)
- Flow state is transitioned from `PLANNING` to `TEACHING`
- User is notified: "I've mapped out your learning path for [topic]. We'll start with [first concept]."
