# Redesign bundles

This directory holds in-flight Claude Design redesign bundles only. Everything
else graduated (2026-07-03):

- **Slug map + bundle contract** → `.claude/skills/butlers-redesign-prompt/references/bundle-registry.md`
  (the skill's Phase 0 source-of-truth; add new bundle rows there, in the same commit as the bundle).
- **Design language** → `openspec/specs/dashboard-design-language/spec.md` (the Dispatch spec, binding).
- **dispatch-kit/** → `.claude/skills/butlers-redesign-prompt/references/dispatch-kit/` (execution material).

Currently in flight:

- `entity-redesign/` — retained while the `entity-v3-lifecycle-and-depth` OpenSpec change is active.
- Top-level `*.jsx` (`design-canvas.jsx`, `data.jsx`, `primitives.jsx`, `sidebar.jsx`,
  `tweaks-panel.jsx`) — cross-cutting prototype canvases; system material, not redesigns.

When the last bundle graduates, delete this directory.
