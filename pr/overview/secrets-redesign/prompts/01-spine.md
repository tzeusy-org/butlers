# 01 · Spine

> Phase B. End state: the left index is fully functional. Search,
> sort, identity-switching, and the pinned `needs hand` group all
> work. Clicking a row updates the URL and the page slot can react.

## What you're building

The left index of the passport book. Source: `secrets-spine.jsx` in
the prototype. Port verbatim where structure is concerned; trade
inline styles for your project's styling solution.

## Inputs

```tsx
interface SpineProps {
  inventory: Inventory;            // from fetchInventory
  identityId: string;
  focus: string | null;            // the URL `focus` param
  sort: 'severity' | 'recency' | 'alpha';
  search?: string;                 // local state, not URL — searches don't deep-link
  identities: Identity[];          // for the switcher; empty for non-owner
  onChange: (params: URLSearchParams) => void;
}
```

`search` stays local — it's a finder affordance, not a navigation
state. `focus`, `identity`, and `sort` are URL params (back button
works).

## Build it

### 1. Project the inventory into spine entries

Implement `buildSpineEntries(inventory, identityId): SpineEntry[]`.
Mirror the prototype exactly:

```ts
interface SpineEntry {
  key: string;          // "u:spotify" | "s:GMAIL_SENDER_ADDRESS" | "c:claude-cli"
  family: 'cli' | 'system' | 'user';
  label: string;
  provider?: string;    // for user-secret rows; drives the letter-mark
  state: State;
  mono?: boolean;       // true for system-key labels
  subline: string;      // small mono caption under the label
  lastTouchOrder: number; // for recency sort
}
```

Spine sublines are short, mono, and state-coloured:

- ok → `verified 14:21 today`
- expired → `refresh failed · 2d`
- expiring → `expires 2026-05-27`
- scope_mismatch → `1 scope missing`
- rotating → `rotating in 14s`
- never_set → `not set` / `not connected`
- system shared → `shared default`
- system local → `local · <butler>`
- cli ok → `used 14:15 today`

### 2. Partition into `needs hand` vs by-family

`needsHand(state)` is true for any of `expired | revoked |
scope_mismatch | expiring | rotating`. Everything matching is pulled
out and pinned at the top of the spine as a single group, regardless
of family. The remaining items render in their original family
sections.

When `needs hand` is empty, show the header anyway with a
serif-italic gloss:

```tsx
<NeedsHandHeader count={0} />
<EmptyVoice>Nothing waiting.</EmptyVoice>
```

### 3. Sort within each group

Three modes:

- `severity` (default): use `STATE_CATALOG[state].rank`. Lower rank
  first.
- `recency`: most-recently-touched first. `lastTouchOrder` from the
  projection.
- `alpha`: label.toLowerCase() locale compare.

The sort radios are the segmented `mono` row at the top of the
spine (see `SortPicker` in prototype).

### 4. Render rows

`<SpineRow>` is a `<button>` (the whole row is the hit target):

```
[§NN] [letter-mark] [label]                            [• state-dot]
              [subline · state-coloured]
```

- The 2px left attention sliver appears only on sick rows that are
  **not** the active row. On the active row, the 2px left border is
  full-foreground colour (`--fg`).
- Active background is `--bg-elev`.
- Numbering `§NN` is global across the entire visible (post-filter)
  list, in render order — not array-position. Always 2-digit
  zero-pad.

### 5. Search input

```
/ search                                 ×
```

- Single-line, no border, `border-bottom: 1px solid var(--border-soft)`.
- Mono 11px.
- Local state; filters by `label.toLowerCase().includes(search)`.
- The `×` clears the input.
- Pressing `Esc` while focused also clears.

### 6. Identity switcher

Owner sees the full list as little chip-buttons (`Tze owner`, `Wei
member`). One is active (`--bg-elev` + `--border-strong`); clicking a
non-active flips `identityId` (drives a URL change, which drives a
re-fetch of the inventory).

Non-owner identities render a static chip — no switcher.

### 7. Footer

- `+ add page` button (pill, secondary). Opens the connect-new modal
  (Phase C — for now, no-op or a `TODO`).
- Right side: `<count of filtered> of <total>` mono dim.

## State

- `focus`: URL param.
- `identity`: URL param.
- `sort`: URL param.
- `search`: local React state (resets on identity change).

On row click, push the new `focus` to URL via `onChange`. **Use
`replace: false`** so back-button navigation between credentials
works.

## Acceptance for this phase

- [ ] All sample data states from `secrets-data.jsx` render correctly.
- [ ] Severity sort puts the expired Spotify row at §01.
- [ ] Recency sort puts the most-recently-verified ok credential
      first.
- [ ] Alpha sort works.
- [ ] Searching `spot` filters to only Spotify.
- [ ] Clicking a row changes the URL `focus` param.
- [ ] Browser back/forward moves between credentials.
- [ ] Switching identity from Tze to Wei re-fetches inventory and
      repopulates the spine.
- [ ] Empty `needs hand` shows the serif gloss, not a phantom group.
- [ ] No layout shift when sort changes (numbering recomputes
      gracefully).
