# 02 · The User page (integration)

> Phase C. End state: clicking any user-secret row in the spine
> renders a fully-functional integration page. All credential states
> render correctly. Every commit (re-authorize, rotate, test,
> disconnect) hits the API and revalidates.

## Source of truth

`secrets-pages.jsx#PageUser` in the prototype. Port the layout
verbatim. The evidence atoms (`Fingerprint`, `WhatBreaks`,
`ProbeResult`, `ScopeBalance`, `VisaRow`, `StampGlyph`, `StampRow`,
`HeadingBand`, `CrossRefFooter`, `CommitFooter`) are reusable across
the three page kinds — extract them as their own components in
`frontend/src/components/secrets/evidence/`.

## Page anatomy

```
┌────────────────────────────────────────────────────────────────┐
│ ISSUING AUTHORITY · kind · oauth                              │
│                                                                │
│ [S]  Spotify                          [   EXPIRED    ]         │
│      accounts.spotify.com · oauth     [ 401 invalid_… ]        │
│                                                                │
│  Recent listens. Feeds the chronicler butler.                  │
│                                                                │
│ ──────────────────────────────────────────────────────────────│
│ PASSPORT NO.  │ ISSUED   │ EXPIRES │ LAST VERIFIED │ … SCOPES  │
│ sha256:d4e1…  │ 2025-11  │ 2026-05 │ 2 days ago    │   1/1     │
│ + verify cmd  │          │         │               │           │
│ ──────────────────────────────────────────────────────────────│
│                                                                │
│ VISA · scopes                  PROBE · last test               │
│ ┌──────────┐                   ● 401  134ms  at 2 days ago     │
│ │ ✓ user-…│                   refresh-token expired           │
│ └──────────┘                                                   │
│                                                                │
│ WHAT BREAKS · 1 feature        STAMPS · audit                  │
│ ▰ spotify · daily listens     [✕] 2026-05-21 06:08 · failed   │
│   breaks · chronicler         [▷] 2026-05-21 06:02 · attempt …│
│                                                                │
│ FEEDS · chronicler                                             │
│ ──────────────────────────────────────────────────────────────│
│ ELSEWHERE         │ CONFIG         │ IDENTITY                  │
│ /ingestion/…      │ kind · oauth   │ [Tze] entity_info ·…      │
│ /entities/owner   │ endpoint · …   │                           │
│ ──────────────────────────────────────────────────────────────│
│ [re-authorize] [test]                          [reveal] [disc] │
└────────────────────────────────────────────────────────────────┘
```

## Key components

### `<HeadingBand>` — the most opinionated visual move

- Left: `ISSUING AUTHORITY · kind · <kind>` eyebrow, then a 36px
  letter-mark + 30px display title + mono `<authority> · <kind>`.
- Right: the **state plaque** — a 1.5px-bordered block tilted
  exactly `1.5deg`. Border colour comes from `STATE_CATALOG[state]`:
  - `expired / revoked` → red
  - `expiring / scope_mismatch / rotating` → amber
  - `ok` → green
  - `never_set` → dim
- The plaque carries the state label (mono uppercase 12px, tracked
  0.18em) plus 0–2 short mono lines underneath. State-line content:
  - `expired` → `<failureTail>` (mono 9px)
  - `expiring` → `expires <date>`
  - `scope_mismatch` → `N scope missing`
  - `ok` → `verified <time>`
  - `never_set` → `never connected`

**Do not** add a hover state to the plaque. It is information, not an
affordance.

### `<KvBand>` — kind-aware

Below the voice paragraph, a `padding: 14px 0` band bordered on top
and bottom with hairlines. Column layout varies by `kind`:

- **oauth / token / apikey**:
  `180px 110px 110px 130px 130px 1fr`
  → `passport no.` · `issued` · `expires` · `last verified` ·
    `last used` · `scopes` (with `<ScopeBalance>`)

- **webhook**:
  `180px 1fr 130px 130px`
  → `passport no.` · `incoming url` (mono 12, wraps if necessary) ·
    `issued` · `last seen`

The `passport no.` cell carries a `<Fingerprint>` plus a one-line
"+ verify cmd" expander that reveals the SHA verification command in
mono dim. Hide the expander entirely when the Tweaks toggle
`showVerifyCmd` is off.

### `<WhatBreaks>` — the dramatic anchor

```
WHAT BREAKS · 3 features
────────────────────────
▰  [B] calendar events read          breaks      calendar
▰  [B] gmail thread scan             breaks      relationship
▱  [B] drive recent index            degrades    chronicler
```

