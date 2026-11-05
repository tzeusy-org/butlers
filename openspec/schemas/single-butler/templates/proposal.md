## Why

<!-- Explain the problem or opportunity. -->

## What Changes

<!-- Identify behavior changes and mark breaking changes. -->

## Workflow Eligibility

<!-- Cite scope evidence for each exclusion. If any boundary changes, use living-specs
     with design, or keep spec-driven for a retained local-delta program. -->
- Cross-butler contract: <!-- unchanged, with evidence -->
- Schema shared across butlers: <!-- unchanged, with evidence -->
- Security boundary: <!-- unchanged, with evidence -->

## Canonical Contract Impact

<!-- Name affected openspec/specs/<capability>/spec.md files. Update an affected
     requirement directly in the same change when required behavior changes. -->
- <canonical capability spec>: <!-- unchanged, updated, or not applicable -->

## Local Delta Specs

This schema intentionally has no change-local specs. Preserve the generated
`schema` and `created` fields in `.openspec.yaml` and add `skip_specs: true`.
Do not create a placeholder `specs/` directory.

## Impact

<!-- Affected code, APIs, dependencies, systems, and existing designs to read. -->
