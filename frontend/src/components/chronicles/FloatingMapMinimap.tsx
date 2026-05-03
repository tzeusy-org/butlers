// ---------------------------------------------------------------------------
// FloatingMapMinimap — game-style minimap for the Chronicles page.
//
// Wraps MapWidget in a fixed-positioned floating panel anchored to the
// bottom-right of the viewport, replacing the old in-flow Map section.
// Reduces vertical stacking and gives the Gantt area more room above.
//
// Modes:
//   - "open"      — default. ~360x260 panel in the corner.
//   - "expanded"  — large overlay (min(720px, 90vw) × min(540px, 70vh)).
//   - "minimized" — collapsed to a small pill button; click to reopen.
//
// Z-index: stays below shadcn dialogs/drawers (z-50) but above page content.
// ---------------------------------------------------------------------------

import { Maximize2, Minimize2, MapPin, X } from "lucide-react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

import { MapWidget } from "./MapWidget"
import type { MapWidgetInnerProps } from "./MapWidgetInner"

type Mode = "open" | "expanded" | "minimized"

export interface FloatingMapMinimapProps
  extends Pick<MapWidgetInnerProps, "playheadPoint" | "trailPoints"> {
  /** Optional initial mode. @default "open" */
  initialMode?: Mode
}

export function FloatingMapMinimap({
  playheadPoint,
  trailPoints,
  initialMode = "open",
}: FloatingMapMinimapProps) {
  const [mode, setMode] = useState<Mode>(initialMode)

  if (mode === "minimized") {
    return (
      <Button
        type="button"
        variant="default"
        size="sm"
        className="fixed bottom-4 right-4 z-30 shadow-lg"
        onClick={() => setMode("open")}
        data-testid="map-minimap-restore"
        aria-label="Show map"
      >
        <MapPin className="size-3.5" />
        Map
      </Button>
    )
  }

  const isExpanded = mode === "expanded"

  return (
    <div
      className={cn(
        "fixed bottom-4 right-4 z-30 flex flex-col rounded-lg border bg-card shadow-lg overflow-hidden",
        // Smooth transition between sizes — not a layout-thrash because the
        // map element below uses h-full and reflows automatically.
        "transition-[width,height] duration-200 ease-out",
        isExpanded
          ? "w-[min(720px,90vw)] h-[min(540px,70vh)]"
          : "w-[min(360px,90vw)] h-[min(260px,40vh)]",
      )}
      data-testid="map-minimap"
      data-mode={mode}
      aria-label="Location map"
      role="region"
    >
      {/* Header bar */}
      <div className="flex items-center justify-between gap-2 border-b bg-card/80 px-2 py-1 backdrop-blur">
        <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
          <MapPin className="size-3.5" />
          <span>Map</span>
        </div>
        <div className="flex items-center gap-0.5">
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            onClick={() => setMode(isExpanded ? "open" : "expanded")}
            aria-label={isExpanded ? "Shrink map" : "Expand map"}
            data-testid="map-minimap-toggle-size"
          >
            {isExpanded ? <Minimize2 /> : <Maximize2 />}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            onClick={() => setMode("minimized")}
            aria-label="Minimize map"
            data-testid="map-minimap-minimize"
          >
            <X />
          </Button>
        </div>
      </div>

      {/* Map fills the remaining space. height="h-full" lets MapLibre size
          itself to whichever dimensions the floating chrome currently has. */}
      <div className="flex-1 min-h-0">
        <MapWidget
          points={[]}
          playheadPoint={playheadPoint}
          trailPoints={trailPoints}
          height="h-full"
        />
      </div>
    </div>
  )
}
