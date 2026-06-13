# Ingestion redesign — Claude Code handoff

> A working prototype of the new `/ingestion` page lives in this folder.
> Open `Ingestion.html` directly in a browser (no server required) to
> review. This document is the recipe for porting it into the real
> codebase under `frontend/src/` and wiring it to a backend.

---

## 0. TL;DR

We're rebuilding `/ingestion` as a single page with three sub-routes:

| Path                                                 | View              |
|------------------------------------------------------|-------------------|
| `/ingestion`                                         | Timeline (default)|
| `/ingestion/connectors`                              | Connectors (roster) |
| `/ingestion/connectors/:connectorType/:endpointIdentity` | Connector detail  |
| `/ingestion/filters`                                 | Filters (pipeline)|

The page shell is **the same shape as `/butlers/{butler}`** — a sticky
sub-nav with three tabs, a single content column below. No `/ingestion/history`
tab; "history" is the Timeline tab with the time-range picker scoped back.

The visual language is **Dispatch** — fully spec'd in `DESIGN_LANGUAGE.md`.
Don't deviate. Hairline rules, no card chrome, mono numerals, butler hues
only on letter-marks, three state colors used sparingly.

---

## 1. What's on each tab

### 1a. Timeline (`/ingestion`) — the Ledger Stream

The default landing view. Reads like a financial statement: every external
item the system received, top to bottom, with end-to-end pipeline detail
behind a click-to-expand drawer.

**Header band:**
- Eyebrow: `Ingestion · timeline` + a **live status pill**
  (`composing… / fresh · 4s` — same pattern as the Overview briefing pill).
- Display headline: changes with time range —
  *"Today, in order of arrival." · "The last hour." · "Live, as it arrives."*
- Serif sub-paragraph (1 sentence).
- Right rail: 3 KPIs — events / sessions / cost — mono, tabular, 24px.

**Toolbar (sticky-ish under the page nav):**
- **Range picker** — `live · 1h · 24h · 7d · custom…` (pill group, segmented).
- **Search** — matches across sender, payload summary, channel, kind,
  full event ID, session IDs, butler names, model names.
- **Saved views** — `all · errors · priority · spend`. `spend` re-sorts the
  ledger by cost desc instead of chronological.

**Channel chip strip:** below the toolbar, hairline-bordered. One chip
per active channel in the current window: letter-mark glyph + name + count
+ error dot. Click to scope. To the right, a small **status filter**:
`ok · filtered · replay · error` pills (toggle on/off).

**Bulk-action bar:** slides in once any row is selected (checkbox column).
Mono "{n} selected" + `Replay all` (commit pill) + `Copy IDs` + `Clear`.

**The ledger itself:**
- Rows grouped by hour with a small hairline header (`11:00 · 3 events · $0.0023`).
- Columns: `[checkbox] · short id · time · channel (glyph + name + priority ★) · sender + summary · pipeline (inline flame + butler names + duration) · tok in · tok out · cost · replay/expand`.
- **Inline flame** — proportional bar(s) of butler durations across the
  row's pipeline cell, scaled against the day's max duration so durations
  are comparable across rows.
- Row hover: 3% surface tint on dark / 2.5% on light. No transforms.
- Selected/expanded row: drawer opens below in-place.

