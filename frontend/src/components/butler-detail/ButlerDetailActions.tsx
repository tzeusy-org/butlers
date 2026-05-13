// ---------------------------------------------------------------------------
// ButlerDetailActions
//
// Composes the Page shell `actions` slot for the Butler detail page per the
// Gate-A A2 resolution (bu-rx6c2):
//
//   status pill | force-run button | pause/resume button | ChatPanel button
//
// Status pill:    derived from butler.status (ok/degraded/down/error/unknown)
// Force-run:      calls triggerButler with a default scheduler prompt
// Pause/Resume:   sets eligibility to "quarantined" (pause) or "active" (resume)
//                 via the Switchboard registry eligibility API
//
// NO Tier-2 hero block is added — identity stays in the Overview tab card.
// ---------------------------------------------------------------------------

import { useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { triggerButler } from "@/api/index.ts";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { Button } from "@/components/ui/button";
import { useRegistry, useSetEligibility } from "@/hooks/use-general";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const operationalButtonClassName =
  "h-7 rounded-[3px] border-border bg-transparent px-2.5 font-mono text-[10px] font-medium uppercase tracking-[0.06em] shadow-none " +
  "hover:bg-muted/50 hover:text-foreground dark:bg-transparent dark:border-border dark:hover:bg-muted/50";

const primaryOperationalButtonClassName =
  "h-7 rounded-[3px] border-foreground bg-foreground px-2.5 font-mono text-[10px] font-medium uppercase tracking-[0.06em] text-background shadow-none " +
  "hover:bg-foreground/90 hover:text-background dark:border-foreground dark:bg-foreground dark:text-background dark:hover:bg-foreground/90";

function actionLinkClassName(): string {
  return [
    "inline-flex h-7 items-center rounded-[3px] border border-border bg-transparent px-2.5",
    "font-mono text-[10px] font-medium uppercase tracking-[0.06em] text-foreground shadow-none",
    "transition-colors hover:bg-muted/50 hover:text-foreground",
    "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
  ].join(" ");
}

interface ButlerDetailActionsProps {
  butlerName: string;
  /** Allows target actions to switch into the tab vocabulary they address. */
  onModeChange?: (mode: "resident" | "operator") => void;
}

// ---------------------------------------------------------------------------
// ButlerDetailActions
// ---------------------------------------------------------------------------

export function ButlerDetailActions({
  butlerName,
  onModeChange,
}: ButlerDetailActionsProps) {
  const { data: registryResponse, isLoading: registryLoading } = useRegistry();
  const setEligibility = useSetEligibility();

  const [isForceRunning, setIsForceRunning] = useState(false);

  // Find the registry entry to determine current eligibility / paused state.
  // registryEntry is undefined while loading or when the butler is not in the
  // registry; disable the pause control until we have a known state.
  const registryEntry = registryResponse?.data?.find((r) => r.name === butlerName);
  const isPaused = registryEntry?.eligibility_state === "quarantined";
  const pauseDisabled = registryLoading || registryEntry === undefined || setEligibility.isPending;

  async function handleForceRun() {
    if (isForceRunning) return;
    setIsForceRunning(true);
    try {
      await triggerButler(butlerName, "Run your scheduled tick now.", "medium");
      toast.success("Force run triggered");
    } catch {
      toast.error("Failed to trigger force run");
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
      <Button
        variant="outline"
        size="sm"
        data-testid="butler-force-run"
        disabled={isForceRunning}
        onClick={handleForceRun}
        className={operationalButtonClassName}
      >
        {isForceRunning ? "Running…" : "Force Run"}
      </Button>

      <Link
        to="?tab=logs"
        onClick={() => onModeChange?.("resident")}
        className={actionLinkClassName()}
        data-testid="butler-logs-link"
      >
        Logs
      </Link>

      <Link to="?tab=config" className={actionLinkClassName()} data-testid="butler-config-link">
        Config
      </Link>

      <ChatPanel
        butlerName={butlerName}
        triggerClassName={operationalButtonClassName}
        triggerLabel="Prompt"
        showTriggerIcon={false}
      />

      <Button
        variant={isPaused ? "default" : "outline"}
        size="sm"
        data-testid="butler-pause"
        disabled={pauseDisabled}
        onClick={handlePauseToggle}
        className={isPaused ? operationalButtonClassName : primaryOperationalButtonClassName}
      >
        {setEligibility.isPending
          ? isPaused
            ? "Resuming…"
            : "Pausing…"
          : isPaused
            ? "Resume"
            : "Pause"}
      </Button>

    </div>
  );
}
