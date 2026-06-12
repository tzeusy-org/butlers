# 04 · The daybook (Episodes register)

> Phase E. End state: `register=episodes` reads as a journal — day
> groups, mono time gutter, butler letter-marks, consolidation glyphs,
> in-place expansion.

## What you're building

The episodes register, grammar in `MEMORY_LANGUAGE.md` §3c. This is the
only register where butler category hues appear (on `ButlerMark`).

## Day grouping

Group client-side on `created_at` (local timezone). Day header is a
hairline-bound rule, mono 10px uppercase, muted:

```
─ THU 12 JUN ────────────────────────────────────────────────
```

Today's header reads `─ TODAY ─…`; yesterday `─ YESTERDAY ─…`; older
days `─ THU 12 JUN ─…`. Groups render in reverse chronological order;
pagination follows the API's order (offset, page size 50) and a group
may split across pages — acceptable; repeat the day header at the top of
the next page.

## Row template

```
grid-template-columns: 50px 24px 1fr 16px;
```

```
14:21  [c]  Owner mentioned fatigue again during the          ◦
            afternoon check-in; took ibuprofen.
```

- **Time gutter**: `HH:MM` mono 11px. Muted by default; renders `--fg`
  when `importance >= 8` (ink weight, Memory Language §4).
- **Mark**: `ButlerMark` (16px, neutral tone) for `episode.butler`.
- **Content**: sans 13px, clamp 2 lines. Click toggles in-place
  expansion to full content (120ms height, linear); a second click on
  the expanded row navigates to `/memory/episodes/:id`. Render an
  explicit `open ↗` mono link at the end of expanded content as the
  unambiguous navigation affordance.
- **Glyph** (right, mono): `◦` pending · `•` consolidated · `✕`
  dead-letter/failed (`--red`). Never a word, never a chip, no tooltip
  beyond `title`.

## Filters

`Pill` row: consolidation state — `all` (default) · `pending` ·
`consolidated` · `dead letter` (maps to API `consolidated` /
status params). A butler scope pill-select may follow in v2; not in
this pass.

## Empty states

- No episodes today, none ever: serif italic — *"Nothing observed yet."*
- Filter empty: *"Nothing in the daybook for this."*

## Acceptance for this phase

- [ ] Day headers render TODAY / YESTERDAY / dated correctly across a
      multi-day dataset.
- [ ] `✕` rows are the only red in the register; pending/consolidated
      rows are colorless.
- [ ] Expansion animates height only (no fade, no scale) and never
      shifts neighboring days' headers out from under the cursor.
- [ ] Importance ≥ 8 brightens only the time gutter.
- [ ] ButlerMark is the sole carrier of butler hue on the page outside
      the activity rail.
