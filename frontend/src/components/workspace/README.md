# workspace components

This directory holds reusable UI building blocks for dashboard workspace pages —
that is, full-page views that display time-windowed data with optional live
polling. Components here are domain-agnostic and can be composed into any page
that follows the workspace pattern.

## Components

### `TimeWindowPicker`

A three-mode time-range selector: **Today**, **Last 7 days**, and a custom date
input pair. Accepts the result of `useTimeWindow()` as its only prop. The picker
is purely presentational — all state lives in the hook.

```tsx
import { TimeWindowPicker } from "@/components/workspace/TimeWindowPicker"
import { useTimeWindow } from "@/hooks/use-time-window"

function MyPage() {
  const timeWindow = useTimeWindow(ownerTz)
  return <TimeWindowPicker window={timeWindow} />
}
```

### `ManualRefreshButton` (chronicles — `@/components/chronicles/ManualRefreshButton`)

A window-scoped cache-invalidation button for the Chronicles dashboard. Accepts
`timeWindow: { from: Date; to: Date }` and invalidates the TanStack Query cache
for that exact window. Not yet promoted to this directory; remains in chronicles
because it has tight coupling to `chroniclesKeys`.

_Future: if a domain-agnostic version is needed, extract the invalidation
callback and accept it as a prop instead of importing `chroniclesKeys` directly._

## `useTimeWindow` hook (`@/hooks/use-time-window`)

Manages the active time window and syncs it to `?from=YYYY-MM-DD&to=YYYY-MM-DD`
URL params. Defaults to today in the owner timezone.

```ts
const timeWindow = useTimeWindow(tz)
// timeWindow: { from, to, preset, pollingDisabled, setPreset, setCustomRange }
```

Key fields:

| Field | Type | Description |
|---|---|---|
| `from` | `Date` | Window start (start-of-day in owner tz) |
| `to` | `Date` | Window end (end-of-day in owner tz) |
| `preset` | `"today" \| "week" \| "custom"` | Which preset is active |
| `pollingDisabled` | `boolean` | True when `to` is at least 24 hours in the past |
| `setPreset` | `(p) => void` | Switch to a named preset |
| `setCustomRange` | `(from, to) => void` | Set an arbitrary range |

## Composition pattern — workspace page

The standard composition for a workspace page with time-windowed, live-polling
data:

```tsx
import { useTimeWindow } from "@/hooks/use-time-window"
import { TimeWindowPicker } from "@/components/workspace/TimeWindowPicker"

const LIVE_POLL_MS = 30_000

export default function MyWorkspacePage() {
  const timeWindow = useTimeWindow(ownerTz)

  // Gate polling: no polling for historical windows.
  const refetchInterval = timeWindow.pollingDisabled ? false : LIVE_POLL_MS

  const { data } = useMyData({ from: timeWindow.from, to: timeWindow.to, refetchInterval })

  return (
    <div>
      {/* Toolbar */}
      <div className="flex items-center gap-3">
        <TimeWindowPicker window={timeWindow} />
      </div>

      {/* Page body */}
      {/* ... */}
    </div>
  )
}
```

The pattern has three steps:

1. **Resolve the window** — `useTimeWindow` reads URL params and owns state.
2. **Gate polling** — `pollingDisabled` short-circuits `refetchInterval` to
   `false` for historical windows.
3. **Pass the window to data hooks** — every data hook receives `from` and `to`
   so the query key changes when the user selects a different range.

If the underlying data is bus-covered (its cache key is invalidated by the
fleet event bus — see `event-cache-registry.ts` and
`event-cache-manifest.ts`), prefer `useBusAwarePollInterval`
(`@/hooks/use-bus-aware-poll-interval`) over a fixed `LIVE_POLL_MS` literal:
it polls at the slow reconciliation cadence while the bus is connected and
tightens to a fast fallback while it's down, instead of a flat interval that
can't tell the difference (bu-01r64.3 — see `use-notifications.ts` for an
adopter). The manual `AutoRefreshToggle` primitive this section used to
document retired alongside that change: bus-covered surfaces now poll
automatically with no user-facing toggle.
