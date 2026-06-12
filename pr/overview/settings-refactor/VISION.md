# Vision — settings redesign

> Distilled from `PLAN.md` (§1 orientation, §2 decisions, §3 visual language,
> §8 out-of-scope, §10 north star) and `README.md` on 2026-06-07 by the
> `butlers-redesign-prompt` skill, then confirmed by Tze. This is the binding
> Section 0 of the redesign brief. Scope locked to the **full bundle**.

## Problem being solved

Today's `/settings` is still in the pre-Dispatch language while `/`, `/butlers`,
and `/qa` have already moved over. Concretely, `SettingsPage.tsx` is a vertical
stack of shadcn `<Card>`s with a `text-3xl font-bold` header, word-badges
(`Connected` / `Not configured` / `Not set`), and no severity treatment — a
silently-expired Google OAuth renders at the same visual weight as a healthy
toggle. It reads like a default Bootstrap dashboard, not a sheet of paper you
would trust. The information architecture is also muddled: per-user OAuth,
system knobs, and (conceptually) per-butler config all blur together.

## Primary audience

The **owner / operator** — someone who already knows what the system does.
No onboarding tooltips, no "comfortable" density mode. Dense by default, dark-
canonical with a paper-warm light variant. External users are not in scope.

## Deliberate design moves

1. **Direction B · The Console.** The `/settings` shell is a panel grid with a
   top **AttentionStrip** that surfaces items demanding human attention
   (open approval, auth-renewal needed, model in error). Each panel summarizes
   one sub-route and fetches independently so a slow panel never blocks the page.
2. **Dispatch typography, no exceptions.** Inter Tight (UI) · Source Serif 4
   (Voice) · JetBrains Mono (numerals, eyebrows, IDs). Display weight is 500,
   never 700 — tight tracking does the work. Numerals are always
   `tabular-nums`. No system fonts, no Inter regular.
3. **Severity earns color only when state demands it.** A quiet settings tree
   has zero red/amber pixels. State color appears only on foreground or border,
   with one new exception this refactor establishes: a 4–7% alpha background
   tint paired with a 2px left rail in the same color, on rows/panels that
   demand attention. One affordance per signal — never two of {dot, sliver,
   numeral, color} for the same thing.
4. **IA cleanup.** `/settings` is **system-side only**. Three sub-routes:
   `/settings/models` (catalog grouped by complexity tier — reasoning →
   workhorse → cheap → specialty → local → legacy, sorted server-side by
   tier, priority desc, enabled desc), `/settings/spend`, `/settings/permissions`
   (matrix + audit reel + data ops + webhooks; every mutation requires a reason
   and writes to an append-only audit log).
5. **Adjacent surfaces, properly placed.** `/approvals` becomes its own
   top-level route and the new `ApprovalsPage` *replaces* the old one (old file
   deleted). Per-butler config (prompt, tools, fallback chain, activity) folds
   into the existing `/butlers/{name}`. Memory config (tier flow, retention,
   compaction log, inspect search) folds into `/memory`. Butler hues stay
   exclusive to the letter-mark.

## What we are deliberately NOT doing

- **No merge of per-user OAuth into settings.** Google / Spotify / Telegram /
  Steam credentials stay on `/secrets`. Strictly disjoint surfaces.
- **No per-butler config under `/settings/`.** It lives on `/butlers/{name}`.
- **No new design system.** Reuse what's in `frontend/src/`.
- **No theme knobs** beyond dark-canonical + paper-warm light.
- **No density / "comfortable" toggle.** Dense by default.
- **No onboarding tooltips** on settings.
- **No graphical wiring diagram** of butlers ↔ models ↔ permissions — it would
  be SaaS-coded; the matrix is enough.
- **No emoji, no drop shadows, no gradient hero, no "delight" motion.**

## Success criteria

- Every route in the bundle renders in the Dispatch language and passes the
  north-star test: *"If the system handed me this as a sheet of paper, would I
  trust the typography?"*
- State color appears only when state demands it; the 4–7% alpha + 2px left-rail
  attention pattern is implemented consistently.
- Numerals are tabular everywhere; no emoji anywhere in the new pages.
- The model catalog sorts `(tier, priority desc, enabled desc)` server-side; the
  frontend never sorts, only filters. Priority stepper round-trips < 200ms in dev.
- The spend chart is hand-rolled SVG (no chart library); forecast line dashed
  from "today" forward.
- Permissions mutations refuse without a `reason`; the audit log records every
  config change and reads as prose at `/audit`.
- `/approvals` replaces — not duplicates — the old route; old component deleted
  in the same PR.
- Per-butler config does not exist under `/settings/`; anyone looking for it
  lands on `/butlers/{name}`.
