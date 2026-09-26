## Context

The canonical Dispatch spec already requires surfaces instead of cards, rule and rhythm hierarchy,
eyebrow section titles, quiet states without decorative filler, and one visual affordance per
operational signal. `frontend/src/index.css` remains the token-value authority and
`frontend/src/lib/visual-token-roles.ts` remains the semantic resolver.

## Decisions

1. `Section` renders semantic `<section>` markup and an optional Eyebrow heading. Its default
   surface is flat and rule-led. A quiet section renders no children and may show one serif-italic
   sentence supplied by the caller.
2. `Tile` is a separate semantic `<section>` used only by dense status-grid modules whose own
   data can be loading or degraded independently. Its `loading` and `degraded` markers describe
   that boundary; consumers retain their existing state copy and retry behavior.
3. Card subparts are migrated to named Section or Tile anatomy (`Header`, `Title`, `Description`,
   `Content`, `Action`, and `Footer`) so the new primitive is explicit. No `Card` export, alias,
   path shim, or duplicate elevated surface remains.
4. Connector and topology status marks use `StateDot` or `stateColorVar`. A module does not
   maintain a private state-to-token map when the shared resolver can express the same meaning.
5. The new ESLint selectors only reject imports from the retired Card path and CSS custom-property
   fallbacks. They do not add a blanket Tailwind named-color ban or replace the existing visual
   role registry.

## Verification

- Section and Tile render tests pin semantic markup, Eyebrow anatomy, quiet behavior, independent
  loading/degraded markers, and the absence of Card chrome.
- Existing connector roster, connector detail, connector auth, Google Health, topology, and
  StateDot tests remain the behavior seams for state rendering.
- Frontend lint, `npm run knip`, build, targeted Vitest files, `make check-guards`, and the dirty
  worktree test planner provide the pre-push evidence. Terminal hosted CI remains the broad gate.
