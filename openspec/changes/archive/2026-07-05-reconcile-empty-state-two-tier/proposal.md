## Why

PR #2934 (bu-qvnce.7) rewrote `EmptyState` to the letter of
`openspec/specs/dashboard-design-language/spec.md`'s "Interface Copy" and
"Page Conformance" requirements: "Empty states are one serif-italic sentence
with no trailing explanation" / "(8) every empty state is one serif-italic
sentence with no illustration." Applied uniformly, that rewrite moved
`description` to `sr-only` for every one of the ~44 consumers of the shared
`EmptyState` component — including page-level error and empty states that had
real, load-bearing visible guidance for sighted users (e.g. CirclesPage's
"Ask the relationship butler to create one...", ConcentrationPage's two
failure causes, IssuesPanel's auto-retry/escalation note, TimelineLedger's
failed-vs-empty disambiguation).

But `about/heart-and-soul/design-language.md` § Empty states — the doctrine
document the Dispatch spec is supposed to operationalize — already draws a
two-tier line the spec's blanket rule does not capture:

- **Page-level empty states** (an ordinary page, panel, or table with nothing
  to show) use a `{Noun} + verb phrase` title, "one short sentence of context
  if needed," and a single action button.
- **Inline empty states inside a Voice surface** (the briefing column, the
  attention list when nothing needs attention, the Next list when nothing is
  upcoming) are held to the stricter rule: one serif-italic sentence, no
  explanation, no action button.

The spec's blanket "one serif-italic sentence, no trailing explanation, no
illustration" line only matches the second (Voice-surface) tier. It
overclaims when applied to the first (page-level) tier, and PR #2934 followed
the spec's letter over the doctrine's WHY, regressing sighted-user guidance
across the majority tier.

This change amends the spec text to the two-tier model doctrine already
specifies, so implementation (`EmptyState`'s new `variant="page" | "voice"`
prop, landed alongside this change under bu-eyo56) and spec agree, and no
future page-level empty state gets flattened to the stricter rule again.

## What Changes

- `Interface Copy` requirement: replace the blanket "empty states are one
  serif-italic sentence with no trailing explanation" rule with the two-tier
  model (page-level vs. Voice-surface-inline), each with its own scenario.
- `Page Conformance` requirement, criterion (8): replace "every empty state
  is one serif-italic sentence with no illustration" with a tier-aware
  criterion — neither tier renders an illustration, but only the
  Voice-surface-inline tier is held to the single-sentence rule.

No API, schema, or non-copy behavioral change. This is a doctrine-alignment
correction to an existing spec, not a new capability.

## Impact

- Affected spec: `dashboard-design-language`
- Affected code (implemented alongside this change, not gated behind it):
  `frontend/src/components/ui/empty-state.tsx` (`variant` prop),
  `frontend/src/components/ui/error-state.tsx` (new — error-tier sibling for
  the call sites that were misusing `EmptyState` for a genuine fetch
  failure), and the ~44 call sites across `frontend/src/components/**` and
  `frontend/src/pages/**` that consume `EmptyState`.