**The drawer (the page's "second screen"):**
A two-column band. Left is tabbed work area, right is request meta + sessions index.

Left tabs:
- **`flame · step ledger`** — the flame at full size + a **per-session step
  block** for every butler that ran. Each block:
  - Header: butler mark · name · `● ok` / `■ error` · full session ID (mono,
    click-to-copy) · model · session duration · session cost · `open →` link.
  - Steps table: step name (with status dot) · duration · % of session ·
    tokens in · tokens out · cost. Last row of the table is a session
    totals row.
  - Token + cost values are per-step, distributed proportionally to step
    duration; the last step picks up rounding remainder so per-session
    totals stay exact. Cost is `tokensIn × rate.in + tokensOut × rate.out`
    using per-model rates (`gpt-5.4-nano`, `gpt-5.4-mini`, `gpt-5.4`,
    `opencode-go/deepseek-v4-pro`).
- **`raw payload`** — pretty-printed JSON of the original inbound, in a
  bordered mono pre block (max-height 320px, scroll). Header has byte size
  + channel + `download` + `open in editor`. Footer: "truncated · headers +
  body shown · full payload Nk tokens".
- **`replay history`** — table of replay attempts (`at · by · result ·
  cost`). Trailing serif italic note explains the current policy
  (`retry × 3 · backoff 2^n · then human`) and why this event is in its
  current state.

Right column:
- `request` KV block (id, received, channel, kind, tier, sender, error if
  any, filtered-reason if any, cost summary).
- `sessions (n)` — compact index. Each row is an anchor: clicking scrolls
  to the matching session block on the left. A trailing `open →` link
  navigates to `/sessions/<id>`.
- Footer actions: `replay event` (commit, only if event needs replay) /
  `copy id` / `copy curl`.

**Footer rollup band:** 8-cell hairline-divided grid. `events · {range}`,
`accepted`, `filtered`, `needs replay`, `sessions`, `tok in`, `tok out`,
`cost`. Mono 18px tabular numbers.

### 1b. Connectors (`/ingestion/connectors`) — the Roster

A single dense register of every channel the house listens on.

Columns (left → right):
1. **health dot** — 6px circle (green=ok / amber=expiring / red=needs reauth / off=dormant).
2. **channel** — letter-mark glyph (20px) + name + mono kind (webhook / imap / poll / long-poll).
3. **function** — serif gloss describing what the channel does, plus a
   mono meta line: `last · HH:MM:SS · poll · 10m · 100% routed`.
4. **24h sparkline** — 24 bars (1 per hour) of throughput, with `00 / 12 / 24` mono axis labels below.
5. **auth pill** — dot + mono uppercase status (`AUTHORIZED / EXPIRING / REAUTH / NOT SET`) + serif note (`oauth refresh · channel expires in 4d`).
6. **events** (mono tabular, right-aligned).
7. **sess** (sessions triggered, right-aligned).
8. **cost** (right-aligned).
9. **disclosure** (`›`).

Above the table, an **attention strip** appears only if any connector
has auth issues or degraded health — a row of underlined links scoping
to the connector with a single-line reason.

Below, a **dormant section** (eyebrow `available · not connected`):
list of channels the system could ingest from but currently doesn't —
serif italic descriptions, each with a `connect →` pill on the right.

Footer: 5-cell KPI band + a `+ add connector` commit pill.

### 1c. Connector detail (`/ingestion/connectors/spotify/me`)

Two-zone page. The header band identifies the connector with a 56px
letter-mark + display headline (`Spotify.`) + mono meta line + serif
purpose paragraph. To the right, a **reauth call-to-action** box appears
only when the channel needs it — bordered in `--red`, with a serif
explanation, an inline link to QA's investigation, and a `re-authorize`
commit pill.

Below the header, a two-column editorial body:

**Left (1.4fr):**
- 4-cell KPI strip (events 24h · sessions · cost · latency p50). Mono 26px.
- 24h throughput histogram (96px tall bars, 24 cols, peak bar in fg).
- `recent · N` — compact list of the most recent events from this channel,
  click-thru to Timeline.
- `incidents · 24h` — chronological list of incident entries (`qa.flag`,
  `error`, `qa.alert`, `warning`) with timestamps and serif descriptions.

**Right (1fr):**
- `oauth scopes · N` — each scope shown with a status dot (green=granted,
  red=mismatch) + mono name + mono uppercase verdict. Trailing serif
  italic note when reauth is in play.
- `schedule` — KV block (cadence, next run, paused) + pause/run-now pills.
- `routing rules · N` — list of filter rules that target this channel
  specifically, click-thru to Filters.
- `config` — endpoint, latency, auth type, enabled. Trailing pill row:
  `rotate token / copy config / disconnect`.

### 1d. Filters (`/ingestion/filters`) — the pipeline

The page that makes ingestion *explainable*. After reading this once, the
operator should be able to predict what happens to a new event before
clicking anything.

**Header:**
- Eyebrow: `Filters · 4572 events · last 24h`.
- Display headline: `How signals earn dispatch.`
- Serif sub: 2 sentences explaining that this is the pipeline; rules at
  each gate decide whether the system stores, drops, tiers, routes, or replays.

**Pipeline diagram band:**
A 5-column hairline-divided strip — one column per gate.
- §1 `accept` · §2 `dedupe` · §3 `tier` · §4 `route` · §5 `execute`.
- Each column shows the mono `out` count after that gate (28px tabular).
  When drops occurred, an inline mono delta in `--red` shows the loss
  (`− 168`). The `route` column also shows the **preserved-without-dispatch**
  count in `--amber` (`− 4204 preserved`).
- Below the column, a mono 10px gloss explains the gate in one sentence.
- A **funnel bar** under the columns visualises the in→out shrinkage —
  proportional segments, with the lost portion of each segment fading to
  red (drop) or amber (preserve). Honest numbers, not decorative.

**Five gate sections (§1 … §5):**
Each section is hairline-headed (`§N` + lowercase name + serif gloss +
right-aligned mono `in N · out M · − K`). Below the header:

- The **rules** that fire at this gate (using the rule layout below).
- Optional gate-specific summary inside the section:
  - §1 `accept`: a `drops · 24h` mini-list of the actual lines that
    produced the drop number (each: red mono `− N` + serif italic label
    + mono rule ID).
  - §3 `tier`: a tiered breakdown (`● priority · 12   ○ default · 4370`).
  - §5 `execute`: outcome breakdown (`● ok · 177   ◐ replay · 1   ■ error · 0`).
- When a gate's behaviour lives in code rather than rules (`dedupe`,
  `execute`), the section *says so* in serif italic instead of being empty
  ("Canonicalises (source, ts) before checking the dedup window. Window
  = 90s for sensor data, 24h for content. Lives in `butlers/<x>/ingest/dedup.py`.").

**Rule row layout** (re-used across gates):
- Status dot (green = enabled).
- **Body cell** — name (16px sans 500), `· owner` mono tag, serif gloss
  (14px, max-w-60ch), then a **mono pseudocode block** (`when ... → action`)
  in a hairline-bordered surface, then a **serif italic list of 1–3 example
  matches** separated by mono `·`.
- 24h match count (mono 18px tabular) with mono small caption `matched · 24h`.
- Toggle (32×18 pill, accent = `--fg`).
- Disclosure `›`.

**Two adjacent surfaces below the gates** (2-column grid, 1.3fr/1fr):

- **Priority senders** — first-class data block. People who bypass the
  default tier. Table columns: name+handle / channel+routes-to / added /
  last seen / edit / remove. Header explains that this is data, not rules.
  `+ add` pill.
- **Channel defaults** — what each connector does with an *unmatched*
  event. Tight 3-col list: channel · policy · serif italic note.

**Archived** section at the bottom: disabled rules, rendered at 0.55
opacity with `restore` pills.

**Footer:** 2 commit pills — `+ add rule` (commit) + `open DSL` (pill).

---

## 2. Data model

The prototype runs on synthetic data. The shapes below are the **target
domain model** — keep them stable across backend and frontend.

### 2a. `IngestionEvent`

```ts
type IngestionEvent = {
  id: string;                   // ULID, sortable by time
  t: string;                    // 'HH:MM:SS' display time (server-formatted; raw ISO also acceptable)
  channel: ConnectorKind;       // 'home_assistant' | 'email' | 'telegram' | 'whatsapp' | 'spotify' | 'calendar' | 'notion' | …
  kind: string;                 // domain-specific kind, e.g. 'message.inbound' | 'state.changed' | 'play.recent'
  sender: string;               // long form (e.g. 'Wei (telegram:@weiminator)')
  senderShort?: string;         // short display ('Wei · @weiminator')
  summary: string;              // one-line serif body ('"hey are we still on for dinner sunday"')
  status: 'ingested' | 'filtered' | 'error' | 'replay_pending' | 'replay_complete' | 'replay_failed';
  tier: 'default' | 'priority';
  tokensIn: number;             // sum across all sessions
  tokensOut: number;
  cost: number;                 // USD
  durationMs: number;           // end-to-end (max butler end)
  bytes?: number;
  hopFiltered?: string;         // human reason if status='filtered'
  error?: string;               // top-level error if any session errored
  butlers: ButlerSession[];     // 0..N sessions this event triggered
};
```

### 2b. `ButlerSession`

```ts
type ButlerSession = {
  name: ButlerName;             // 'switchboard' | 'relationship' | 'chronicler' | …
  session: string;              // UUID — primary identifier across the system
  model: string;                // 'gpt-5.4-mini' etc.
  startedAt: string;            // 'HH:MM:SS'
  startOffsetMs: number;        // ms after event acceptance
  durationMs: number;
  tokensIn: number;
  tokensOut: number;
  cost?: number;                // sum of step costs (derived; can also live on the row)
  status: 'ok' | 'error';
  error?: string;
  steps: SessionStep[];
};

type SessionStep = {
  name: string;                 // 'classify' | 'draft.reply' | 'persist.outbox' | 'pdf.parse' | …
  durMs: number;
  status: 'ok' | 'error';
  tokensIn: number;             // per-step (distributed proportionally to durMs if backend doesn't track natively)
  tokensOut: number;
  cost: number;
};
```

> Decide on the backend whether per-step token/cost is tracked natively or
> derived. If derived (cheaper), expose it via a server-side computation
> so the client doesn't reinvent the distribution.

### 2c. `Connector`

```ts
type Connector = {
  id: ConnectorKind;            // also serves as URL slug for /ingestion/connectors/:type
  endpointIdentity: string;     // e.g. 'me' for spotify, 'primary' for calendar
  label: string;
  kind: 'webhook' | 'imap' | 'poll' | 'long-poll' | 'file-drop';
  glyph: string;                // single uppercase letter for the channel mark
  description: string;          // serif gloss

  auth: {
    status: 'ok' | 'expiring' | 'needs_reauth' | 'unconfigured';
    note: string;               // 'oauth refresh · channel expires in 4d'
    expires: string | null;     // '4d' | 'now' | '364d' | null
  };
  health: 'ok' | 'degraded' | 'error' | 'off';
  enabled: boolean;
  lastEventAt: string | null;

  events24h: number;
  rate1h: number;               // events/hr in the last hour
  sessions24h: number;
  cost24h: number;
  spark24h: number[];           // length-24 hourly throughput
  filtered24h: number;
  routedPct: number | null;     // 0–100; null if dormant

  config: {
    endpoint: string;           // 'imap.gmail.com:993' | '/wh/whatsapp'
    cadence: string;            // 'poll · 10m' | 'event-driven' | 'IDLE · 30s heartbeat'
    latencyMs: number;
  } | null;
  scopes: string[];
  incidents?: { ts: string; kind: string; text: string }[];
  note?: string;
};
```

### 2d. `IngestionRule`

```ts
type IngestionRule = {
  id: string;
  name: string;
  note: string;                 // serif gloss
  when: string;                 // mono DSL: 'channel = whatsapp · kind = group.message · group ∈ filters.lowsignal'
  action: string;               // mono DSL: 'drop · preserve for replay' | 'route := chronicler' | 'tier := priority'
  matches24h: number;
  enabled: boolean;
  owner: 'system' | 'tze';      // who authored
  examples: string[];           // 1–3 short serif lines
};
```

Rules are grouped on the frontend by the **verb** in `action` (drop /
preserve / tier / route) and rendered under their matching gate in the
pipeline:

- `drop` and `preserve` → §1 accept
- `tier` → §3 tier
- `route` → §4 route
- §2 dedupe and §5 execute have no rules in the DSL; their behaviour is
  code-resident and the page renders a serif italic note.

### 2e. `PipelineStats`

```ts
type PipelineStats = {
  window: '24h' | '1h' | '7d';
  total_received: number;
  stages: PipelineStage[];
};

type PipelineStage = {
  key: 'accept' | 'dedupe' | 'tier' | 'route' | 'execute';
  label: string;
  gloss: string;
  in: number;
  out: number;
  drops?: { count: number; label: string; rule: string }[];
  tiered?: { priority: number; default: number };
  preserved?: number;
  executed?: { ok: number; replay_pending: number; errored: number };
};
```

### 2f. `PriorityContact`

```ts
type PriorityContact = {
  name: string;
  handle: string;
  channel: ConnectorKind;
  butler: ButlerName;           // which butler the message is routed to
  added: string;                // 'DD MMM YYYY'
  lastSeen: string;             // 'HH:MM' or '3d ago'
};
```

### 2g. `ChannelDefault`

```ts
type ChannelDefault = {
  channel: ConnectorKind;
  policy: string;               // 'preserve · no dispatch' | 'route · switchboard' | …
  note: string;                 // serif italic line
};
```

---

## 3. APIs — what exists vs. what to create

The existing app already routes:
- `/ingestion` → `IngestionPage.tsx`
- `/ingestion/connectors/:connectorType/:endpointIdentity` → `ConnectorDetailPage.tsx`
- Legacy `/connectors` → redirect to `/ingestion?tab=connectors`.

**Add a route** for `/ingestion/connectors` (the roster — currently missing
from the router; the legacy redirect points at a query string).

Search the codebase for existing endpoints first; the names below are the
*minimum* surface the redesign needs.

### 3a. Endpoints required by Timeline

```http
GET /api/ingestion/events
  ?range=live|1h|24h|7d|custom
  &start=...&end=...           # for custom
  &channels=email,telegram     # CSV
  &statuses=ingested,replay_pending
  &tier=priority|all
  &q=...                       # free-text search across sender/payload/session/event id/butler/model
  &sort=time|cost
  &limit=200&cursor=...
```

Returns `{ events: IngestionEvent[], next_cursor?: string, totals: Totals }`.

`Totals` = `{ count, accepted, filtered, failed, sessions, tokensIn, tokensOut, cost }` for the
*filtered* result, plus a `window: { count: <unfiltered count in range> }` for the chip strip.

```http
POST /api/ingestion/events/:id/replay
POST /api/ingestion/events/replay        # body: { ids: [...] } — bulk
GET  /api/ingestion/events/:id/payload   # raw inbound, may be large; gated by audit log
GET  /api/ingestion/events/:id/replays   # full replay history
```

Live mode is **server-sent events** (matches what `/overview` does for
briefing refresh):

```http
GET /api/ingestion/events/stream  (text/event-stream)
   event: append
   data: { event: IngestionEvent }
```

Frontend prepends new events to the ledger and increments the rollup
totals.

### 3b. Endpoints required by Connectors

```http
GET  /api/ingestion/connectors                              # roster + 24h aggregates
GET  /api/ingestion/connectors/:type/:identity              # full detail
POST /api/ingestion/connectors/:type/:identity/reauth       # starts OAuth dance
POST /api/ingestion/connectors/:type/:identity/pause
POST /api/ingestion/connectors/:type/:identity/run-now      # for poll connectors
POST /api/ingestion/connectors/:type/:identity/rotate-token
DELETE /api/ingestion/connectors/:type/:identity            # disconnect
```

Plus a discovery endpoint for the "available · not connected" block:

```http
GET /api/ingestion/connectors/available
```

### 3c. Endpoints required by Filters

```http
GET  /api/ingestion/pipeline?window=24h          # PipelineStats
GET  /api/ingestion/rules                        # IngestionRule[]
POST /api/ingestion/rules                        # add rule
PATCH /api/ingestion/rules/:id                   # rename / toggle / re-order
DELETE /api/ingestion/rules/:id

GET  /api/ingestion/priority-contacts            # PriorityContact[]
POST /api/ingestion/priority-contacts
DELETE /api/ingestion/priority-contacts/:id

GET  /api/ingestion/channel-defaults             # ChannelDefault[]
PATCH /api/ingestion/channel-defaults/:channel
```

### 3d. Existing infrastructure to reuse

- **Sessions API** — `/sessions/:id` already exists somewhere (the
  `open →` link points there). Don't reinvent — just navigate.
- **QA investigation links** — when a connector has `incidents` with
  `qa.alert` / `qa.flag` kinds, the connector detail's reauth callout
  deep-links into `/qa/investigations/:id`. Use the existing QA route.
- **Approvals counter** — the existing `badgeKey` system in `nav-config.ts`
  is the right place to expose a `ingestion-needs-reauth` badge counter
  on the sidebar's Ingestion item.

---

## 4. Frontend port

### 4a. Files to create / modify

```
frontend/src/pages/IngestionPage.tsx                        REPLACE — shell with sub-nav, default tab = Timeline
frontend/src/pages/ConnectorDetailPage.tsx                  REPLACE — Spotify-style detail page
frontend/src/router.tsx                                     EDIT   — add `/ingestion/connectors` route (roster)
frontend/src/components/ingestion/
  IngestionSubNav.tsx                                       NEW
  timeline/
    TimelineTab.tsx                                         NEW
    RangePicker.tsx                                         NEW
    SavedViews.tsx                                          NEW
    LiveStatusPill.tsx                                      NEW
    SearchInput.tsx                                         NEW
    ChannelChip.tsx                                         NEW
    StatusFilter.tsx                                        NEW
    BulkActionBar.tsx                                       NEW
    LedgerRow.tsx                                           NEW
    HourBlock.tsx                                           NEW
    FlameStrip.tsx                                          NEW (the one canonical flame component)
    ExpandedDrawer.tsx                                      NEW
    drawer/
      DrawerFlame.tsx                                       NEW (per-session step blocks)
      SessionStepBlock.tsx                                  NEW
      DrawerRaw.tsx                                         NEW
      DrawerReplay.tsx                                      NEW
      SessionIndex.tsx                                      NEW
      CopyableId.tsx                                        NEW
    RollupBand.tsx                                          NEW
  connectors/
    ConnectorsRoster.tsx                                    NEW
    ConnectorRow.tsx                                        NEW
    Sparkline.tsx                                           NEW
    AttentionStrip.tsx                                      NEW
    DormantList.tsx                                         NEW
    ConnectorDetail.tsx                                     NEW (the Spotify-style page)
    ReauthCallout.tsx                                       NEW
    ScopeList.tsx                                           NEW
    ConnectorHistogram.tsx                                  NEW
  filters/
    FiltersTab.tsx                                          NEW
    PipelineDiagram.tsx                                     NEW
    GateSection.tsx                                         NEW
    RuleRow.tsx                                             NEW
    Toggle.tsx                                              NEW
    PrioritySendersBlock.tsx                                NEW
    ChannelDefaultsBlock.tsx                                NEW
  shared/
    ChannelGlyph.tsx                                        NEW (the channel letter-mark)
    Eyebrow.tsx                                             NEW (10px mono uppercase)
    Mono.tsx                                                NEW
    PillBtn.tsx                                             NEW (pill + commit variants)
    KV.tsx                                                  NEW
    StatusBadge.tsx                                         NEW
frontend/src/hooks/
  use-ingestion-events.ts                                   NEW — TanStack Query for events list + SSE for live
  use-connector-roster.ts                                   NEW
  use-connector-detail.ts                                   NEW
  use-pipeline-stats.ts                                     NEW
  use-ingestion-rules.ts                                    NEW
  use-priority-contacts.ts                                  NEW
frontend/src/api/ingestion.ts                               NEW — client-side wrappers around the endpoints in §3
```

### 4b. Tech-stack notes

- **Framework**: React + Vite (per existing `frontend/src/main.tsx` +
  `App.tsx` + `router.tsx`). Use **TanStack Query** for fetches; the
  Overview already uses it (`hooks/use-briefing.ts` per `IMPLEMENTATION.md`).
- **Routing**: React Router v6 (already in place).
- **Components**: shadcn/ui primitives where they cleanly map — but most
  of the surfaces here are bespoke layouts (rule-separated grids), not
  card primitives. Don't reach for `<Card>`.
- **Styling**: Tailwind with the existing tokens in `frontend/src/index.css`.
  The prototype uses inline-style objects; rewrite to Tailwind classes
  during the port, but preserve **exact** measurements:
  - Hairlines: `1px solid var(--border)` / `var(--border-soft)`.
  - Eyebrow: `font-mono text-[10px] tracking-[0.14em] uppercase text-muted-foreground`.
  - Display: `font-sans font-medium text-[44px] tracking-[-0.025em] leading-[1.08]`.
  - Title (drawer / section): `font-medium text-[28px] tracking-[-0.02em]`.
  - Body: `text-[14px] leading-[1.5]`.
  - Voice (serif): `font-serif text-[16px] leading-[1.55]` (or 13-15px in
    smaller blocks).
  - Mono inline: `font-mono text-[11px] tracking-[0.01em]`.
  - All numeric cells: `font-variant-numeric: tabular-nums` (already
    provided by the `.tnum` utility — add via `index.css`).
- **State colors**: `--red`, `--amber`, `--green` from the existing
  tokens. Foreground or border only — never background fills.
- **Butler hue map**: extend the existing `BUTLER_HUE` in
  `frontend/src/lib/butler-hue.ts` (or wherever the overview's letter-mark
  lives) to cover `switchboard`, `lifestyle`, `chronicler`, etc — see
  `ingestion-data.jsx`'s `BUTLER_HUE` for the canonical map.

### 4c. URL state

- **Tab**: hash routing in the prototype (`#timeline`); in the real app
  this is the route (`/ingestion`, `/ingestion/connectors`, `/ingestion/filters`).
- **Time range**: `?range=24h&start=...&end=...` query string.
- **Filters**: `?channels=email,telegram&statuses=replay_pending`.
- **Saved view**: `?view=errors`.
- **Open event in drawer**: `?event=<id>` — scrolls to and expands the
  row on load. Closing the drawer clears the param.
- **Connector roster vs. detail**: standard route navigation
  (`/ingestion/connectors` ↔ `/ingestion/connectors/:type/:identity`).

### 4d. Interactions / behaviour worth pinning

- **Live status pill** cycles `composing… → fresh · Ns` every ~18s when
  range=`live`; static when range is non-live (just shows the cache age).
- **Range switch** preserves channel/status/view filters across switches.
- **Bulk select** persists across hour-group boundaries; clears on tab
  switch.
- **Drawer tabs** preserve their selection across row switches (open the
  next event with `replay history` tab already active if that's what was
  last on screen).
- **Session-id click-to-copy** uses `navigator.clipboard`; show the word
  `copied` for ~900ms beside the id.
- **SessionIndex** in the drawer right rail is an **anchor scroll** to
  `#session-<uuid>` on the left, not a route change.
- **Hour group** headers compute event count + cost sum for that hour.
- **Channel chip** click toggles the channel filter — clicking the same
  active chip un-scopes.
- **Reauth flow** on Spotify detail: clicking `re-authorize` triggers the
  standard OAuth popup; success transitions `auth.status` to `ok` and
  clears the reauth callout. The connector row's red severity rail also
  drops.

---

## 5. Design language compliance (non-negotiable)

Read `DESIGN_LANGUAGE.md` end-to-end before writing UI. The most-violated
rules in normal SaaS porting:

1. **No card chrome.** Hairlines + rhythm, never bordered/shadowed cards.
2. **One elevation.** No `box-shadow` on anything except the briefing-style
   pill (and even then, sparingly).
3. **Type weight 500 for display.** Never 700.
4. **State colors are foreground/border only.** Don't fill backgrounds with
   red/amber/green.
5. **Butler hues appear only on letter-marks.** Never as accent on a
   button, hover, or border.
6. **Tabular numerals everywhere.** Mono fonts already do this; sans
   numerics need `font-variant-numeric: tabular-nums`.
7. **Empty states are one serif italic sentence.** *"Nothing matches."*
   not *"You don't have any events..."*.
8. **No emoji in interface chrome.** The prototype uses ascii glyphs
   (●, ◐, ■, ◇, ›) — preserve them or replace with stroke-only SVG icons,
   but no emoji.
9. **No animation beyond the prototype's set:** 200ms briefing fade,
   120ms chevron rotation, 120ms toggle slide. No "delight".
10. **Voice rules** (`§8` in `DESIGN_LANGUAGE.md`) govern all copy. Past
    tense for events, present for state. No first person, no exclamation,
    no hedging adverbs.

---

## 6. Behaviours that are not in the prototype yet

Build these alongside the port:

- **Pagination / virtualisation** on Timeline once `events.length` > ~200.
  The hairline rule rhythm makes `react-virtual` straightforward — keep
  hour-group headers as items in the virtual list, not floating.
- **Persistent drawer position** in URL (`?event=<id>`) — when the user
  reloads with that param, the row scrolls into view and the drawer
  opens on the last-active tab (`flame` / `raw` / `replay`).
- **Audit-log gating on `raw payload`** — the raw inbound is potentially
  PII (telegram message text, email body). Fetching it should write to
  the audit log and require fresh authentication on a quiet timeout.
- **Replay confirmation modal** for any replay whose downstream effects
  are not idempotent. Most ingestion replays *are* idempotent (re-emit the
  event; downstream butlers dedupe by `event_id`) — but for the few cases
  that aren't (`email` "send drafted reply" replays would re-send), the
  confirm is mandatory.
- **OAuth callback** for `/connectors/:type/:identity/reauth` — return
  the user to `/ingestion/connectors/:type/:identity` with a transient
  toast.
- **Search persistence** — last search string lives in `localStorage`
  scoped per tab so coming back to Timeline restores it.

---

## 7. Suggested Claude Code prompts

Drop in order. Each is scoped to a single concern.

### 1 · Backend shape

> I'm rebuilding the `/ingestion` page. Here's the synthetic data shape
> the redesign assumes: `ingestion-redesign/ingestion-data.jsx` (events +
> sessions + steps) and `ingestion-redesign/ingestion-connectors-data.jsx`
> (connectors, rules, pipeline stats, priority contacts, channel
> defaults). The `IngestionEvent` / `ButlerSession` / `SessionStep` /
> `Connector` / `IngestionRule` / `PipelineStats` / `PriorityContact` /
> `ChannelDefault` types are documented in `INGESTION_HANDOFF.md` §2.
>
> Look at our existing FastAPI/SQLModel layer and propose the smallest
> backend that supports these shapes. Where does each field come from
> (connector adapter, butler trace, log, computed)? Which are stored vs.
> derived? Output a migration plan that keeps existing connector tables
> compatible. Especially flag where per-step token/cost should be tracked
> natively vs. derived proportionally from step duration server-side.

### 2 · API surface

> Implement the endpoints listed in `INGESTION_HANDOFF.md` §3. Match the
> existing FastAPI router style. The hottest path is
> `GET /api/ingestion/events` — implement pagination via opaque cursors
> and SSE-streamed live updates over `GET /api/ingestion/events/stream`.
> Audit-log access to `/api/ingestion/events/:id/payload`. Don't wire
> `replay` to actually re-emit yet — return a stub `{ accepted: true }`.

### 3 · Page shell

> Port `ingestion-redesign/Ingestion.html` + `ingestion-redesign/ingestion-app.jsx`
> into `frontend/src/pages/IngestionPage.tsx`. The page is a sticky
> sub-nav (Timeline / Connectors / Filters) like `/butlers/{butler}`,
> default tab = Timeline. Match the existing sidebar, theme, and font
> stack. URL: `/ingestion` (timeline) / `/ingestion/connectors` (roster) /
> `/ingestion/connectors/:type/:identity` (detail) / `/ingestion/filters`.
> Add the missing `/ingestion/connectors` route to `router.tsx`. No data
> wiring yet — render against the synthetic data files in
> `ingestion-redesign/` as fixtures.

### 4 · Timeline (Ledger Stream)

> Port the Timeline view (`ingestion-redesign/ingestion-v1.jsx`) to
> `frontend/src/components/ingestion/timeline/`, one file per component.
> Component list in `INGESTION_HANDOFF.md` §4a. Preserve the exact column
> widths, hairline rhythm, hour-group headers, inline flame strip, and
> the three drawer tabs (`flame · step ledger`, `raw payload`,
> `replay history`). The per-session step block is the most important
> piece — full session ID at the head (click-to-copy), step rows with
> tokens + cost per step, totals row at the bottom. SessionIndex in the
> right rail is an anchor scroll (`#session-<uuid>`), not a route change.

### 5 · Connectors (Roster + Detail)

> Port the Roster (`ingestion-redesign/ingestion-connectors-a.jsx`) and
> the Spotify detail (`ingestion-redesign/ingestion-connector-detail.jsx`)
> to `frontend/src/components/ingestion/connectors/`. The roster's
> attention strip + dormant list must render in their respective states.
> The detail page is a template — make it work for every connector kind,
> with the reauth callout only appearing when `connector.auth.status !==
> 'ok'`. The OAuth flow on `re-authorize` should kick off the existing
> connector auth dance and return here on success.

### 6 · Filters (Pipeline)

> Port the Filters tab (`ingestion-redesign/ingestion-filters.jsx`) to
> `frontend/src/components/ingestion/filters/`. The page IS the pipeline:
> five gates, with the funnel diagram up top and per-gate rule sections
> below. Rules are bucketed by their action verb (see §2d in
> `INGESTION_HANDOFF.md`). The §2 dedupe and §5 execute sections have no
> rules; render the serif italic "policy lives in code" note instead.
> The priority-senders block is a first-class data list, not a rule list.
> The funnel bar's drop / preserved segments must use proportional widths
> against the total received count.

### 7 · Wire up data

> Replace the synthetic-data fixtures with real queries against the
> endpoints from step 2. Use TanStack Query — same pattern as
> `hooks/use-briefing.ts` (referenced in `IMPLEMENTATION.md` §5). Live
> mode subscribes to the SSE stream and prepends new events. The connector
> detail page polls every 30s while the user is on it; on visibility
> change, refetches immediately.

### 8 · Behaviors not in mock

> Add the behaviors listed in `INGESTION_HANDOFF.md` §6:
>
> 1. URL state for active event (`?event=<id>` opens the drawer on load).
> 2. Virtualisation in Timeline once events.length > 200.
> 3. Audit-log gating + fresh-auth challenge for `raw payload` viewer.
> 4. Replay confirmation modal for non-idempotent replays.
> 5. OAuth callback returning to the connector detail with a toast.
> 6. Per-tab search persistence in localStorage.

---

## 8. Open questions for whoever owns this

- **Per-step token tracking** — does our existing butler trace already
  emit token-counted spans, or does the backend need to add that? The
  prototype derives per-step tokens proportionally to step duration, which
  is fine for display but lies about the actual cost shape (the
  `extract.applets` step likely consumed most of the tokens, not just
  most of the duration). Native tracking is preferable.
- **Replay idempotency boundary** — at what point in the pipeline does a
  replay re-emit vs. resume? Email "send drafted reply" should not replay
  the *send*. Document the boundary in the replay-history note.
- **Connector-level vs endpoint-level identity** — the URL has both
  (`:connectorType/:endpointIdentity`). When a user has two Gmail
  accounts, the roster should show two rows; treat `connector` as
  `(type, identity)` pair throughout the data model.
- **Search semantics** — is search full-text against the payload too, or
  only against the indexed metadata fields the prototype searches today
  (sender, summary, channel, kind, ids, model)? Full-text is heavier and
  needs PII review.
- **Pipeline window** — should the funnel band be filterable per
  range, or fixed at 24h? Current prototype is 24h-only; range picker
  doesn't apply to it. If filterable, the §3a `route` `preserved` value
  matters less for `live`/`1h`.
- **DSL editor** — the `+ add rule` and `open DSL` pills in the Filters
  footer hint at a separate editor view. Out of scope for the redesign
  itself, but where does that page live? `/ingestion/filters/new`?

---

## 9. File index — copy these to your handoff bundle

```
ingestion-redesign/
├── Ingestion.html                       host (open in browser)
├── INGESTION_HANDOFF.md                 (this file)
├── DESIGN_LANGUAGE.md                   canonical visual spec (copy of overview/)
├── IMPLEMENTATION.md                    overview-era recipe; reuse the patterns
│
├── ingestion-app.jsx                    page shell + sub-nav + tab routing + theme toggle
├── ingestion-data.jsx                   IngestionEvent[] + ButlerSession[] + SessionStep[] (with per-step token/cost computation)
├── ingestion-connectors-data.jsx        CONNECTOR_DETAILS + INGESTION_RULES + PIPELINE_STATS + PRIORITY_CONTACTS + CHANNEL_DEFAULTS
├── ingestion-shared.jsx                 StatusBadge / ChannelGlyph / BMark / Eyebrow / Mono / PillBtn / ReplayIcon / FlameStrip / PageHeader / TabsRow / formatters
├── ingestion-v1.jsx                     Timeline view (Ledger Stream — V1) — full file
├── ingestion-connectors-a.jsx           Connectors Roster
├── ingestion-connector-detail.jsx       Spotify-style connector detail
├── ingestion-filters.jsx                Filters / pipeline view
│
├── primitives.jsx                       PALETTES + applyTheme + ButlerMark + StatusDot + StripeChart (shared with overview)
├── sidebar.jsx                          existing nav rail (already in production — don't replace)
├── data.jsx                             BUTLERS_DATA used by sidebar
│
└── (reference / not needed for port):
    ├── IngestionProposals.html          design canvas with Roster A vs. Board B + filter v1
    ├── ingestion-v2.jsx                 Trace Console — channel lanes (rejected direction)
    ├── ingestion-v3.jsx                 Editorial Chronology (rejected direction)
    ├── ingestion-v4.jsx                 Tape Log (rejected direction)
    ├── ingestion-connectors-b.jsx       Board direction (rejected)
    └── design-canvas.jsx                pan/zoom canvas — used only by IngestionProposals.html
```

**To preview standalone:** open `Ingestion.html` in any browser — no
server needed. The page has a dark/light toggle in the bottom-right.

---

**The prototype is canonical, this document is the recipe.** Where they
disagree, the prototype wins; the document is a summary that may go
stale faster than the code.
