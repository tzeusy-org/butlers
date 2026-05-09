# Butlers — Overview Page Implementation Reference

> Hand this to an Opus session. Goal: implement the Overview page (the
> "Editorial / Dispatch" direction we landed on) inside the existing
> `frontend/` codebase, replacing `frontend/src/pages/DashboardPage.tsx`.

---

## 1. What we're building

A two-column editorial overview, dark-by-default with light mode parity.

- **Left column (~1.4fr):** a date/time eyebrow + a briefing status pill, a
  large headline ("Good afternoon. Things are quiet, with two exceptions."),
  a serif elaboration paragraph, an attention list (rule-separated rows),
  and a 4-cell KPI strip ("Today, in numbers").
- **Right column (~1fr):** a "Butlers" index (status dot, name, 24h sessions,
  spend) and a "Next" list (5 upcoming items).
- **Sidebar:** 56px icon rail (replaces the current `Sidebar.tsx`), with
  hover-out tooltips and indented sub-items under butler groups.

Visual spec lives in `DESIGN_LANGUAGE.md`. This document is about wiring.

## 2. Routes / files to change

```
frontend/src/pages/DashboardPage.tsx        REPLACE — new editorial layout
frontend/src/components/layout/Sidebar.tsx  REPLACE — icon rail
frontend/src/components/layout/Shell.tsx    EDIT     — narrower rail width
frontend/src/hooks/use-briefing.ts          NEW      — TanStack Query hook
frontend/src/api/dashboard.ts               EDIT     — add briefing() endpoint
frontend/src/components/overview/*          NEW      — all overview pieces
frontend/src/index.css                      EDIT     — add tokens (see §6)
```

Backend (separate repo / Python):

```
butlers/dashboard/api/briefing.py           NEW
butlers/dashboard/briefing/classify.py      NEW
butlers/dashboard/briefing/prompts.py       NEW
butlers/dashboard/briefing/fallback.py      NEW
```

## 3. The briefing — how it composes

The briefing has two parts; both are produced server-side and returned as
one `Briefing` object the frontend renders verbatim.

### 3a. Headline (deterministic, templated)

`classify(state)` buckets the world into a `state_class`:

| Class             | Trigger                                       |
|-------------------|-----------------------------------------------|
| `urgent`          | ≥1 attention item with `severity=high`        |
| `busy`            | ≥3 attention items, none high                 |
| `mild`            | 1–2 attention items, none high                |
| `degraded-quiet`  | 0 attention items but ≥1 butler degraded/error|
| `quiet`           | 0 attention items, all butlers healthy        |

`time_of_day` is computed from `state.now.hour`:
late-night (<5) / morning (<12) / afternoon (<17) / evening (<21) / night.

`headline_for(class)` returns `{greet, body}` from a fixed table:

```py
{
  "urgent":          f"{n} things need you now." if n>1 else "One thing needs you now.",
  "busy":            f"Things are busy — {total} items waiting.",
  "mild":            f"Things are quiet, with {total} exception{s}.",
  "degraded-quiet":  f"Quiet, but {n} butler{s} {is/are} degraded.",
  "quiet":           "Everything is in hand.",
}
```

Greet is `f"Good {time_of_day}."`. The frontend renders `greet` in the
muted color and `body` in the foreground color, on two lines.

### 3b. Elaboration (LLM, with deterministic fallback)

Server calls Claude Haiku with a structured state JSON and the prompt
below. On any failure (timeout, exception, empty response), fall back to
`elaborate_fallback(state, cls)` which returns a templated 1–3 sentence
paragraph.

**Prompt** (pin verbatim, version it):

```
You are writing a single short paragraph for a personal AI dashboard.

CONSTRAINTS:
- 1-3 sentences, max 50 words total.
- Past tense for events, present for state. No future tense.
- No exclamation marks. No emoji. No first person ("I"). Avoid "your".
- No hedging adverbs (currently, presently, just, simply).
- Mention the most important attention item by name and time, if any.
- If everything is quiet, write a single calm sentence noting that.
- Voice: a butler announcing, not a chatbot reporting.

STATE:
<JSON state>

Write only the paragraph. No quotes, no preamble.
```

**Model + params:** `claude-haiku-4-5`, `max_tokens=120`, `temperature=0.4`,
`timeout=4.0s`.

