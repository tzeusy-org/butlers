# Interfaces and Dependencies

This file defines how interfaces should change in Butlers and what bar a new
dependency must meet.

## Interface Hygiene

Changes to MCP tools, API routes, schema contracts, migrations, or config
surfaces should be:

- explicit about what changed
- narrow in scope
- documented in the same change
- covered by tests or contract checks where practical

## Compatibility Rules

- Preserve compatibility only when there is a verified external consumer or a
  real staged migration constraint.
- Inside this repo, prefer deleting retired aliases and fallback branches once
  the new path is ready.
- Do not let two names, two payload shapes, or two code paths coexist without a
  clear migration reason and removal plan.

## Contract-Touching Changes

Before changing a boundary, check the relevant pillar:

- RFCs in `about/legends-and-lore/` for wire and architecture contracts
- specs in `openspec/` for promised behavior
- topology docs in `about/lay-and-land/` for ownership and placement

If the contract changes, update the contract document too.

## Dependency Admission Bar

Do not add a dependency unless it clearly beats the existing stack on net:

simpler implementation, lower maintenance burden, or better correctness or
safety, with no reasonable in-repo alternative.

When dependency behavior affects tests, startup, networking, or migrations,
document the operational consequence.
