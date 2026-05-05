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

### `AutoRefreshToggle` (primitive — `@/components/ui/auto-refresh-toggle`)

A controlled toggle that shows a **Live** badge when enabled, a refresh-interval
`<Select>`, and a Pause/Resume button. Lives in `components/ui/` as a generic UI
primitive with no domain knowledge.

Props: `enabled`, `interval`, `onToggle`, `onIntervalChange`.

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
| `pollingDisabled` | `boolean` | True when `to` is >= 24 h before now |
| `setPreset` | `(p) => void` | Switch to a named preset |
| `setCustomRange` | `(from, to) => void` | Set an arbitrary range |

## Composition pattern — workspace page

The standard composition for a workspace page with time-windowed data and
auto-refresh:

```tsx
import { useTimeWindow } from "@/hooks/use-time-window"
import { useAutoRefresh } from "@/hooks/use-auto-refresh"
import { TimeWindowPicker } from "@/components/workspace/TimeWindowPicker"
import { AutoRefreshToggle } from "@/components/ui/auto-refresh-toggle"

export default function MyWorkspacePage() {
  const timeWindow = useTimeWindow(ownerTz)
  const autoRefresh = useAutoRefresh(30_000)

  // Gate polling: no auto-refresh for historical windows.
  const refetchInterval = timeWindow.pollingDisabled
    ? false
    : autoRefresh.refetchInterval

  const { data } = useMyData({ from: timeWindow.from, to: timeWindow.to, refetchInterval })

  return (
    <div>
      {/* Toolbar */}
      <div className="flex items-center gap-3">
        <TimeWindowPicker window={timeWindow} />
        {!timeWindow.pollingDisabled && (
          <AutoRefreshToggle
            enabled={autoRefresh.enabled}
            interval={autoRefresh.interval}
            onToggle={autoRefresh.setEnabled}
            onIntervalChange={autoRefresh.setInterval}
          />
        )}
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
   `false` for historical windows, keeping `AutoRefreshToggle` hidden (or
   disabled) for those views.
3. **Pass the window to data hooks** — every data hook receives `from` and `to`
   so the query key changes when the user selects a different range.