**Cache:** 5 minutes per user. `briefing` is not real-time; it sets a
mood, not a status. The status pill says `llm · cached 5m` so the user
knows.

## 4. API contract

```ts
// GET /api/dashboard/briefing
type Briefing = {
  greet: string;        // "Good afternoon."
  headline: string;     // "Things are quiet, with two exceptions."
  elaboration: string;  // 1-3 sentences
  source: 'llm' | 'fallback';
  state_class: 'quiet' | 'mild' | 'busy' | 'urgent' | 'degraded-quiet';
  generated_at: string; // ISO
};

// GET /api/dashboard/state  (already exists, extend if needed)
type DashboardState = {
  now: string;
  attention: AttentionItem[];
  butlers: ButlerSummary[];
  upcoming: UpcomingItem[];
  kpis: { sessionsToday: Kpi; momentsLogged: Kpi; costToday: Kpi; };
};
```

## 5. React layer

### `use-briefing.ts`

```ts
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/api";

export function useBriefing() {
  return useQuery({
    queryKey: ["dashboard", "briefing"],
    queryFn: () => apiClient.dashboard.briefing(),
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
    refetchOnWindowFocus: true,
  });
}
```

### `DashboardPage.tsx` shape

```tsx
const { data: briefing, isFetching, refetch } = useBriefing();
const { data: state }  = useDashboardState();

<Page>
  <div className="grid grid-cols-[1.4fr_1fr] gap-14 px-14 py-12 max-w-[1280px] mx-auto">
    <LeftColumn>
      <DateEyebrow now={state.now}>
        <BriefingStatus
          source={briefing.source}
          loading={isFetching}
          onRefresh={refetch}
        />
      </DateEyebrow>
      <Headline greet={briefing.greet} body={briefing.headline} />
      <Elaboration text={briefing.elaboration} loading={isFetching} />
      <AttentionList items={state.attention} />
      <KpiStrip kpis={state.kpis} butlers={state.butlers} />
    </LeftColumn>
    <RightColumn>
      <ButlerIndex butlers={state.butlers} />
      <NextList upcoming={state.upcoming} />
    </RightColumn>
  </div>
</Page>
```

### Components to create

All under `frontend/src/components/overview/`:

- `DateEyebrow.tsx` — uppercase mono, "Overview · Wed, 7 May 2026 · 14:21",
  with a slot on the right for `BriefingStatus`.
- `BriefingStatus.tsx` — pill button: dot + label + ↻. Three states:
  `loading` (amber, "composing…"), `llm` (green, "llm · cached 5m"),
  `fallback` (dim, "templated"). Click → refetch.
- `Headline.tsx` — `font-sans font-medium tracking-tight` at 44px,
  `line-height: 1.08`, two lines. `greet` in `text-muted-foreground`,
  `body` below in `text-foreground`. `max-w-[14ch]`.
- `Elaboration.tsx` — `font-serif text-base leading-relaxed
  text-muted-foreground max-w-[50ch]`, with a 200ms opacity transition
  while `loading`.
- `AttentionList.tsx` — rule-separated rows. Grid `24px 1fr auto`. Severity
  glyph on left (◇ for approval, ⚠ for reauth/error), then title + serif
  detail line, then action arrow on right. Empty state: `font-serif italic
  text-muted-foreground`, "Nothing waiting."
- `KpiStrip.tsx` — 4-column grid divided by hairlines. Mono eyebrow,
  large tabular number, mono delta below.
- `ButlerIndex.tsx` — section with `Section` header. Each row: status dot,
  name, 24h sessions (mono), today spend (mono right-aligned).
- `NextList.tsx` — section with rule rows: time, label, kind tag.
- `Section.tsx` — `<div>` with mono-uppercase eyebrow + bottom border,
  reused for both indices.

## 6. Tokens (extend `frontend/src/index.css`)

Add these to both `:root` and `.dark`:

```css
:root {
  --font-sans: 'Inter Tight', ui-sans-serif, system-ui, sans-serif;
  --font-serif: 'Source Serif 4', ui-serif, Georgia, serif;
  --font-mono: 'JetBrains Mono', ui-monospace, monospace;

  /* Overview-specific role tokens */
  --eyebrow-size: 10px;
  --eyebrow-tracking: 0.14em;
  --display-size: 44px;
  --display-tracking: -0.025em;
  --display-leading: 1.08;
  --rule: 1px solid var(--border);
  --gutter: 56px;
  --col-readable: 50ch;
  --col-headline: 14ch;
}
```

