---
name: "OPSX: New"
description: Start a new change using the experimental artifact workflow (OPSX)
category: Workflow
tags: [workflow, artifacts, experimental]
---

Start a new change using the experimental artifact-driven approach.

**Input**: The argument after `/opsx:new` is the change name (kebab-case), OR a description of what the user wants to build.

**Steps**

1. **If no input provided, ask what they want to build**

   Use the **AskUserQuestion tool** (open-ended, no preset options) to ask:
   > "What change do you want to work on? Describe what you want to build or fix."

   From their description, derive a kebab-case name (e.g., "add user authentication" → `add-user-auth`).

   **IMPORTANT**: Do NOT proceed without understanding what the user wants to build.

2. **Choose an explicit schema for a new change**

   Read `about/heart-and-soul/development.md` and classify the actual scope and whether this is part
   of a new multi-PR program or continues an existing legacy program.

   - A new eligible single-butler change with no cross-butler contract, shared schema, or security
     boundary uses `single-butler` (proposal → tasks; design may be added).
   - A new standalone change that requires a design uses
     `living-specs` (proposal → design → tasks).
   - New multi-PR programs may use `spec-driven` with local deltas. New standalone work uses
     a no-delta schema; selecting local deltas is not an additional standalone option.
   - Preserve every existing legacy program with its resolved schema, metadata, content and
     deltas unchanged until verified completion. Do not retag it or add `skip_specs`.

   The project default stays `spec-driven` for existing changes and other legacy CLI use. New
   `/opsx:new` changes choose a schema explicitly rather than relying on that default.

3. **Create the change directory**
   ```bash
   openspec new change "<name>" --schema <selected-schema>
   ```
   For a new `single-butler` or `living-specs` change, preserve the CLI-generated `schema` and
   `created` fields in `.openspec.yaml` and add `skip_specs: true`. Do not create an empty `specs/`
   directory. These schemas use canonical specs as the maintained requirements; update an affected
   canonical requirement in the same source change whenever required behavior changes. Keep all
   existing artifact files and designs. A retained `spec-driven` change keeps its delta specs and
   metadata unchanged. Verify the new no-delta change has its generated schema/created fields and
   `skip_specs: true` before continuing; if they are missing or malformed, correct this new change first.

4. **Show the artifact status**
   ```bash
   openspec status --change "<name>"
   ```
   This shows which artifacts need to be created and which are ready (dependencies satisfied).

5. **Get instructions for the first artifact**
   The first artifact depends on the schema. Check the status output to find the first artifact with status "ready".
   ```bash
   openspec instructions <first-artifact-id> --change "<name>"
   ```
   This outputs the template and context for creating the first artifact.

6. **STOP and wait for user direction**

**Output**

After completing the steps, summarize:
- Change name and location
- Schema/workflow being used and its artifact sequence
- Current status (0/N artifacts complete)
- The template for the first artifact
- Prompt: "Ready to create the first artifact? Run `/opsx:continue` or just describe what this change is about and I'll draft it."

**Guardrails**
- Do NOT create any artifacts yet - just show the instructions
- Do NOT advance beyond showing the first artifact template
- If the name is invalid (not kebab-case), ask for a valid name
- If a change with that name already exists, suggest using `/opsx:continue` instead
- Pass the selected `--schema` explicitly for every new change
