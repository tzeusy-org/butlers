# Skill: Curriculum Planning

## Purpose

Two-phase curriculum generation: (1) LLM-driven concept decomposition — decompose a topic into
a DAG of concepts with prerequisite edges; (2) deterministic ordering — topological sort with
depth and effort weighting to produce a learning sequence. The output is a mind map with
sequenced nodes, ready for the teaching phase.

## When to Use

Use this skill when:
- The teaching flow state is `PLANNING`
- The curriculum needs re-planning after analytics feedback (struggling subtree, low retention)
- The user explicitly requests a different learning path

## Phase 1: LLM Concept Decomposition

### Step 1: Read Flow Context

From the session context, read:
- `mind_map_id`: UUID of the mind map to populate
- `diagnostic_results`: Mastery seeds from the diagnostic phase (node_id → quality, inferred_mastery)
- Topic name from the mind map title

### Step 2: Design the Concept DAG

Decompose the topic into a DAG of concepts. Think through the full concept graph before calling
any tools. Plan your nodes and edges mentally first:

1. Identify all key concepts needed to understand the topic from beginner to advanced.
2. For each concept, identify its direct prerequisite concepts (must be understood first).
3. Assign depths: root concepts are depth 0, each dependent level increments depth by 1.
4. Estimate `effort_minutes` for each concept (e.g., 15–90 minutes per concept).

### Structural Constraints (Enforce Before Creating Nodes and Edges)

- **Maximum depth: 5** — the longest prerequisite chain must not exceed 5 levels. Flatten
  chains that would exceed depth 5 by combining closely related concepts.
- **Maximum 30 nodes per topic** — scope down if needed. Prefer coverage depth over excessive
  breadth. A 20-node focused curriculum is better than a 30-node sprawling one.
- **Must be a DAG** — prerequisite edges must form a directed acyclic graph. Never add an edge
  that creates a cycle. If a cycle would form, remove the weaker dependency (the less
  foundational of the two relationships).
- **Validate acyclicity mentally** before each `mind_map_edge_create()` call. The tool enforces
  DAG acyclicity, but thinking it through first prevents wasted calls.

### Step 3: Create Nodes

For each concept, create a node:

```
mind_map_node_create(
    mind_map_id=<mind_map_id>,
    label=<concept_name>,
    description=<1-2 sentence description of what the concept covers>,
    depth=<integer 0-5>,
    effort_minutes=<estimated learning effort in minutes>
)
```

Save the returned `node_id` for each concept — you need it for edge creation.

### Step 4: Create Prerequisite Edges

For each prerequisite relationship (parent must be learned before child):

```
mind_map_edge_create(
    parent_node_id=<prerequisite_node_id>,
    child_node_id=<dependent_node_id>,
    edge_type="prerequisite"
)
```

The tool validates DAG acyclicity before persisting. If it rejects an edge (cycle detected),
skip that edge and continue — do not retry the same edge.

## Phase 2: Topological Sort and Sequencing

### Step 5: Generate Curriculum

After all nodes and edges are created, call:

```
curriculum_generate(
    mind_map_id=<mind_map_id>,
    goal=<user's learning goal if provided, else None>,
    diagnostic_results=<dict of {node_label: quality_score} from diagnostic phase, or None>
)
```

This tool:
1. Validates structural constraints (max 30 nodes, max depth 5, DAG).
2. Applies diagnostic mastery seeding if `diagnostic_results` provided.
3. Runs topological sort with tie-breaking:
   - Depth (shallower first — prerequisites before dependents)
   - `effort_minutes` (lower effort first within same depth — quick wins build momentum)
   - Diagnostic mastery (partially-known concepts before fully-unknown — reinforce rather than start cold)
4. Writes `sequence` integers to each node.
5. Transitions mind map status to `'active'`.

Returns: `{ mind_map_id, node_count, edge_count, status }`.

### Step 6: Advance Flow State

Call `teaching_flow_advance(mind_map_id)` to transition from `PLANNING` to `TEACHING`.
This sets `current_node_id` to the first frontier node and `current_phase = "explaining"`.

### Step 7: Notify User

Retrieve the first frontier node to mention in the notification:

```
curriculum_next_node(mind_map_id)
```

Then notify:

```python
notify(
    channel="telegram",
    message=f"I've mapped out your learning path for [topic] — {node_count} concepts, "
            f"from [first_concept] to [advanced_concept]. "
            f"We'll start with [{first_frontier_node_label}].",
    intent="proactive",
    request_context=<session_request_context>
)
```

Exit. The TEACHING phase begins in the next triggered session.

## Re-planning

Call `curriculum_replan(mind_map_id, reason=<reason>)` when any of the following occur:
- Analytics feedback: `retention_rate_7d < 0.60` — low retention suggests sequence is too fast
- Analytics feedback: `struggling_nodes >= 3` — multiple struggling concepts need re-ordering
- User explicitly requests a different learning path or asks to slow down
- A new prerequisite gap is discovered mid-teaching (concept requires unmastered knowledge)

Re-planning does NOT modify the existing DAG structure (no nodes/edges added or removed).
It re-runs the topological sort with updated mastery data and marks mastered nodes as skippable.

If new nodes need to be added before re-planning:
1. Call `mind_map_node_create()` for new concepts.
2. Call `mind_map_edge_create()` for their prerequisite edges.
3. Then call `curriculum_replan()`.

## Exit Criteria

- All concept nodes exist in the mind map (5–30 nodes) with `sequence` integers assigned
- All prerequisite edges are created (DAG validated — no cycles)
- `curriculum_generate()` was called and returned successfully
- Flow state is transitioned from `PLANNING` to `TEACHING` via `teaching_flow_advance()`
- User is notified of the learning path and first concept
- Session exits without entering the TEACHING phase
