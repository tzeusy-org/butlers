---
name: "OPSX: Fast Forward"
description: Create a change and generate all artifacts needed for implementation in one go
category: Workflow
tags: [workflow, artifacts, experimental]
---

Fast-forward through artifact creation - generate everything needed to start implementation.

**Input**: The argument after `/opsx:ff` is the change name (kebab-case), OR a description of what the user wants to build.

**Steps**

1. **If no input provided, ask what they want to build**

   Use the **AskUserQuestion tool** (open-ended, no preset options) to ask:
   > "What change do you want to work on? Describe what you want to build or fix."

   From their description, derive a kebab-case name (e.g., "add user authentication" → `add-user-auth`).

   **IMPORTANT**: Do NOT proceed without understanding what the user wants to build.

2. **Choose an explicit schema and create the change directory**
   Classify the actual scope under `about/heart-and-soul/development.md` and determine whether the
   change is part of a retained multi-PR delta program.

   - New eligible single-butler work with no cross-butler contract, shared schema, or security
     boundary uses `single-butler` (proposal → tasks; design may be added).
   - New standalone work requiring a design but no change-local deltas uses `living-specs`
     (proposal → design → tasks).
   - Retained multi-PR delta programs stay on `spec-driven` with all current deltas and metadata.
     Do not retag them or add `skip_specs`.

   Create it with the selected schema explicitly:
   ```bash
   openspec new change "<name>" --schema <selected-schema>
   ```
   For a new `single-butler` or `living-specs` change, preserve the generated `schema` and `created`
   values in `.openspec.yaml`, add `skip_specs: true`, and do not create a placeholder `specs/`
   directory. These schemas use canonical specs as the maintained requirements; update affected
   canonical requirements in the same source change when required behavior changes. Verify a new
   no-delta change has its generated schema/created fields and `skip_specs: true` before continuing;
   if they are missing or malformed, correct this new change first. If the change already exists,
   preserve all artifacts and metadata and continue it under its resolved schema.

3. **Get the artifact build order**
   ```bash
   openspec status --change "<name>" --json
   ```
   Parse `applyRequires` and the schema's artifact dependencies; do not infer a different schema
   from the project default.

4. **Create artifacts in sequence until apply-ready**

   Use the **TodoWrite tool** to track progress through the artifacts.

   Loop through artifacts in dependency order (artifacts with no pending dependencies first):

   a. **For each artifact that is `ready` (dependencies satisfied)**:
      - Get instructions:
        ```bash
        openspec instructions <artifact-id> --change "<name>" --json
        ```
      - The instructions JSON includes:
        - `context`: Project background (constraints for you - do NOT include in output)
        - `rules`: Artifact-specific rules (constraints for you - do NOT include in output)
        - `template`: The structure to use for your output file
        - `instruction`: Schema-specific guidance for this artifact type
        - `outputPath`: Where to write the artifact
        - `dependencies`: Completed artifacts to read for context
      - Read any completed dependency files for context
      - Create the artifact file using `template` as the structure
      - Apply `context` and `rules` as constraints - but do NOT copy them into the file
      - Show brief progress: "✓ Created <artifact-id>"

   b. **Continue until all `applyRequires` artifacts are complete**
      - After creating each artifact, re-run `openspec status --change "<name>" --json`
      - Check if every artifact ID in `applyRequires` has `status: "done"` in the artifacts array
      - Stop when all `applyRequires` artifacts are done
      - Recheck the repository design requirement before declaring apply-ready;
        if the actual scope needs a design, a missing design still blocks work.
        Artifact readiness does not confer spec/owner approval or waive review
        and executing verification.

   c. **If an artifact requires user input** (unclear context):
      - Use **AskUserQuestion tool** to clarify
      - Then continue with creation

5. **Show final status**
   ```bash
   openspec status --change "<name>"
   ```

**Output**

After completing all artifacts, summarize:
- Change name and location
- List of artifacts created with brief descriptions
- What's ready: "All artifacts created! Ready for implementation."
- Prompt: "Run `/opsx:apply` to start implementing."

**Artifact Creation Guidelines**

- Follow the `instruction` field from `openspec instructions` for each artifact type
- The schema defines what each artifact should contain - follow it
- Read dependency artifacts for context before creating new ones
- Use the `template` as a starting point, filling in based on context

**Guardrails**
- Create ALL artifacts needed for implementation (as defined by schema's `apply.requires`)
- Always read dependency artifacts before creating a new one
- If context is critically unclear, ask the user - but prefer making reasonable decisions to keep momentum
- If a change with that name already exists, ask if user wants to continue it or create a new one
- Verify each artifact file exists after writing before proceeding to next
