# 01 · Overture

> Phase B. End state: the top of `/memory` answers "is remembering
> working" before any scrolling — eyebrow, display headline, Voice
> sentence, KPI strip, pipeline band.

## What you're building

Band 1 and Band 2 of the page grammar (`MEMORY_LANGUAGE.md` §2). Use the
existing primitives: `Eyebrow`, `Voice`, `Mono` from
`frontend/src/components/ui/`, and the editorial page shell from
`page.tsx`.

## Structure

```
MEMORY                                          ← Eyebrow
What the house believes.                        ← display: sans 44px/500, tracking -0.025em, max-width 14ch
<voice sentence>                                ← Voice (serif 16px), max-width 50ch

PENDING        ACTIVE FACTS    PROVEN RULES    LAST WRITE-UP
41             3,182           9               06:00 · 12 facts
────────────────────────────────────────────────────────────
episodes 1,204 ─→ pending 41 ─→ facts 3,182 · fading 207 ─→ rules 58 · proven 9        dead letters 0
────────────────────────────────────────────────────────────
```

### Headline

Static: `What the house believes.` Sans 500, never bold, never serif.

### Voice sentence

Templated from `GET /stats` — **not** LLM-generated. Compose:

- pending > 0: `Forty-one observations await the evening write-up; the
  last ran at 06:00 and produced twelve facts.` (Numbers under 100 as
  words in the Voice line only; numerals everywhere else.)
- pending == 0: `The pipeline is idle. Nothing pending since 06:00.`
- `last_consolidation_at` null (never run): `The first write-up has not
  run yet.`

Past tense for events, present for state. No first person, no
exclamation marks.

### KPI strip

Four hairline-divided cells (no card chrome, no background fills):

| Eyebrow (mono 10px) | Mega-number (32px sans 500, tnum) | Source |
|---|---|---|
| `PENDING` | `unconsolidated_episodes` | stats |
| `ACTIVE FACTS` | `active_facts` | stats |
| `PROVEN RULES` | `proven_rules` | stats |
| `LAST WRITE-UP` | `HH:MM` + mono sub `· N facts` | stats delta |

No deltas/sparklines in v1. `LAST WRITE-UP` renders `—` when null.

### Pipeline band

A single line, hairline rules above and below, mono 11px throughout:

```
episodes 1,204 ─→ pending 41 ─→ facts 3,182 · fading 207 ─→ rules 58 · proven 9
                                                  dead letters 3   ← right-aligned
```

- Labels muted (`--mfg`), numerals `--fg`, tabular.
- Connector is the character sequence `─→`, muted.
- `dead letters N` right-aligned in the band: muted when 0; numeral and
  label take `--red` when > 0. This is the only state color outside the
  rail (Memory Language §6).
- The band is read-only — no hover states, no links in v1.

## Acceptance for this phase

- [ ] With stats `{pending: 41, dead_letter: 0}` the page renders zero
      red pixels above the fold.
- [ ] `dead_letter: 3` turns exactly the `dead letters 3` fragment red;
      nothing else changes.
- [ ] Voice sentence matches the three templates exactly (pending > 0,
      pending == 0, never-ran).
- [ ] All numerals tabular mono; KPI mega-numbers do not animate on
      load.
- [ ] Headline wraps at 14ch max-width with no layout shift while stats
      load (reserve the band's height).
