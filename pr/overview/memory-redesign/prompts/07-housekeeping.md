# 07 · Housekeeping

> Phase H. End state: retention policies, compaction log, and re-embed
> controls live in one quiet band at the foot of `/memory` — reachable,
> functional, visually subordinate.

## What you're building

Band 4. One mono eyebrow `HOUSEKEEPING`, then three sub-surfaces
separated by hairlines, all in small type (13px and below). No cards,
no panel borders, no section backgrounds. An anchor (`#housekeeping`)
so the rail's stale-embeddings row can land here.

## 1 · Retention policies

The existing editable table, restyled to a rule-separated grid:

```
kind         ttl days     max rows     updated
event        30           50,000       2026-05-02 · api
fact         —            —            2026-04-18 · api
```

- Kind mono 11px; TTL and max-rows are inline editable mono inputs
  (borderless, `border-bottom` on focus); `—` for null.
- **Fix the known gap**: kind is constrained to the backend's valid set
  {event, fact, preference, summary, transcript, embedding} — existing
  rows only, no free-text kind creation.
- Edits track locally; a single `Save` commit pill appears (the band's
  one commit button) only when a row is dirty; PUT
  `/retention-policies` on click, then the pill disappears. No toast
  celebration; the `updated` column refreshing is the confirmation.

## 2 · Compaction log

Read-only quiet list (`GET /compaction-log?limit=50`):

```
06:02  fact        1,204 rows · 3.1 MB
06:02  embedding   89 rows
```

Mono time · mono kind · sans counts. Bytes omitted when null (no
em-dash filler). No pagination; 50 rows, scroll within the page flow.

## 3 · Embeddings

The re-embed surface, compressed from today's panel:

```
EMBEDDINGS
412 rows on an older embedding model.        dry run · re-embed
```

- One sans sentence stating drift (`GET /reembed/pending`, summed
  across tiers); serif-italic *"All embeddings current."* when zero.
- `dry run` — secondary pill; renders its result as one mono line below
  (`would re-embed 412 rows across 2 tiers`), no modal.
- `re-embed` — this is a long synchronous run; keep the existing
  confirm step, but as the inline pill-morph pattern
  (`re-embed — confirm?` for 5s), not a dialog. While running:
  `composing…`-style mono status line. On completion, one mono line:
  `re-embedded 412 rows · 38s`. No progress bar.
- Note: `Save` (retention) and `re-embed` are on separate sub-surfaces
  divided by hairlines; each sub-surface has at most one commit-class
  action, satisfying Dispatch's one-commit-per-surface rule. If a
  reviewer reads the whole band as one surface, demote `re-embed` to
  secondary styling — the rule outranks the convenience.

## Acceptance for this phase

- [ ] The band renders below the registers and is visibly quieter:
      smaller type, muted eyebrows, no chrome.
- [ ] Retention edit → Save → table reflects the new `updated` stamp;
      no toasts.
- [ ] Kind column cannot be free-typed into an invalid kind.
- [ ] Dry-run result renders inline as one mono line.
- [ ] `#housekeeping` anchor lands with the band's eyebrow at the top
      of the viewport (rail deep-link works).
- [ ] Empty compaction log: serif italic — *"No sweeps recorded."*
