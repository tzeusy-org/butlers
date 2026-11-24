## Why

Ordinary butler sessions rely on roster skills for domain workflows. Several shipped prompts point directly to local files or include developer commands. A daemon MCP skill reader lets model-facing instructions remain on the domain's tool surface as legacy runtime confinement advances.

## What Changes

- Add `skill_read(name)` to the existing `infra` core tool group.
- Guide ordinary sessions to load their local and configured shared skills by name.
- Check real prompt sources, shared snippets and scheduled prompts with the existing skill-instruction gate.
- Retain Chronicler's operator verification recipes in its developer documentation.

## Capabilities

### Modified Capabilities

- `core-skills`: Define the legacy read-only roster skill contract and its prompt usage.

## Impact

The legacy daemon MCP surface and checked-in roster prompts use this capability. Runtime launch policies and the goal-runtime guidance corpus have separate governing changes.
