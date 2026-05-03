/**
 * EmptyStatePanel -- actionable cold-start state for the social map.
 *
 * Rendered by SocialMapPage when scoredCount < 5. Sits above the canvas
 * (which remains visible, dimmed) so the user can see what they are
 * working toward.
 *
 * CTA routes to /ingestion?tab=connectors, the canonical connect-service
 * destination confirmed from frontend/src/router.tsx.
 */

import { Link } from "react-router";
import { Button } from "@/components/ui/button";

// ---------------------------------------------------------------------------
// Inline SVG hint: three empty Dunbar rings, hinting at what the map
// will look like once populated. No stock icons, no illustration clichés.
// ---------------------------------------------------------------------------

function RingsHint() {
  return (
    <svg
      width="80"
      height="80"
      viewBox="-40 -40 80 80"
      aria-hidden="true"
      style={{ opacity: 0.35 }}
    >
      {/* Outer ring */}
      <circle cx={0} cy={0} r={36} fill="none" stroke="currentColor" strokeWidth={1} strokeOpacity={0.5} />
      {/* Mid ring */}
      <circle cx={0} cy={0} r={22} fill="none" stroke="currentColor" strokeWidth={1} strokeOpacity={0.6} />
      {/* Inner ring */}
      <circle cx={0} cy={0} r={10} fill="none" stroke="currentColor" strokeWidth={1} strokeOpacity={0.7} />
      {/* Center dot (you) */}
      <circle cx={0} cy={0} r={3} fill="currentColor" opacity={0.5} />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------

export function EmptyStatePanel() {
  return (
    <div
      className="flex flex-col items-center gap-4 py-8 px-6 text-center"
      data-testid="empty-state-panel"
    >
      <RingsHint />

      <div className="flex flex-col gap-1.5 max-w-xs">
        <h2 className="text-base font-semibold text-foreground">
          Your circle is quiet.
        </h2>
        <p className="text-sm text-muted-foreground">
          Connect a service so the butler can learn who matters most to you.
        </p>
      </div>

      <div className="flex flex-col items-center gap-2">
        <Button asChild size="sm">
          <Link to="/ingestion?tab=connectors">Connect a service</Link>
        </Button>
      </div>
    </div>
  );
}
