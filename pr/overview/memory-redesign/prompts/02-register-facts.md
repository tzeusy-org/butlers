# 02 · The ledger (Facts register)

> Phase C. End state: the default register renders facts as ledger rows
> — subject · predicate grid, belief column, ink-mapped decay, validity
> filter, provenance glyph.

## What you're building

The left column of Band 3, `register=facts` (the default). Grammar in
`MEMORY_LANGUAGE.md` §3a and §4.

## Row template

CSS grid, hairline `--border-soft` between rows, the whole row a link to
`/memory/facts/:id`:

```
grid-template-columns: minmax(180px, 0.8fr) 1fr auto;
```

| Cell | Content | Type |
|---|---|---|
| subject · predicate | subject (entity link when `entity_id` set) `·` predicate | sans 13px / mono 11px muted |
| content | single line, truncated with ellipsis | sans 13px |
| belief | `0.94 st ↳` — effective confidence, permanence tag, provenance glyph | mono 11px tabular, right-aligned |

- Entity-anchored subjects: underline, `text-underline-offset: 4px`,
  href `/entities/:entity_id`; click does not open the fact (stop
  propagation).
- Effective confidence from `effectiveConfidence()` (00), two decimal
  places, no `%`.
- Permanence tag muted: `pm st sd vo ep`.
- `↳` renders only when `source_episode_id` is set; muted; `title`
  attribute `from episode <first-8-chars>`. It is not a separate link in
  the register (provenance navigation lives on the detail page).

## Confidence is ink

- `validity === 'active'` → row foreground `--fg` (content), subject
  cell as specced.
- `validity === 'fading'` → entire row (all three cells, including the
  entity link) renders `--dim`. No italic, no opacity transition, no
  color.
- Other validities never appear unless their filter pill is selected.

## Filter pills

A `Pill` row above the register: `active` (default) · `fading` ·
`superseded` · `expired` · `retracted`. Single-select; writes the
`validity` URL param; resets `offset`. Mono, no color — selected state
is the inverted pill per Dispatch.

## Pagination

Offset-based, page size 50. Footer line, mono dim:
`1–50 of 3,182` left; `prev` / `next` pills right. No numbered pages.

## Empty states

- Filter yields nothing: serif italic — *"No facts answer this."*
- Tier empty entirely: *"The ledger is empty."*

## Acceptance for this phase

- [ ] Fading rows are visually dimmer than active rows with no other
      difference; a screenshot at 100% zoom makes the distinction
      obvious in grayscale.
- [ ] `validity` pill writes the URL param; back button restores the
      previous filter.
- [ ] Entity-anchored subject navigates to `/entities/:id`; clicking
      anywhere else on the row opens the fact detail page.
- [ ] `↳` appears only on rows with `source_episode_id`.
- [ ] No horizontal scroll at 1280px; content truncates instead.
- [ ] Zero red/amber/green pixels in this register under any data.