- Heading text shifts by state: ok → `WHAT WOULD BREAK`; expiring →
  `WHAT WILL BREAK`; everything else → `WHAT BREAKS`.
- Severity pip on the left: filled square `▰` for high/medium,
  outline `▱` for low.
- Feature name carries the butler `<ButlerMark>` (the only place a
  butler hue ever appears outside the sidebar).
- Right column states the verb ("breaks" / "degrades" / "ok") and
  the butler name in mono.
- When `breaks` is empty AND the credential is sick, show a single
  serif italic line: `*Nothing depends on this credential yet.*`

### `<ProbeResult>`

```
●  200   42ms   at 14:21 today                              [probe]
```

- Dot at left in the test outcome colour (green ok, red fail).
- Code in same colour, then a `·` separator, then latency in fg.
- The serif-italic `<test.message>` (if any) hangs below at red on
  failure or mfg on warn.
- `[probe]` button kicks
  `POST /api/secrets/user/<provider>/probe?identity=…`, invalidates
  the user-secret query, and re-renders with the new test.

### `<ScopeBalance>` and `<VisaRow>`

- `<ScopeBalance>` inline numeric ratio `3/3` with a tiny segmented
  bar; amber when any required scope is missing. Used in the KV
  band's `scopes` column.
- `<VisaRow>` lives in the full visa-permissions block below; one
  scope per row with `✓ granted` / `∅ not granted` / `✓ extra`.

### `<StampRow>` + `<StampGlyph>`

The audit stamp glyphs:

```
verified    ✓  green
rotated     ↻  fg
failed      ✕  red
revoked     ⊘  red
connected   ⊕  fg
disconnected⊖  dim
warned      !  amber
overrode    ⤳  fg
attempted   ▷  dim
set         ⊙  fg
```

Each glyph is a 14px square 1px-bordered box. Stamps differentiate by
shape *and* colour so a column of stamps reads as a narrative before
the captions do.

### `<CommitFooter>`

State-aware. Two groups:

- **Left (primary commits):**
  - `expired | revoked | scope_mismatch` → `[re-authorize]` (commit
    pill — fg-on-bg)
  - `expiring` → `[rotate]` (commit)
  - `never_set` → `[connect]` (commit)
  - `ok` → `[test]` `[rotate]` (both regular pills)

- **Right (sensitive):**
  - `[reveal value]` — when a fingerprint exists. Reveal behaviour
    governed by the Tweaks `revealMode` setting.
  - `[disconnect]` — danger pill. Confirmation modal before firing
    `POST .../disconnect`.

## Per-kind branches

- **`oauth`** — full layout as drawn. Connect/reauthorize opens the
  OAuth dance in a popup; callback returns to
  `/secrets?focus=…&toast=connected`.
- **`token`** — same as oauth but the connect/rotate UI is a
  paste-token modal, not a redirect dance.
- **`apikey`** — same as token; the *what breaks* block emphasises
  model calls; the probe is provider-specific.
- **`webhook`** — KV band shows `incoming url` instead of
  issued/expires columns. The cross-references footer adds
  *"webhook secret rotates with token"* mono in the `config` column.

## Mutations

| Action | Endpoint | Optimistic? |
|---|---|---|
| Probe | `POST /api/secrets/user/<provider>/probe` | No — show a small spinner; replace `test` on response. |
| Re-authorize | `POST .../reauthorize` → redirect | N/A (redirect) |
| Rotate (paste-token kinds) | `POST .../rotate` body `{ value }` | Yes — invalidate inventory + this credential. |
| Disconnect | `POST .../disconnect` | Yes. |
| Reveal | `POST .../reveal` | No — value shown in a transient modal with copy-to-clipboard. |

All mutations write audit server-side; refetch the credential after
to pick up the new stamps.

## Acceptance for this phase

- [ ] All seven states render correctly with sample data.
- [ ] Webhook kind shows the incoming URL.
- [ ] What-breaks block dims on healthy pages and colours on sick.
- [ ] Probe button calls the endpoint and replaces the result.
- [ ] OAuth re-authorize round-trip succeeds and the page flips.
- [ ] Disconnect requires confirmation.
- [ ] Reveal honours the Tweaks `revealMode` (see prompt 05).
- [ ] All stamp glyphs render at the correct colour.
- [ ] Print-preview of the Spotify-expired page reads as a passport
      page (issuing authority, passport number, visa entries, audit
      stamps).
