// ---------------------------------------------------------------------------
// ButlerDetailActions
//
// Composes the Page shell `actions` slot for the Butler detail page per the
// Gate-A A2 resolution (bu-rx6c2):
//
//   ChatPanel button | status pill | force-run button | pause/resume button
//
// Status pill:    derived from butler.status (ok/degraded/down/error/unknown)
// Force-run:      calls triggerButler with a default scheduler prompt
// Pause/Resume:   sets eligibility to "quarantined" (pause) or "active" (resume)
//                 via the Switchboard registry eligibility API
//
// NO Tier-2 hero block is added — identity stays in the Overview tab card.
// ---------------------------------------------------------------------------

import { useState } from "react";

import { triggerButler } from "@/api/index.ts";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useButler } from "@/hooks/use-butlers";
import { useRegistry, useSetEligibility } from "@/hooks/use-general";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ButlerDetailActionsProps {
  butlerName: string;
}

// ---------------------------------------------------------------------------
// Status pill
// ---------------------------------------------------------------------------

function StatusPill({ status }: { status: string }) {
  switch (status) {
    case "ok":
      return (
        <Badge
          data-testid="butler-status-pill"
          className="bg-emerald-600 text-white hover:bg-emerald-600/90"
        >
          Up
        </Badge>
      );
    case "degraded":
      return (
        <Badge
          data-testid="butler-status-pill"
          variant="outline"
          className="border-amber-500 text-amber-600"
        >
          Degraded
        </Badge>
      );
    case "error":
    case "down":
      return (
        <Badge data-testid="butler-status-pill" variant="destructive">
          Down
        </Badge>
      );
    default:
      return (
        <Badge data-testid="butler-status-pill" variant="secondary">
          {status}
        </Badge>
      );
  }
}

// ---------------------------------------------------------------------------
// ButlerDetailActions
// ---------------------------------------------------------------------------

export function ButlerDetailActions({ butlerName }: ButlerDetailActionsProps) {
  const { data: butlerResponse } = useButler(butlerName);
  const { data: registryResponse } = useRegistry();
  const setEligibility = useSetEligibility();

  const [isForceRunning, setIsForceRunning] = useState(false);

  const butler = butlerResponse?.data;
  const status = butler?.status ?? "unknown";

  // Find the registry entry to determine current eligibility / paused state
  const registryEntry = registryResponse?.data?.find((r) => r.name === butlerName);
  const isPaused = registryEntry?.eligibility_state === "quarantined";

  async function handleForceRun() {
    if (isForceRunning) return;
    setIsForceRunning(true);
    try {
      await triggerButler(butlerName, "Run your scheduled tick now.", "medium");
    } finally {
      setIsForceRunning(false);
    }
  }

  function handlePauseToggle() {
    if (setEligibility.isPending) return;
    setEligibility.mutate({
      name: butlerName,
      state: isPaused ? "active" : "quarantined",
    });
  }

  return (
    <div className="flex items-center gap-2" data-testid="butler-detail-actions">
      <StatusPill status={status} />

      <Button
        variant="outline"
        size="sm"
        data-testid="butler-force-run"
        disabled={isForceRunning}
        onClick={handleForceRun}
      >
        {isForceRunning ? "Running…" : "Force Run"}
      </Button>

      <Button
        variant={isPaused ? "default" : "outline"}
        size="sm"
        data-testid="butler-pause"
        disabled={setEligibility.isPending}
        onClick={handlePauseToggle}
      >
        {setEligibility.isPending
          ? isPaused
            ? "Resuming…"
            : "Pausing…"
          : isPaused
            ? "Resume"
            : "Pause"}
      </Button>

      <ChatPanel butlerName={butlerName} />
    </div>
  );
}
