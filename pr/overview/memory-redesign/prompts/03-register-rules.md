# 03 · Standing orders (Rules register)

> Phase D. End state: `register=rules` renders rules as numbered
> directives with outcome tallies and the maturity word; anti-pattern
> rules carry the only in-register state color.

## What you're building

The rules register, grammar in `MEMORY_LANGUAGE.md` §3b. Rules are read,
not scanned — row padding 18px vertical, the most generous on the page.

## Row template

```
grid-template-columns: 44px 1fr auto;
```

```
§01  Suggest a sleep study when fatigue is reported          proven
     three days running.
     applied 41 · helpful 38 · harmful 1                     0.86
```

- **Gutter**: `§NN` mono 11px muted, zero-padded, numbering global in
  render order across the filtered list.
- **Directive**: sans 14px, wraps, clamp to 2 lines in the register
  (full text on the detail page).
- **Tally line**: mono 11px muted: `applied N · helpful N · harmful N`.
  When `harmful > 0`, the word `harmful` and its numeral — only that
  fragment — take `--red`.
- **Right column**: maturity word top (mono 11px, lowercase, exactly the
  API value: `candidate` / `established` / `proven` / `anti_pattern`),
  confidence numeral below (mono, 2 places, tabular).
- **Anti-pattern rows**: 2px left sliver in `--red` spanning the row.
  No background tint, no icon.

## Ordering

`maturity` rank (`proven` → `established` → `candidate`) then
confidence descending. `anti_pattern` rules pin to the **top** — they
are the rows demanding attention, and the rail links here.

## Filters

`Pill` row: `all` (default) · `candidate` · `established` · `proven` ·
`anti_pattern`, single-select, writes a `maturity` URL param.

## Empty states

- No rules at all: serif italic — *"No standing orders yet."*
- Filter empty: *"Nothing of this maturity."*

## Pagination

Same footer as the ledger (offset, page size 50, `prev`/`next` pills).

## Acceptance for this phase

- [ ] A dataset with zero anti-pattern, zero-harmful rules renders this
      register with zero red pixels.
- [ ] One rule with `harmful: 4` reddens exactly the `harmful 4`
      fragment; an `anti_pattern` rule additionally carries the left
      sliver.
- [ ] `§NN` numbering recomputes on filter change with no layout shift.
- [ ] Maturity renders the raw API word — no title-casing, no chip.
- [ ] Row click opens `/memory/rules/:id`.
