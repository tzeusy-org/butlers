# Reference — implementation notes for Claude Code

A few cross-cutting notes that didn't fit cleanly into a single prompt.

## File map for the new code

Suggested layout under `frontend/src/`:

```
lib/
  entity-model.ts                    types + predicate / type catalogs (prompt 00)
  entity-api.ts                      thin fetcher wrappers around /api/entities/*

pages/
  EntitiesPage.tsx                   redesigned Index (prompt 01)
  EntityHopPage.tsx                  Hop (prompt 02)
  EntityColumnsPage.tsx              Columns (prompt 03)
  EntityConcentrationPage.tsx        Concentration (prompt 04)
  EntityDetailPage.tsx               Editorial detail (prompt 05) + ?view=workbench (prompt 06)

components/entities/
  EntityMark.tsx                     existing or new — initials/glyph mark
  TierBadge.tsx
  StatePill.tsx                      unidentified / duplicate / stale
  PredicateChip.tsx
  Sparkline.tsx                      90-day touch buckets, no axes
  QueueCard.tsx                      three variants — unidentified, duplicate, stale
  CurationLink.tsx
  TripleRow.tsx                      Workbench RDF row

components/finder/
  Finder.tsx                         overlay (prompt 07)
  FinderResultRow.tsx
  useFinderKeyBindings.ts            global Cmd-K / / listener

components/layout/
  SubpageTabs.tsx                    Index / Hop / Columns / Concentration / Social map
```

The prototype's component names map almost 1:1. Use the prototype as the
visual source of truth; translate JSX inline styles to the shadcn/Tailwind
idiom the app uses, but **keep the token names** (`--bg`, `--mfg`,
`--border`, etc.) wherever they appear in the prototype.

## Translating prototype JSX → real app idiom

The prototype uses inline styles for clarity (one file, easy to read).
The real app uses Tailwind + the tokens in `frontend/src/index.css`. The
translation is mechanical:

- `style={{ color: 'var(--fg)' }}` → `className="text-foreground"`
- `style={{ borderTop: '1px solid var(--border)' }}` → `className="border-t border-border"`
- `style={{ fontFamily: 'var(--font-mono)' }}` → `className="font-mono"`
- `style={{ fontFeatureSettings: 'tnum' }}` → `className="tabular-nums"`
- `style={{ fontFamily: 'var(--font-serif)' }}` → likely needs a custom
  utility class `font-serif` or `class="dispatch-voice"` — match what
  Overview.tsx and other Dispatch surfaces already do.

For colours that don't have a Tailwind utility yet (`--mfg`, `--dim`,
`--bg-deep`), add them to the Tailwind config under `theme.extend.colors`
keyed to the same CSS var names. Same for `--border-soft`,
`--border-strong`.

## Naming the tabs in the sidebar

Today's sidebar has Entities under "Main" and Contacts under "Butlers".
After this work:

- Keep Entities under "Main"
- Remove Contacts (the route 301s to `/entities?has=contact`)
- The sidebar item count badge can show the queue count
  (`unidentified + duplicate-candidate + stale`). Use the existing badge
  primitive from the Approvals item.

## State management

There is no need for global state in this redesign. Every page can be a
straight React Query hook over the entity API. The Finder, since it's
app-wide, lives in a top-level layout component that owns its own state
and a ref to the previously-focused element.

## Performance notes

- `/api/entities` may return thousands of rows. Use cursor-based
  pagination and virtualise the table with `@tanstack/react-virtual` if
  the existing Entities page doesn't already.
- `/api/entities/:id/neighbours` should cap returned neighbours at, say,
  200 per call, then the page can request more. The Hop view does not
  need to render an entity with 1,000 direct neighbours.
- The Finder's search endpoint should respond in <100 ms for 10k
  entities. Server-side trigram or a simple in-memory inverted index is
  fine.

## Accessibility minimum

- All keyboard shortcuts must have at least one click affordance.
- Focus rings are 2-px `--fg` outlines with 2-px offset, on
  `:focus-visible` only. Per Dispatch §7.
- `aria-pressed` on every `Pill`. The prototype already does this.
- The Finder overlay traps focus. `Esc` restores prior focus.
- Sparkline has a `<title>` child summarising the 90-day shape ("12
  touches in 90 days, 3 today").

## Things that are *not* changing in this pack

- `/entities/social-map` — left as-is. If it needs work, propose a
  separate piece of design.
- The sidebar's overall shape.
- The Dispatch design language. **Don't extend tokens.** If you need a
  new colour, you need a design conversation first.
- The Approvals queue page (which the queue right-rail on `/entities`
  echoes).