Tabular numbers: add `.tnum { font-variant-numeric: tabular-nums; }`
to `index.css` and apply to every numeric element.

Google Fonts (add to `index.html`):

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter+Tight:wght@400;500;600;700&family=Source+Serif+4:ital,wght@0,400;0,500;1,400&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
```

## 7. Sidebar replacement

Replace `frontend/src/components/layout/Sidebar.tsx` with a 56px rail.

- Read existing `nav-config.ts` — keep its three sections (Main / Butlers
  / Operations) and the same routes.
- Each item collapses to a 16px icon. Items in the **Butlers** section
  use the existing `<ButlerMark>` letter-glyph in the butler's category
  hue, not an SVG icon.
- Hover → tooltip floats out to the right at `left: 56px`.
- Active item: 2px left bar, 6% white tint background fill.
- Status indicator: 6px dot top-right of icon, shown only when butler
  status is `degraded` or `error`. Ring stroke matches rail bg so it
  reads as detached.
- Badge: red for reauth count (`/settings`), amber for approvals count
  (`/approvals`). Reads from the same data the page uses.
- Group expand (Relationships): chevron at bottom-right of the glyph;
  click to reveal indented children (`Contacts`, `Groups`).
- Footer: tiny dot summary ("1 degraded · 2 awaiting") via `title` attr.

`Shell.tsx` only needs `width: 56px` for the sidebar slot and corresponding
`ml-14` on the main column. Mobile sheet behavior unchanged.

## 8. Empty / loading / error states

- **Initial load (no briefing yet):** show the deterministic-fallback
  greeting + "Reading the day…" elaboration in dim text. Don't blank.
- **LLM in flight (`isFetching`):** keep current text, drop opacity to
  0.4 for 200ms. Status pill says "composing…".
- **Backend down:** state query fails → render skeleton rules with
  "Couldn't reach the house." in serif italic where the elaboration goes.
- **No attention items:** render `<div>Nothing waiting.</div>` (serif
  italic, muted), not a celebratory empty state.
- **All butlers healthy:** no status dots in sidebar, no footer summary.

## 9. Accessibility

- Headline is `<h1>` with `aria-live="polite"` so screen readers re-announce
  on briefing refresh.
- Elaboration is `<p>` with `aria-live="polite"`.
- Sidebar items: `<a>` with full `aria-label` (the tooltip text); status
  dot uses a hidden `<span class="sr-only">degraded</span>`.
- KPI numbers have `aria-describedby` pointing to their label text.
- Focus order: skip to main content link → headline → attention list →
  sidebar.
- Hover tooltips also appear on `:focus-visible`.

## 10. Print

`@media print` — collapse sidebar, headline at 32pt, body at 11pt,
hairlines become 0.5pt. Briefing prints; backend sketch and status pill
do not.

## 11. Test plan

1. Render with all 5 `state_class` values; confirm headline text matches table.
2. Force LLM timeout → fallback path renders, status pill says "templated".
3. Refetch button calls `useBriefing.refetch()`; UI shows "composing…"
   for ≥200ms even if cached.
4. Toggle theme; every surface flips, no flashes.
5. Sidebar: hover each item, confirm tooltip; click Relationships,
   confirm sub-items appear indented.
6. KPI tabular nums: deltas line up vertically across columns.
7. axe-core scan: zero violations.

## 12. Out of scope (do not build)

- Animation beyond the 200ms briefing fade and chevron rotation.
- Charts beyond the existing 24h stripe (which lives elsewhere now).
- Onboarding overlays, tour tooltips, or "what's new" callouts.
- A separate "AI / generated" badge anywhere except the briefing pill.
- Light-mode tweaks to butler category hues — accept slightly muted
  versions in light, do not invent new ones.

---

**Reference prototype:** `overview/Overview.html` (this project) renders
the full editorial layout with both themes, scenario cycler, working
briefing path against `window.claude.complete`, and an embedded backend
sketch. Inspect that file directly for exact spacing, type sizes, and
copy decisions; the prototype is canonical, this document is the recipe.
