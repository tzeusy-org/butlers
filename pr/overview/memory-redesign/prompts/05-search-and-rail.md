# 05 · Search and the attention rail

> Phase F. End state: one search affordance scoped by kind pills, with
> results in register shapes; the right rail carries the attention list
> and recent activity — and all the page's state color.

## Part 1 — Unified search

A single search band above the register pills:

```
/ search                                                     ×
[All] [Facts] [Rules] [Episodes]
```

- Input: single line, no border, `border-bottom: 1px solid
  var(--border-soft)`, mono 11px, `/` placeholder prefix. Pressing `/`
  anywhere on the page focuses it; `Esc` clears and blurs.
- Text is local state; **Enter** submits → writes `q` (and `kind`) URL
  params → `GET /api/memory/inspect?q&kind`.
- While `q` is set, the register area renders **results mode**: rows
  grouped under mono kind-headers (`FACTS · 12`, `RULES · 2`,
  `EPISODES · 7`), each group's rows in that kind's register shape
  (ledger / standing-orders / daybook row templates — reuse the row
  components, no fourth shape).
- `×` (and `Esc`) clears `q`, restoring browse mode with prior register
  and filters intact.
- Empty result: one serif-italic line — *"Nothing in the books."*
- The old per-tab search boxes and the standalone Inspect section are
  **deleted**. One affordance.

## Part 2 — The attention rail

Right column of Band 3 (`1fr` of the `1.4fr 1fr` grid). Two stacked
sections under mono eyebrows.

### NEEDS ATTENTION

Attention-list rows (`24px glyph · 1fr title+detail · auto action`),
18px vertical padding:

| Condition | Source | Severity | Action |
|---|---|---|---|
| `dead_letter_episodes > 0` | stats delta | red | → `/memory?register=episodes&status=dead letter` |
| write-up overdue (now − `last_consolidation_at` > 2× cadence) | stats delta | amber | none (informational) |
| anti-pattern rules > 0 | stats `anti_pattern_rules` | red | → `/memory?register=rules&maturity=anti_pattern` |
| important facts fading (`validity=fading` ∧ `importance >= 8`) | facts query, count only | amber | → ledger filtered to fading |
| stale embeddings > 0 | `GET /reembed/pending` | amber | → housekeeping anchor |

- Glyph: 6px square in severity color. Title sans 13px; detail line
  serif 13px (`Voice` small variant). Action is an underlined word +
  `→`, never a button.
- Rows render only when their condition holds. When none hold, the
  eyebrow stays and the body is one serif-italic line — *"Nothing
  waiting."*
- This rail and the pipeline band's dead-letter numeral are the only
  places red/amber may appear on the page.

### RECENT ACTIVITY

`GET /activity?limit=20`. Quiet list rows, 10px vertical padding:

```
14:21  [m]  fact stored — Owner · preferred_pain_relief
```

Mono time · `ButlerMark` (neutral) · sans summary, truncated. No color,
no severity, no links in v1. Auto-refresh interval 15s (keep existing
hook behavior).

## Acceptance for this phase

- [ ] Exactly one search input exists on `/memory`; `/` focuses it from
      anywhere on the page.
- [ ] Submitting a query deep-links (`/memory?q=fatigue&kind=all`) and
      reload reproduces the results view.
- [ ] Results reuse the register row components — visual diff between a
      fact row in browse mode and in results mode is zero.
- [ ] With a fully healthy dataset the rail shows the eyebrow +
      *"Nothing waiting."* and the page has zero red/amber pixels.
- [ ] Each attention row's action lands on the pre-filtered register it
      names.
