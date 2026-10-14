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

2. **Determine the workflow schema**

   Read `about/heart-and-soul/development.md` and classify the actual scope.
   A single-butler change that alters no cross-butler contract, schema shared
   across butlers, or security boundary can use `--schema single-butler`:
   proposal, delta specs and tasks suffice without a design. Use the default
   `spec-driven` workflow when design is required or voluntarily chosen.
   Directory placement alone is not eligibility evidence; if the exclusions
   are not established, keep the default and resolve scope before proceeding.

   **For other explicitly requested workflows:**
   - A specific schema name → use `--schema <name>`
   - "show workflows" or "what workflows" → run `openspec schemas --json` and let them choose

   An explicitly requested workflow still must satisfy the repository's
   design requirement. Preserve existing design documents and all spec,
   owner-approval, implementation review and verification gates.

3. **Create the change directory**
   ```bash
   openspec new change "<name>"
   ```
   For an eligible single-butler change omitting design, add `--schema single-butler`.
   Otherwise omit it for the default or use an explicitly requested compatible schema.
   This creates a scaffolded change at `openspec/changes/<name>/` with the selected schema.

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
- Pass --schema if using a non-default workflow
