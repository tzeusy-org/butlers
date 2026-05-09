# Dispatch — Review Checklist

Run this against any page before merging. If any item fails, the page
is not in the language yet. Twelve items, no skipping.

---

### Type

- [ ] **1. Display weight is 500, not 700.** Search the diff for
      `font-bold` and `font-weight: 700` — there should be zero on
      display headlines.
- [ ] **2. Numbers are tabular and mono.** Every numeric value
      (counts, costs, deltas, timestamps, IDs) has
      `font-variant-numeric: tabular-nums` AND uses `var(--font-mono)`
      where it's a fact, not a measure.
- [ ] **3. Eyebrows are 10px mono uppercase 0.14em.** No exceptions.
      Section titles use eyebrows; they do not use `<h2>` with a
      sans-large size.

### Color

- [ ] **4. No invented colors.** Every color reference resolves to a
      token in `tokens.css`. Search the diff for `oklch(`, `#`, `rgb(`,
      `hsl(` — should only appear in `tokens.css` itself.
- [ ] **5. State color (red/amber/green) is foreground/border only.**
      No background fills tinted with state color. No `bg-red-500/10`,
      `bg-amber-500/20`, etc.
- [ ] **6. Butler hues appear only on letter-marks.** Grep the diff
      for `var(--category-` — every match is inside a `ButlerMark`
      component or its style block. No category color on borders,
      backgrounds, accents.

### Surface

- [ ] **7. No cards.** Search the diff for `<Card`, `shadow-`,
      `rounded-xl border bg-card`. If a card appears, it must replace
      a worse pattern, never add chrome.
- [ ] **8. Lists are rule-separated grids.** Every list of more than
      one item uses `divide-y divide-border/60` or `border-b
      border-border` between rows. No padded card-rows.

### Voice

- [ ] **9. Empty states are one serif-italic sentence.** No
      illustration, no helpful explanation, no "items will appear here
      when…". Just *"Nothing waiting."* or equivalent.
- [ ] **10. Copy passes the voice rules.** No exclamation marks. No
      first person. No "Welcome back". No future tense. No "currently",
      "presently", "just", "simply". Past for events, present for
      state.

### State honesty

- [ ] **11. If the system reports its own process, it uses a status
      pill.** Cache age, sync status, model version, last patrol —
      visible, not hidden. The user knows what they're looking at.
- [ ] **12. The page reads calm at 3am during an outage.** Run the
      page mentally with: butler down, API timing out, attention list
      empty. Does anything panic visually? (Red banner? Pulsing dot?
      Modal?) If yes, fix.

---

## Bonus checks (not blocking, but signal)

- Does the dark→light flip happen with no flashes? (Test it.)
- Is there exactly **one** commit button on the surface? (Approve,
  Send, Save — never two.)
- Does keyboard focus order match reading order?
- Does `axe-core` report zero violations on the rendered page?
- Does the print stylesheet (`@media print`) collapse the sidebar
  and produce a clean A4 dispatch?

---

## Reviewer note

This checklist exists because the language is precise but the failure
modes are subtle — a single `bg-amber-500/10` doesn't crash the page,
it just slowly drifts the brand toward a generic SaaS dashboard. Catch
it on review or it ships.
